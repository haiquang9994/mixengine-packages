#!/usr/bin/env python3
"""What it means for a MariaDB artifact to work, written once for the three recipes that produce one.

MariaDB is the first kind in this repository packed by three different routes — an official zip on
Windows x86_64, an official tarball on Linux x86_64, an official `.deb` on Linux aarch64, and a
source build on macOS and Windows on ARM. That is exactly the situation ``ruby_smoke`` exists for:
**a check two producers implement separately will drift, and the drift is invisible because they
agree on the field name.** So every route ends here.

A runtime is packed to be executed and a service is packed to be *run*, so ``mariadbd --version`` is
not the claim. What T33 will actually do to this artifact is done here instead, in its order:

*It creates a data directory.* ``mariadb-install-db`` is a first-run job rather than a step of
installation — the archive ships no usable ``mysql`` schema on Unix, and the one Windows ships in
``data/`` is a template that still has to be copied per instance. This is the single most
platform-divergent thing MariaDB does and the reason this module exists at all.

*It starts the server against a generated ``my.cnf``*, which is what ``core::generate`` will render
and what makes ``basedir`` and ``datadir`` separable in the first place.

*It waits for the server to answer.* ``mariadb-admin ping`` is T33's ``ReadyCheck`` — a TCP port
that accepts a connection is not a MariaDB that finished crash recovery.

*It runs a query*, which is the only one of these that proves the thing anybody wants, and it asks
for ``VERSION()`` so the answer can be checked against the version being published.

*And it stops the way it will be stopped* — ``mariadb-admin shutdown``, which is T33's
``StopBehaviour::Command``. Killing the process instead would leave an unclean InnoDB shutdown, which
is precisely the state a supervisor must never produce.

Two decisions worth stating, because both were forced by what happens on a real machine:

**Root is created with a password rather than with ``unix_socket``.** MariaDB's default on Unix is to
authenticate ``root@localhost`` against the OS user of the same name, which means the account cannot
be reached by the user MixEngine runs the daemon as, and cannot be reached at all on Windows. T33
generates a random root password and puts it in the OS keyring; ``--auth-root-authentication-method
=normal`` is what makes that possible, so it is what is proven here.

**Everything runs over TCP on a port the kernel picked.** Not the default 3306, which is reliably
wrong on a developer's machine that already runs a MySQL, and not a Unix socket, because the point is
to exercise the path Windows also has.

Python 3 stdlib only, by policy: this runs on a GitHub runner with nothing installed.
"""

from __future__ import annotations

import getpass
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import borrow

# Where each command lives inside the tree, by the name MixEngine knows it under, most-preferred
# spelling first. A list rather than a path because MariaDB renamed every one of these binaries
# between 10.4 and 10.6 and kept the old name as a symlink — and because the Windows zip, the Linux
# tarball and a `.deb` do not agree on which directory the first-run job lives in. Nothing here
# guesses: `find` takes the first spelling that exists and `describe` fails naming what it looked for.
LAYOUT = {
    "mariadbd": ["bin/mariadbd", "bin/mysqld", "sbin/mariadbd", "libexec/mariadbd"],
    "mariadb": ["bin/mariadb", "bin/mysql"],
    "mariadb-admin": ["bin/mariadb-admin", "bin/mysqladmin"],
    "mariadb-install-db": [
        "bin/mariadb-install-db", "scripts/mariadb-install-db",
        "bin/mysql_install_db", "scripts/mysql_install_db",
    ],
    "mariadb-dump": ["bin/mariadb-dump", "bin/mysqldump"],
    "mariadb-upgrade": ["bin/mariadb-upgrade", "bin/mysql_upgrade"],
    # Published so that the daemon can offer a physical backup where one exists, rather than
    # discovering the file by looking. Two spellings because upstream renamed it and kept both.
    "mariadb-backup": ["bin/mariadb-backup", "bin/mariabackup"],
}

# Without these there is no service to supervise. `mariadb-dump` and `mariadb-upgrade` are published
# where they exist and are not required: a user backing up a database wants the first, and MixEngine
# will want the second when it starts moving data directories between versions, but an artifact
# missing either is still a working server.
REQUIRED = ("mariadbd", "mariadb", "mariadb-admin", "mariadb-install-db")

