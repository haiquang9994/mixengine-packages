#!/usr/bin/env python3
"""What it means for a PostgreSQL artifact to work, written once for the recipes that produce one.

The same argument :mod:`mariadb_smoke` makes, one database over: **a check two producers implement
separately will drift, and the drift is invisible because they agree on the field name.** PostgreSQL
will reach this repository by at least two routes — EDB's binary archives on Windows and macOS, and
Debian's own packages on Linux, which nothing here can borrow a tarball for — so the claim is written
once and both routes end here.

A runtime is packed to be executed and a service is packed to be *run*, so ``postgres --version`` is
not the claim. What a supervisor will actually do to this artifact is done here instead, in its
order:

*It creates a data directory.* ``initdb`` is a first-run job rather than a step of installation, and
unlike MariaDB it is the same program on every platform — the divergence PostgreSQL has instead is
*locale*, below.

*It starts the server against a generated ``postgresql.conf``*, which is what ``core::generate`` will
render and what makes the data directory separable from the installation in the first place.

*It waits for the server to answer.* ``pg_isready`` is the ``ReadyCheck``: a port that accepts a
connection is not a PostgreSQL that finished crash recovery, and PostgreSQL says so explicitly —
``pg_isready`` answers 1 for "rejecting connections", which is precisely the state a supervisor must
not report as healthy.

*It runs a query*, and then a second and third thing a bare query does not prove: it creates
``hstore`` and ``pgcrypto``. Those are the check that this recipe's *pruning* did not break anything
— an extension needs a module in the library directory **and** a control file and SQL script in the
share directory, and the two live in different halves of the tree. ``pgcrypto`` earns its place
twice, because it is the one that calls the OpenSSL travelling inside the archive.

*And it stops the way it will be stopped* — ``pg_ctl stop -m fast``, which is the ``StopBehaviour``.
Killing the process instead leaves an unclean shutdown and a recovery on the user's next start.

Three decisions, each forced by something measured rather than assumed:

**The superuser is created with a password and reached over TCP.** ``initdb`` defaults to naming the
superuser after the OS user and trusting anything local, which is unreachable by any account other
than that one and means nothing on Windows. So the role is ``postgres`` — the name every tool and
every tutorial assumes — with ``--auth-local`` and ``--auth-host`` both ``scram-sha-256`` and a
random password through ``--pwfile``, which is what a daemon holding a secret in the OS keyring will
do. Proving the trusted path would prove the path nobody uses.

**The locale is stated rather than inherited, and this is a finding rather than a tidiness.** Run
without one on a machine whose system locale is Vietnamese, ``initdb`` says *could not find suitable
text search configuration for locale "Vietnamese_Vietnam.1252"* and silently sets the default text
search configuration to ``simple`` — a cluster where full-text search does not stem. It exits zero.
So the same artifact, initialised on two developers' machines, produces two databases that answer
differently, and nothing in the packaging can see it. ``--locale=C -E UTF8`` is what makes the check
reproducible; what it teaches the daemon is that it has to *choose*, because the default is whatever
the machine happens to be.

**Everything runs on a port the kernel picked**, not 5432, which is reliably wrong on a developer's
machine that already runs a PostgreSQL.

Python 3 stdlib only, by policy: this runs on a GitHub runner with nothing installed.
"""

from __future__ import annotations

import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import borrow

# Where each command lives inside the tree, by the name MixEngine knows it under. A list, as in
# `mariadb_smoke.LAYOUT`, because a route is free to disagree — `find` takes the first spelling that
# exists and `describe` fails naming what it looked for.
#
# **Every route has one spelling all the same, and the Debian one buys it with a symlink.** Debian
# keeps all thirty-six programs, server and client alike, in `/usr/lib/postgresql/<major>/bin`, and
# `postgres_deb` has to keep them there — see its docstring for the one function in PostgreSQL that
# makes that mandatory rather than tidy. So it lays `bin` as a link to that directory, and the
# manifests of all three cells say `bin/postgres` rather than three things a daemon would have to
# tell apart.
LAYOUT = {
    "postgres": ["bin/postgres"],
    "initdb": ["bin/initdb"],
    "pg_ctl": ["bin/pg_ctl"],
    "psql": ["bin/psql"],
    "pg_isready": ["bin/pg_isready"],
    "pg_dump": ["bin/pg_dump"],
    "pg_dumpall": ["bin/pg_dumpall"],
    "pg_restore": ["bin/pg_restore"],
    "createdb": ["bin/createdb"],
    "dropdb": ["bin/dropdb"],
    "createuser": ["bin/createuser"],
    "vacuumdb": ["bin/vacuumdb"],
    "reindexdb": ["bin/reindexdb"],
    "pg_basebackup": ["bin/pg_basebackup"],
    "pg_upgrade": ["bin/pg_upgrade"],
}

