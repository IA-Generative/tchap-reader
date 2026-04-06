"""Entrypoint: starts both the main API and MCP server."""
import multiprocessing
import uvicorn


def run_api():
    uvicorn.run("app.main:app", host="0.0.0.0", port=8087)


def run_mcp():
    uvicorn.run("app.mcp_app:app", host="0.0.0.0", port=8088)


if __name__ == "__main__":
    p1 = multiprocessing.Process(target=run_api)
    p2 = multiprocessing.Process(target=run_mcp)
    p1.start()
    p2.start()
    p1.join()
    p2.join()
