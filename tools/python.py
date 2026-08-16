#!/usr/bin/env python3
"""Borrow a CPython from python-build-standalone and repack it as a MixEngine artifact.

This is the row the whole "borrow before you build" rule was named after: MixEngine's runtime table
has written *"python-build-standalone (relocatable, all platforms)"* since before there was a
pipeline, on the strength of what the project says about itself. This is the task that checks it
rather than repeating it, and the answer is yes — with two things the table did not say.

*The Unix entry points are already relocatable, and by a trick worth knowing.* ``bin/pip3`` is not a
Python script with a ``#!`` pointing at an interpreter that will not be there; it is a ``/bin/sh``
script whose second line re-executes ``$(dirname "$(realpath "$0")")/python3.12`` on itself. So the
whole tree moves and every console script keeps working, with nothing for this recipe to fix.

*On Windows there is no pip to run at all.* ``Scripts/`` is empty — the pip *package* is installed in
``Lib/site-packages`` and only ``python -m pip`` reaches it. See :func:`ensure_pip` for why the fix
is a two-line batch file written here rather than the post-install hook
``.claude/features/runtime-versions.md`` reserved for it.

**What this recipe chooses is a graphical toolkit it does not ship**, which is the whole of
:func:`prune`. Upstream builds tkinter into every cell and builds a *different* tkinter into each
half — Tk 8.6 and Tix on Windows, Tk 9.0 with itcl and the Tcl Thread package on Unix — so keeping
it would be keeping two things under one version number. What is kept instead, and is normally
surplus, is the C API: see :func:`keeps`, which states that in the artifact rather than in a comment.

Everything mechanical is in :mod:`borrow`, shared with the Node.js and Ruby recipes. What stays here
is the resolution against upstream's release and the smoke test, which is a claim about Python and
not about downloads.

Python 3 stdlib only, by policy: this runs on a GitHub runner with nothing installed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from fnmatch import fnmatch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import borrow  # noqa: E402  — siblings, and this directory is not importable as a package
import relocate  # noqa: E402

REPOSITORY = "https://github.com/astral-sh/python-build-standalone"

# (os, arch) -> the Rust target triple upstream names its builds after. There is no second spelling
# to keep in step here, unlike Node: the triple is the whole per-target difference.
TARGETS = {
    ("windows", "x86_64"): "x86_64-pc-windows-msvc",
    ("windows", "aarch64"): "aarch64-pc-windows-msvc",
    ("macos", "aarch64"): "aarch64-apple-darwin",
    ("macos", "x86_64"): "x86_64-apple-darwin",
    ("linux", "x86_64"): "x86_64-unknown-linux-gnu",
    ("linux", "aarch64"): "aarch64-unknown-linux-gnu",
}

# **`gnu` and never `musl`, for the reason PHP's row already records**: a musl build of anything in
# this table is a build that cannot `dlopen`, and a Python that cannot load a compiled wheel is not
# a Python anyone can develop against. And plain `x86_64` rather than the `x86_64_v2`/`v3`/`v4`
# variants upstream also publishes, because those raise the CPU floor to buy a benchmark nobody
# running a local development environment is measuring.
#
# `install_only_stripped` rather than `install_only`: the same tree without the debug symbols, and
# the saving is not marginal — 46.1 MB against 22.0 MB on Windows and 109.2 MB against 34.1 MB on
# Linux, measured on 3.12.14. macOS is identical either way because those builds carry no separate
# symbols to strip. Every `install_only` asset in the release has a stripped counterpart, so this is
# not a variant that exists for some targets and not others.
VARIANT = "install_only_stripped"

# Where each command lives inside the tree, per OS. `python3` and `pip3` are absent from both halves
# on purpose: they are *command* names, which `core::shims::COMMANDS` maps onto these *executable*
# names, and an artifact that published both spellings would be inviting the two to disagree.
LAYOUT = {
    "windows": {"python": "python.exe", "pip": "Scripts/pip.cmd"},
    "unix": {"python": "bin/python3", "pip": "bin/pip3"},
}

REQUIRED = ("python", "pip")

# What tkinter is in the standard library, on every platform and every line. `turtle.py` is here
# because it is not a module that happens to use tkinter, it is tkinter with a pen; `idlelib` is an
# editor written in it and `turtledemo` a gallery of the pen. Left behind, all three would import
# nothing and raise on every machine, which is worse than being gone.
TKINTER_STDLIB = ("tkinter", "idlelib", "turtledemo", "turtle.py")

# What a Tcl or Tk file is called, **anchored at the start of the name**, which is not fussiness:
# `_testclinic.pyd` contains the letters of `tcl` in the middle and is not part of a toolkit. The
# `\d` after `thread` is the same care — Tcl's `thread3.0.6` is a package, `threading.py` is not.
TOOLKIT = re.compile(r"(tcl|tk|tix|itcl|thread\d|_tkinter)", re.IGNORECASE)

# What `lib/` holds on Unix that is not the toolkit: the standard library, the interpreter as a
# shared object, and one pkg-config file. A keep-list rather than a list of Tcl package directories,
# for the reason P2 and P3 both give — the delete-list would be written against whichever release
# was measured, and `itcl4.3.8` and `thread3.0.6` carry their versions in their names.
UNIX_LIB_KEEP = ("python3.*", "libpython3*", "pkgconfig")

# What `libs/` holds on Windows that anything links: the import library for this interpreter and the
# one for the stable ABI. Upstream also ships one per built-in extension module — 31 files on 3.10,
# `_socket.lib` and `_tkinter.lib` among them — which exist to link those modules *into* a Python
# being built, and nothing installing a wheel or compiling an sdist has ever opened one.
WINDOWS_IMPORT_KEEP = ("python3*.lib",)

# The one path in either tree that `TOOLKIT` matches and that is not the toolkit: `share/terminfo`
# carries ncurses' description of a terminal emulator called `tkterm`, in a database the built-in
# `curses` reads and Tcl has never touched. Named here rather than dodged by narrowing the sweep
# below to the directories Tcl lives in today, which would also stop it looking wherever upstream
# moves Tcl to next — and moving Tcl is precisely what 3.14 did.
NOT_TOOLKIT = ("share/terminfo",)

# Imported from the relocated tree, and every one of them is a compiled extension module or the thing
# that proves one works. A CPython missing any of these starts, answers `--version` correctly and
# then fails on the first real project: no `ssl` is no `pip install`, no `sqlite3` is no Django
# development database, no `ctypes` is no `cffi`, and `lzma`/`bz2`/`zlib` are how wheels and
# tarballs are opened. `tkinter` is not here because it is not in the archive: `prune` takes it out,
# and `PROBE` proves it is gone rather than leaving the claim to this comment.
MODULES = (
    "ssl", "hashlib", "sqlite3", "zlib", "bz2", "lzma", "ctypes", "decimal", "socket",
    "venv", "ensurepip", "json", "uuid",
)

# The batch file the Windows artifacts get in place of the `pip.exe` upstream does not ship. Two
# lines of it are a comment, because a file this repository wrote into a borrowed archive should say
# so to whoever finds it.
PIP_CMD = """@echo off
rem Written by MixEngine's tools/python.py, because python-build-standalone ships Scripts/ empty.
rem `pip.exe` would be a launcher with this machine's interpreter path baked into it, and this tree
rem is going to move. Computing the interpreter from the batch file's own location cannot go stale.
"%~dp0..\\python.exe" -m pip %*
"""


def release_tag() -> str:
    """The newest python-build-standalone release, without asking the GitHub API for it.

    ``/releases/latest`` is a redirect to ``/releases/tag/<date>``, so following it costs one request
    and no token. The API would answer the same question and is rate limited per IP — which on a
    six-legged matrix of shared GitHub runners is a limit six builds at once can genuinely reach,
    and it would fail as "resolution broke" rather than as "too many builds".
    """
    with urllib.request.urlopen(f"{REPOSITORY}/releases/latest", timeout=120) as response:
        location = response.url
    tag = location.rstrip("/").rsplit("/", 1)[-1]
    if not re.fullmatch(r"\d{8}", tag):
        raise SystemExit(f"{location} does not look like a dated release; got tag {tag!r}")
    return tag


def catalogue(tag: str) -> dict[str, str]:
    """Every asset in the release and its published SHA-256, from the release's own ``SHA256SUMS``.

    One file answers both questions this recipe has — *what exists* and *what it should hash to* —
    which is why nothing here enumerates assets any other way. A name absent from it is a name
    upstream does not describe, and downloading it anyway would be downloading something nobody
    stated.
    """
    try:
        sums = borrow.fetch(f"{REPOSITORY}/releases/download/{tag}/SHA256SUMS").decode(
            "utf-8", "replace"
        )
    except urllib.error.HTTPError as error:
        raise SystemExit(f"release {tag} has no SHA256SUMS ({error.code})") from error

    published = {}
    for line in sums.splitlines():
        digest, _, name = line.partition("  ")
        if name.strip():
            published[name.strip()] = digest.strip()
    if not published:
        raise SystemExit(f"SHA256SUMS for {tag} is empty")
    return published


def resolve(spec: str, triple: str, published: dict[str, str], tag: str) -> tuple[str, str]:
    """Turn ``3.12``, ``3.12.14`` or ``latest`` into ``(version, asset name)``.

    Read off the release's own file list, so "does this target have a build" is answered before the
    download rather than by a 404 half of one later — on the one leg in the matrix whose
    architecture is the reason. Python 3.10 has no ``aarch64-pc-windows-msvc`` build, which is an
    empty cell of the table and not a failure.

    Release candidates are refused rather than ranked: upstream publishes ``3.15.0rc1`` beside the
    stable lines, and a version whose channel nobody asked for should not be what ``latest`` means.
    """
    offered: dict[str, str] = {}
    for name in published:
        match = re.fullmatch(
            rf"cpython-(\d+\.\d+\.\d+)\+{tag}-{re.escape(triple)}-{VARIANT}\.tar\.gz", name
        )
        if match:
            offered[match.group(1)] = name

    if not offered:
        borrow.unavailable(
            f"python-build-standalone {tag} publishes no {triple} build at all; "
            f"{spec} is not available for this target"
        )

    if spec == "latest":
        candidates = sorted(offered, key=borrow.parts)
    else:
        prefix = borrow.parts(spec)
        candidates = sorted(
            (version for version in offered if borrow.parts(version)[: len(prefix)] == prefix),
            key=borrow.parts,
        )

    if not candidates:
        lines = sorted({".".join(version.split(".")[:2]) for version in offered}, key=borrow.parts)
        borrow.unavailable(
            f"python-build-standalone {tag} has no {spec} for {triple}. It offers "
            f"{', '.join(lines)}."
        )

    version = candidates[-1]
    return version, offered[version]


def stdlib(tree: Path) -> Path | None:
    """Where this build put the standard library — ``Lib`` on Windows, ``lib/python3.X`` on Unix.

    Read off the tree rather than out of ``sysconfig``, because everything that uses it is checking
    the interpreter *against* the archive, and asking the interpreter both questions would let one
    wrong answer confirm the other.

    **The Unix shape is tested first, and the order is the whole correctness of this.** ``Lib`` and
    ``lib`` are the same directory on a case-insensitive file system, which is what a default macOS
    install has — so a check that asks ``tree / "Lib"`` first is answered *yes* on a Mac by a
    directory holding ``libpython3.14.dylib``, and every caller then looks for the standard library
    one level above where it is. Asking for ``lib/python3.*`` cannot be answered by accident.
    """
    unix = sorted((tree / "lib").glob("python3.*"))
    if unix:
        return unix[0]
    windows = tree / "Lib"
    return windows if windows.is_dir() else None


def site_packages(tree: Path) -> Path | None:
    """Where this build put third-party packages, inside the standard library `stdlib` locates."""
    library = stdlib(tree)
    if library is None:
        return None
    packages = library / "site-packages"
    return packages if packages.is_dir() else None


def ensure_pip(tree: Path, operating_system: str) -> list[str]:
    """Give Windows a ``pip`` that can be *run*, and answer T27's open question while doing it.

    ``.claude/features/runtime-versions.md`` reserves a per-runtime post-install hook and names
    *ensure pip* as Python's. Measured, the hook is not needed, and the reason is worth writing down
    because it is the same reason on any runtime whose archive moves.

    What upstream ships on Windows is the pip *package*, in ``Lib/site-packages``, with ``Scripts/``
    holding a single ``.empty``. So ``python -m pip`` works and there is nothing named ``pip`` to put
    in a ``provides`` map. The obvious repair — run ``ensurepip`` and let it generate ``pip.exe`` —
    produces a launcher with the *absolute path of the interpreter that generated it* written inside,
    which is precisely the thing every artifact here is built to survive. Generated at build time it
    names the runner; generated by a post-install hook it names the install directory, and breaks the
    first time the user moves ``~/.mixengine`` or restores it onto another machine.

    A batch file that computes the interpreter from its own location has neither problem, cannot go
    stale, and needs nothing to run it: ``std::process::Command`` starts a ``.cmd`` through
    ``cmd.exe``, returns its exit code and escapes its arguments — measured for ``npm.cmd`` in T27's
    Node half and pinned by a test there, so this recipe is leaning on a proven mechanism rather than
    on a hope.

    If upstream ever does ship ``Scripts/pip.exe``, that is used instead and nothing is written:
    a publisher's own entry point beats one of ours, and this stops being needed without anyone
    noticing.
    """
    if operating_system != "windows":
        return []

    scripts = tree / "Scripts"
    if (scripts / "pip.exe").exists():
        print("upstream now ships Scripts/pip.exe; nothing generated")
        return []

    packages = site_packages(tree)
    if packages is None or not sorted(packages.glob("pip-*.dist-info")):
        raise SystemExit(
            "this archive has neither Scripts/pip.exe nor a pip package in site-packages, "
            "so there is nothing for a generated pip.cmd to call"
        )

    scripts.mkdir(exist_ok=True)
    # `newline=""` so the CRLF written here is the CRLF that lands: a batch file with bare LF line
    # endings is read by some cmd.exe versions as one long line.
    with (scripts / "pip.cmd").open("w", encoding="ascii", newline="") as handle:
        handle.write(PIP_CMD.replace("\n", "\r\n"))
    print("wrote Scripts/pip.cmd (upstream ships Scripts/ empty on Windows)")
    return ["Scripts/pip.cmd"]


def strip_unportable(tree: Path, operating_system: str) -> list[str]:
    """Delete the one compiled module that reaches out of the tree, and say which.

    On Linux, `_crypt` links `libcrypt.so.1` and upstream ships no copy of it. That library is not
    the C runtime: it is libxcrypt, which Debian and Ubuntu install as a base package and Fedora and
    RHEL offer as `libxcrypt-compat` and do not install by default. So the module makes the archive
    work on some glibc distributions and not others — the one property `relocate.verify` exists to
    keep an artifact from having, and the reason it is checked from a directory the build has never
    seen rather than trusted from a build log.

    The alternative to deleting it is an allowance in `verify`, and that is worse than it looks: the
    rule "an artifact does not reach outside itself" is either absolute or it is a habit, and the
    first exception is what teaches the second one to be written. Deleting one file keeps the rule.

    The file is also the cheapest thing in the whole standard library to lose. `crypt` is a wrapper
    around a Unix password hash nobody should be computing in a web project; it has been deprecated
    since 3.11 and CPython **removed it outright in 3.13**, so this makes 3.10, 3.11 and 3.12 behave
    on this one point like the versions after them, and does nothing at all from 3.13 on because
    there is no such file to delete. What a caller sees is `ModuleNotFoundError: No module named
    '_crypt'` on every machine, instead of a working import on Ubuntu and that same error on Fedora.

    Nothing in the standard library imports it on the way to anything else — `crypt.py`, which does,
    is left in place so the failure names the module that is gone rather than a missing attribute.
    """
    if operating_system != "linux":
        return []
    removed = []
    for module in sorted((tree / "lib").glob("python3.*/lib-dynload/_crypt.*.so")):
        removed.append(module.relative_to(tree).as_posix())
        module.unlink()
    for path in removed:
        print(f"removed {path} (needs a libxcrypt this archive does not carry)")
    return removed


def prune(tree: Path, operating_system: str) -> list[str]:
    """Take the graphical toolkit out, on every cell, and prove none of it is left.

    **This is the one decision this recipe makes rather than inherits**, and the argument for it is
    not that tkinter is large. It is that upstream ships a *different* tkinter to each half of the
    table: Windows gets Tk 8.6 with Tix 8.4.3 beside it, Unix gets Tk 9.0 with itcl and the Tcl
    Thread package, and on 3.14 the Windows DLL turns into Tcl 9 while keeping neither of the other
    two. So ``python 3.13.15`` already means one toolkit on one machine and another toolkit on the
    next, which is exactly what one version is not allowed to mean.

    Dropped rather than levelled up, because the alternative is to make six cells agree on a Tk
    nobody asked them for. Nothing MixEngine runs is a desktop application; a local web development
    environment serves HTTP, and the two things in the standard library that need a display —
    tkinter and the editor written in it — are the ones a runtime aimed at that can most obviously
    do without. It is also the largest single thing in the Windows cell after the interpreter,
    11.7 MB of 59.8, and 19.6% of an archive that is supposed to carry no more than is needed.

    What goes with it: `libs/_tkinter.lib` and every other per-extension import library on Windows
    — 31 of them on 3.10, one per built-in module, which exist to link a module into a Python being
    compiled and not to compile anything against one — and `share/man` on Unix, one manual page for
    an interpreter whose Windows twin has never had one and which nothing in the tree reads.

    Keep-lists where the surplus is versioned in its own name (`lib/` on Unix, `libs/` on Windows)
    and names where it is not, and then **a sweep that fails the pack if any part of any path still
    matches `TOOLKIT`**. The sweep is what makes this a rule rather than a list: 3.14 renamed
    `tcl86t.dll` and `tk86t.dll` to `tcl90.dll` and `tcl9tk90.dll` and moved the whole Tcl script
    library inside the DLL, and a recipe written against 3.13 would have shipped both and said
    nothing — which is the `php_gd2.dll` failure from P2, one runtime over.
    """
    library = stdlib(tree)
    if library is None:
        raise SystemExit(
            f"this archive has no standard library directory to take tkinter out of; its root is "
            f"{sorted(path.name for path in tree.iterdir())}"
        )

    removed: list[str] = []
    freed = 0

    def discard(path: Path) -> None:
        nonlocal freed
        # `lexists` for the mysql_ldb reason `borrow.declare` gives: a dangling symlink is still a
        # file in the archive, and `exists` follows the link and answers no.
        if not os.path.lexists(path):
            return
        if path.is_dir() and not path.is_symlink():
            freed += sum(child.stat().st_size for child in path.rglob("*") if child.is_file())
            shutil.rmtree(path)
        else:
            freed += path.stat().st_size if not path.is_symlink() else 0
            path.unlink()
        removed.append(path.relative_to(tree).as_posix())

    for name in TKINTER_STDLIB:
        discard(library / name)
    for cached in sorted((library / "__pycache__").glob("turtle.*")):
        discard(cached)
    for module in sorted((library / "lib-dynload").glob("_tkinter*")):
        discard(module)

    if operating_system == "windows":
        discard(tree / "tcl")
        for path in sorted((tree / "DLLs").iterdir()):
            if TOOLKIT.match(path.name):
                discard(path)
        for path in sorted((tree / "libs").iterdir()):
            if not any(fnmatch(path.name, pattern) for pattern in WINDOWS_IMPORT_KEEP):
                discard(path)
    else:
        for path in sorted((tree / "lib").iterdir()):
            if not any(fnmatch(path.name, pattern) for pattern in UNIX_LIB_KEEP):
                discard(path)
        for launcher in sorted((tree / "bin").glob("idle*")):
            discard(launcher)
        discard(tree / "share" / "man")

    survivors = sorted(
        relative for relative in (path.relative_to(tree).as_posix() for path in tree.rglob("*"))
        if any(TOOLKIT.match(part) for part in relative.split("/"))
        and not relative.startswith(NOT_TOOLKIT)
    )
    if survivors:
        raise SystemExit(
            f"{len(survivors)} path(s) of the toolkit survived the prune, starting with "
            f"{', '.join(survivors[:5])} — upstream has moved something this does not name"
        )

    print(f"dropped {', '.join(removed)} ({freed:,} bytes)")
    return removed


def keeps(tree: Path, operating_system: str) -> dict[str, str]:
    """The paths kept here that the rule's second half throws out everywhere else, and why.

    *"No more than is needed"* is a claim about need, so it has to be answered per runtime rather
    than per file type, and CPython answers it the opposite way to PHP. P2 deleted `dev/php8.lib`
    from the Windows PHP archives — *"892 KB of import library in a runtime that is not an SDK"* —
    and that was right, because a PHP extension reaches a developer's machine as a DLL somebody
    else compiled. A Python extension does not. ``pip install`` of any source distribution without
    a matching wheel compiles C on the machine doing the installing, against ``Python.h`` and, on
    Windows, linked to ``python3XX.lib``. Take those out and the runtime still starts, still passes
    every check in `smoke`, and fails the first ``pip install`` that has no wheel for this platform.

    So the headers stay on all six cells and the import libraries stay on the two that have a
    linker needing them — the same decision, spelled the way each toolchain spells it, in the way
    P2's static-versus-shared extensions are one feature set spelled twice. The asymmetry is real
    and is not a defect: an ELF or Mach-O extension leaves Python's symbols undefined and resolves
    them from whatever interpreter loaded it, so there is nothing on Unix for ``libs/`` to be.

    Returned as a mapping and written to the manifest, because P6's within-artifact check is going
    to refuse an `include/` it was not told about, and the thing that tells it should also say why.
    """
    reasons = {
        "include": "the CPython C API headers. `pip install` of a source distribution compiles a "
                   "C extension against these on the machine doing the installing, and there is "
                   "nowhere else to fetch a set that matches this interpreter.",
    }
    if operating_system == "windows":
        libraries = sorted(path.name for path in (tree / "libs").iterdir())
        reasons["libs"] = (
            f"{', '.join(libraries)} — the import libraries MSVC has to link a C extension "
            f"against. There is no Unix counterpart because an ELF or Mach-O extension leaves "
            f"Python's symbols undefined and resolves them from the interpreter that loads it."
        )
    return reasons


def describe(
    tree: Path, version: str, target: tuple[str, str], url: str, digest: str, tag: str,
    added: list[str], removed: list[str],
) -> dict:
    """What is in the archive, as the daemon will read it."""
    operating_system, arch = target
    layout = LAYOUT["windows" if operating_system == "windows" else "unix"]

    provides = {name: path for name, path in layout.items() if (tree / path).exists()}
    missing = [name for name in REQUIRED if name not in provides]
    if missing:
        raise SystemExit(
            f"the archive provides no {', '.join(missing)} — expected at "
            f"{', '.join(layout[name] for name in missing)}. Contents: "
            f"{sorted(path.name for path in tree.iterdir())[:20]}"
        )

    manifest = {
        "schema": 1,
        "kind": "python",
        "version": version,
        "os": operating_system,
        "arch": arch,
        "source": "borrowed",
        "upstream": {
            "project": "astral-sh/python-build-standalone",
            "release": tag,
            "variant": VARIANT,
            "url": url,
            "sha256": digest,
            "verified_against": "the release's own SHA256SUMS over HTTPS to the publisher",
        },
        "provides": provides,
    }
    # Named rather than implied: this archive is not byte-for-byte what upstream published, and
    # somebody comparing hashes a year from now should find the difference written down here instead
    # of deducing it. `borrow.declare` also checks the claim against the tree before writing it —
    # this recipe is the one that had the fields first, and it is not the one that gets to keep its
    # own copy of what they mean. `keeps` is the difference from the *rule* rather than from the
    # publisher, and is stated for the same reason.
    return borrow.declare(tree, manifest, added, removed, keeps(tree, operating_system))


PROBE = """
import json, os, sys, sysconfig
import ssl, hashlib, sqlite3, urllib.request

