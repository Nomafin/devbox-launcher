import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
# Bound by name: `threading` itself is swapped out in one test.
from threading import Lock

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from . import authctl as _authctl
from . import discovery as _discovery
from . import gitremote as _gitremote
from . import hostinfo as _hostinfo
from . import pointer as _pointer
from . import state as _state
from . import tmuxctl as _tmuxctl
from . import trust as _trust
from . import ui as _ui
from .config import base_dir as _base_dir

# Messages carry their own "restore:"/"start:" prefix, so one logger covers both.
log = logging.getLogger("launcher")

# What each listener state is called on the page. A running tmux session is not
# the same thing as a session the Claude app can reach, so only CONNECTED reads
# as live. The words are the user's, not the CLI's: "live" is what they want to
# know, and a failure says what failed rather than naming an internal state.
_STATES = {
    _tmuxctl.CONNECTED: "Live",
    _tmuxctl.READY: "No session yet",
    _tmuxctl.STARTING: "Starting…",
    _tmuxctl.FAILED: "Start failed",
    _tmuxctl.STUCK: "Stuck",
}

# What the row says to do about a state it cannot fix by itself.
_ADVICE = {
    _tmuxctl.STUCK: "No session after 30s. Stop, then Start to retry.",
}

# The documented fix for the origin gotcha, in the place it actually bites.
_ORIGIN_WARNING = ("This repo's origin points at GitHub, which stops a listener "
                   "creating a session.")

# `claude auth status` is a subprocess that talks to the network, and the page
# polls several times a minute; a login does not lapse between one poll and
# the next, so the answer is kept this long. Keyed on the auth module so an
# injected fake never reads another's answer.
AUTH_STATUS_TTL = 30.0
_auth_cache: dict = {"auth": None, "at": 0.0, "value": False}


def _signed_in(auth) -> bool:
    now = time.monotonic()
    if _auth_cache["auth"] is auth and now - _auth_cache["at"] < AUTH_STATUS_TTL:
        return _auth_cache["value"]
    value = bool(auth.status())
    _auth_cache.update(auth=auth, at=now, value=value)
    return value


def _forget_signed_in() -> None:
    _auth_cache["auth"] = None


def _age(seconds: int | None) -> str:
    """How long this listener has been up, at a glance: a session you forgot
    nine days ago should not look like one you started a minute ago."""
    if seconds is None:
        return ""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"

def _mode(parallel: bool, sessions: int | None) -> str:
    """What a running listener is doing beyond its state: worktree mode, and
    how many sessions it holds once there is more than one to count."""
    parts = []
    if parallel:
        parts.append("Parallel")
    if sessions is not None and sessions > 1:
        parts.append(f"{sessions} sessions")
    return " · ".join(parts)


