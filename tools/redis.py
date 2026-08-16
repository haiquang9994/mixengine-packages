#!/usr/bin/env python3
"""Compile Redis from upstream source for the four cells that can run it, and say so about the two.

**The Windows cells are empty, and the finding is stronger than the roadmap expected.** P8 was
written to decide between compiling Redis natively on a Windows runner and declaring the cell empty,
on the understanding that Microsoft's fork died at 3.0 and what circulates since is community
rebuilds. Asked rather than assumed, there is nothing to decide: Redis 8.10 has **no Windows build
system at all** — no ``CMakeLists.txt``, no ``win32/``, no project file, and a ``src/Makefile``
around POSIX ``fork()``, ``epoll`` and ``kqueue``. Upstream's own README lists Linux, OSX, OpenBSD,
NetBSD and FreeBSD and does not mention Windows. This is not a build that needs the right flags; it
is a build that does not exist, and a recipe cannot supply one.

The three ways out were each asked and each answers no. **Valkey** — MixEngine's runtime table names
it as the alternative — is a fork of the same POSIX program and is not supported on Windows either;
its own installation page sends a Windows user to WSL, which
[ADR 0003](https://github.com/haiquang9994/MixEngine/blob/master/.claude/decisions/0003-no-container-isolation.md)
excludes. **Memurai** is a proprietary product; a repository that redistributes what it packs cannot
pack one. **The community rebuilds** are a fork nobody maintains, which is the thing the roadmap
already refused to ship. So the cell is stated rather than filled, here and in the index, and a
Windows leg of the workflow runs anyway and exits 75 — an empty cell that says so on every run is
worth a runner minute.

What the four remaining cells get:

*Core Redis, and none of the bundled modules.* Since 8.0 the release tarball vendors RediSearch,
RedisJSON, RedisTimeSeries, RedisBloom and vector-sets — 6,671 files, and the reason the 8.10.0
tarball is 21 MB where 7.2.15 is 3.4 MB. Building them wants LLVM 21, Rust 1.94 and a CMake pinned
between 3.25 and 3.31.6, on four targets, for every security release, to ship data structures a
local web development environment does not reach for; and it would make the 7.2 cells of this row
mean something different from the 8.x ones, since 7.x has no modules to build. Upstream supplies the
switch by name — ``scripts/build.sh redis`` is "Redis only, no modules" — and what that script does
for the core is ``make -C src all``, which is what this recipe runs directly so that one code path
serves 7.x and 8.x alike.

*No TLS.* ``BUILD_TLS=yes`` links OpenSSL, which is then a library to bundle, a version to keep
current and a floor to measure, in exchange for encrypting a loopback connection between two
processes on a developer's own machine. Left off, ``redis-server`` imports nothing outside the C
runtime on Linux and nothing outside ``libSystem`` on macOS — the artifact is self-contained the way
Caddy's is, and ``relocate.verify`` is what says so rather than this paragraph.

*Nothing to relocate, which is worth stating because it is rare here.* Redis compiles no prefix into
anything: the server takes its configuration from ``argv`` and resolves nothing relative to where it
was built. Every other built row in this repository spends most of its length on that problem.

*The version and its digest come from the same document.* ``redis/redis-hashes`` is upstream's own
catalogue of every published tarball with a SHA-256 and a URL per line, which is the trade
``caddy.py`` makes with ``caddy_<version>_checksums.txt`` and ``mariadb_build.py`` makes with the
MariaDB REST API: what exists and what it should hash to are read from one place, so a digest can
never be taken from a different release than the archive it is checked against.

Python 3 stdlib only, by policy: this runs on a GitHub runner with nothing installed.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import borrow  # noqa: E402  — siblings, and this directory is not importable as a package
import relocate  # noqa: E402

# Upstream's catalogue: one line per published tarball, `hash <file> sha256 <digest> <url>`. It is a
# plain file in a git repository rather than an API, which is the reason to prefer it over the
# GitHub releases listing — the 8.x releases attach one `redis-full.tar.gz` and the older lines
# attach nothing at all, so the assets are not a catalogue of anything.
HASHES = "https://raw.githubusercontent.com/redis/redis-hashes/master/README"

# Who is asking, because `download.redis.io` refuses to say otherwise. It answers **403** to
# `Python-urllib/3.x` and 200 to anything else, on the same URL in the same second — so without this
# the recipe resolves a version correctly and then dies on the download with a status that reads
# like the release was withdrawn. Named rather than disguised: a publisher blocking a default agent
# is entitled to know which program replaced it.
AGENT = {"User-Agent": "mixengine-packages (+https://github.com/haiquang9994/mixengine-packages)"}

# The oldest line offered, and the choice is about licences as much as about age. Redis 7.2 is the
# last BSD-3 release line; 7.4 is RSALv2/SSPLv1 and 8.0 onwards adds AGPLv3 as a third option. Both
# ends of that are still patched by upstream — 6.2, 7.2, 7.4 and every 8.x line took a release on
# the same day in July 2026 — so a floor here is this repository's decision and not upstream's, and
# the reason to put it at 7.2 rather than lower is that it is where a user who will not accept a
# source-available licence still has a supported Redis. See the README section for what shipping the
# newer ones obliges this repository to do.
FLOOR = (7, 2)

# What `make -C src install` puts in `bin/`, and what MixEngine will run. The last two are symlinks
# to `redis-server` that upstream's install target creates — a running instance's supervisor wants
# them after a crash, and they cost nothing.
LAYOUT = {
    "redis-server": "bin/redis-server",
    "redis-cli": "bin/redis-cli",
    "redis-check-rdb": "bin/redis-check-rdb",
    "redis-check-aof": "bin/redis-check-aof",
}

# Installed by upstream and then thrown away. `redis-benchmark` is a benchmark, which the second
# half of *One version means one thing, and no more than is needed* names outright; `redis-sentinel`
# is a different service — a failover monitor for a replica set — and MixEngine supervises one
# unreplicated instance. Both are deleted after installation rather than kept out of it, for the
# reason `mariadb_build` gives about the test suite: upstream's install target is one recipe and
# taking a file out of the tree afterwards is checkable, while persuading a Makefile to install
# three of six things is a patch that goes stale.
PRUNE = ("bin/redis-benchmark", "bin/redis-sentinel")

# Every directory under `deps/` is compiled into `redis-server`, so every one of them is
# redistributed by this archive and its licence has to travel with it. The value is where upstream
# keeps the notice — a file of its own for seven of the eight, and the header comment of the source
# file itself for linenoise, which has no licence file and is BSD-2 in `linenoise.c`.
#
# It is a table rather than a glob so that a dependency added in a future release **fails the build**
# instead of shipping unlicensed: `licences` below checks that every directory under `deps/` has a
# row here. That is the MariaDB lesson in one check — three separate archives shipped GPL binaries
# with no licence text at all, and no smoke test could ever have shown it.
DEPS_LICENCES = {
    "fpconv": "LICENSE.txt",
    "hdr_histogram": "COPYING.txt",
    "hiredis": "COPYING",
    "jemalloc": "COPYING",
    "linenoise": "linenoise.c",
    "lua": "COPYRIGHT",
    "tre": "LICENSE",
    "xxhash": "LICENSE",
}

# Redis's own, from the root of the tarball, and **the row spans a licence change** so both
# spellings are looked for and neither is required on its own. Through 7.2 the file is `COPYING` and
# says BSD-3; from 7.4 it is `LICENSE.txt` and says RSALv2 or SSPLv1, with AGPLv3 added at 8.0.
# `REDISCONTRIBUTIONS.txt` is not decoration on the newer lines: it is the document `LICENSE.txt`
# refers to for which contributions arrived under which terms, and a reader holding one without the
# other cannot answer that.
OWN_LICENCES = ("LICENSE.txt", "REDISCONTRIBUTIONS.txt", "COPYING")

# One of these has to be in the tarball, or there is nothing stating the terms of the thing being
# redistributed and the build stops.
REQUIRED_LICENCE = ("LICENSE.txt", "COPYING")


def run(*command: str, cwd: Path | None = None, env: dict | None = None,
        timeout: int = 3600) -> None:
    print("$ " + " ".join(str(part) for part in command), flush=True)
    result = subprocess.run([str(part) for part in command], cwd=cwd, env=env, timeout=timeout)
    if result.returncode != 0:
        raise SystemExit(f"{command[0]} exited {result.returncode}")


def jobs() -> str:
    return str(os.cpu_count() or 2)


def catalogue() -> dict[tuple[int, ...], tuple[str, str, str]]:
    """Every stable release upstream has published, as ``version -> (version, url, sha256)``.

    Release candidates, betas and the milestone builds (``redis-8.10-m01.tar.gz``) are all in the
    same file and none of them is a release, so the pattern insists on three numeric components.
    The URLs upstream writes are ``http``; they are upgraded here rather than followed, because a
    digest fetched over a plaintext connection is worth exactly as much as the archive it describes.
    """
    listing = borrow.fetch(HASHES, headers=AGENT).decode("utf-8", "replace")
    offered: dict[tuple[int, ...], tuple[str, str, str]] = {}
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) != 5 or fields[0] != "hash" or fields[2] != "sha256":
            continue
        match = re.fullmatch(r"redis-(\d+\.\d+\.\d+)\.tar\.gz", fields[1])
        if not match:
            continue
        version = match.group(1)
        key = borrow.parts(version)
        if key[:2] < FLOOR:
            continue
        url = fields[4]
        offered[key] = (version, "https://" + url.partition("://")[2], fields[3])

    if not offered:
        raise SystemExit(f"{HASHES} listed no redis-<x.y.z>.tar.gz at all; upstream changed its shape")
    return offered


def resolve(spec: str) -> tuple[str, str, str]:
    """Turn ``8``, ``8.10``, ``8.10.0`` or ``latest`` into one published tarball and its digest."""
    offered = catalogue()
    if spec == "latest":
        candidates = sorted(offered)
    else:
        prefix = borrow.parts(spec)
        candidates = sorted(key for key in offered if key[: len(prefix)] == prefix)
    if not candidates:
        # Sorted on the tuple, not on the text: `8.10` is a later line than `8.2` and sorts before
        # it as a string, which would print a list nobody could read as a range.
        lines = [".".join(str(part) for part in line) for line in sorted({key[:2] for key in offered})]
        raise SystemExit(
            f"redis-hashes lists no stable {spec} at or above "
            f"{'.'.join(str(part) for part in FLOOR)}. It offers {', '.join(lines)}."
        )
    return offered[candidates[-1]]


def source(spec: str, work: Path) -> tuple[str, Path, str, str]:
    """Fetch and unpack the release tarball, checked against the digest the catalogue states."""
    version, url, digest = resolve(spec)
    if version != spec:
        print(f"{spec} resolves to Redis {version}")

    tarball = work / f"redis-{version}.tar.gz"
    print(f"fetching {url}")
    tarball.write_bytes(borrow.fetch(url, timeout=1800, headers=AGENT))
    actual = borrow.sha256(tarball)
    if actual != digest:
        raise SystemExit(f"{tarball.name} hashes to {actual}, redis-hashes states {digest}")
    print(f"sha256 {actual} (verified against redis/redis-hashes)")

    with tarfile.open(tarball) as archive:
        archive.extractall(work, filter="data")
    unpacked = work / f"redis-{version}"
    if not (unpacked / "src" / "Makefile").is_file():
        raise SystemExit(f"{unpacked} has no src/Makefile; this is not a Redis release tarball")
    return version, unpacked, actual, url


def build(source_tree: Path, prefix: Path) -> list[str]:
    """Compile the core and install it, and answer with what was asked for.

    ``make -C src`` rather than the top-level ``make``, and the difference is the whole of the
    modules decision in the docstring. From 8.0 the top-level goal routes through
    ``scripts/build.sh``, which builds every module cloned under ``modules/*/src`` — and the release
    tarball ships them cloned. ``src`` is the subdirectory upstream's own script recurses into for
    the core, it is the only Makefile 7.x has, and driving it directly is what makes one code path
    serve both lines.
    """
    asked = ["make", "-C", "src", f"-j{jobs()}", "all"]
    run(*asked, cwd=source_tree)
    run("make", "-C", "src", "install", f"PREFIX={prefix}", cwd=source_tree)
    return ["make -C src all", f"make -C src install PREFIX={prefix.name}"]


def licences(tree: Path, source_tree: Path) -> list[str]:
    """Ship the licence of Redis and of everything compiled into it, having checked the list.

    Several of these require their text to travel with the binary, so this is a condition of
    redistributing the archive rather than tidiness. The check that matters is the last one: a
    dependency upstream adds to ``deps/`` in a future release has no row in :data:`DEPS_LICENCES`
    and stops the build here, rather than shipping in ``redis-server`` with nothing to say for it.
    """
    into = tree / "licenses"
    into.mkdir(exist_ok=True)
    shipped: list[str] = []

    for name in OWN_LICENCES:
        if (source_tree / name).is_file():
            shutil.copy2(source_tree / name, into / f"redis-{name}")
            shipped.append(f"redis-{name}")

    deps = source_tree / "deps"
    present = sorted(path.name for path in deps.iterdir() if path.is_dir())
    unknown = [name for name in present if name not in DEPS_LICENCES]
    if unknown:
        raise SystemExit(
            f"deps/ carries {', '.join(unknown)}, which DEPS_LICENCES does not name — this build "
            f"would redistribute compiled code whose licence text it cannot find. Add the row."
        )
    for name in present:
        text = deps / name / DEPS_LICENCES[name]
        if not text.is_file():
            raise SystemExit(
                f"deps/{name}/{DEPS_LICENCES[name]} is where its licence used to be and is not "
                f"there now; upstream moved it and the row has to move with it"
            )
        shutil.copy2(text, into / f"redis-deps-{name}-{text.name}")
        shipped.append(f"redis-deps-{name}-{text.name}")

    if not any(f"redis-{name}" in shipped for name in REQUIRED_LICENCE):
        raise SystemExit(
            f"the tarball carries none of {', '.join(REQUIRED_LICENCE)}; nothing states the terms "
            f"this archive would be redistributed under"
        )
    print(f"shipping {len(shipped)} licence file(s) for Redis and its {len(present)} bundled deps")
    return shipped


def assemble(prefix: Path, work: Path, source_tree: Path) -> tuple[Path, dict[str, str]]:
    """The installed prefix as the tree that will be packed, minus what does not ship."""
    tree = work / "tree"
    shutil.copytree(prefix, tree, symlinks=True)

    dropped = []
    for relative in PRUNE:
        path = tree / relative
        # `lexists`, not `exists`: `redis-sentinel` is a symlink to `redis-server`, and if the
        # target had already gone `exists` would answer no about a file that is still in the archive.
        # That is the `mysql_ldb` bug MariaDB shipped for four rounds, in one call.
        if os.path.lexists(path):
            path.unlink()
            dropped.append(relative)
    if dropped:
        print(f"not shipping {', '.join(dropped)}")

    provides = {name: path for name, path in LAYOUT.items() if os.path.lexists(tree / path)}
    missing = sorted(set(LAYOUT) - set(provides))
    if missing:
        raise SystemExit(
            f"the build installed no {', '.join(missing)} — expected at "
            f"{', '.join(LAYOUT[name] for name in missing)}. Installed: "
            f"{sorted(path.name for path in (tree / 'bin').iterdir())}"
        )

    licences(tree, source_tree)
    return tree, provides


def free_port() -> int:
    """A port nothing is listening on, as the kernel's own answer rather than as a guess.

    Racy in principle — it is closed before Redis binds it — and the alternative is a hard-coded
    6379, which is *reliably* wrong on a machine already running a Redis, including a developer's.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def await_pong(cli: Path, port: int, process: subprocess.Popen, log: Path,
               seconds: float = 30) -> None:
    """Wait for the server to answer ``PING``, or say what it said instead."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit(
                f"redis-server exited {process.returncode} before it answered PING\n"
                f"{log.read_text(encoding='utf-8', errors='replace')}"
            )
        answer = subprocess.run(
            [str(cli), "-p", str(port), "ping"], capture_output=True, text=True, timeout=30
        )
        if answer.returncode == 0 and answer.stdout.strip() == "PONG":
            return
        time.sleep(0.2)
    process.kill()
    raise SystemExit(
        f"redis-server never answered PING on {port}\n"
        f"{log.read_text(encoding='utf-8', errors='replace')}"
    )


def smoke(tree: Path, version: str, provides: dict[str, str]) -> dict:
    """Run the artifact from somewhere it has never been, and make it be a *cache* while there.

    The same argument ``caddy.py`` makes, applied to what MixEngine will actually do to a Redis. A
    runtime is packed to be executed and ``redis-server --version`` would be the whole claim; a
    service is packed to be run, configured, health-checked and stopped, and each of those is a
    specific mechanism T35 depends on — a ``redis.conf`` rendered by ``core::generate``,
    ``redis-cli ping`` as the ``ReadyCheck``, and ``redis-cli shutdown`` as the
    ``StopBehaviour::Command``. So all four happen here, in that order, against the archive, from a
    directory it was moved to.

    ``SET``/``GET`` between the ping and the shutdown is the only one of the five that proves the
    thing anybody wants, and the ``INFO`` before it is what would catch a ``redis-cli`` talking to
    some *other* Redis the runner already had running — which is exactly what a hard-coded 6379
    would have arranged.
    """
    elsewhere = borrow.moved(tree)

    problems = relocate.verify(elsewhere)
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if problems:
        raise SystemExit("the relocated tree reaches outside itself")

    server = elsewhere / provides["redis-server"]
    cli = elsewhere / provides["redis-cli"]
    path = borrow.clean_path(server.parent)

    banner = borrow.run(server, "--version", path=path)
    if f"v={version} " not in banner:
        raise SystemExit(f"redis-server reports {banner!r}, expected a v={version} build")
    print(f"redis-server: {banner}")

    port = free_port()
    work = elsewhere.parent / "instance"
    work.mkdir(parents=True, exist_ok=True)
    config = work / "redis.conf"
    config.write_text(
        # Everything a MixEngine dev instance is: loopback only, no snapshot, no append-only log.
        # `dir` is set because a server that writes its dump where it was started from would write
        # into the moved copy, and the point of the move is that nothing does.
        f"bind 127.0.0.1\n"
        f"port {port}\n"
        f"dir {work.as_posix()}\n"
        f"save \"\"\n"
        f"appendonly no\n"
        f"daemonize no\n",
        encoding="utf-8",
    )

    log = work / "redis.log"
    environment = {**os.environ, "PATH": path}
    with log.open("wb") as sink:
        process = subprocess.Popen(
            [str(server), str(config)],
            stdout=sink, stderr=subprocess.STDOUT, env=environment, cwd=str(work),
        )

    try:
        await_pong(cli, port, process, log)
        print(f"redis-cli ping: PONG on {port}")

        info = borrow.run(cli, "-p", str(port), "info", "server", path=path)
        reported = dict(
            line.split(":", 1) for line in info.splitlines() if ":" in line and not line.startswith("#")
        )
        if reported.get("redis_version", "").strip() != version:
            raise SystemExit(
                f"the server on {port} reports redis_version "
                f"{reported.get('redis_version')!r}; this archive is {version}"
            )
        print(f"redis-cli info server: redis_version {version}, pid {reported.get('process_id', '?').strip()}")

        expected = f"mixengine {version}"
        borrow.run(cli, "-p", str(port), "set", "mixengine:smoke", expected, path=path)
        stored = borrow.run(cli, "-p", str(port), "get", "mixengine:smoke", path=path)
        if stored != expected:
            raise SystemExit(f"GET answered {stored!r}, expected {expected!r}")
        print(f"redis-cli set/get: {stored}")

        # Not `borrow.run`: the server closes the connection as it goes down, and whether redis-cli
        # calls that success has changed between lines. What is being checked is the *server*, so
        # the exit code that matters is the one below.
        subprocess.run(
            [str(cli), "-p", str(port), "shutdown", "nosave"],
            capture_output=True, text=True, timeout=60, env=environment,
        )
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            raise SystemExit("redis-cli shutdown returned and the server was still running") from None
        print("redis-cli shutdown nosave: the server exited")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=30)

    borrow.discard(elsewhere)
    return {
        "relocated": True,
        "ran": [
            "bin/redis-server --version",
            "redis-server against a rendered redis.conf",
            "redis-cli ping",
            "redis-cli info server, checked against this archive's version",
            "redis-cli set/get",
            "redis-cli shutdown nosave",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version", required=True,
        help="exact version (8.10.0), a line (8 or 8.10) for its newest release, or 'latest'",
    )
    parser.add_argument("--out", default=Path("dist"), type=Path)
    arguments = parser.parse_args()

    operating_system, arch = borrow.host("Redis")
    if operating_system == "windows":
        borrow.unavailable(
            "Redis has no Windows build: no CMakeLists.txt, no win32 directory, no project file, "
            "and a src/Makefile around POSIX fork(), epoll and kqueue. Upstream's README lists "
            "Linux, OSX, OpenBSD, NetBSD and FreeBSD. Valkey is the same program forked and is not "
            "supported there either; Memurai is proprietary; the community rebuilds are a fork "
            "nobody maintains. This cell is empty and the index says so."
        )

    work = Path(tempfile.mkdtemp(prefix="mixengine-redis-"))
    version, source_tree, digest, url = source(arguments.version, work)
    print(f"building Redis {version} for {operating_system}/{arch}")

    # Installed into a prefix nothing will ever look at again — Redis compiles no path into any
    # binary, so unlike every other built row here the prefix is a staging directory and not a
    # promise. It still gets the version in its name, so that two runs on one machine cannot mix.
    prefix = work / f"prefix-{version}"
    asked = build(source_tree, prefix)
    tree, provides = assemble(prefix, work, source_tree)

    manifest = {
        "schema": 1,
        "kind": "redis",
        "version": version,
        "os": operating_system,
        "arch": arch,
        "source": "built",
        "recipe": (
            f"redis-{version}.tar.gz from source (sha256 {digest[:12]}…, as published in "
            f"redis/redis-hashes); {'; '.join(asked)}; core only — no bundled modules, no TLS"
        ),
        "provides": provides,
    }
    measured = relocate.floor(tree)
    if measured:
        manifest["requires"] = {measured[0]: measured[1]}
        print(f"needs {measured[0]} {measured[1]} or newer")

    manifest["smoke"] = smoke(tree, version, provides)
    print(f"built from {url}")

    borrow.publish(tree, manifest, arguments.out, "tar")
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
