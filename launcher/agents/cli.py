"""agentctl — named Claude agents on the devbox.

A terminal (over SSH) and the systemd timers use this; the launcher
imports `service` directly. Exit codes are AgentError codes, so a terminal wrapper can ask
the right follow-up question (create? start fresh anyway? recreate?).
"""

import argparse
import json
import sys
from . import service
from .model import MAIN, AgentError

TARGETED = ("resolve", "new", "start", "stop", "park", "archive", "mode", "brief-status")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentctl", description="Named Claude agents on the devbox.")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("ls", help="list agents")
    ls.add_argument("--project")
    ls.add_argument("--dir")
    ls.add_argument("--all", action="store_true", help="include archived agents")
    for name in TARGETED:
        sp = sub.add_parser(name)
        sp.add_argument("target", nargs="?",
                        help="<project>/<name>, or with --dir just <name> (default: main)")
        sp.add_argument("--dir", help="a directory inside the project")
        if name == "new":
            sp.add_argument("--mode")
        if name == "start":
            sp.add_argument("--fresh", action="store_true")
            sp.add_argument("--force", action="store_true", help="fresh even if the brief is stale")
            sp.add_argument("--create", action="store_true", help="create the agent if missing")
            sp.add_argument("--repair", action="store_true", help="recreate a missing worktree")
        if name == "mode":
            sp.add_argument("--set", dest="mode", required=True)
    sc = sub.add_parser("scratch", help="start a scratch session")
    sc.add_argument("--dir")
    sc.add_argument("--project")
    sub.add_parser("reap", help="park idle agents, delete idle scratch sessions")
    sub.add_parser("restore", help="start agents that should be running")
    imp = sub.add_parser("import", help="one-time migration of old sessions")
    imp.add_argument("--dry-run", action="store_true")
    return p


def _target(ctx, args):
    if args.dir:
        return service.place_for(ctx, dir=args.dir, name=args.target)
    if not args.target:
        raise AgentError(AgentError.INVALID, "give <project>/<name>, or --dir")
    return service.place_for(ctx, key=args.target)


def _ago(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def _ls_text(ctx, vs) -> str:
    now = ctx.now()
    lines = []
    for v in vs:
        status = ("broken" if v.broken else "running" if v.running
                  else "crashed" if v.dead
                  else "failed" if v.agent.last_error else v.agent.state)
        if v.attached:
            status += "*"
        lines.append(f"{v.agent.display:<36} {status:<9} {_ago(now - v.last_active):>4}  "
                     f"{v.mission or ''}".rstrip())
    return "\n".join(lines)


def _run(ctx, args, extra) -> tuple[object, str]:
    """(json payload, text output) for one command."""
    if args.cmd == "ls":
        project, places = args.project, ()
        if args.dir:
            place = service.place_for(ctx, dir=args.dir)[0]
            project, places = place.slug, (place,)
        vs = service.views(ctx, project=project, include_archived=args.all, places=places)
        return {"agents": [v.to_dict() for v in vs]}, _ls_text(ctx, vs)
    if args.cmd == "scratch":
        if args.dir:
            place = service.place_for(ctx, dir=args.dir)[0]
        else:
            place = service.place_for(ctx, key=f"{args.project}/{MAIN}")[0]
        a = service.scratch(ctx, place)
        return a.to_dict(), a.display
    if args.cmd == "reap":
        done = service.reap(ctx)
        return {"done": done}, "\n".join(done)
    if args.cmd == "restore":
        done = service.restore(ctx)
        return {"done": done}, "\n".join(done)
    if args.cmd == "import":
        from . import importer
        done = importer.run(ctx, dry_run=args.dry_run)
        return {"done": done}, "\n".join(done)

    place, name = _target(ctx, args)
    if args.cmd == "resolve":
        try:
            agent = service.lookup(ctx, place, name)   # synthesizes main
        except AgentError as exc:
            if exc.code != AgentError.UNKNOWN:
                raise
            display = f"{place.slug}·{name}"           # a wrapper's dry run shows what would be created
            if args.json:
                exc.display = display                  # main() puts it in the error object
            else:
                print(display)
            raise
        return agent.to_dict(), agent.display
    if args.cmd == "new":
        a = service.new(ctx, place, name, args.mode)
    elif args.cmd == "start":
        a = service.start(ctx, place, name, fresh=args.fresh, force=args.force,
                          create=args.create, repair=args.repair, extra=extra)
    elif args.cmd == "stop":
        a = service.stop(ctx, place, name)
        return (a.to_dict() if a else {}), ""
    elif args.cmd == "park":
        a = service.park(ctx, place, name)
    elif args.cmd == "archive":
        a = service.archive(ctx, place, name)
    elif args.cmd == "mode":
        a = service.set_mode(ctx, place, name, args.mode)
    else:  # brief-status
        status = service.brief_status(ctx, place, name)
        return status, ("stale" if status["stale"] else "current") + f"  {status['brief']}"
    return a.to_dict(), a.display


def main(argv=None, ctx=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    extra = []
    if "--" in argv:
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1:]
    args = _parser().parse_args(argv)
    ctx = ctx or service.Ctx.default()
    try:
        payload, text = _run(ctx, args, extra)
    except AgentError as exc:
        if args.json:
            body = {"error": str(exc), "code": exc.code}
            if getattr(exc, "display", None):
                body["display"] = exc.display
            print(json.dumps(body))
        else:
            print(f"agentctl: {exc}", file=sys.stderr)
        return exc.code
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    elif text:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