def create_app(base_dir_fn=None, tmx=_tmuxctl, trust=_trust, disc=_discovery,
               ptr=_pointer, st=_state, auth=_authctl, git=_gitremote,
               host=_hostinfo) -> FastAPI:
    resolve_base = base_dir_fn or _base_dir

    # One lock per slug: a Stop landing in the middle of a Start (the row is
    # tappable while the listener settles, and reconcile runs on its own
    # thread) must not have the two interleave their tmux calls.
    locks: dict[str, Lock] = {}
    locks_guard = Lock()

    def lock_for(slug: str) -> Lock:
        with locks_guard:
            return locks.setdefault(slug, Lock())

    def spawn_reconcile() -> None:
        # Restore on a daemon thread: each listener takes seconds to settle and
        # the web UI must answer immediately. Starting the thread itself can
        # fail (e.g. "can't start new thread" under boot-time resource
        # pressure); if it raises during startup, uvicorn treats lifespan as
        # failed and exits, and systemd's restart limit can then leave the
        # unit dead with no remote way to recover the box. Restore is a
        # best-effort convenience — it must never be able to prevent the web
        # app itself from serving.
        try:
            threading.Thread(target=reconcile, daemon=True).start()
        except Exception:
            log.exception("restore: failed to start the reconcile thread")

    @asynccontextmanager
    async def lifespan(_app):
        spawn_reconcile()
        yield

    app = FastAPI(lifespan=lifespan)

    def _home_relative(path) -> str:
        """`~/projects/demo` rather than `/home/dev/projects/demo` — the
        prefix is the same on every row, so it is width a phone cannot spare."""
        try:
            return f"~/{Path(path).relative_to(Path.home())}"
        except ValueError:
            return str(path)

    def row_html(project, status, parallel: bool) -> str:
        """The row markup for one project, as both the page and /api/* serve it.

        Everything it shows comes from the batched status: the row asks tmux
        nothing on its own.
        """
        # Flagged whether running or not — knowing before you tap is the point.
        warning = _ORIGIN_WARNING if git.blocks_remote_control(project.cwd) else None
        if not status.running:
            # Start resumes the recorded conversation; offer the other option
            # only when there is actually one to discard, so the control appears
            # exactly when it would do something different.
            return _ui.row(slug=project.slug, name=project.name, state=_tmuxctl.STOPPED,
                           ident=_home_relative(project.cwd),
                           action="start", label="Start", warning=warning,
                           # A parallel listener never resumes, so there is
                           # nothing for a fresh start to do differently.
                           fresh=not parallel and ptr.session_id(project.cwd) is not None,
                           parallel=parallel)
        # "Stop, then Start to retry" was useless advice when the real problem
        # was elsewhere — e.g. the box being logged out, which fails every
        # listener the same silent way — so the row carries the CLI's own
        # reason instead.
        return _ui.row(slug=project.slug, name=project.name, state=status.state,
                       status=_STATES.get(status.state, status.state),
                       # Pure function of the slug, so it comes from the real
                       # module rather than the injected (fakeable) one.
                       ident=_tmuxctl.session_name(project.slug),
                       age=_age(status.age),
                       mode=_mode(parallel, status.sessions),
                       reason=status.reason or _ADVICE.get(status.state),
                       url=status.url, warning=warning,
                       # Stop, then Start, is what you always did next to a
                       # broken listener; one control does both. Stop keeps its
                       # meaning in the head — this is offered beside the
                       # explanation of what went wrong.
                       retry=status.state in (_tmuxctl.FAILED, _tmuxctl.STUCK),
                       action="stop", label="Stop")

    def snapshot() -> dict:
        """Everything the page shows, as data. The rendered row travels with
        each project so the client never needs a second copy of the template.

        One batched pass answers for every project, so a poll costs two tmux
        calls plus one per *running* listener — not four per project.
        """
        projects = disc.discover_projects(resolve_base())
        statuses = tmx.snapshot([p.slug for p in projects])
        parallel = set(st.parallel())
        # Running first, alphabetical within each group: eleven projects is more
        # than one phone screen, and the ones you care about are the live ones.
        # discover_projects already sorts by name, and sorted() is stable, so
        # only the running/stopped split is applied here.
        projects.sort(key=lambda p: not statuses[p.slug].running)
        rows = [{"slug": p.slug, "html": row_html(p, statuses[p.slug], p.slug in parallel)}
                for p in projects]
        live = sum(1 for p in projects if statuses[p.slug].running)
        # What is left of the box: the question you ask right before tapping
        # Start. Disk sits beside memory because a box that builds Docker images
        # into a 20 GB rootfs runs out of that sooner than out of 12 GB of RAM.
        usage = " · ".join(part for part in (
            f"RAM {host.memory()}" if host.memory() else "",
            f"Disk {host.disk()}" if host.disk() else "",
        ) if part)
        return {
            "projects": rows,
            "count": f"{live} of {len(projects)} running" if projects else "",
            "usage": usage,
            "signedIn": _signed_in(auth),
        }

    def render() -> str:
        data = snapshot()
        return _ui.page(rows=[p["html"] for p in data["projects"]],
                        count=data["count"], usage=data["usage"],
                        signed_in=data["signedIn"])

    def start_listener(project) -> bool:
        """One start attempt, under the slug's lock. False when tmux refused."""
        with lock_for(project.slug):
            # A previous listener that died is still holding its session open
            # so its error stays readable. tmux refuses a duplicate name, so
            # clear the corpse or this start silently does nothing.
            if tmx.is_dead(project.slug):
                tmx.stop(project.slug)
            # Resume the conversation the bridge last recorded, so a reboot
            # picks up where it left off instead of opening a fresh one. None
            # means this project has never run a listener, and the listener
            # registers fresh.
            try:
                if project.slug in st.parallel():
                    # Worktree mode, and never a resume: the CLI refuses
                    # --session-id with --spawn, and a resumed listener is
                    # single-session — the opposite of what was asked for.
                    tmx.start(project, spawn="worktree")
                else:
                    tmx.start(project, session_id=ptr.session_id(project.cwd))
            except _tmuxctl.StartError as exc:
                log.warning("start: %s: tmux refused to start the listener: %s",
                            project.slug, exc)
                return False
        return True

    def launch(project) -> None:
        trust.ensure_trusted(project.cwd)
        if not start_listener(project):
            return
        if tmx.wait_for_health(project.slug) == _tmuxctl.FAILED:
            # A Stop while the listener was settling kills it, which reads as
            # FAILED here — but that one was asked for, so it is not retried.
            if project.slug not in st.desired():
                return
            # Almost always a stale resume pointer: the retained environment
            # was archived server-side, so the adopted session dies on spawn.
            # Drop the pointer and retry once — that start registers fresh.
            ptr.clear(project.cwd)
            with lock_for(project.slug):
                tmx.stop(project.slug)
            # Re-read rather than reusing the id above: clear() just removed it,
            # so this retry starts fresh. Passing the dead session again would
            # fail identically and the listener would never come up.
            if not start_listener(project):
                return
            if tmx.wait_for_health(project.slug) == _tmuxctl.FAILED:
                # Both attempts died, so this is not the stale-pointer case the
                # retry is for — something outside the launcher is wrong (the
                # box being logged out is the one that has actually happened).
                # Say so in the journal instead of redirecting in silence.
                log.warning("start: %s failed to come up: %s", project.slug,
                            tmx.failure_reason(project.slug) or "no output on the pane")

    def reconcile() -> None:
        """Bring listeners back to the state the user last asked for.

        Runs once at startup. tmux sessions die with the host, so after a reboot
        this is what puts the phone-started listeners back without a phone.
        """
        base = resolve_base()
        for slug in st.desired():
            # Every per-slug step is guarded, not just launch(): resolve_slug or
            # is_running can also raise (e.g. a permission error walking the
            # projects tree), and one bad slug must not abort the rest of the pass.
            try:
                project = disc.resolve_slug(base, slug)
                if project is None:
                    log.warning("restore: project %s no longer exists, forgetting it", slug)
                    st.record_stopped(slug)
                    continue
                # A dead session still answers has-session, so check that the
                # listener is actually alive — otherwise a failed one is skipped
                # here for good, and the tmux server outlives this service
                # (KillMode=process) so a redeploy would never clear it.
                if tmx.is_running(slug) and not tmx.is_dead(slug):
                    continue
                launch(project)
                log.info("restore: started %s", slug)
            except Exception:
                # boot-only, no retries: leave it desired-running and let the UI
                # show its real state rather than loop unattended
                log.exception("restore: failed to restore %s", slug)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return render()

    @app.post("/start/{slug}")
    def start(slug: str):
        project = disc.resolve_slug(resolve_base(), slug)
        if project is not None:
            # record intent before launching: a project that fails to start is
            # still one the user wants running, so it is retried at next boot
            st.record_started(slug)
            launch(project)
        return RedirectResponse("/", status_code=303)

    @app.post("/stop/{slug}")
    def stop(slug: str):
        with lock_for(slug):
            # Intent first: a launch() mid-wait for this slug reads it and
            # leaves the listener stopped instead of "healing" it back up.
            st.record_stopped(slug)
            tmx.stop(slug)
        return RedirectResponse("/", status_code=303)

    # The /api/* twins of the three routes above. The forms keep working
    # without JavaScript — these exist so a tap can give feedback on the row it
    # happened on instead of a full white reload, which on a phone is the whole
    # difference between the UI feeling dead and feeling responsive.
    #
    # Start still blocks while the listener settles (~7 s): the honest answer is
    # worth the wait, and the row says "Starting…" for exactly as long as it
    # actually is.
    @app.get("/api/projects")
    def api_projects():
        return snapshot()

    @app.post("/api/start/{slug}")
    def api_start(slug: str):
        start(slug)
        return snapshot()

    @app.post("/api/stop/{slug}")
    def api_stop(slug: str):
        stop(slug)
        return snapshot()

    # Recovering a broken listener was always Stop, wait, Start, wait. One
    # control does both, and clears the resume pointer on the way: a listener
    # that failed or wedged is exactly the case where the recorded session is
    # the prime suspect, so a Retry that re-adopted it would repeat the failure.
    @app.post("/retry/{slug}")
    def retry(slug: str):
        project = disc.resolve_slug(resolve_base(), slug)
        if project is not None:
            st.record_started(slug)
            tmx.stop(slug)
            ptr.clear(project.cwd)
            launch(project)
        return RedirectResponse("/", status_code=303)

    @app.post("/api/retry/{slug}")
    def api_retry(slug: str):
        retry(slug)
        return snapshot()

    # Start resumes the conversation the bridge recorded; this is the way to
    # deliberately not do that. The row only offers it when a pointer exists.
    @app.post("/start-fresh/{slug}")
    def start_fresh(slug: str):
        project = disc.resolve_slug(resolve_base(), slug)
        if project is not None:
            ptr.clear(project.cwd)
            st.record_started(slug)
            launch(project)
        return RedirectResponse("/", status_code=303)

    @app.post("/api/start-fresh/{slug}")
    def api_start_fresh(slug: str):
        start_fresh(slug)
        return snapshot()

    # Which spawn mode the next Start uses. Offered on stopped rows only: the
    # mode is fixed when a listener starts.
    @app.post("/parallel/{slug}")
    def set_parallel(slug: str, on: str = Form("")):
        if disc.resolve_slug(resolve_base(), slug) is not None:
            st.set_parallel(slug, on == "1")
        return RedirectResponse("/", status_code=303)

    @app.post("/api/parallel/{slug}")
    def api_set_parallel(slug: str, on: str = Form("")):
        set_parallel(slug, on)
        return snapshot()

    @app.get("/api/pane/{slug}")
    def api_pane(slug: str):
        """The last lines a session printed.

        Deliberately not part of the snapshot: it is a capture per session, and
        paying that on every poll for output nobody is looking at is exactly the
        cost this design removed. Fetched only when the disclosure is opened.
        """
        if disc.resolve_slug(resolve_base(), slug) is None:
            return {"lines": []}
        return {"lines": tmx.pane_tail(slug)}

    # The launcher's only write into a repo, so it is a route of its own rather
    # than something Start does silently on your behalf.
    @app.post("/fix-remote/{slug}")
    def fix_remote(slug: str):
        project = disc.resolve_slug(resolve_base(), slug)
        if project is not None:
            error = git.rename_origin(project.cwd)
            if error:
                log.warning("fix-remote: %s: %s", slug, error)
            else:
                log.info("fix-remote: renamed origin to github in %s", project.cwd)
        return RedirectResponse("/", status_code=303)

    @app.post("/api/fix-remote/{slug}")
    def api_fix_remote(slug: str):
        fix_remote(slug)
        return snapshot()

    @app.post("/login/start")
    def login_start():
        # Don't restart a flow already in progress: the PKCE challenge lives in
        # that process, so a restart invalidates the link the user may already
        # have open on their phone.
        if not auth.is_active():
            auth.start_login()
            auth.wait_for_url()
        return RedirectResponse("/login", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    def login_page() -> str:
        if auth.status():
            return _ui.login_page(_ui.login_done())
        error = auth.error()
        # A rejection that ended the flow spends its link, so the only way on is
        # a fresh one. A malformed paste doesn't: the CLI is still sitting at the
        # prompt, so show what went wrong and keep the same link and paste box.
        if error and not auth.is_active():
            return _ui.login_page(_ui.login_error(error))
        url = auth.authorize_url()
        if not url:
            return _ui.login_page(_ui.login_waiting())
        return _ui.login_page(_ui.login_steps(url=url, error=error))

    @app.post("/login/code")
    def login_code(code: str = Form("")):
        # Rejections already on the pane belong to earlier attempts; without
        # this baseline a stale one would read as this code having failed.
        baseline = len(auth.errors())
        # Phones love to add a leading space to a pasted value.
        auth.submit_code(code.strip())
        if not auth.wait_for_login(baseline=baseline):
            # Leave the session alone — its pane is holding the error that
            # /login is about to show.
            return RedirectResponse("/login", status_code=303)
        auth.cancel()
        _forget_signed_in()     # the cached "logged out" is now wrong
        # Signing in is exactly when the listeners are all down, so replay the
        # same intent restore uses at boot rather than making the user tap
        # Start once per project on a phone.
        log.info("login: signed in, restoring listeners")
        spawn_reconcile()
        return RedirectResponse("/", status_code=303)

    app.state.reconcile = reconcile

    return app


app = create_app()
