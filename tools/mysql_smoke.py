#!/usr/bin/env python3
"""Prove that a packed MySQL is a database, from a directory it has never been in before.

Shared by :mod:`mysql_borrow` and :mod:`mysql_build`, and shared deliberately. The mechanics of a
smoke test are the same for every publisher and *the claim is not*, so no two kinds here share one —
but inside a kind the opposite holds, and for the same reason read backwards: two producers of the
same runtime that check it differently will drift, and the drift is invisible because they agree on
the field name. `mariadb_smoke.py` is shared by three recipes and `postgres_smoke.py` by two for
exactly this.

What is proven is what a *database* raises rather than what a web server does: a data directory is
bootstrapped from scratch, the server starts against a rendered ``my.cnf``, it answers a ping, a row
is written and read back **through InnoDB** — checked in ``information_schema``, because a server
whose storage engine failed to initialise falls back without failing — and it is stopped through
``mysqladmin shutdown``, with the clean-shutdown line looked for in the log afterwards. A supervisor
that kills a database instead leaves crash recovery for the user's first start.

Python 3 stdlib only, by policy.
"""

from __future__ import annotations

import getpass
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import borrow

# Command name -> where each recipe might have put it. A table rather than a lookup in `bin/`,
# because a built tree and a borrowed one disagree about `scripts/` and a Windows zip about nothing
# at all, and `postgres_smoke.LAYOUT` established that the spellings belong in one place a reader can
# check rather than in an `if` inside a function.
LAYOUT = {
    "mysqld": ["bin/mysqld", "sbin/mysqld", "libexec/mysqld"],
    "mysql": ["bin/mysql"],
    "mysqladmin": ["bin/mysqladmin"],
    "mysqldump": ["bin/mysqldump"],
    "mysql_install_db": ["scripts/mysql_install_db", "bin/mysql_install_db"],
    "mysql_upgrade": ["bin/mysql_upgrade"],
    "mysqlpump": ["bin/mysqlpump"],
    "mysqlbinlog": ["bin/mysqlbinlog"],
    "mysqlcheck": ["bin/mysqlcheck"],
    "mysqlimport": ["bin/mysqlimport"],
    "mysqlshow": ["bin/mysqlshow"],
    "mysqlslap": ["bin/mysqlslap"],
    "my_print_defaults": ["bin/my_print_defaults"],
}

# Three, and `mysql_install_db` is deliberately not among them. It exists in 5.6 and was deleted in
# 5.7, where `mysqld --initialize-insecure` does the job; a required list naming it would fail four
# lines of five. Which program bootstraps a data directory is :func:`bootstrap`'s business, and that
# `provides` is shorter on a newer version than on an older one is upstream's decision rather than a
# packing fault — see docs/packages/mysql.md, which says so before anybody files it as one.
REQUIRED = ("mysqld", "mysql", "mysqladmin")

DATABASE = "mixengine_smoke"


def find(tree: Path, windows: bool) -> dict[str, str]:
    """Every command of :data:`LAYOUT` this tree actually has, at the path it has it."""
    found: dict[str, str] = {}
    for name, candidates in LAYOUT.items():
        for candidate in candidates:
            relative = f"{candidate}.exe" if windows else candidate
            if (tree / relative).is_file():
                found[name] = relative
                break
            # `mysql_install_db` is a shell script rather than a program, so it has no `.exe` even
            # on a machine where everything else does — and on Windows it is simply absent.
            if windows and (tree / candidate).is_file():
                found[name] = candidate
                break
    return found


def describe(tree: Path, windows: bool) -> dict[str, str]:
    """The ``provides`` map, refusing a tree that is missing something a daemon must supervise."""
    provides = find(tree, windows)
    missing = [name for name in REQUIRED if name not in provides]
    if missing:
        listing = (
            sorted(path.name for path in (tree / "bin").iterdir()) if (tree / "bin").is_dir()
            else sorted(path.name for path in tree.iterdir())
        )
        raise SystemExit(
            f"the tree provides no {', '.join(missing)} — looked for "
            f"{'; '.join(' or '.join(LAYOUT[name]) for name in missing)}. Contents: {listing[:25]}"
        )
    return provides