# Without these there is no service to supervise. The rest are published where they exist and are not
# required: a user backing up a database wants `pg_dump`, and MixEngine will want `pg_upgrade` when
# it starts moving data directories between majors, but an artifact missing either is still a working
# server.
REQUIRED = ("postgres", "initdb", "pg_ctl", "psql", "pg_isready")

# **The two halves an extension needs, and the three routes here put them in three places.** Windows
# puts its modules straight in `lib/` and its share tree at `share/`; EDB's macOS zip puts modules in
# `lib/postgresql/` and the whole share tree under `share/postgresql/`; a tree rearranged from
# Debian's packages keeps `/usr`'s own shape — `lib/postgresql/<major>/lib` and
# `share/postgresql/<major>` — because that is what makes upstream's binaries find their own share
# directory, which `postgres_deb` explains at length. `pg_config --pkglibdir` would answer this and
# is not shipped — see the note on `pg_config` in `postgres.prune` — so the layout is read off the
# tree, most specific spelling first.
#
# `CONTROLS` names the extension directory itself rather than the share root above it. That is the
# thing every caller actually wanted, and naming it directly is what keeps `share/postgresql/*` from
# matching `share/postgresql/extension` on the one route where the major is not in the path.
MODULES = ("lib/postgresql/*/lib", "lib/postgresql", "lib")
CONTROLS = ("share/postgresql/*/extension", "share/postgresql/extension", "share/extension")

# The database the smoke test creates. Named for what it is, so a data directory left behind by a
# failed run on somebody's machine says where it came from.
DATABASE = "mixengine_smoke"

# Proven by creating them, because between them they exercise every part of the tree an extension
# touches. `hstore` is a plain contrib module — a shared object beside the server and a SQL script in
# the share tree. `pgcrypto` is the one that reaches further: `digest()` is computed by the OpenSSL
# this archive carries, so an artifact whose bundled libcrypto did not survive relocation fails here
# rather than on a user's first `INSERT`.
EXTENSIONS = ("hstore", "pgcrypto")


def find(tree: Path, windows: bool) -> dict[str, str]:
    """``{command: path inside the tree}`` for everything this artifact actually provides.

    The ``.exe`` is appended here rather than written into :data:`LAYOUT` twice, because the only
    difference between the two tables is that suffix and a table repeated is a table that drifts.
    """
    found: dict[str, str] = {}
    for name, candidates in LAYOUT.items():
        for candidate in candidates:
            relative = f"{candidate}.exe" if windows else candidate
            if (tree / relative).is_file():
                found[name] = relative
                break
    return found


def describe(tree: Path, windows: bool) -> dict[str, str]:
    """:func:`find`, refusing a tree that is missing something a server cannot run without."""
    provides = find(tree, windows)
    missing = [name for name in REQUIRED if name not in provides]
    if missing:
        listing = sorted(path.name for path in (tree / "bin").iterdir()) \
            if (tree / "bin").is_dir() else sorted(path.name for path in tree.iterdir())
        raise SystemExit(
            f"the tree provides no {', '.join(missing)} — looked for "
            f"{'; '.join(' or '.join(LAYOUT[name]) for name in missing)}. Contents: {listing[:25]}"
        )
    return provides


def where(tree: Path, candidates: tuple[str, ...]) -> Path | None:
    """The first of *candidates* this tree actually has. See :data:`MODULES` and :data:`CONTROLS`.

    Patterns rather than paths, because one of the three routes puts the major version in the middle
    of both of them and a table cannot name a number it will only learn at run time.
    """
    for pattern in candidates:
        for path in sorted(tree.glob(pattern)):
            if path.is_dir():
                return path
    return None