# The database the smoke test creates. Named for what it is, so that a data directory left behind by
# a failed run on somebody's machine says where it came from.
DATABASE = "mixengine_smoke"


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
        listing = sorted(path.name for path in (tree / "bin").iterdir()) if (tree / "bin").is_dir() \
            else sorted(path.name for path in tree.iterdir())
        raise SystemExit(
            f"the tree provides no {', '.join(missing)} — looked for "
            f"{'; '.join(' or '.join(LAYOUT[name]) for name in missing)}. Contents: {listing[:25]}"
        )
    return provides


def free_port() -> int:
    """A port nothing is listening on, as the kernel's own answer rather than as a guess.

    Racy in principle — it is closed before mariadbd binds it — and the alternative is a hard-coded
    3306, which is *reliably* wrong on a machine that already runs a MySQL, including a developer's.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def configuration(work: Path, tree: Path, port: int, windows: bool) -> Path:
    """Write the ``my.cnf`` the server is started against — the shape ``core::generate`` will render.

    Forward slashes on Windows too, and every path quoted: MariaDB's option parser treats a backslash
    as an escape and takes everything after an unquoted ``#`` as a comment, and this tree is
    deliberately in a directory whose name contains a space. A configuration file that only works
    from a path without one is a configuration file that fails on ``C:\\Users\\Nguyen Hai Quang``.
    """
    lines = [
        "[mysqld]",
        f'basedir = "{tree.as_posix()}"',
        f'datadir = "{(work / "data").as_posix()}"',
        # Stated rather than left to the default, and measured rather than assumed: on Windows
        # mariadbd writes its error log to `<datadir>/<hostname>.err` and sends nothing to stdout, so
        # a check that reads the process's own output finds an empty file and concludes the server
        # said nothing. It said plenty. T33 will name this file for the same reason — a supervisor
        # that cannot find the log cannot report why a service failed.
        f'log_error = "{(work / "mariadbd.err").as_posix()}"',
        f"port = {port}",
        "bind-address = 127.0.0.1",
        # Nothing here should ever consult a DNS server to decide whether 127.0.0.1 may connect.
        "skip-name-resolve",
        # A dev-tuned default rather than the shipped one: a runner does not have the memory to give
        # InnoDB its usual buffer pool, and T33 will render something in this spirit.
        "innodb_buffer_pool_size = 32M",
    ]
    if not windows:
        # Under `/tmp` rather than beside the data directory, and with a name of two characters:
        # `sockaddr_un` allows 103, and a runner's own temporary directory can be half of that
        # before anything here is appended. See the note in `server`.
        socket_directory = Path(tempfile.mkdtemp(prefix="mxe-", dir="/tmp"))
        lines.append(f'socket = "{(socket_directory / "s.sock").as_posix()}"')
    else:
        # The plugin directory is derived from basedir on Unix and is not always on Windows, where
        # the server has been known to look beside its own executable instead. Stating it costs a
        # line and turns a plugin load failure into something that cannot happen.
        lines.append(f'plugin-dir = "{(tree / "lib" / "plugin").as_posix()}"')

    path = work / "my.cnf"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def install_db(tree: Path, work: Path, provides: dict[str, str], path: str, windows: bool) -> str:
    """Create the data directory, which is the one step that is a different program per platform.

    On Unix ``mariadb-install-db`` is a shell script that bootstraps a server against a fresh
    directory. On Windows it is a **different program under the same name** — a C++ one whose job is
    normally to register a Windows service — and the two share almost no options. Measured rather
    than assumed: ``--auth-root-authentication-method=normal`` is a Unix-only flag, and the Windows
    build answers ``unknown variable`` and exits 7.

    That flag is the decision in the module docstring, and it is needed on Unix alone for the reason
    it exists: MariaDB authenticates ``root@localhost`` against the OS user of the same name through
    the ``unix_socket`` plugin, so without it the account cannot be reached by whoever MixEngine runs
    as. Windows has no such plugin and creates a password-less root already, so there is nothing to
    turn off there — the same end state, reached by not asking.

    ``--service`` is deliberately never passed: MixEngine supervises this process itself, and an
    artifact whose first-run job registers a system service is one that has installed something the
    daemon cannot see.
    """
    program = tree / provides["mariadb-install-db"]
    data = work / "data"

    if not windows:
        data.mkdir(parents=True, exist_ok=True)
        # **Upstream's script cannot be given a basedir containing a space, and this is a limitation
        # rather than a workaround.** `mariadb-install-db` resolves its own helpers with
        # `find_in_dirs my_print_defaults $basedir/bin $basedir/extra` — `$basedir` unquoted — so a
        # path with a space is split into two arguments and the script exits with "FATAL ERROR:
        # Could not find my_print_defaults", naming a file that is exactly where it was told to look.
        #
        # It is upstream's escaping, it has nothing to do with relocation, and it will fail the same
        # way for a user whose installation path has a space in it — which T33 needs to know, because
        # the daemon either keeps its data directories out of such paths or bootstraps them without
        # this script. The same shape as the `mkmf -bundle_loader` finding in `ruby_unix.py`.
        #
        # So the *bootstrap* is given a space-free view of the same tree, and everything after it —
        # the server, the client, the shutdown — still runs from the path with the space, which is
        # the part that has to keep working. A symlink rather than a copy: this tree is most of a
        # gigabyte and nothing here writes into it.
        basedir = tree
        if " " in str(tree):
            link = Path(tempfile.mkdtemp(prefix="mixengine-basedir-")) / "tree"
            link.symlink_to(tree, target_is_directory=True)
            basedir = link
            program = link / provides["mariadb-install-db"]
            print(f"mariadb-install-db: run against {link}, because upstream's script does not "
                  f"quote $basedir and this tree's path contains a space")
        arguments = [
            # **First, and it is the difference between bootstrapping this artifact and bootstrapping
            # whatever the machine already has.** Without it the script and the bootstrap server it
            # starts read `/etc/mysql/my.cnf` — which exists on a GitHub Linux runner, because those
            # images ship a MySQL — and the system tables installation fails with a message that
            # blames the data directory. The script itself suggests this: "The problem could be
            # conflicting information in an external my.cnf files."
            #
            # T33 wants it for a stronger reason than CI does: a user with a MariaDB of their own
            # installed has a my.cnf naming a datadir, a socket and a port, and a MixEngine instance
            # that silently inherited any of them would be writing into somebody else's database.
            "--no-defaults",
            # The script resolves everything else — the bootstrap server, the error messages, the
            # plugin directory — relative to this, and it is why a bintar can be unpacked anywhere.
            f"--basedir={basedir}",
            f"--datadir={data}",
            "--auth-root-authentication-method=normal",
            "--skip-test-db",
            # Whoever is running this, stated. Left out, the script decides it should hand the data
            # directory to a user called `mysql` — the account a distribution's package would have
            # created — and stops when it cannot: "Cannot change ownership of the database
            # directories to the 'mysql' user". MixEngine runs its services as the user who
            # installed them and has no such account either, so this is the answer there as well.
            f"--user={getpass.getuser()}",
        ]
        # **`/usr/sbin` and `/sbin` on the path, which every other check in this repository is right
        # to leave off.** The cut-down PATH exists so a runner's own interpreter cannot answer for
        # the archive — but this is a *shell script* calling the system's own tools rather than the
        # artifact answering a question. `chown` is in `/usr/sbin` on macOS and `/usr/bin` on Linux,
        # so the cut-down path produced `chown: command not found` on one platform and not the other.
        # The same distinction `ruby_unix.py` draws for compiling a gem.
        installing = os.pathsep.join([path, "/usr/sbin", "/sbin"])

        # `Path("/bin/sh")` rather than the string: `borrow.run` names the program in its failure
        # message, and a `str` there turns a diagnosable script failure into an AttributeError from
        # the error handler itself — which is what the .deb leg reported instead of what went wrong.
        try:
            output = borrow.run(Path("/bin/sh"), str(program), *arguments, path=installing,
                                timeout=900)
        except SystemExit:
            # The script's advice is "examine the logs in <datadir>", and then the runner is thrown
            # away. So they are quoted here: the bootstrap server writes the actual SQL error there,
            # and the script's own summary never contains it.
            for log in sorted(data.glob("*.err")) + sorted(data.glob("*.log")):
                print(f"--- {log.name}", file=sys.stderr)
                print(log.read_text(encoding="utf-8", errors="replace")[-4000:], file=sys.stderr)
            raise
    else:
        # Not created first: the Windows program writes the directory itself and refuses one that is
        # already there. Its parent is what has to exist.
        data.parent.mkdir(parents=True, exist_ok=True)
        output = borrow.run(program, f"--datadir={data}", path=path, timeout=900)

    if not (data / "mysql").is_dir():
        raise SystemExit(
            f"{program.name} exited zero and left no mysql schema in {data}\n{output[-4000:]}"
        )
    ran = f"{provides['mariadb-install-db']} --datadir (a data directory bootstrapped from scratch)"
    if not windows and " " in str(tree):
        ran += "; run through a space-free path, which upstream's script requires"
    return ran


def said(logs: list[Path], tail: int = 8000) -> str:
    """Everything the server wrote, from both places it can write it.

    Two files rather than one because which of them holds the answer is a platform difference: on
    Unix mariadbd inherits stdout and also honours ``log_error``, and on Windows it uses the error
    log alone. Quoting whichever exists is how a failure here is diagnosable on both.
    """
    parts = []
    for log in logs:
        if log.is_file():
            text = log.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                parts.append(f"--- {log.name}\n{text[-tail:]}")
    return "\n".join(parts) if parts else "(the server wrote nothing to either log)"


def await_ping(
    tree: Path, provides: dict[str, str], port: int, process: subprocess.Popen, logs: list[Path],
    path: str, seconds: float = 120,
) -> None:
    """Wait for ``mariadb-admin ping``, or say what the server said instead.

    This is T33's readiness check and it is used here for the same reason it will be used there: a
    server that has accepted the port is not a server that has finished crash recovery, and the
    window between the two is where a supervisor that watches the port reports a service healthy and
    then hands a user a connection refusal.
    """
    admin = tree / provides["mariadb-admin"]
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit(
                f"mariadbd exited {process.returncode} before it answered a ping\n{said(logs)}"
            )
        answered = subprocess.run(
            [str(admin), "--protocol=TCP", "--host=127.0.0.1", f"--port={port}", "--user=root",
             "ping"],
            capture_output=True, text=True, timeout=60, env={**os.environ, "PATH": path},
        )
        if answered.returncode == 0 and "alive" in answered.stdout:
            return
        time.sleep(0.5)
    process.kill()
    raise SystemExit(f"mariadbd never answered a ping on 127.0.0.1:{port}\n{said(logs)}")


def query(tree: Path, provides: dict[str, str], port: int, statement: str, path: str) -> str:
    """Run one statement as root over TCP and answer with what came back, unadorned."""
    client = tree / provides["mariadb"]
    return borrow.run(
        client, "--protocol=TCP", "--host=127.0.0.1", f"--port={port}", "--user=root",
        "--batch", "--skip-column-names", "--execute", statement, path=path, timeout=300,
    )


def server(tree: Path, version: str, provides: dict[str, str], windows: bool) -> list[str]:
    """Bootstrap, start, ping, query and stop this artifact, from wherever *tree* currently is.

    The caller has already moved the tree somewhere it has never been; this makes it be a *database*
    while it is there. Returns the list of what was actually run, for the manifest.
    """
    # **The instance lives outside the moved tree, and both reasons are limitations worth stating.**
    #
    # The tree itself stays where the caller put it — a path containing a space, deliberately — and
    # that is the claim being tested: the *artifact* works from anywhere. What cannot live beside it
    # is the data directory, for two independent reasons found one CI round apart.
    #
    # `mariadb-install-db` leaves `$datadir` unquoted just as it leaves `$basedir` unquoted, so it
    # runs `chown` against two halves of a split path and stops with "Cannot change ownership of the
    # database directories".
    #
    # And a Unix socket path is limited to 103 characters by `sockaddr_un` — a kernel limit, not
    # MariaDB's — which the runner's own temporary directory very nearly exhausts before anything
    # here is added. mariadbd reports it and aborts *after* InnoDB has started, which reads like a
    # storage failure and is not one.
    #
    # Both are things T33 has to live with too: a data directory under "C:/Users/Nguyen Hai Quang"
    # is fine on Windows and a socket beside it is not fine on macOS.
    work = Path(tempfile.mkdtemp(prefix="mxe-instance-"))
    path = borrow.clean_path((tree / provides["mariadbd"]).parent, tree / "bin")

    banner = borrow.run(tree / provides["mariadbd"], "--version", path=path)
    # `mariadbd  Ver 11.8.8-MariaDB for Linux on x86_64` — the version is the third word and carries
    # a suffix upstream chooses, so the comparison is a prefix rather than an equality.
    stated = re.search(r"Ver\s+(\d+\.\d+\.\d+)", banner)
    if not stated or stated.group(1) != version:
        raise SystemExit(f"mariadbd reports {banner!r}, expected a {version} build")
    print(f"mariadbd version: {banner}")
    ran = [f"{provides['mariadbd']} --version"]

    ran.append(install_db(tree, work, provides, path, windows))
    print(f"mariadb-install-db: bootstrapped {work / 'data'}")

    port = free_port()
    my_cnf = configuration(work, tree, port, windows)
    logs = [work / "mariadbd.err", work / "mariadbd.log"]
    with logs[1].open("wb") as sink:
        process = subprocess.Popen(
            [str(tree / provides["mariadbd"]), f"--defaults-file={my_cnf}"],
            stdout=sink, stderr=subprocess.STDOUT, cwd=str(work),
            env={**os.environ, "PATH": path},
        )

    try:
        await_ping(tree, provides, port, process, logs, path)
        print(f"mariadb-admin ping: the server answered on 127.0.0.1:{port}")
        ran.append(f"{provides['mariadbd']} --defaults-file (a rendered my.cnf), started")
        ran.append(f"{provides['mariadb-admin']} ping")

        reported = query(tree, provides, port, "SELECT VERSION()", path)
        if not reported.startswith(version):
            raise SystemExit(f"SELECT VERSION() answered {reported!r}, expected {version}")
        print(f"SELECT VERSION(): {reported}")

        # A real write, through InnoDB, read back. Creating a database alone touches nothing but the
        # data dictionary, and a storage engine that failed to initialise is the failure worth
        # catching here — it is written into the log and the server otherwise starts perfectly.
        query(tree, provides, port, f"CREATE DATABASE {DATABASE}", path)
        query(
            tree, provides, port,
            f"CREATE TABLE {DATABASE}.t (id INT PRIMARY KEY, note TEXT) ENGINE=InnoDB; "
            f"INSERT INTO {DATABASE}.t VALUES (1, 'mixengine')",
            path,
        )
        stored = query(tree, provides, port, f"SELECT note FROM {DATABASE}.t WHERE id = 1", path)
        if stored != "mixengine":
            raise SystemExit(f"the row came back as {stored!r}")
        engine = query(
            tree, provides, port,
            f"SELECT engine FROM information_schema.tables WHERE table_schema = '{DATABASE}' "
            f"AND table_name = 't'",
            path,
        )
        if engine.lower() != "innodb":
            raise SystemExit(
                f"the table was created with the {engine} engine rather than InnoDB, which means "
                "InnoDB did not initialise and the server fell back without failing"
            )
        print(f"wrote and read back a row through {engine}")
        ran.append(f"{provides['mariadb']} CREATE/INSERT/SELECT through InnoDB")

        # The one that makes a reload possible at all, and the one a supervisor must use: an InnoDB
        # killed mid-write recovers on the next start, which is a user watching a progress bar
        # because a packaging check took a shortcut.
        borrow.run(
            tree / provides["mariadb-admin"], "--protocol=TCP", "--host=127.0.0.1", f"--port={port}",
            "--user=root", "shutdown", path=path, timeout=300,
        )
        try:
            process.wait(timeout=120)
        except subprocess.TimeoutExpired:
            process.kill()
            raise SystemExit("mariadb-admin shutdown returned and the server was still running") \
                from None
        if process.returncode not in (0, None):
            raise SystemExit(f"mariadbd exited {process.returncode} on a clean shutdown")
        print("mariadb-admin shutdown: the server exited cleanly")
        ran.append(f"{provides['mariadb-admin']} shutdown (a clean InnoDB shutdown)")

        # Proof that the shutdown was clean, rather than a belief about the exit code: InnoDB writes
        # `Shutdown completed` only after its last checkpoint is flushed, and the server follows it
        # with `Shutdown complete` of its own. A crash-recovering server on the user's first start is
        # the whole failure mode being ruled out here.
        text = said(logs, tail=200_000)
        if "Shutdown complete" not in text:
            raise SystemExit(f"no clean-shutdown line in either log\n{text[-4000:]}")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=120)

    return ran