def free_port() -> int:
    """A port nothing is listening on, asked of the kernel rather than picked."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def configuration(work: Path, tree: Path, port: int, windows: bool) -> Path:
    """Render the ``my.cnf`` the server is started against.

    A file rather than a command line, because that is how MixEngine will start it and because a
    server that only works when every setting is an argument is a server whose configuration
    generation is untested.

    A ``user`` line appears only when this is running as root, which is the container the compiled
    Linux cells are built in and nowhere else — see below.

    **No ``skip-name-resolve``, which MariaDB's equivalent does set**, and the difference is not a
    preference: ``mysqld --initialize-insecure`` creates exactly one account, ``root@localhost``,
    while MariaDB's installer also creates ``root@127.0.0.1``. With name resolution off the server
    compares the incoming address as a string, so a TCP connection from 127.0.0.1 matches nothing
    and every client here is refused by a server whose own log says it is ready for connections —
    which is what this check found the first time it ran.
    """
    lines = [
        "[mysqld]",
        f'basedir = "{tree.as_posix()}"',
        f'datadir = "{(work / "data").as_posix()}"',
        f'log_error = "{(work / "mysqld.err").as_posix()}"',
        f"port = {port}",
        "bind-address = 127.0.0.1",
        "innodb_buffer_pool_size = 32M",
    ]
    if not windows:
        # In a directory of its own under /tmp, because a Unix socket path has a length limit of
        # about a hundred characters and the smoke test deliberately runs from a long path with a
        # space in it.
        socket_directory = Path(tempfile.mkdtemp(prefix="mxe-", dir="/tmp"))
        lines.append(f'socket = "{(socket_directory / "s.sock").as_posix()}"')
    if not windows and hasattr(os, "geteuid") and os.geteuid() == 0:
        # **`mysqld` refuses to start as root unless it is told to be root.** `check_user` prints
        # `Fatal error: Please read "Security" section of the manual to find out how to run mysqld
        # as root!` and aborts when it is running as uid 0 and no user was named; naming one is the
        # documented way through, and `root` is special-cased there rather than looked up.
        #
        # Only ever true inside the manylinux container the compiled Linux cells are built in,
        # which has one account. A borrow leg runs on the runner as an ordinary user and adds
        # nothing here — the file a user of the artifact gets is not this one either way, since
        # MixEngine renders its own.
        lines.append(f"user = {getpass.getuser()}")
    path = work / "my.cnf"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def interpreter(script: Path) -> Path:
    """What runs *script*, read off its own first line rather than assumed.

    `scripts/mysql_install_db` is **Perl** in a tree compiled from source, and shell in one
    upstream packaged for Debian. 5.6's `scripts/CMakeLists.txt` configures `mysql_install_db.pl.in`
    on every platform and only appends `.pl` to the name on Windows, so a compiled Unix tree has a
    Perl program under a name that says nothing. Handed to `/bin/sh` it answers
    ``use: command not found`` twice and then a syntax error at ``my @req_mods = (``, which reads
    like a corrupt tree and is a wrong interpreter.

    The shebang is a path off the machine that built the tree, so it is used when it is still there
    and looked up by name when it is not.
    """
    first = script.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0].strip()
    if not first.startswith("#!"):
        return Path("/bin/sh")
    words = first[2:].strip().split()
    named = Path(words[0])
    if named.name == "env" and len(words) > 1:
        named = Path(words[1])
    if named.is_absolute() and named.is_file():
        return named
    found = shutil.which(named.name)
    return Path(found) if found else Path("/bin/sh")


def bootstrap(tree: Path, work: Path, provides: dict[str, str], version: str,
              path: str, windows: bool) -> str:
    """Make a data directory, by whichever of three mechanisms this artifact has.

    Tried in order rather than branched on a version number, because the split is not only between
    lines: it is between *what upstream put in this tree*, and 5.6 answers differently on Windows
    than on Unix.

    * **5.7 and newer**: ``mysqld --initialize-insecure``. It writes into an empty directory only,
      and leaves the root account without a password — which is what a local development environment
      wants and what ``--initialize`` (a random password buried in the error log) does not.
    * **5.6 on Unix**: ``scripts/mysql_install_db``, which takes ``--basedir`` and ``--datadir``
      and does not quote ``$basedir``, so a tree whose path contains a space has to be reached
      through a symlink. That is not a hypothetical: it is why `mariadb_smoke.install_db` has the
      same workaround, found the same way. What runs it comes off its own first line — see
      `interpreter`, which is not a nicety either.
    * **5.6 on Windows**: neither exists. Upstream's zip ships a ``data/`` directory with the system
      tables already built, and the documented first run is to copy it.

    A refusal naming all three is better than a fourth guess, because a data directory made the
    wrong way starts a server that works until the first upgrade.
    """
    data = work / "data"
    program = provides.get("mysqld")

    if borrow.parts(version)[:2] > (5, 6):
        data.parent.mkdir(parents=True, exist_ok=True)
        if data.exists():
            shutil.rmtree(data)
        borrow.run(tree / program, "--no-defaults", f"--basedir={tree}", f"--datadir={data}",
                   "--initialize-insecure", path=path, timeout=1800)
        if not (data / "mysql").is_dir():
            raise SystemExit(
                f"mysqld --initialize-insecure exited zero and left no mysql schema in {data}"
            )
        return f"{program} --initialize-insecure (a data directory bootstrapped from scratch)"

    if "mysql_install_db" in provides and not windows:
        installer = tree / provides["mysql_install_db"]
        data.mkdir(parents=True, exist_ok=True)
        basedir = tree
        if " " in str(tree):
            link = Path(tempfile.mkdtemp(prefix="mixengine-basedir-")) / "tree"
            link.symlink_to(tree, target_is_directory=True)
            basedir = link
            installer = link / provides["mysql_install_db"]
            print(f"mysql_install_db: run against {link}, because upstream's script does not quote "
                  f"$basedir and this tree's path contains a space")
        installing = os.pathsep.join([path, "/usr/sbin", "/sbin"])
        try:
            output = borrow.run(
                interpreter(installer), str(installer), "--no-defaults", f"--basedir={basedir}",
                f"--datadir={data}", f"--user={getpass.getuser()}",
                path=installing, timeout=1800,
            )
        except SystemExit:
            for log in sorted(data.glob("*.err")) + sorted(data.glob("*.log")):
                print(f"--- {log.name}", file=sys.stderr)
                print(log.read_text(encoding="utf-8", errors="replace")[-4000:], file=sys.stderr)
            raise
        if not (data / "mysql").is_dir():
            raise SystemExit(
                f"mysql_install_db exited zero and left no mysql schema in {data}\n{output[-4000:]}"
            )
        return f"{provides['mysql_install_db']} --datadir (a data directory bootstrapped from scratch)"

    if (tree / "data" / "mysql").is_dir():
        shutil.copytree(tree / "data", data, symlinks=True)
        return "copied the data/ directory upstream's 5.6 zip ships with its system tables built"

    raise SystemExit(
        f"nothing in this {version} tree can make a data directory: it has no mysqld accepting "
        f"--initialize-insecure, no mysql_install_db, and no data/ holding a mysql schema. One of "
        f"those three is how every MySQL there has ever been is bootstrapped."
    )


def said(logs: list[Path], tail: int = 8000) -> str:
    """Whatever the server wrote, wherever it wrote it.

    Two files rather than one, and the second is the half that a check reading the process's own
    output would miss: **Windows mysqld writes its error log to a file in the data directory and
    sends nothing to stdout**, so a smoke test watching the pipe concludes the server said nothing
    while the reason it died is on disk.
    """
    parts = []
    for log in logs:
        if log.is_file():
            text = log.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                parts.append(f"--- {log.name}\n{text[-tail:]}")
    return "\n".join(parts) if parts else "(the server wrote nothing to either log)"


def await_ping(tree: Path, provides: dict[str, str], port: int, process: subprocess.Popen,
               logs: list[Path], path: str, seconds: float = 180) -> None:
    """Wait until ``mysqladmin ping`` answers, or until the server proves it will not."""
    admin = tree / provides["mysqladmin"]
    refused = "(it was never asked)"
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit(
                f"mysqld exited {process.returncode} before it answered a ping\n{said(logs)}"
            )
        answered = subprocess.run(
            [str(admin), "--protocol=TCP", "--host=127.0.0.1", f"--port={port}", "--user=root",
             "ping"],
            capture_output=True, text=True, timeout=60, env={**os.environ, "PATH": path},
        )
        if answered.returncode == 0 and "alive" in answered.stdout:
            return
        refused = (answered.stderr or answered.stdout).strip()
        time.sleep(0.5)
    process.kill()
    # What the client said, not only what the server wrote. A server that is up and refusing every
    # connection writes "ready for connections" and nothing else, so a failure quoting only the log
    # reads as a server that never started, which is the opposite of what happened.
    raise SystemExit(
        f"mysqld never answered a ping on 127.0.0.1:{port}\n"
        f"mysqladmin said: {refused}\n{said(logs)}"
    )


def query(tree: Path, provides: dict[str, str], port: int, statement: str, path: str) -> str:
    """One statement through the client, in the batch form a script would use."""
    client = tree / provides["mysql"]
    return borrow.run(
        client, "--protocol=TCP", "--host=127.0.0.1", f"--port={port}", "--user=root",
        "--batch", "--skip-column-names", "--execute", statement, path=path, timeout=300,
    )


def server(tree: Path, version: str, provides: dict[str, str], windows: bool) -> list[str]:
    """Run the artifact as a database and answer with what was actually done to it."""
    work = Path(tempfile.mkdtemp(prefix="mxe-instance-"))
    path = borrow.clean_path((tree / provides["mysqld"]).parent, tree / "bin")

    banner = borrow.run(tree / provides["mysqld"], "--version", path=path)
    stated = re.search(r"Ver\s+(\d+\.\d+\.\d+)", banner)
    if not stated or stated.group(1) != version:
        raise SystemExit(f"mysqld reports {banner!r}, expected a {version} build")
    print(f"mysqld version: {banner}")
    ran = [f"{provides['mysqld']} --version"]

    ran.append(bootstrap(tree, work, provides, version, path, windows))
    print(f"bootstrapped {work / 'data'}")

    port = free_port()
    my_cnf = configuration(work, tree, port, windows)
    logs = [work / "mysqld.err", work / "mysqld.log"]
    with logs[1].open("wb") as sink:
        process = subprocess.Popen(
            [str(tree / provides["mysqld"]), f"--defaults-file={my_cnf}"],
            stdout=sink, stderr=subprocess.STDOUT, cwd=str(work),
            env={**os.environ, "PATH": path},
        )
    try:
        await_ping(tree, provides, port, process, logs, path)
        print(f"mysqladmin ping: the server answered on 127.0.0.1:{port}")
        ran.append(f"{provides['mysqld']} --defaults-file (a rendered my.cnf), started")
        ran.append(f"{provides['mysqladmin']} ping")

        reported = query(tree, provides, port, "SELECT VERSION()", path)
        if not reported.startswith(version):
            raise SystemExit(f"SELECT VERSION() answered {reported!r}, expected {version}")
        print(f"SELECT VERSION(): {reported}")

        query(tree, provides, port, f"CREATE DATABASE {DATABASE}", path)
        query(tree, provides, port,
              f"CREATE TABLE {DATABASE}.t (id INT PRIMARY KEY, note TEXT) ENGINE=InnoDB; "
              f"INSERT INTO {DATABASE}.t VALUES (1, 'mixengine')", path)
        stored = query(tree, provides, port,
                       f"SELECT note FROM {DATABASE}.t WHERE id = 1", path)
        if stored != "mixengine":
            raise SystemExit(f"the row came back as {stored!r}")
        engine = query(
            tree, provides, port,
            f"SELECT engine FROM information_schema.tables WHERE table_schema = '{DATABASE}' "
            f"AND table_name = 't'", path,
        )
        if engine.lower() != "innodb":
            raise SystemExit(
                f"the table was created with the {engine} engine rather than InnoDB, which means "
                f"InnoDB did not initialise and the server fell back without failing"
            )
        print(f"wrote and read back a row through {engine}")
        ran.append(f"{provides['mysql']} CREATE/INSERT/SELECT through InnoDB")

        borrow.run(tree / provides["mysqladmin"], "--protocol=TCP", "--host=127.0.0.1",
                   f"--port={port}", "--user=root", "shutdown", path=path, timeout=300)
        try:
            process.wait(timeout=180)
        except subprocess.TimeoutExpired:
            process.kill()
            raise SystemExit("mysqladmin shutdown returned and the server was still running") from None
        if process.returncode not in (0, None):
            raise SystemExit(f"mysqld exited {process.returncode} on a clean shutdown")
        print("mysqladmin shutdown: the server exited cleanly")
        ran.append(f"{provides['mysqladmin']} shutdown (a clean InnoDB shutdown)")

        text = said(logs, tail=200000)
        if "Shutdown complete" not in text:
            raise SystemExit(f"no clean-shutdown line in either log\n{text[-4000:]}")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=120)
    return ran
