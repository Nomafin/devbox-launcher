import logging
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import authctl as _authctl
from . import discovery as _discovery
from . import gitremote as _gitremote
from . import hostinfo as _hostinfo
from . import runctl as _runctl
from . import tmuxctl as _tmuxctl
from . import trust as _trust
from . import ui as _ui
from .agents import places as _places
from .agents import registry as _registry
from .agents import runtime as _runtime
from .agents import service as _service
from .agents.model import ARCHIVED, MAIN, PARKED, SCRATCH, SEP, AgentError
from .config import base_dir as _base_dir
from .config import run_user as _run_user

log = logging.getLogger("launcher")

# The documented fix for the origin gotcha, in the place it actually bites.
_ORIGIN_WARNING = ("This repo's origin points at GitHub, which stops an agent "
                   "creating a Remote Control session.")

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
    """How long, at a glance: a session you forgot nine days ago should not
    look like one you started a minute ago."""
    if seconds is None:
        return ""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


# create_app(run_user=...) left out: read LAUNCHER_RUN_USER. None disables it.
_FROM_ENV = object()


_AGENT_STATES = {"connected": "Live", "starting": "Starting…", "ready": "Not connected",
                 "failed": "Start failed", "stopped": "", "broken": "Worktree missing"}

# The row key of the "agents could not be listed" notice. Not a slug any
# project or agent can have (slugs are [a-z0-9-], agent keys hold a "/").
_AGENTS_DOWN = "~agents"


def _pop(marks: set, key) -> bool:
    found = key in marks
    marks.discard(key)
    return found


