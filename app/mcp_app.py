"""Standalone MCP server — runs on port 8088."""

# Disable DNS rebinding protection before importing FastMCP
import mcp.server.transport_security as ts
_orig_init = ts.TransportSecurityMiddleware.__init__
def _patched_init(self, settings=None):
    _orig_init(self, ts.TransportSecuritySettings(enable_dns_rebinding_protection=False))
ts.TransportSecurityMiddleware.__init__ = _patched_init

from app.mcp_server import mcp

app = mcp.streamable_http_app()
