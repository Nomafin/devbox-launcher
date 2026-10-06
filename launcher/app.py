import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
# Bound by name: `threading` itself is swapped out in one test.
from threading import Lock

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import authctl as _authctl
from . import discovery as _discovery
from . import gitremote as _gitremote
from . import hostinfo as _hostinfo
from . import instances as _instances
from . import pointer as _pointer
from . import runctl as _runctl
from . import state as _state
from . import tmuxctl as _tmuxctl
from . import trust as _trust
from . import ui as _ui
from . import worktrees as _worktrees
from .config import base_dir as _base_dir
from .config import run_user as _run_user

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


# create_app(run_user=...) left out: read LAUNCHER_RUN_USER. None disables it.
_FROM_ENV = object()


def create_app(base_dir_fn=None, tmx=_tmuxctl, trust=_trust, disc=_discovery,
               ptr=_pointer, st=_state, auth=_authctl, git=_gitremote,
               host=_hostinfo, runner=_runctl, run_user=_FROM_ENV,
               wt=_worktrees, inst=_instances) -> FastAPI:
    resolve_base = base_dir_fn or _base_dir
    # Resolved once: the unit's environment does not change under a running app.
    allowed_user = _run_user() if run_user is _FROM_ENV else run_user

    def resolve(slug: str):
        """A project or one of its instances. Instances come from state, so
        every route that took a slug now takes an instance's too."""
        return disc.resolve_slug(resolve_base(), slug, st.instances())

    # One lock per slug: a Stop landing in the middle of a Start (the row is
    # tappable while the listener settles, and reconcile runs on its own
    # thread) must not have the two interleave their tmux calls.
    locks: dict[str, Lock] = {}
    locks_guard = Lock()

    # One-shot messages for a row: a refused name, or git's own reason. Popped
    # when the row that owns them is rendered, so the answer reaches the page
    # that asked for it and is not still there on the next poll.
    notes: dict[str, str] = {}

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
            relative = Path(path).relative_to(Path.home())
        except ValueError:
            return str(path)
        return "~" if relative == Path(".") else f"~/{relative}"

    def run_label(run) -> tuple[str, str]:
        """What the row says about a command started from it, and its tone."""
        if run is None or not run.exists:
            return "", ""
        if run.running:
            return "Command running", "busy"
        if run.exit_code is None:
            return "Command finished", "bad"
        return (f"Command finished · exit {run.exit_code}",
                "ok" if run.exit_code == 0 else "bad")

    def row_html(project, status, parallel: bool, run=None, instance: bool = False,
                blocked: bool = False, origin_slug: str | None = None,
                permission: str | None = None) -> str:
        # No Run control at all where the page would refuse it anyway.
        label, tone = run_label(run)
        run_kw = {"run": label, "run_tone": tone} if allowed_user else {}
        """The row markup for one project, as both the page and /api/* serve it.

        Everything it shows comes from the batched status: the row asks tmux
        nothing on its own.
        """
        # Flagged whether running or not — knowing before you tap is the point.
        # `blocked` is passed in rather than asked of `project.cwd` here: an
        # instance's cwd is its worktree, whose `.git` is a file, so asking
        # directly would always say no even though the worktree shares its
        # parent's remotes and fails to create a session the exact same way.
        note = notes.pop(project.slug, None)
        warning = note or (_ORIGIN_WARNING if blocked else None)
        # The origin warning's Rename button fixes that warning specifically;
        # a refused name or git's own error has nothing for it to do.
        new_instance = not instance
        fix_remote = note is None
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
                           parallel=None if instance else parallel,
                           permission=permission,
                           instance=instance, remove=instance,
                           new_instance=new_instance, fix_remote=fix_remote,
                           origin_slug=origin_slug, **run_kw)
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
                       action="stop", label="Stop",
                       permission=permission,
                       instance=instance, remove=instance,
                       new_instance=new_instance, fix_remote=fix_remote,
                       origin_slug=origin_slug, **run_kw)

    def snapshot() -> dict:
        """Everything the page shows, as data. The rendered row travels with
        each project so the client never needs a second copy of the template.

        One batched pass answers for every project, so a poll costs two tmux
        calls plus one per *running* listener — not four per project.
        """
        projects = disc.discover_projects(resolve_base())
        table = st.instances()
        # Each project with its instances, so a row's children follow it
        # wherever the running-first sort puts it. The parent Project is kept
        # (not just its slug) so an instance row can be built from it directly
        # below — resolving each instance slug from scratch would re-walk
        # ~/projects once per instance for a project already in hand.
        groups = [(p, table.get(p.slug, [])) for p in projects]
        slugs = [slug for p, names in groups
                 for slug in (p.slug, *(disc.instance_of(p, n).slug for n in names))]
        statuses = tmx.snapshot(slugs)
        # One more tmux call for the whole list, and only when the feature is on.
        runs = runner.statuses() if allowed_user else {}
        parallel = set(st.parallel())
        # One read of desired.json for the whole page, as for parallel: the
        # picker is on every row, and a row must not cost a file read.
        modes = st.permission_modes()
        # Running first, alphabetical within each group: eleven projects is more
        # than one phone screen, and the ones you care about are the live ones.
        # discover_projects already sorts by name, and sorted() is stable, so
        # only the running/stopped split is applied here.
        groups.sort(key=lambda g: not statuses[g[0].slug].running)
        rows = []
        for project, names in groups:
            # Asked once per project, from the project's own cwd: an instance
            # is a worktree of it, sharing the same remotes, so its answer is
            # this one too — asking again from the worktree's cwd would always
            # come back False (a worktree's `.git` is a file, not a directory).
            blocked = git.blocks_remote_control(project.cwd)
            rows.append({"slug": project.slug,
                         "html": row_html(project, statuses[project.slug],
                                          project.slug in parallel,
                                          runs.get(project.slug), blocked=blocked,
                                          permission=modes.get(project.slug))})
            for name in names:
                child = disc.instance_of(project, name)
                rows.append({"slug": child.slug,
                             "html": row_html(child, statuses[child.slug], False,
                                              runs.get(child.slug), instance=True,
                                              blocked=blocked, origin_slug=project.slug,
                                              permission=modes.get(child.slug))})
        live = sum(1 for p, _ in groups if statuses[p.slug].running)
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
                        signed_in=data["signedIn"], run=bool(allowed_user))

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
            # The listener fixes its permission mode at startup, so every
            # path that starts one reads it here — a restore after a reboot
            # must bring the sessions back in the mode the row last asked for.
            permission = st.permission_mode(project.slug)
            try:
                if project.slug in st.parallel():
                    # Worktree mode, and never a resume: the CLI refuses
                    # --session-id with --spawn, and a resumed listener is
                    # single-session — the opposite of what was asked for.
                    tmx.start(project, spawn="worktree", permission_mode=permission)
                else:
                    tmx.start(project, session_id=ptr.session_id(project.cwd),
                              permission_mode=permission)
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
        for slug in st.desired():
            # Every per-slug step is guarded, not just launch(): resolve_slug or
            # is_running can also raise (e.g. a permission error walking the
            # projects tree), and one bad slug must not abort the rest of the pass.
            try:
                project = resolve(slug)
                if project is None:
                    log.warning("restore: project %s no longer exists, forgetting it", slug)
                    st.record_stopped(slug)
                    # An instance's entry names a worktree under a project that
                    # is gone too; without this a later re-clone resurrects a
                    # row pointing at a worktree that no longer exists.
                    parts = inst.split(slug)
                    if parts is not None:
                        st.forget_instance(*parts)
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
        project = resolve(slug)
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

    @app.post("/instances/{slug}")
    def instance_create(slug: str, name: str = Form("")):
        project = resolve(slug)
        # An instance of an instance is a worktree of a worktree: no.
        if project is None or inst.split(slug):
            return RedirectResponse("/", status_code=303)
        name = name.strip()
        if not inst.valid(name):
            notes[slug] = "Use lowercase letters, numbers and dashes, e.g. fix-auth."
        elif name in st.instances().get(slug, []):
            notes[slug] = f"There is already an instance called {name}."
        else:
            error = wt.create(project.cwd, name)
            if error:
                notes[slug] = error
            else:
                st.record_instance(slug, name)
                instance = resolve(inst.slug(slug, name))
                # The project can vanish between this resolve and the one that
                # found it above (a concurrent delete, a redeploy) — rare, but
                # a 500 here is a worse answer than the create silently having
                # nothing left to launch.
                if instance is not None:
                    st.record_started(instance.slug)
                    launch(instance)
        return RedirectResponse("/", status_code=303)

    @app.post("/api/instances/{slug}")
    def api_instance_create(slug: str, name: str = Form("")):
        instance_create(slug, name)
        return snapshot()

    @app.post("/instances/{slug}/remove")
    def instance_remove(slug: str):
        parts = inst.split(slug)
        instance = resolve(slug)
        if parts is None or instance is None:
            return RedirectResponse("/", status_code=303)
        project_slug, name = parts
        parent = resolve(project_slug)
        if parent is None:
            return RedirectResponse("/", status_code=303)
        if wt.exists(parent.cwd, name):
            # Asked before anything is stopped: a Remove that refuses must not
            # also cost the session it refused to remove.
            blocked = wt.blockers(instance.cwd)
            if blocked:
                notes[slug] = blocked
                return RedirectResponse("/", status_code=303)
        with lock_for(slug):
            st.record_stopped(slug)
            tmx.stop(slug)
        if wt.exists(parent.cwd, name):
            error = wt.remove(parent.cwd, name)
            if error:
                notes[slug] = error
                return RedirectResponse("/", status_code=303)
        else:
            # The directory went behind git's back; without this the next
            # instance of that name cannot be created.
            wt.prune(parent.cwd)
        st.forget_instance(project_slug, name)
        log.info("instance: removed %s", slug)
        return RedirectResponse("/", status_code=303)

    @app.post("/api/instances/{slug}/remove")
    def api_instance_remove(slug: str):
        instance_remove(slug)
        return snapshot()

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
        project = resolve(slug)
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
        project = resolve(slug)
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
        # A worktree of a worktree: the create route already refuses this for
        # the same reason, and no UI reaches this route with an instance slug,
        # but the guard belongs here too rather than trusting every caller.
        if resolve(slug) is not None and inst.split(slug) is None:
            st.set_parallel(slug, on == "1")
        return RedirectResponse("/", status_code=303)

    @app.post("/api/parallel/{slug}")
    def api_set_parallel(slug: str, on: str = Form("")):
        set_parallel(slug, on)
        return snapshot()

    # Which permission mode the listener's sessions run in. The Claude app has
    # no control over this and the CLI fixes it at startup, so a session that
    # stops to ask about every edit could otherwise only be fixed from a
    # terminal — which is exactly what this page exists to avoid needing.
    @app.post("/mode/{slug}")
    def set_mode(slug: str, mode: str = Form("")):
        project = resolve(slug)
        # An unknown mode is a client that has drifted from the server, not
        # something to pass to the CLI: the listener would die on start with
        # the row's own Start button as the only way back.
        if project is None or mode not in _tmuxctl.PERMISSION_MODES:
            return RedirectResponse("/", status_code=303)
        # "default" is recorded as nothing at all, so the launcher stays out of
        # the way of a defaultMode in settings.json rather than overriding it
        # with a flag that happens to have the same name.
        st.set_permission_mode(slug, None if mode == "default" else mode)
        # A running listener took its mode from the command line seconds or
        # days ago; the new one only reaches it through a restart. Start's own
        # resume path then brings the same conversation back, so the cost is
        # the seven seconds it takes to settle, not the conversation.
        if tmx.is_running(slug):
            # Intent follows the listener that is actually up, exactly as a
            # Start would record it: launch() reads it to tell a failure it
            # should retry from one the user asked for by tapping Stop.
            st.record_started(slug)
            with lock_for(slug):
                tmx.stop(slug)
            launch(project)
            log.info("mode: restarted %s in permission mode %s", slug, mode)
        return RedirectResponse("/", status_code=303)

    @app.post("/api/mode/{slug}")
    def api_set_mode(slug: str, mode: str = Form("")):
        set_mode(slug, mode)
        return snapshot()

    @app.get("/api/pane/{slug}")
    def api_pane(slug: str):
        """The last lines a session printed.

        Deliberately not part of the snapshot: it is a capture per session, and
        paying that on every poll for output nobody is looking at is exactly the
        cost this design removed. Fetched only when the disclosure is opened.
        """
        if resolve(slug) is None:
            return {"lines": []}
        return {"lines": tmx.pane_tail(slug)}

    # The launcher's only write into a repo, so it is a route of its own rather
    # than something Start does silently on your behalf.
    @app.post("/fix-remote/{slug}")
    def fix_remote(slug: str):
        project = resolve(slug)
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

    # --- running a command ------------------------------------------------
    # A shell on the box, so it is gated twice. Tailscale Serve stamps every
    # request it proxies with the tailnet login, and only the configured one
    # gets in. And a POST must come from this page: the phone's browser gets
    # that header added to *any* request to the box, including one a hostile
    # page makes it send, so a cross-site form must not be able to run a
    # command. Neither check stops a process on the box itself, which can reach
    # 127.0.0.1 directly — that is why Claude sessions are told never to call
    # this; it exists for the person to run what Claude may not.
    #
    # Every route has an /api/* twin returning run_state() — including the
    # server-rendered panel — which is what the main page's modal drives. The
    # plain routes serve /run/<slug>, the same panel as a page, for no-JS use.
    def same_origin(request: Request) -> bool:
        site = request.headers.get("sec-fetch-site")
        if site is not None:
            return site == "same-origin"
        origin = request.headers.get("origin")
        if origin is None:
            return True
        hosts = {request.headers.get("host"), request.headers.get("x-forwarded-host")}
        return urlsplit(origin).netloc in hosts

    def run_allowed(request: Request) -> bool:
        if not allowed_user or request.headers.get("tailscale-user-login") != allowed_user:
            return False
        return request.method == "GET" or same_origin(request)

    def run_project(request: Request, slug: str):
        """The project, or the response that refuses the request."""
        if not run_allowed(request):
            return None, HTMLResponse(_ui.run_page("Not allowed", _ui.run_forbidden()),
                                      status_code=403)
        project = resolve(slug)
        if project is None:
            return None, HTMLResponse(_ui.run_page("No such project", ""), status_code=404)
        return project, None

    def run_panel(project, status) -> str:
        if not status.exists:
            return _ui.run_form(slug=project.slug)
        record = runner.last(project.slug)
        return _ui.run_view(
            slug=project.slug, command=record.get("command", ""),
            cwd=_home_relative(record["cwd"]) if record.get("cwd") else "",
            output=runner.output(project.slug), running=status.running,
            exit_code=status.exit_code)

    def run_state(project) -> dict:
        status = runner.status(project.slug)
        return {"exists": status.exists, "running": status.running,
                "exitCode": status.exit_code,
                "output": runner.output(project.slug) if status.exists else "",
                "html": run_panel(project, status)}

    def do_run_start(project, command: str) -> None:
        command = command.strip()
        # Never replace a command still running: its output and its prompt are
        # what the person is in the middle of dealing with.
        if not command or runner.status(project.slug).running:
            return
        try:
            runner.start(project.slug, command, project.cwd)
        except _tmuxctl.StartError as exc:
            log.warning("run: %s: tmux refused to start the command: %s", project.slug, exc)
            return
        log.info("run: %s ran %r in %s (%s)", allowed_user, command, project.cwd, project.slug)

    def do_run_input(project, text: str) -> None:
        # Not logged: what gets typed at a prompt is often a code or a secret.
        if runner.status(project.slug).running:
            runner.send_input(project.slug, text.strip())

    def do_run_interrupt(project) -> None:
        if runner.status(project.slug).running:
            runner.interrupt(project.slug)

    def back_to_run(project) -> RedirectResponse:
        return RedirectResponse(f"/run/{project.slug}", status_code=303)

    @app.post("/run/{slug}")
    def run_start(request: Request, slug: str, command: str = Form("")):
        project, refused = run_project(request, slug)
        if refused:
            return refused
        do_run_start(project, command)
        return back_to_run(project)

    @app.post("/api/run/{slug}")
    def api_run_start(request: Request, slug: str, command: str = Form("")):
        project, refused = run_project(request, slug)
        if refused:
            return refused
        do_run_start(project, command)
        return run_state(project)

    @app.post("/run/{slug}/input")
    def run_input(request: Request, slug: str, text: str = Form("")):
        project, refused = run_project(request, slug)
        if refused:
            return refused
        do_run_input(project, text)
        return back_to_run(project)

    @app.post("/api/run/{slug}/input")
    def api_run_input(request: Request, slug: str, text: str = Form("")):
        project, refused = run_project(request, slug)
        if refused:
            return refused
        do_run_input(project, text)
        return run_state(project)

    @app.post("/run/{slug}/interrupt")
    def run_interrupt(request: Request, slug: str):
        project, refused = run_project(request, slug)
        if refused:
            return refused
        do_run_interrupt(project)
        return back_to_run(project)

    @app.post("/api/run/{slug}/interrupt")
    def api_run_interrupt(request: Request, slug: str):
        project, refused = run_project(request, slug)
        if refused:
            return refused
        do_run_interrupt(project)
        return run_state(project)

    @app.post("/run/{slug}/close")
    def run_close(request: Request, slug: str):
        project, refused = run_project(request, slug)
        if refused:
            return refused
        runner.close(project.slug)
        return back_to_run(project)

    @app.post("/api/run/{slug}/close")
    def api_run_close(request: Request, slug: str):
        project, refused = run_project(request, slug)
        if refused:
            return refused
        runner.close(project.slug)
        return run_state(project)

    @app.get("/run/{slug}", response_class=HTMLResponse)
    def run_page_route(request: Request, slug: str):
        project, refused = run_project(request, slug)
        if refused:
            return refused
        return _ui.run_page(project.name, run_panel(project, runner.status(project.slug)),
                            slug=project.slug)

    @app.get("/api/run/{slug}")
    def api_run(request: Request, slug: str):
        project, refused = run_project(request, slug)
        if refused:
            return refused
        return run_state(project)

    app.state.reconcile = reconcile

    return app


app = create_app()
