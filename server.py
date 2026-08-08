"""Backward-compatible entry point — delegates to promptstudio package."""

from promptstudio.server.handler import run_server

if __name__ == "__main__":
    run_server()
