#!/bin/sh
# Start both the main API and the MCP server
uvicorn app.main:app --host 0.0.0.0 --port 8087 &
uvicorn app.mcp_app:app --host 0.0.0.0 --port 8088 &
wait
