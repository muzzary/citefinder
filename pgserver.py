"""
Bundled Postgres lifecycle manager (Phase 12).

CiteFinder ships as a desktop app (ADR 0006/0007) that runs its OWN PostgreSQL —
no Docker, no system install. This module init/starts/stops a portable Postgres
cluster living entirely under the app-data dir, on a private loopback port, with
pgvector enabled. The portable binaries + the MSVC-built pgvector are vendored in
dev (vendor/pgextract/pgsql) and bundled by PyInstaller in a packaged build
(Phase 16); CITEFINDER_PG_ROOT overrides the location.

Design rules:
- The app-data cluster is PRECIOUS user data: never `initdb` over an existing one
  (guarded by the PG_VERSION marker), and crash recovery only ever clears a stale
  `postmaster.pid`, never touches data files.
- Single-instance: if something is already accepting on the private port, reuse it
  rather than starting a second postmaster over the same data dir.
- Idempotent: `ensure_ready()` is safe to call on every launch — it brings the
  cluster up, creates the database + the vector extension, and runs setup_db.

CLI: `python pgserver.py {start|stop|status|ensure}`.
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from appdata import app_data_dir

PG_PORT = 54329                 # private loopback port (app-owned)
PG_HOST = "127.0.0.1"
PG_SUPERUSER = "postgres"
PG_DBNAME = "citefinder"
_READY_TIMEOUT = 30             # seconds to wait for the server to accept connections


def pg_root() -> Path:
    """Locate the portable Postgres install (bin/, lib/, share/, include/).

    CITEFINDER_PG_ROOT wins (packaging / custom layouts); then a PyInstaller
    bundle dir (sys._MEIPASS/pgsql); then the dev vendor location."""
    env = os.environ.get("CITEFINDER_PG_ROOT")
    if env:
        return Path(env)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass and (Path(meipass) / "pgsql" / "bin").exists():
        return Path(meipass) / "pgsql"
    return Path(__file__).resolve().parent / "vendor" / "pgextract" / "pgsql"


def _bin(exe: str) -> str:
    p = pg_root() / "bin" / (exe + (".exe" if os.name == "nt" else ""))
    return str(p)


def data_dir() -> Path:
    return app_data_dir() / "pgdata"


def _log_file() -> Path:
    return app_data_dir() / "pg.log"


def conn_string(dbname: str = PG_DBNAME) -> str:
    """The connection string for the bundled cluster (trust auth on loopback)."""
    return f"host={PG_HOST} port={PG_PORT} dbname={dbname} user={PG_SUPERUSER}"


def is_running() -> bool:
    """True if something is already accepting connections on the private port.
    Cheap TCP probe — also the single-instance guard."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((PG_HOST, PG_PORT)) == 0


# Windows pops a console window for each console child (pg_ctl/initdb) launched
# from the windowed desktop app. CREATE_NO_WINDOW suppresses that flash. 0 on
# non-Windows / older Pythons.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True,
                          creationflags=_NO_WINDOW, **kw)


def initdb_if_needed() -> bool:
    """Create the cluster ONCE. Guarded by PG_VERSION so we never initdb over an
    existing (precious) data dir. Returns True if a fresh cluster was created."""
    d = data_dir()
    if (d / "PG_VERSION").exists():
        return False
    d.parent.mkdir(parents=True, exist_ok=True)
    # trust auth: the server is bound to a private loopback port on a single-user
    # machine, so there is no network surface to protect with a password.
    r = _run([_bin("initdb"), "-D", str(d), "-U", PG_SUPERUSER,
              "-A", "trust", "-E", "UTF8", "--no-locale"])
    if r.returncode != 0:
        raise RuntimeError(f"initdb failed:\n{r.stdout}\n{r.stderr}")
    return True


def _pg_ctl(*args):
    return _run([_bin("pg_ctl"), "-D", str(data_dir()), *args])


def _clear_stale_pid():
    """Conservative crash recovery: if pg_ctl reports no live server but a
    postmaster.pid remains (hard kill / power loss), remove ONLY that lock file so
    the server can start. Never touches data."""
    status = _pg_ctl("status")
    pid = data_dir() / "postmaster.pid"
    # pg_ctl status: rc 0 = running, 3 = not running, others = error.
    if status.returncode == 3 and pid.exists():
        pid.unlink()


def start():
    """Start the cluster (idempotent). Reuses an already-running instance."""
    if is_running():
        return
    initdb_if_needed()
    _clear_stale_pid()
    # IMPORTANT: do NOT capture pg_ctl's output via a pipe. `pg_ctl start` spawns
    # the long-lived postgres daemon, which inherits the pipe handles and never
    # closes them, so subprocess.run(capture_output=True) would block forever
    # waiting for EOF (hangs the whole app in a frozen/windowed build). Send to
    # DEVNULL; the server's own log still goes to the -l logfile.
    r = subprocess.run(
        [_bin("pg_ctl"), "-D", str(data_dir()), "-o", f"-p {PG_PORT}",
         "-l", str(_log_file()), "-w", "-t", str(_READY_TIMEOUT), "start"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=_NO_WINDOW,
    )
    if r.returncode != 0 and not is_running():
        raise RuntimeError(f"pg_ctl start failed (rc={r.returncode}); see log: {_log_file()}")
    deadline = time.monotonic() + _READY_TIMEOUT
    while time.monotonic() < deadline:
        if is_running():
            return
        time.sleep(0.5)
    raise RuntimeError(f"Postgres did not become ready; see log: {_log_file()}")


def stop():
    """Stop the cluster (fast shutdown). No-op if not running."""
    if not is_running():
        return
    _pg_ctl("-m", "fast", "-w", "stop")


def _ensure_db_and_extension():
    """Create the app database + the vector extension if missing. The Docker dev
    image had pgvector preinstalled; the bundled cluster must enable it itself."""
    import psycopg
    with psycopg.connect(conn_string("postgres"), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname=%s;", (PG_DBNAME,))
        if not cur.fetchone():
            cur.execute(f'CREATE DATABASE "{PG_DBNAME}";')
    with psycopg.connect(conn_string(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")


def ensure_ready() -> str:
    """The launch entrypoint: bring the bundled cluster fully up and return its
    connection string. Safe to call on every app start.

    Boots Postgres -> creates the database + vector extension -> runs the
    idempotent schema migrations. Points CITEFINDER_DB at the bundled cluster so
    db.py/settings resolve to it for this process.
    """
    start()
    _ensure_db_and_extension()
    os.environ["CITEFINDER_DB"] = conn_string()
    import setup_db
    setup_db.setup()
    return conn_string()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "start":
        start(); print("started:", conn_string())
    elif cmd == "stop":
        stop(); print("stopped")
    elif cmd == "ensure":
        print("ready:", ensure_ready())
    elif cmd == "status":
        print("running" if is_running() else "stopped", "on", f"{PG_HOST}:{PG_PORT}")
        print("data dir:", data_dir(), "(exists)" if data_dir().exists() else "(absent)")
        print("pg_root:", pg_root())
    else:
        print("usage: python pgserver.py {start|stop|status|ensure}")
        sys.exit(2)
