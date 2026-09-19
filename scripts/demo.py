#!/usr/bin/env python3
"""Single-process local launcher; built frontend is served by the API."""
import argparse
import os
from pathlib import Path
import threading
import webbrowser
import uvicorn
from saac.api import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", default=".runtime")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--operator", action="store_true", help="Open the administrator workspace instead of the public entry")
    args = parser.parse_args()
    app = create_app(args.data_dir)
    url = f"http://127.0.0.1:{args.port}/#token={app.state.operator_token}"
    print("\nSAAC · Govern The Rail Lab\nFive workspaces: payments, trading, referrals, contained runtime and Incident / Swarm Lab.\n", flush=True)
    print(f"Public demo: http://127.0.0.1:{args.port}/\nAdministrator dashboard: {url}\n", flush=True)
    print("The URL contains a local operator credential. Keep it separate from actor tools.\n", flush=True)
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url if args.operator else f"http://127.0.0.1:{args.port}/")).start()
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__": main()