paths = ssl.get_default_verify_paths()

# The handshake is the claim; the counts below are only the diagnosis when it fails. See `smoke`.
try:
    with urllib.request.urlopen("https://github.com/", timeout=30) as answer:
        handshake = answer.status
except Exception as error:
    handshake = f"{type(error).__name__}: {error}"

# Asked of the interpreter rather than of the directory listing. `borrow.declare` already checks
# that every path named in `upstream.removed` is gone; what this checks is the other direction —
# that nothing left behind can still reach a toolkit, which is a question only an import answers.
try:
    import tkinter
    tkinter_left = tkinter.__file__
except ImportError as error:
    tkinter_left = None

report = {
    "version": ".".join(str(part) for part in sys.version_info[:3]),
    "executable": sys.executable,
    "prefix": sys.prefix,
    "openssl": ssl.OPENSSL_VERSION,
    "sha256": hashlib.sha256(b"mixengine").hexdigest(),
    "sqlite": sqlite3.connect(":memory:").execute("select sqlite_version()").fetchone()[0],
    "trusted": ssl.create_default_context().cert_store_stats()["x509_ca"],
    "handshake": handshake,
    "tkinter_left": tkinter_left,
    "headers": os.path.join(sysconfig.get_paths()["include"], "Python.h"),
    "trusts": {
        "cafile": paths.openssl_cafile,
        "capath": paths.openssl_capath,
        "cafile_exists": bool(paths.openssl_cafile) and os.path.exists(paths.openssl_cafile),
        "capath_exists": bool(paths.openssl_capath) and os.path.isdir(paths.openssl_capath),
    },
    "site": sysconfig.get_paths()["purelib"],
}
print(json.dumps(report))
"""


def trust(trusts: dict) -> str:
    """Where the authorities came from, in a few words, for the line the recipe prints."""
    if trusts["cafile_exists"]:
        return trusts["cafile"]
    if trusts["capath_exists"]:
        return f"{trusts['capath']}/ (a hash directory, read one certificate at a time)"
    return "this operating system's own store"


def smoke(tree: Path, version: str, manifest: dict) -> dict:
    """Run the artifact from somewhere it has never been, and make it be itself while doing it.

    ``python --version`` alone would pass on an archive that is unusable, three ways over: the
    runner's own Python could be answering, an interpreter that found the *runner's* standard
    library would answer the same, and a build whose compiled modules did not survive being moved
    answers it perfectly right up to the first ``import ssl``. So six things are proven.

    *It is this Python.* ``sys.executable`` has to be inside the relocated tree.

    *It found its own standard library after moving.* ``sys.prefix`` has to be inside it too — this
    is the whole relocation claim for a runtime that computes its ``prefix`` from ``argv[0]`` at
    every start.

    *Its compiled modules load, and two of them are called rather than reported*: OpenSSL hashes a
    string and SQLite answers a query, where reading ``ssl.OPENSSL_VERSION`` alone exercises a
    string constant that a broken build states just as confidently.

    *It can verify a certificate* — and this is proven by **verifying one**, over a real connection
    to a real host, rather than by counting what the default context has loaded. Counting answers a
    different question, and answers it wrongly on exactly the platform where the trust store is a
    directory: a Unix ``capath`` is a hash directory that OpenSSL reads one certificate at a time,
    at verification, so ``cert_store_stats()["x509_ca"]`` is **0** on a perfectly working Linux and
    26 on Windows, which loads its store eagerly. The first version of this check asserted the count
    and refused two archives that verify fine. What is at stake is worth a network call: a Python
    that starts, imports ``ssl`` and then fails every ``pip install`` with a handshake error is the
    failure furthest from its cause in this whole table. The host is the one this run has already
    downloaded from, so an unreachable network has failed the recipe long before here.

    *It is this pip.* The version ``pip`` reports has to match the ``pip-*.dist-info`` in the
    ``site-packages`` that came in the same archive, which is a fact no other Python on the machine
    can produce.

    *What `prune` took out is gone, and what `keeps` kept is reachable.* ``import tkinter`` has to
    fail, and ``Python.h`` has to be where this interpreter says its headers are. Both are the same
    check in opposite directions, and both are the direction the file system cannot answer:
    `borrow.declare` proves a named path is absent or present, while only the interpreter can say
    that nothing left behind still reaches a toolkit — a frozen module, a stale ``.pyc``, a second
    copy under another name — or that the headers a compiling ``pip install`` will look for are the
    ones this archive carries rather than a set left on the machine by something else.
    """
    elsewhere = borrow.moved(tree)

    if sys.platform != "win32":
        problems = relocate.verify(elsewhere)
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        if problems:
            raise SystemExit("the relocated tree reaches outside itself")

    python = elsewhere / manifest["provides"]["python"]
    path = borrow.clean_path(python.parent)
    drop = ("PYTHON",)      # PYTHONHOME and PYTHONPATH both make this test lie if they survive

    banner = borrow.run(python, "--version", path=path, drop=drop)
    if banner.split()[-1] != version:
        raise SystemExit(f"python reports {banner!r}, expected {version}")

    for module in MODULES:
        borrow.run(python, "-c", f"import {module}", path=path, drop=drop)

    report = json.loads(borrow.run(python, "-c", PROBE, path=path, drop=drop))

    if report["version"] != version:
        raise SystemExit(f"sys.version_info says {report['version']}, expected {version}")
    # The `keeps` claim, checked the way the interpreter will meet it. `borrow.declare` proves the
    # directory is in the tree; this proves the interpreter looks for headers *inside* the archive
    # and finds `Python.h` where it looked, which is the whole of what a compiling `pip install`
    # does before it opens a compiler.
    headers = Path(report["headers"])
    if not headers.is_file():
        raise SystemExit(
            f"this Python says its headers are at {headers}, and there is no Python.h there — "
            f"every `pip install` of a source distribution with a C extension would stop on it"
        )
    for field in ("executable", "prefix", "site", "headers"):
        where = Path(report[field])
        if not where.resolve().is_relative_to(elsewhere.resolve()):
            raise SystemExit(
                f"this interpreter reports {field} as {where}, which is not inside the tree it was "
                f"copied to — it is answering from somewhere else on this machine"
            )
    if len(report["sha256"]) != 64:
        raise SystemExit(f"the bundled OpenSSL answered {report['sha256']!r}")
    if report["handshake"] != 200:
        raise SystemExit(
            f"this Python could not verify a real certificate chain: {report['handshake']}. "
            f"Its default trust store is {report['trusts']}, and every `pip install` made with it "
            f"would fail the same way"
        )
    if report["tkinter_left"] is not None:
        raise SystemExit(
            f"`import tkinter` still works, from {report['tkinter_left']} — this cell would offer "
            f"a toolkit the other five do not"
        )

    print(f"python {report['version']}, {report['openssl']}, sqlite {report['sqlite']}, "
          f"verified a live chain against {trust(report['trusts'])} "
          f"({report['trusted']} authorities loaded eagerly)")
    ran = ["python --version", "import " + ", ".join(MODULES),
           "python -c (execPath, ssl, sqlite3, verified https://github.com, tkinter gone, Python.h)"]

    packages = site_packages(elsewhere)
    for name in sorted(set(manifest["provides"]) - {"python"}):
        reported = borrow.run(
            elsewhere / manifest["provides"][name], "--version", path=path, drop=drop
        )
        # `pip 26.2.1 from ...` — the version is the second word, and the rest of the line is the
        # path it is running from, which the check below is about to make its own assertion about.
        installed = sorted(packages.glob(f"{name}-*.dist-info")) if packages else []
        if installed:
            expected = installed[0].name[len(name) + 1: -len(".dist-info")]
            if expected not in reported.split():
                raise SystemExit(
                    f"{name} reports {reported!r}, but the {name} inside this archive is "
                    f"{expected} — something else on this machine answered"
                )
        # `.resolve()` on both sides, not just the tree's: macOS puts a temporary directory under
        # `/var`, which is a symlink to `/private/var`, and `pip` reports whichever spelling it was
        # started through. Comparing one resolved path against one unresolved one fails there and
        # nowhere else.
        running = Path(reported.split(" from ")[-1].split(" (")[0]).resolve()
        if not running.is_relative_to(elsewhere.resolve()):
            raise SystemExit(f"{name} is running from outside the tree: {reported}")
        print(f"{manifest['provides'][name]}: {reported}")
        ran.append(f"{manifest['provides'][name]} --version")

    borrow.discard(elsewhere)
    return {"relocated": True, "ran": ran, "openssl": report["openssl"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version", required=True,
        help="exact version (3.12.14), a line (3.12) for its newest release, or 'latest'",
    )
    parser.add_argument(
        "--release",
        help="a python-build-standalone release tag (a date, 20260814). Default: the newest. "
             "Pin it to reproduce an older build of the same CPython version.",
    )
    parser.add_argument("--out", default="dist", type=Path)
    args = parser.parse_args()

    target = borrow.host("Python")
    triple = TARGETS[target]

    tag = args.release or release_tag()
    published = catalogue(tag)
    version, asset = resolve(args.version, triple, published, tag)
    print(f"python-build-standalone {tag}: {args.version} resolves to {version} ({triple})")

    url = f"{REPOSITORY}/releases/download/{tag}/{asset.replace('+', '%2B')}"
    work = Path(tempfile.mkdtemp(prefix="mixengine-python-"))
    downloaded = work / asset
    print(f"borrowing {url}")
    try:
        urllib.request.urlretrieve(url, downloaded)
    except urllib.error.HTTPError as error:
        raise SystemExit(f"{url} answered {error.code}") from error

    actual = borrow.sha256(downloaded)
    if actual != published[asset]:
        raise SystemExit(f"sha256 mismatch: got {actual}, SHA256SUMS says {published[asset]}")
    print(f"sha256 {actual} (verified against SHA256SUMS)")

    tree = borrow.unpack(downloaded, work / "unpacked", "tar.gz")

    added = ensure_pip(tree, target[0])
    # Pruned before it is described and before it is proven, in that order: the manifest has to
    # describe the tree that ships, and the smoke test is what stands between a keep-list and a
    # runtime that was quietly cut in half.
    removed = prune(tree, target[0]) + strip_unportable(tree, target[0])
    manifest = describe(tree, version, target, url, actual, tag, added, removed)
    manifest["smoke"] = smoke(tree, version, manifest)

    # Measured off the archive rather than assumed from the runner. Upstream chose these floors when
    # it chose its build image, which is exactly why they are read rather than written down.
    measured = relocate.floor(tree) if sys.platform != "win32" else None
    if measured:
        manifest["requires"] = {measured[0]: measured[1]}
        print(f"needs {measured[0]} {measured[1]} or newer")

    borrow.publish(tree, manifest, args.out, "zip" if target[0] == "windows" else "tar.zst")
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
