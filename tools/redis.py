#!/usr/bin/env python3
"""Compile Redis from upstream source for the five cells that can run it, and say so about the one.

**P8 read the Windows question as "is there a Windows build system", and that was the wrong
question.** There is not one, and re-reading the 8.10.0 tarball on a runner confirms it: no
``CMakeLists.txt``, no ``win32/``, no project file, and a ``src/Makefile`` around POSIX ``fork()``,
``epoll`` and ``kqueue``. No tag of ``redis/redis`` from 2.6 to 8.10 has ever contained one; the
Windows support that existed lived in ``microsoftarchive/redis``, a separate fork with its own
``msvs/`` and ``src/Win32_Interop``, which stopped at 3.0.504 in 2016.

But a program does not need to be *ported* to run somewhere — it needs the interfaces it calls. Both
of the routes that supply POSIX on Windows compile this source unmodified, and this recipe takes
the more conservative of them: **Cygwin**, whose runtime is documented for redistribution and whose
licence and runtime exception ship as files an archive can carry, over MSYS2, whose own
documentation says its runtime is for its build tools rather than for programs to be distributed.
Whatever the build actually imports travels beside the binaries — measured off the import table, not
copied from anyone's list — and is the only thing outside ``C:\\Windows`` the result loads. How many
that is depends on the line rather than on this recipe: one, ``cygwin1.dll``, for 7.2, 7.4, 8.8 and
8.10, and five for 8.0 through 8.6, which vendor the C++ ``fast_float`` and so pull in
``cygstdc++-6.dll`` with ``cygiconv-2.dll``, ``cygintl-8.dll`` and ``cyggcc_s-seh-1.dll`` behind it.

The alternatives are still no, and for the reasons P8 gave. **Valkey** is the same POSIX program
forked and sends a Windows user to WSL, which
[ADR 0003](https://github.com/haiquang9994/MixEngine/blob/master/.claude/decisions/0003-no-container-isolation.md)
excludes. **Memurai** is proprietary; a repository that redistributes what it packs cannot pack one.
**The community rebuilds** are a fork nobody maintains — and the point of compiling here is that
their *method* can be borrowed without their binaries: the tarball is upstream's, checked against
upstream's own digest, and nothing in it is patched.

What that costs, stated rather than smoothed over. Cygwin has no ``epoll``, so ``ae.c`` falls to its
``select`` backend, and the runtime cannot raise the descriptor limit as far as the default asks, so
``maxclients`` settles lower. Both are properties of one unreplicated development instance on a
developer's own machine, which is the only thing MixEngine runs. And ``windows/aarch64`` stays empty:
neither Cygwin nor MSYS2 has an ARM64 build, x86_64 under emulation would work, and an artifact
labelled ``aarch64`` may not hold binaries that are not.

One more Windows cell is empty and it is a *version* rather than an architecture: 7.2 builds there
and then faults in its own startup, which :data:`WINDOWS_FLOOR` states in full. Its four Unix cells
are packed as usual.

What the five cells get:

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
# source-available licence still has a supported Redis. See docs/packages/redis-memcached.md for
# what shipping the newer ones obliges this repository to do.
FLOOR = (7, 2)

# **Windows starts one line higher, and it is Redis 7.2 that says so rather than Cygwin.** 7.2.15
# compiles cleanly under Cygwin, links, installs, and then `redis-server.exe --version` dies before
# it prints anything. Traced on a `windows-2022` runner with Cygwin's own `strace`: the last system
# call is `time(0)`, then `exception c0000005` — an access violation — which Cygwin turns into
# signal 11 and exits with `0xB00`, the wait status of a process killed by SIGSEGV.
#
# Everything that could have made it an accident was ruled out on the same runner. `redis-cli.exe`,
# from the same compiler, the same flags and the same `cygwin1.dll` beside it, answers
# `redis-cli 7.2.15` and exits 0. The unmoved tree faults identically to the relocated one, so it is
# not `borrow.moved`. `cygcheck` reads the import table as `cygwin1.dll` and Windows API sets and
# nothing else, so nothing is missing. And the code it dies in — `tzset`, `gettimeofday`, `srand`,
# `init_genrand64`, `crc64_init`, between `time()` and the banner — is **byte for byte the same in
# 7.4.10**, which builds and runs on this cell. So it is the older source and this toolchain
# disagreeing, in a way that no evidence here places in Redis, in Cygwin, or in the recipe.
#
# What is not done about it, and why. Patching the source is what "nothing in it is patched" exists
# to refuse. Compiling this one line differently — a lower `-O`, another `-f` — would make the
# 7.2 Windows artifact a different build from the four Unix cells of its own version, which is the
# rule this repository is named after. So the cell is empty and says which line opens it, the same
# answer `windows/aarch64` gets one paragraph up for a different reason.
WINDOWS_FLOOR = (7, 4)

# What `make -C src install` puts in `bin/`, and what MixEngine will run. The last two are symlinks
# to `redis-server` that upstream's install target creates — a running instance's supervisor wants
# them after a crash, and they cost nothing. On Windows they are copies rather than links, because
# `ln -sf` under Cygwin writes a symlink only Cygwin resolves and MixEngine starts a native process.
# The server tells the three apart by reading its own `argv[0]`, so a copy serves as well as a link.
SUFFIX = ".exe" if sys.platform == "win32" else ""
LAYOUT = {
    name: f"bin/{name}{SUFFIX}"
    for name in ("redis-server", "redis-cli", "redis-check-rdb", "redis-check-aof")
}

# Upstream's own `DEPENDENCY_TARGETS`, needed because the Windows build drives `deps` directly
# rather than letting `persist-settings` do it with a leading `-` that turns a failed dependency
# into an ignored `Error 2` and a link failure twenty minutes later.
#
# **Read out of the tarball being packed rather than copied into this file**, because the list is
# not a property of Redis — it is a property of *this release of* Redis, and a copy is only ever
# right for the line it was written against. Upstream's own `src/Makefile` across the eight lines
# this recipe offers:
#
#     7.2, 7.4    hiredis linenoise lua hdr_histogram fpconv
#     8.0, 8.2    hiredis linenoise lua hdr_histogram fpconv fast_float
#     8.4, 8.6    hiredis linenoise lua hdr_histogram fpconv fast_float xxhash
#     8.8, 8.10   hiredis linenoise lua hdr_histogram fpconv xxhash tre
#
# A copy of the last row is what was here, and it is why `7.2` stopped at `No rule to make target
# 'xxhash'` — asking a 2023 tarball to build a dependency added in 2026. Nothing weaker than reading
# the Makefile would have been right for more than two of the eight lines, and the four in the
# middle would each have been wrong in their own way.
#
# `jemalloc` is in `deps/` and appears in none of those rows, which is upstream's decision rather
# than this recipe's: the Makefile only chooses it as the allocator on Linux, where it is built by
# the `all` target this function's caller does not drive.
DEPENDENCY_TARGETS = re.compile(r"^DEPENDENCY_TARGETS\s*=\s*(.+)$", re.MULTILINE)


def dependency_targets(source_tree: Path) -> tuple[str, ...]:
    """Answer the dependencies *this* tarball's ``src/Makefile`` says the core needs.

    Refuses rather than falling back on a default. A Makefile that stopped stating the variable is a
    change in how upstream builds, and guessing at it here would produce the same failure one link
    step later with nothing pointing at the cause.
    """
    makefile = source_tree / "src" / "Makefile"
    found = DEPENDENCY_TARGETS.search(makefile.read_text(encoding="utf-8", errors="replace"))
    if not found:
        raise SystemExit(
            f"{makefile} states no DEPENDENCY_TARGETS; upstream changed how deps/ is driven and "
            f"this recipe has to be read against the new spelling rather than assume the old one"
        )
    targets = tuple(found.group(1).split())
    print(f"deps/ targets, read from src/Makefile: {' '.join(targets)}")
    return targets

# **Goals, because `all` cannot be one here.** `src/Makefile`'s `all` ends in `module_tests`, which
# links `tests/modules/*.so` against symbols `redis-server` exports — and a PE image has no
# undefined symbol resolved at load time, so that target stops on `undefined reference to strncmp`
# however cleanly the server itself builds. Naming goals avoids it without patching the Makefile,
# which is also why `make install` is not used: `install: all`, so it drags the same target back in.
WINDOWS_TARGETS = ("redis-server", "redis-cli", "redis-check-rdb", "redis-check-aof")

# Two compiler flags, and neither touches a line of Redis.
#
# `-D_GNU_SOURCE` because Cygwin guards `dladdr` and `RTLD_DEFAULT` behind `__GNU_VISIBLE`, which is
# derived from the feature-test macros — asking for the feature is the supported way to get it, and
# it leaves the toolchain as installed. The community workflow edits `/usr/include/dlfcn.h` instead.
#
# `-Wno-char-subscripts` because `deps/hiredis` compiles with its own `-Werror` and `sds.c`
# subscripts an array with a plain `char` through `isspace()`. glibc's macro casts to `int` so the
# warning never fires there; Cygwin's newlib does not cast, and the same unmodified line is an error.
WINDOWS_CFLAGS = "-D_GNU_SOURCE -Wno-char-subscripts"

# Installed by upstream and then thrown away. `redis-benchmark` is a benchmark, which the second
# half of *One version means one thing, and no more than is needed* names outright; `redis-sentinel`
# is a different service — a failover monitor for a replica set — and MixEngine supervises one
# unreplicated instance. Both are deleted after installation rather than kept out of it, for the
# reason `mariadb_build` gives about the test suite: upstream's install target is one recipe and
# taking a file out of the tree afterwards is checkable, while persuading a Makefile to install
# three of six things is a patch that goes stale.
PRUNE = (f"bin/redis-benchmark{SUFFIX}", f"bin/redis-sentinel{SUFFIX}")

# Every directory under `deps/` is compiled into `redis-server`, so every one of them is
# redistributed by this archive and its licence has to travel with it. The value is where upstream
# keeps the notice — a file of its own for seven of the nine, and the header comment of the source
# file itself for the other two: linenoise, which has no licence file and is BSD-2 in `linenoise.c`,
# and fast_float, which is an amalgamation generated with `amalgamate.py --license=MIT` and carries
# the MIT text at the top of `fast_float.h`.
#
# The table spans every line this recipe offers rather than any one of them, and no version has all
# nine: `fast_float` is in 8.0 through 8.6, `xxhash` from 8.4, `tre` from 8.8, and 7.2 and 7.4 have
# six. Only what is actually in the tree is looked up, so a row for a dependency this tarball does
# not carry costs nothing — while a missing row stops the build, which is the direction that matters.
#
# It is a table rather than a glob so that a dependency added in a future release **fails the build**
# instead of shipping unlicensed: `licences` below checks that every directory under `deps/` has a
# row here. That is the MariaDB lesson in one check — three separate archives shipped GPL binaries
# with no licence text at all, and no smoke test could ever have shown it.
DEPS_LICENCES = {
    "fast_float": "fast_float.h",
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


def cygwin_root() -> Path:
    """Where Cygwin is installed, **proved by asking it rather than by finding a `bash.exe`**.

    Every Windows runner in this matrix already has a `bash` on `PATH` — Git for Windows ships one,
    and it is an MSYS2 build. It compiles this source too, and the result would be a different
    artifact depending on which one happened to be first in the path: a different runtime DLL, a
    different licence to ship, a different set of quirks to have measured. That is the kind of
    difference no smoke test asks about, so it is settled here instead. `uname -s` answers
    `CYGWIN_NT-…` for Cygwin and `MSYS_NT-…` for the other, and the check costs one process.

    Cygwin rather than MSYS2 for two reasons that outlast this recipe: MSYS2's own documentation
    says its runtime is for its build tools rather than for programs to be distributed, and Cygwin
    publishes the runtime licence and its exception as documents an archive can carry.
    """
    candidates: list[Path] = []
    stated = os.environ.get("CYGWIN_ROOT")
    if stated:
        candidates.append(Path(stated))
    found = shutil.which("bash")
    if found:
        candidates.append(Path(found).resolve().parent.parent)
    # The install action puts Cygwin on the *work* volume — `D:\cygwin` on a GitHub runner, not the
    # `C:\cygwin64` a constant would guess — so the environment variable is what normally answers
    # and these are only for a developer's own machine.
    candidates += [Path(r"C:\cygwin64"), Path(r"C:\cygwin"), Path(r"D:\cygwin")]

    tried: list[str] = []
    seen: set[Path] = set()
    for root in candidates:
        if root in seen:
            continue
        seen.add(root)
        # **`uname.exe` by absolute path, not `bash -c "uname -s"`.** The first attempt asked through
        # the shell and every candidate answered `MINGW64_NT` — including Cygwin's own bash, sitting
        # in the directory the install action had just reported. It was running Git for Windows'
        # `uname`, because `add-to-path: false` means Cygwin's `/usr/bin` is not on PATH and a shell
        # resolves its commands through PATH like anything else. The question is which *installation*
        # this is, so it is asked of a file in it.
        stamp = root / "bin" / "uname.exe"
        if not (root / "bin" / "bash.exe").is_file() or not stamp.is_file():
            continue
        try:
            answer = subprocess.run(
                [str(stamp), "-s"], capture_output=True, text=True, timeout=120
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError) as problem:
            tried.append(f"{stamp} ({problem})")
            continue
        if answer.startswith("CYGWIN_NT"):
            return root
        tried.append(f"{stamp} says {answer!r}")

    raise SystemExit(
        "no Cygwin on this machine, and Redis has no other way onto Windows: upstream's source is "
        "a POSIX program with no project file of any kind, so the build needs a POSIX runtime to "
        "compile against. Install it with cygwin/cygwin-install-action, or set CYGWIN_ROOT. "
        + (f"Looked at: {'; '.join(tried)}." if tried else "Found no bash.exe at all.")
    )


def cygwin(root: Path, script: str, cwd: Path) -> None:
    """Run *script* under Cygwin's shell, from *cwd*, with Cygwin's own tools on ``PATH``.

    **The `PATH` is built here rather than by the workflow, and that is the point.** Cygwin is
    installed with `add-to-path: false`, because on `PATH` it changes what `shell: bash` means for
    every step in the job — and then chokes on the CRLF Actions writes into each step script. Kept
    off, nothing else in the run is affected and the one process that needs Cygwin's `make`, `gcc`
    and `sed` is handed them explicitly. A shell resolves its commands through `PATH` like anything
    else, so `--noprofile --norc` alone would have left this script running Git for Windows' tools
    inside Cygwin's bash, which is exactly how the version check failed before it.

    `borrow.clean_path` puts the operating system after them and nothing else on it at all, which is
    the same cut-down environment every smoke test in this repository runs under.

    No `-o igncr`, unlike the spike that measured all this: that option exists because GitHub writes
    a step's script to a **file** with CRLF endings, and this passes the script as an argument built
    in Python, where the only line endings are the ones written above.
    """
    print(f"$ {root / 'bin' / 'bash.exe'} -c <<\n{script.strip()}\n", flush=True)
    result = subprocess.run(
        [str(root / "bin" / "bash.exe"), "--noprofile", "--norc", "-c", script],
        cwd=str(cwd), timeout=3600,
        env={**os.environ, "PATH": borrow.clean_path(root / "bin")},
    )
    if result.returncode != 0:
        raise SystemExit(f"the Cygwin build exited {result.returncode}")


def build_windows(source_tree: Path, prefix: Path) -> list[str]:
    """Compile the core under Cygwin and put the four binaries where `assemble` expects them.

    `deps` is built here rather than left to `persist-settings`, which invokes it with a leading `-`
    so that a dependency that fails to compile is reported as `Error 2 (ignored)` and the build
    carries on to a link that cannot work. Driving it directly means the failing dependency names
    itself in the step that failed.

    The install is four `copy2` calls because upstream's `install` target cannot run — see
    :data:`WINDOWS_TARGETS` — and because what it would add beyond copying is `ln -sf`, which under
    Cygwin writes a symlink nothing but Cygwin resolves.
    """
    root = cygwin_root()
    print(f"building under Cygwin at {root}")
    targets = dependency_targets(source_tree)
    cygwin(
        root,
        f'set -e\n'
        f'make -C deps -j{jobs()} CFLAGS="{WINDOWS_CFLAGS}" {" ".join(targets)}\n'
        f'make -C src -j{jobs()} CFLAGS="{WINDOWS_CFLAGS}" {" ".join(WINDOWS_TARGETS)}\n',
        cwd=source_tree,
    )

    binaries = prefix / "bin"
    binaries.mkdir(parents=True, exist_ok=True)
    for name in WINDOWS_TARGETS:
        built = source_tree / "src" / f"{name}.exe"
        if not built.is_file():
            raise SystemExit(f"the build produced no {built.name}; make reported success")
        shutil.copy2(built, binaries / built.name)
    return [
        f"make -C deps {' '.join(targets)} (Cygwin)",
        f"make -C src {' '.join(WINDOWS_TARGETS)} CFLAGS={WINDOWS_CFLAGS!r}",
    ]


def build(source_tree: Path, prefix: Path) -> list[str]:
    """Compile the core and install it, and answer with what was asked for.

    ``make -C src`` rather than the top-level ``make``, and the difference is the whole of the
    modules decision in the docstring. From 8.0 the top-level goal routes through
    ``scripts/build.sh``, which builds every module cloned under ``modules/*/src`` — and the release
    tarball ships them cloned. ``src`` is the subdirectory upstream's own script recurses into for
    the core, it is the only Makefile 7.x has, and driving it directly is what makes one code path
    serve both lines.

    Windows is the one target this does not describe, and :func:`build_windows` says why in full:
    there is no `all` to run and no `install` to call, because both end in a target that links a
    shared object against symbols an executable exports.
    """
    if sys.platform == "win32":
        return build_windows(source_tree, prefix)

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
               seconds: float = 30, environment: dict | None = None) -> None:
    """Wait for the server to answer ``PING``, or say what it said instead.

    *environment* carries the cut-down ``PATH`` for the same reason every other call in this test
    does. It matters most on Windows, where `redis-cli.exe` needs `cygwin1.dll`: inheriting the
    build machine's ``PATH`` would let it load the one in the Cygwin installation and answer PONG
    from an archive that is missing it.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit(
                f"redis-server exited {process.returncode} before it answered PING\n"
                f"{log.read_text(encoding='utf-8', errors='replace')}"
            )
        answer = subprocess.run(
            [str(cli), "-p", str(port), "ping"], capture_output=True, text=True, timeout=30,
            env=environment,
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
        #
        # **Quoted, and that is the whole reason this test earns its runner minute.** `borrow.moved`
        # puts the tree under a directory whose name contains a space on purpose, and a `redis.conf`
        # directive is split on whitespace: unquoted, `dir` arrives with two arguments and the
        # server answers `*** FATAL CONFIG FILE ERROR *** ... wrong number of arguments` before it
        # ever listens. A user whose projects live under `C:\Users\Ha Quang` or `/Users/ha quang`
        # would have been the one to find that out. `core::generate` renders the real one and has to
        # quote every path it writes for the same reason — see T35.
        f"bind 127.0.0.1\n"
        f"port {port}\n"
        f"dir \"{work.as_posix()}\"\n"
        f"save \"\"\n"
        f"appendonly no\n"
        f"daemonize no\n",
        encoding="utf-8",
    )

    log = work / "redis.log"
    environment = {**os.environ, "PATH": path}
    with log.open("wb") as sink:
        process = subprocess.Popen(
            # **The config is named relatively, against a working directory, and Windows is why.**
            # `getAbsolutePath()` in `server.c` decides a path is absolute with `relpath[0] == '/'`
            # and otherwise joins it to `getcwd()` — so under Cygwin an ordinary Windows path is
            # treated as relative and glued onto the working directory, and the server dies on
            # `can't open config file '/cygdrive/d/a/…/C:/…/redis.conf'`. Neither `C:\…` nor `C:/…`
            # gets past that line; a `/cygdrive/c/…` path would, and is refused because it would put
            # the emulation layer's private spelling into the command line MixEngine's supervisor
            # builds. Naming the file relatively satisfies the same code path on every platform, so
            # this is one rule rather than a Windows branch — and it is what T35 has to do too.
            [str(server), config.name],
            stdout=sink, stderr=subprocess.STDOUT, env=environment, cwd=str(work),
        )

    try:
        await_pong(cli, port, process, log, environment=environment)
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
    if operating_system == "windows" and arch != "x86_64":
        borrow.unavailable(
            "no Cygwin for Windows on ARM, and Redis needs one: upstream's source is a POSIX "
            "program with no project file of any kind, so it is compiled here against a POSIX "
            "runtime rather than ported. Neither Cygwin nor MSYS2 has an ARM64 build. Emulating "
            "x86_64 would work and is refused: an artifact labelled aarch64 would hold binaries "
            "that are not. This cell is empty and the index says so; the x86_64 one is not."
        )

    work = Path(tempfile.mkdtemp(prefix="mixengine-redis-"))
    version, source_tree, digest, url = source(arguments.version, work)

    # After the resolution rather than beside the `windows/aarch64` check above, because that cell is
    # empty for every version and this one is empty for a version — which cannot be known until a
    # line like `7.2` has been turned into `7.2.15`.
    if operating_system == "windows" and tuple(
        int(part) for part in version.split(".")[:2]
    ) < WINDOWS_FLOOR:
        borrow.unavailable(
            f"Redis {version} compiles under Cygwin and then faults in its own startup: "
            f"`redis-server --version` takes an access violation between `time()` and the banner "
            f"and is killed by SIGSEGV, while `redis-cli` from the same build runs. The four Unix "
            f"cells of this version are unaffected and are packed. Windows starts at "
            f"{'.'.join(str(part) for part in WINDOWS_FLOOR)} — see WINDOWS_FLOOR in tools/redis.py "
            f"for what was measured and why neither patching nor another -O is the answer."
        )

    print(f"building Redis {version} for {operating_system}/{arch}")

    # Installed into a prefix nothing will ever look at again — Redis compiles no path into any
    # binary, so unlike every other built row here the prefix is a staging directory and not a
    # promise. It still gets the version in its name, so that two runs on one machine cannot mix.
    prefix = work / f"prefix-{version}"
    asked = build(source_tree, prefix)
    tree, provides = assemble(prefix, work, source_tree)

    runtime = ""
    if operating_system == "windows":
        # **`libdir="bin"`, not `lib`, and that is the whole of the rewrite on Windows.** The PE
        # loader searches the directory of the image being loaded before anything else, so a DLL
        # copied beside the executable needs no rpath, no install name and no re-signing — the copy
        # *is* the redirection. `search` is the Cygwin installation, which is where the two
        # libraries come from and the one place `verify` deliberately will not look afterwards.
        bundled = relocate.bundle(tree, libdir="bin", search=[cygwin_root() / "bin"])
        if not bundled:
            raise SystemExit(
                "nothing was bundled, and a Cygwin build imports cygwin1.dll by construction — so "
                "the import table was not read rather than the archive being self-contained. A "
                "tree that needed nothing and a tree nothing looked at are the same shape here."
            )
        print(f"bundled {len(bundled)} librar{'y' if len(bundled) == 1 else 'ies'}: "
              f"{', '.join(sorted(bundled))}")
        relocate.bundled_licences(tree, bundled)
        runtime = f"; POSIX runtime supplied by Cygwin ({', '.join(sorted(bundled))}, LGPLv3)"

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
            f"{runtime}"
        ),
        "provides": provides,
    }
    measured = relocate.floor(tree)
    if measured:
        manifest["requires"] = {measured[0]: measured[1]}
        print(f"needs {measured[0]} {measured[1]} or newer")

    manifest["smoke"] = smoke(tree, version, provides)
    print(f"built from {url}")

    borrow.publish(tree, manifest, arguments.out, "zip" if operating_system == "windows" else "tar")
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
