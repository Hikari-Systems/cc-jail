"""Let the MCP server answer to the name the jail actually calls it by.

The stock server constructs `FastMCP('Graphiti Agent Memory', instructions=…)` with no
`host` argument, and only afterwards assigns `mcp.settings.host` from the config file.
That ordering matters: FastMCP auto-enables DNS-rebinding protection when the *constructor*
host is a loopback one — which the default `127.0.0.1` is — and stamps in an allow-list of
`127.0.0.1:*`, `localhost:*` and `[::1]:*`. Setting `server.host: 0.0.0.0` later moves what
the socket binds to and nothing else, so the server listens on every interface and then
answers `421 Invalid Host header` to anything that did not address it as localhost.

Upstream never meets this: its client is on the host and reaches a published port, so the
Host header *is* `localhost:8000`. In cc-jail the client is another container on the compose
network, calling `http://graphiti:8000/mcp` — a Host header the allow-list rejects, with no
config file, environment variable or CLI flag anywhere that extends it.

So `FastMCP.__init__` is wrapped to seed `transport_security` from `MCP_ALLOWED_HOSTS`
before the auto-enable branch can decide for itself. The protection stays *on*, which is the
point of not simply disabling it: the list gains exactly the names cc-jail's compose file
publishes the server under, and nothing else.

Set `MCP_ALLOWED_HOSTS` to a comma-separated list, each entry either an exact `host:port` or
the `host:*` wildcard the middleware understands. Unset, this does nothing at all.
"""

import logging
import os

logger = logging.getLogger(__name__)

# What FastMCP would have installed on its own. Kept rather than replaced: the server is
# still reachable at localhost through a published port, and that path should not break
# because another name was added.
_LOOPBACK_HOSTS = ['127.0.0.1:*', 'localhost:*', '[::1]:*']
_LOOPBACK_ORIGINS = ['http://127.0.0.1:*', 'http://localhost:*', 'http://[::1]:*']

_installed = False


def install() -> None:
    """Extend the Host allow-list from MCP_ALLOWED_HOSTS. Idempotent, and a no-op if unset."""
    global _installed

    extra = [h.strip() for h in os.environ.get('MCP_ALLOWED_HOSTS', '').split(',') if h.strip()]
    if not extra or _installed:
        return

    from mcp.server.fastmcp.server import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings

    original_init = FastMCP.__init__

    def __init__(self, *args, **kwargs):  # noqa: N807 - replacing a dunder on purpose
        # Only when the caller expressed no opinion. One that passes its own settings
        # knows what it wants, and the server does not pass any.
        if kwargs.get('transport_security') is None:
            kwargs['transport_security'] = TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=_LOOPBACK_HOSTS + extra,
                allowed_origins=_LOOPBACK_ORIGINS + [f'http://{h}' for h in extra],
            )
        original_init(self, *args, **kwargs)

    FastMCP.__init__ = __init__
    _installed = True
    logger.info('MCP Host allow-list extended with: %s', ', '.join(extra))
