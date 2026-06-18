"""
Native-window launcher (Phase 15).

Runs CiteFinder as a desktop app: boots the app-owned Postgres (Phase 12), serves
the FastAPI app on a PRIVATE loopback port, and shows it in a native window via
pywebview (Edge WebView2 on Windows). Closing the window stops the web server and
the bundled Postgres. Single-instance: if it's already serving, this exits instead
of starting a second copy over the same data dir.

The browser dev flow (`python app.py` on :8000) is unchanged; this is the packaged
entry point. Run:  python desktop.py
"""
import os
import socket
import sys
import threading
import time

# Use the app-owned Postgres, not Docker. Set BEFORE importing app (which imports
# db at module load); app.py's bootstrap calls pgserver.ensure_ready() for us.
os.environ.setdefault("CITEFINDER_PG", "bundled")

# A frozen WINDOWED build (PyInstaller console=False) has no console, so
# sys.stdout / sys.stderr are None. That breaks any print() in the pipeline AND
# uvicorn's log formatter (which calls sys.stdout.isatty()). Redirect both to a
# log file under app-data so the packaged app runs head-less-ly and still leaves a
# debuggable trace. No-op when run from source (stdout is a real stream).
if sys.stdout is None or sys.stderr is None:
    try:
        from appdata import app_data_dir
        _sink = open(app_data_dir() / "app.log", "a", buffering=1, encoding="utf-8")
    except Exception:
        _sink = open(os.devnull, "w")
    sys.stdout = sys.stdout or _sink
    sys.stderr = sys.stderr or _sink

APP_HOST = "127.0.0.1"
APP_PORT = 8765                 # private; distinct from the dev server's :8000
APP_TITLE = "CiteFinder"


def _port_open(host=APP_HOST, port=APP_PORT, timeout=0.4) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def build_server():
    """Create the uvicorn server bound to the private port. Importing `app` here
    triggers the bundled-Postgres bootstrap (CITEFINDER_PG=bundled)."""
    import uvicorn
    from app import app
    config = uvicorn.Config(app, host=APP_HOST, port=APP_PORT, log_level="warning")
    return uvicorn.Server(config)


def start_server(server, ready_timeout=60):
    """Run the server in a background thread; return once it accepts connections."""
    thread = threading.Thread(target=server.run, name="citefinder-uvicorn", daemon=True)
    thread.start()
    deadline = time.monotonic() + ready_timeout
    while time.monotonic() < deadline:
        if _port_open():
            return thread
        time.sleep(0.25)
    raise RuntimeError(f"web server did not come up on {APP_HOST}:{APP_PORT}")


def shutdown(server):
    """Stop the web server and the bundled Postgres (best-effort)."""
    try:
        server.should_exit = True
    except Exception:
        pass
    try:
        import pgserver
        pgserver.stop()
    except Exception:
        pass


def main():
    # Single-instance: a live server on the private port means we're already open.
    if _port_open():
        print(f"{APP_TITLE} is already running.")
        return

    server = build_server()
    start_server(server)

    import webview
    webview.create_window(APP_TITLE, f"http://{APP_HOST}:{APP_PORT}",
                          width=1200, height=820, min_size=(900, 600))
    try:
        webview.start()          # blocks on the main thread until the window closes
    finally:
        shutdown(server)


if __name__ == "__main__":
    main()
