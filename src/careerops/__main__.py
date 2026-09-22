"""Run: PYTHONPATH=src python -m careerops --port 8765."""
import argparse
from pathlib import Path

from careerops.server import Application, make_server


def main():
    parser = argparse.ArgumentParser(description="CareerOps AI local application")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data", type=Path, default=Path("local_data/careerops.sqlite3"))
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535")
    app = Application(args.data)
    server = make_server(app, args.port)
    print(f"CareerOps AI is running at http://127.0.0.1:{server.server_port}", flush=True)
    print("Local data:", app.store.path, flush=True)
    print("Applications and messages are never submitted by this app. Schedules are off.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for event in app.cancel_events.values():
            event.set()
        server.server_close()


if __name__ == "__main__":
    main()