def create_app(base_dir_fn=None, ctx_fn=None, svc=_service, trust=_trust, disc=_discovery,
               auth=_authctl, git=_gitremote, host=_hostinfo, runner=_runctl,
               run_user=_FROM_ENV) -> FastAPI:
    resolve_base = base_dir_fn or _base_dir
    allowed_user = _run_user() if run_user is _FROM_ENV else run_user

    def ctx():
        return ctx_fn() if ctx_fn else svc.Ctx.default(base_dir=resolve_base())

    # One-shot messages for a row (a refused name, git's reason, a stale
    # brief), popped when the row is rendered. A stale brief also marks the
    # row, for its "Fresh anyway" button; the mark goes with the note, so the
    # next render offers plain "Fresh conversation" and checks again.
    notes: dict[str, str] = {}
    stale: set[str] = set()

    app = FastAPI()

    def resolve(slug: str):
        """A discovered project, for Run command: it runs in a project's own
        checkout, never an agent's worktree."""
        return next((p for p in disc.discover_projects(resolve_base()) if p.slug == slug), None)

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

    def all_places(c) -> list:
        found = _places.discovered(c.base_dir)
        agents = _registry.load(c.root)
        known = {p.slug for p in found}
        extra = sorted({a.project for a in agents.values() if a.state != ARCHIVED} - known)
        return found + [p for s in extra if (p := _places.by_slug(c.base_dir, s, agents))]

    def agent_state(v) -> str:
        if v.broken:
            return "broken"
        if v.dead or (not v.running and v.agent.last_error):
            return "failed"
        if v.running:
            if v.connected:
                return "connected"
            return "starting" if (v.uptime or 0) < 30 else "ready"
        return "stopped"

    def agent_html(c, v) -> str:
        a = v.agent
        state = agent_state(v)
        status = "Parked" if a.state == PARKED and not v.running else _AGENT_STATES[state]
        if v.dead:      # died after its start; the corpse's output is kept
            status = ("Crashed" if v.exit_status is None
                      else f"Crashed (exit {v.exit_status})")
        url = _runtime.url(_runtime.pane(c.home, a.display, c.run)) if v.connected else None
        return _ui.agent_row(
            key=a.key, name=a.name if a.name != MAIN else "main", display=a.display,
            state="failed" if state == "broken" else state, status=status,
            running=v.running, parked=a.state == PARKED and not v.running,
            scratch=a.kind == SCRATCH, archivable=a.has_worktree, resumable=bool(a.session_id),
            broken=v.broken, mode=a.mode, age=_age(v.uptime) if v.running else None,
            active=f"active {_age(int(c.now() - v.last_active))} ago" if not v.running else None,
            mission=v.mission, reason=notes.pop(a.key, None) or a.last_error,
            url=url, stale=_pop(stale, a.key), output=v.running or v.dead)

    def project_html(place, blocked, run, listed: bool) -> str:
        label, tone = run_label(run)
        run_kw = {"run": label, "run_tone": tone} if allowed_user and listed else {}
        note = notes.pop(place.slug, None)
        return _ui.project_row(slug=place.slug, name=place.name, ident=_home_relative(place.cwd),
                               warning=note or (_ORIGIN_WARNING if blocked else None),
                               fix_remote=note is None, **run_kw)

    def snapshot() -> dict:
        c = ctx()
        listed = {p.slug for p in _places.discovered(c.base_dir)}
        # tmux on the agents socket that does not answer (wedged, timed out),
        # or a registry that cannot be read, raises rather than reading as
        # "nothing running". The page still renders: the projects, no agent
        # rows, and one row saying why (for the registry, where its .bak is).
        rows = []
        try:
            places = all_places(c)
            views = svc.views(c, places=places, check_connected=True)
        except AgentError as exc:
            places = _places.discovered(c.base_dir)
            log.warning("agents: listing failed: %s", exc)
            views = []
            rows.append({"slug": _AGENTS_DOWN, "html": _ui.notice_row(
                slug=_AGENTS_DOWN, title="Agents could not be listed", text=str(exc))})
        by_project: dict[str, list] = {p.slug: [] for p in places}
        for v in views:
            by_project.setdefault(v.agent.project, []).append(v)
        runs = runner.statuses() if allowed_user else {}
        places.sort(key=lambda p: (not any(v.running for v in by_project[p.slug]),
                                   p.slug))
        for place in places:
            blocked = git.blocks_remote_control(place.cwd)
            rows.append({"slug": place.slug,
                         "html": project_html(place, blocked, runs.get(place.slug),
                                              place.slug in listed)})
            for v in by_project[place.slug]:
                rows.append({"slug": v.agent.key, "html": agent_html(c, v)})
        live = sum(1 for v in views if v.running)
        usage = " · ".join(part for part in (
            f"RAM {host.memory()}" if host.memory() else "",
            f"Disk {host.disk()}" if host.disk() else "") if part)
        return {"projects": rows,
                "count": f"{live} agent{'' if live == 1 else 's'} running" if places else "",
                "usage": usage, "signedIn": _signed_in(auth),
                "parked": sum(1 for v in views if v.agent.state == PARKED and not v.running)}

    def render() -> str:
        data = snapshot()
        return _ui.page(rows=[p["html"] for p in data["projects"]], count=data["count"],
                        usage=data["usage"], signed_in=data["signedIn"],
                        run=bool(allowed_user), parked=data["parked"])

    def place_of(c, slug):
        return _places.by_slug(c.base_dir, slug, _registry.load(c.root))

    def act(project: str, name: str, action: str, force: bool = False, mode: str = "") -> None:
        c = ctx()
        place = place_of(c, project)
        if place is None:
            return
        key = f"{project}/{name}"
        stale.discard(key)
        try:
            if action == "start":
                svc.start(c, place, name)
            elif action == "fresh":
                svc.start(c, place, name, fresh=True, force=force)
            elif action == "repair":
                svc.start(c, place, name, repair=True)
            elif action == "stop":
                svc.stop(c, place, name)
            elif action == "park":
                svc.park(c, place, name)
            elif action == "archive":
                svc.archive(c, place, name)
            elif action == "mode":
                svc.set_mode(c, place, name, mode)
        except AgentError as exc:
            if exc.code == AgentError.STALE_BRIEF:
                stale.add(key)
                notes[key] = ("Its brief has not been updated since it was last active. "
                              "Run /handoff in it first, or start fresh anyway.")
            else:
                notes[key] = str(exc)
            log.warning("agents: %s %s: %s", action, key, exc)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return render()

    @app.get("/api/projects")
    def api_projects():
        return snapshot()

    @app.post("/agents/{project}/{name}/mode")
    def agent_mode(project: str, name: str, mode: str = Form("")):
        act(project, name, "mode", mode=mode)
        return RedirectResponse("/", status_code=303)

    @app.post("/api/agents/{project}/{name}/mode")
    def api_agent_mode(project: str, name: str, mode: str = Form("")):
        act(project, name, "mode", mode=mode)
        return snapshot()

    @app.post("/agents/{project}/{name}/{action}")
    def agent_action(project: str, name: str, action: str, force: str = Form("")):
        act(project, name, action, force=force == "1")
        return RedirectResponse("/", status_code=303)

    @app.post("/api/agents/{project}/{name}/{action}")
    def api_agent_action(project: str, name: str, action: str, force: str = Form("")):
        act(project, name, action, force=force == "1")
        return snapshot()

    def project_action(slug: str, action: str, name: str = "") -> None:
        c = ctx()
        place = place_of(c, slug)
        if place is None:
            return
        try:
            if action == "new":
                svc.new(c, place, name.strip())
            else:
                svc.scratch(c, place)
        except AgentError as exc:
            notes[slug] = str(exc)

    @app.post("/projects/{slug}/new")
    def project_new(slug: str, name: str = Form("")):
        project_action(slug, "new", name)
        return RedirectResponse("/", status_code=303)

    @app.post("/api/projects/{slug}/new")
    def api_project_new(slug: str, name: str = Form("")):
        project_action(slug, "new", name)
        return snapshot()

    @app.post("/projects/{slug}/scratch")
    def project_scratch(slug: str):
        project_action(slug, "scratch")
        return RedirectResponse("/", status_code=303)

    @app.post("/api/projects/{slug}/scratch")
    def api_project_scratch(slug: str):
        project_action(slug, "scratch")
        return snapshot()

    @app.get("/api/pane/{project}/{name}")
    def api_pane(project: str, name: str):
        c = ctx()
        return {"lines": _runtime.tail(c.home, f"{project}{SEP}{name}", c.run)}

    # The launcher's only write into a repo, so it is a route of its own rather
    # than something Start does silently on your behalf.
    @app.post("/fix-remote/{slug}")
    def fix_remote(slug: str):
        # place_of, not resolve: a nested repo's project row carries the
        # warning too, and its rename must reach that repo.
        project = place_of(ctx(), slug)
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
        log.info("login: signed in")
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

    return app


app = create_app()
