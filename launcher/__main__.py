import logging
import os

import uvicorn

from .app import app

# Loopback by default, on purpose. The launcher has no authentication of its
# own: anything that can reach this port can start an agent session with your
# credentials. Exposure is meant to happen through Tailscale Serve, which
# terminates TLS and limits reachability to your tailnet. Override only if you
# are putting your own authenticating proxy in front of it. See SECURITY.md.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def main() -> None:
    # uvicorn's default logging config adds no root handler, so INFO records
    # from launcher's own loggers (e.g. "restore: started <slug>", the only
    # evidence a boot-time restore ran) are dropped by logging.lastResort,
    # which passes only WARNING and above. Configure a root handler so they
    # reach the journal.
    logging.basicConfig(level=logging.INFO)
    host = os.environ.get("LAUNCHER_HOST", DEFAULT_HOST)
    port = int(os.environ.get("LAUNCHER_PORT", DEFAULT_PORT))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