def extensions(tree: Path) -> list[str]:
    """Every extension ``CREATE EXTENSION`` will accept, read off the control files that say so.

    This is the set that has to match across the cells of one version, and it is read from the
    archive rather than from a list here for the same reason everything else in this repository is
    measured: the two archives EDB publishes for one PostgreSQL already disagree. 18.6 ships
    ``system_stats.control`` on macOS and not on Windows — an extension of EDB's own, present in one
    cell of a version and absent from the other, which is exactly the shape ``tools/parity.py``
    exists to fail on. The recipe removes it from both rather than adding it to one, because a
    third route packing PostgreSQL from Debian's packages could never have it at all.

    That third route has since been packed, and it agrees: Debian's own ``postgresql-18`` offers
    exactly the 46 these two archives were cut down to, with nothing on either side of the
    difference. Two packagers who share no build system arriving at the same set is the closest this
    repository gets to a second opinion on what a version means.
    """
    directory = where(tree, CONTROLS)
    if not directory:
        return []
    return sorted(path.stem for path in directory.glob("*.control"))


def free_port() -> int:
    """A port nothing is listening on, as the kernel's own answer rather than as a guess.

    Racy in principle — it is closed before postgres binds it — and the alternative is a hard-coded
    5432, which is *reliably* wrong on a machine that already runs a PostgreSQL.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def configuration(work: Path, port: int, windows: bool) -> Path:
    """Write the ``postgresql.conf`` the server is started against — what ``core::generate`` renders.

    Not written into the data directory. ``initdb`` puts one there, and a supervisor that regenerates
    configuration from state would be overwriting a file inside the thing it must never rewrite; the
    ``--config-file`` PostgreSQL accepts is what keeps generated configuration disposable and the
    data directory sacred. That is the same separation ``basedir``/``datadir`` gives MariaDB, reached
    through a different door.

    Forward slashes on Windows too: PostgreSQL's parser takes a backslash inside a quoted string as
    an escape, and this tree is deliberately in a directory whose name contains a space.
    """
    lines = [
        f"data_directory = '{(work / 'data').as_posix()}'",
        f"hba_file = '{(work / 'data' / 'pg_hba.conf').as_posix()}'",
        f"ident_file = '{(work / 'data' / 'pg_ident.conf').as_posix()}'",
        "listen_addresses = '127.0.0.1'",
        f"port = {port}",
        # A dev-tuned default rather than the shipped one: a runner does not have the shared memory
        # to give PostgreSQL its usual buffers, and the daemon will render something in this spirit.
        "shared_buffers = 32MB",
        "max_connections = 20",
        # Everything to stderr, which is the pipe this process is capturing. `logging_collector = on`
        # would start a background process that writes into `log/` inside the *data* directory
        # instead, and a check reading the server's own output would find nothing and conclude the
        # server said nothing. It said plenty.
        "logging_collector = off",
        "log_destination = 'stderr'",
    ]
    if windows:
        # There is no Unix socket to place, and stating it empty is how a Windows configuration is
        # kept the same file with one line different rather than a second template.
        lines.append("unix_socket_directories = ''")
    else:
        # **Under `/tmp` with a short name, and that is a kernel limit rather than a preference.** A
        # socket path is capped at 103 characters by `sockaddr_un`, the runner's own temporary
        # directory very nearly exhausts that on macOS, and the failure arrives *after* the server
        # has started — which reads like a storage problem and is not one. The same finding
        # `mariadb_smoke` records, and it belongs to whatever supervises this too: a data directory
        # under a long path is fine, and a socket beside it is not.
        directory = Path(tempfile.mkdtemp(prefix="mxe-", dir="/tmp"))
        lines.append(f"unix_socket_directories = '{directory.as_posix()}'")

    path = work / "postgresql.conf"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def initialise(tree: Path, work: Path, provides: dict[str, str], path: str) -> tuple[str, str]:
    """Bootstrap a data directory, and answer with ``(what was run, the superuser's password)``.

    See the module docstring for why the locale is stated and why the superuser gets a password.
    ``--no-sync`` is the one concession to being a check rather than an installation: it skips the
    fsync of every file just written, which on a runner is most of the wall clock, and what it risks
    is a data directory that does not survive a power cut in the next four seconds.
    """
    data = work / "data"
    password = secrets.token_urlsafe(24)
    pwfile = work / "superuser.txt"
    pwfile.write_text(password + "\n", encoding="utf-8")

    output = borrow.run(
        tree / provides["initdb"],
        "-D", str(data),
        "-U", "postgres",
        f"--pwfile={pwfile}",
        "--auth-local=scram-sha-256",
        "--auth-host=scram-sha-256",
        "-E", "UTF8",
        "--locale=C",
        "--no-sync",
        path=path, timeout=900,
    )
    # Not kept a moment longer than the program that reads it needs it. The password itself stays in
    # this process and reaches `psql` through the environment.
    pwfile.unlink()

    if not (data / "base").is_dir() or not (data / "PG_VERSION").is_file():
        raise SystemExit(f"initdb exited zero and left no cluster in {data}\n{output[-4000:]}")
    return f"{provides['initdb']} -D (a cluster bootstrapped from scratch, scram-sha-256)", password


def said(log: Path, tail: int = 8000) -> str:
    if not log.is_file():
        return "(the server wrote nothing)"
    text = log.read_text(encoding="utf-8", errors="replace").strip()
    return text[-tail:] if text else "(the server wrote nothing)"


def await_ready(
    tree: Path, provides: dict[str, str], port: int, process: subprocess.Popen, log: Path,
    path: str, seconds: float = 120,
) -> None:
    """Wait for ``pg_isready``, or say what the server said instead.

    The readiness check a supervisor will use, and PostgreSQL is unusually clear about the difference
    it measures: exit 0 is *accepting connections*, exit 1 is *rejecting* — a server that has bound
    the port and is still recovering — and exit 2 is no answer at all. Watching the port would report
    the middle one as healthy and hand a user a connection refusal.
    """
    ready = tree / provides["pg_isready"]
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit(
                f"postgres exited {process.returncode} before it was ready\n{said(log)}"
            )
        answered = subprocess.run(
            [str(ready), "-h", "127.0.0.1", "-p", str(port), "-U", "postgres"],
            capture_output=True, text=True, timeout=60, env={**os.environ, "PATH": path},
        )
        if answered.returncode == 0:
            return
        time.sleep(0.5)
    process.kill()
    raise SystemExit(f"postgres never became ready on 127.0.0.1:{port}\n{said(log)}")


def query(
    tree: Path, provides: dict[str, str], port: int, statement: str, path: str, password: str,
    database: str = "postgres",
) -> str:
    """Run one statement as the superuser over TCP and answer with what came back, unadorned.

    ``PGPASSWORD`` rather than a ``.pgpass``: the file has to be mode 600 on Unix and lives in the
    user's home directory, and a check must not write into the home directory of whoever is running
    it. It is also how a daemon holding the secret in a keyring will hand it over.
    """
    return borrow.run(
        tree / provides["psql"], "-h", "127.0.0.1", "-p", str(port), "-U", "postgres",
        "-d", database, "-v", "ON_ERROR_STOP=1", "-tAc", statement,
        path=path, timeout=300, environment={"PGPASSWORD": password},
    )


def server(tree: Path, version: str, provides: dict[str, str], windows: bool) -> list[str]:
    """Bootstrap, start, ready-check, query and stop this artifact, from wherever *tree* now is.

    The caller has already moved the tree somewhere it has never been; this makes it be a *database*
    while it is there. Returns the list of what was actually run, for the manifest.
    """
    # The instance lives outside the moved tree for the same reason MariaDB's does: the tree itself
    # stays at a path containing a space, deliberately, because that is the claim being tested, and
    # the socket path limit above is a reason a data directory cannot always sit beside it.
    work = Path(tempfile.mkdtemp(prefix="mxe-instance-"))
    path = borrow.clean_path((tree / provides["postgres"]).parent)

    banner = borrow.run(tree / provides["postgres"], "--version", path=path)
    # `postgres (PostgreSQL) 18.6` — the version is the last word and upstream appends nothing to it
    # here, but a build carrying a packager's suffix would still match on a prefix.
    stated = re.search(r"(\d+\.\d+)\s*$", banner)
    if not stated or stated.group(1) != version:
        raise SystemExit(f"postgres reports {banner!r}, expected a {version} build")
    print(f"postgres version: {banner}")
    ran = [f"{provides['postgres']} --version"]

    bootstrapped, password = initialise(tree, work, provides, path)
    ran.append(bootstrapped)
    print(f"initdb: bootstrapped {work / 'data'}")

    port = free_port()
    conf = configuration(work, port, windows)
    log = work / "postgres.log"
    with log.open("wb") as sink:
        process = subprocess.Popen(
            [str(tree / provides["postgres"]), f"--config-file={conf}"],
            stdout=sink, stderr=subprocess.STDOUT, cwd=str(work),
            env={**os.environ, "PATH": path},
        )

    try:
        await_ready(tree, provides, port, process, log, path)
        print(f"pg_isready: the server is accepting connections on 127.0.0.1:{port}")
        ran.append(f"{provides['postgres']} --config-file (a rendered postgresql.conf), started")
        ran.append(f"{provides['pg_isready']} (accepting connections)")

        reported = query(tree, provides, port, "SHOW server_version", path, password)
        if not reported.startswith(version):
            raise SystemExit(f"SHOW server_version answered {reported!r}, expected {version}")
        print(f"server_version: {reported}")

        # A real write and a real read, through a table with an index on it. Creating a database
        # alone touches nothing but the catalogue, and a storage layer that failed to initialise is
        # the failure worth catching — it is written into the log and the server otherwise starts.
        query(tree, provides, port, f"CREATE DATABASE {DATABASE}", path, password)
        query(
            tree, provides, port,
            f"CREATE TABLE t (id int PRIMARY KEY, note text); "
            f"INSERT INTO t VALUES (1, 'mixengine')",
            path, password, database=DATABASE,
        )
        stored = query(tree, provides, port, "SELECT note FROM t WHERE id = 1", path, password,
                       database=DATABASE)
        if stored != "mixengine":
            raise SystemExit(f"the row came back as {stored!r}")
        print("wrote and read back a row")
        ran.append(f"{provides['psql']} CREATE DATABASE/TABLE, INSERT and SELECT")

        # See EXTENSIONS: the two halves of the tree an extension needs, and the OpenSSL inside it.
        for extension in EXTENSIONS:
            query(tree, provides, port, f"CREATE EXTENSION {extension}", path, password,
                  database=DATABASE)
        checked = query(
            tree, provides, port,
            "SELECT ('a=>1'::hstore -> 'a') || ':' || encode(digest('mixengine', 'sha256'), 'hex')",
            path, password, database=DATABASE,
        )
        if not checked.startswith("1:"):
            raise SystemExit(f"hstore and pgcrypto answered {checked!r}")
        print(f"CREATE EXTENSION {', '.join(EXTENSIONS)}: {checked}")
        ran.append(f"CREATE EXTENSION {', '.join(EXTENSIONS)}, both used "
                   f"(pgcrypto through the archive's own OpenSSL)")

        # The one that makes the next start fast, and the one a supervisor must use: `-m fast`
        # disconnects clients and checkpoints, where `-m immediate` is a crash by another name and
        # `-m smart` waits for a client that may never disconnect.
        borrow.run(tree / provides["pg_ctl"], "stop", "-D", str(work / "data"), "-m", "fast", "-w",
                   path=path, timeout=300)
        try:
            process.wait(timeout=120)
        except subprocess.TimeoutExpired:
            process.kill()
            raise SystemExit("pg_ctl stop returned and the server was still running") from None
        if process.returncode not in (0, None):
            raise SystemExit(f"postgres exited {process.returncode} on a clean shutdown")
        print("pg_ctl stop -m fast: the server exited cleanly")
        ran.append(f"{provides['pg_ctl']} stop -m fast (a clean shutdown)")

        # Proof that the shutdown was clean rather than a belief about the exit code. PostgreSQL
        # writes this line only after the shutdown checkpoint is on disk; without it the user's next
        # start is a recovery, which is the whole failure mode being ruled out here.
        text = said(log, tail=200_000)
        if "database system is shut down" not in text:
            raise SystemExit(f"no clean-shutdown line in the server log\n{text[-4000:]}")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=120)

    return ran
