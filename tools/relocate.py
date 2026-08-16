#!/usr/bin/env python3
"""Make a freshly built tree carry the libraries it links against, and prove that it does.

A build produced against a distribution's packages is not an artifact. It links `/usr/lib64/libssl.so.1.1`
or `/opt/homebrew/opt/icu4c@78/lib/libicui18n.78.dylib` by absolute path, and on the machine that
installs it those files are a different version, or absent. So everything the build depends on that
is not part of the operating system is copied in beside it and every reference is rewritten to point
at the copy, relative to the binary doing the loading.

This knows nothing about PHP. Every remaining "we build" cell in MixEngine's runtime table — nginx,
Ruby, PostgreSQL, Redis — has exactly this problem, and the answer is the same shape for all of them.

**Two things it deliberately does not treat as bundleable.**

*The C runtime.* `libc`, `libm`, `libpthread`, the dynamic loader itself — bundling those does not
produce a portable binary, it produces one that loads two C libraries into one process. They stay
external, and what that costs is a floor: the oldest glibc the result will start on. So the floor is
**measured** here rather than assumed, and the caller puts the number in the manifest.

*`libstdc++` and `libgcc_s`.* Both are forward compatible — a newer one runs code built against an
older one — so any system new enough to satisfy the glibc floor already satisfies these. This is the
same allowance `manylinux` makes, for the same reason.

On macOS the equivalent line is `/usr/lib` and `/System`, which is not a convention but a rule: those
are the only libraries Apple guarantees, they are not shipped as files any more (they live in the
dyld shared cache), and there is nothing there to copy even if it were wanted.

**Rewriting a Mach-O invalidates its signature**, and on Apple Silicon an unsigned Mach-O does not
load at all — it is killed by the kernel rather than diagnosed by the linker. So every file this
touches is re-signed ad-hoc afterwards. Missing that step produces a build that works on the Intel
runner and dies on arm64 with "Killed: 9" and no explanation.

Three more Mach-O traps, and the reason `verify` runs from a directory the build has never named,
are written up in ``docs/building-from-source.md``. Each of them cost a round of CI, and each
produced an archive that passed every check made in place.
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
from collections import deque
from collections.abc import Sequence
from pathlib import Path

# Left alone on Linux, by soname. The C runtime and the loader for the reason in the module
# docstring; libstdc++/libgcc_s because they are forward compatible and the glibc floor already
# implies them.
SYSTEM_SONAMES = {
    "libc.so.6", "libm.so.6", "libpthread.so.0", "libdl.so.2", "librt.so.1", "libutil.so.1",
    "libresolv.so.2", "libnsl.so.1", "libanl.so.1", "libstdc++.so.6", "libgcc_s.so.1",
    "linux-vdso.so.1", "linux-gate.so.1", "ld-linux-x86-64.so.2", "ld-linux-aarch64.so.1",
}

# Left alone on macOS, by location. Everything Apple ships lives here and nowhere else.
SYSTEM_PREFIXES = ("/usr/lib/", "/System/")

# Where machine code is looked for inside a tree. `lib` is included because a bundled library has
# dependencies of its own, and they have to be followed as well.
BINARY_DIRECTORIES = ("bin", "sbin", "ext", "lib", "libexec", "modules")

# Left alone on Windows, by location, and the location is the only rule that works: Windows has no
# soname, no `/System`, and a DLL is named by a bare file name with no path in it at all.
WINDOWS_ROOT = Path(os.environ.get("SystemRoot", r"C:\Windows"))
SYSTEM_DIRECTORIES = (WINDOWS_ROOT / "System32", WINDOWS_ROOT / "SysWOW64", WINDOWS_ROOT)

# **Named in import tables and not files.** `api-ms-win-core-*.dll` and `ext-ms-*.dll` are API sets:
# the loader resolves them from a schema it carries, and there is nothing on disk to copy. Any tool
# that reports a path for one has found a file that merely happens to be on `PATH` — `cygcheck`
# answered a Java toolcache for twenty of them in one run — so a match here is never bundled.
API_SET_PREFIXES = ("api-ms-win-", "ext-ms-")

ELF_MAGIC = b"\x7fELF"
PE_MAGIC = b"MZ"
MACHO_LITTLE = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe"}    # 64- and 32-bit, little endian
MACHO_BIG = {b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce"}
MACHO_FAT = {b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}       # universal
MACHO_MAGICS = MACHO_LITTLE | MACHO_BIG | MACHO_FAT

# What the loader will actually be asked to load. A file can carry the right magic number and never
# be loaded by anything, and both shapes of that turned up in one Ruby tree: a relocatable object
# left behind in a gem's build directory (`debug.o`), and the debug companion inside a `.dSYM`
# bundle. Neither has a search path worth rewriting, and both *refuse* the tool that would rewrite
# one — `ldd` answers "not a dynamic executable" and `install_name_tool` answers "string table not
# at the end of the file". Reading the type out of the header is the difference between a rule and
# a list of exceptions; the magic number alone was never the question being asked.
ELF_TYPES = {2, 3}              # ET_EXEC, ET_DYN
MACHO_TYPES = {2, 6, 8}         # MH_EXECUTE, MH_DYLIB, MH_BUNDLE


class Unbundleable(SystemExit):
    """Raised where continuing would publish an archive that cannot work on another machine."""


def run(*command: str, check: bool = True, environment: dict[str, str] | None = None) -> str:
    result = subprocess.run(command, text=True, capture_output=True, timeout=600, env=environment)
    if check and result.returncode != 0:
        raise Unbundleable(f"{' '.join(command)} exited {result.returncode}\n{result.stderr}")
    return result.stdout


def kind(path: Path) -> str | None:
    """``elf``, ``macho``, ``pe`` or None, decided by the file's own first bytes.

    Asking ``file(1)`` would mean depending on it being installed and on its wording; the magic
    number is the thing itself.

    ``MZ`` is the weakest of the three — it is a DOS header, and everything from a 1983 ``.com``
    stub to a ``.scr`` carries it — so it is only a *candidate* here and :func:`loadable` is what
    confirms a PE signature behind it.
    """
    try:
        with path.open("rb") as handle:
            magic = handle.read(4)
    except OSError:
        return None
    if magic == ELF_MAGIC:
        return "elf"
    if magic in MACHO_MAGICS:
        return "macho"
    if magic[:2] == PE_MAGIC:
        return "pe"
    return None


def pe_signature(handle) -> int | None:
    """Offset of the ``PE\\0\\0`` signature in *handle*, or None if there is not one.

    The DOS header at 0x3C points at it. A file that has ``MZ`` and nothing there is a DOS
    executable, an installer stub or a resource-only file, and none of those is something the
    Windows loader resolves imports for.
    """
    try:
        handle.seek(0)
        if handle.read(2) != PE_MAGIC:
            return None
        handle.seek(0x3C)
        raw = handle.read(4)
        if len(raw) < 4:
            return None
        offset = int.from_bytes(raw, "little")
        handle.seek(offset)
        return offset if handle.read(4) == b"PE\0\0" else None
    except OSError:
        return None


def loadable(path: Path) -> bool:
    """Whether this file is one the loader loads, read off its header rather than its magic.

    See ELF_TYPES and MACHO_TYPES for what that excludes and why. A universal binary is taken as
    loadable without looking further: its header is a table of architectures rather than a Mach-O
    header, and nothing in this table ships one. That last clause was a fact about the archives
    when it was written and is now a fact this repository keeps: EDB's macOS build *is* universal,
    and `postgres.thin` reduces it to the architecture of the cell being packed before anything
    here reads it — because `otool` answers for every architecture in a fat file, and `verify` and
    `floor` would otherwise be measuring two machines and reporting the stricter of them.
    """
    try:
        with path.open("rb") as handle:
            header = handle.read(20)
            if header[:2] == PE_MAGIC:
                return pe_signature(handle) is not None
    except OSError:
        return False
    if len(header) < 18:
        return False
    if header[:4] == ELF_MAGIC:
        order = "little" if header[5] == 1 else "big"
        return int.from_bytes(header[16:18], order) in ELF_TYPES
    if header[:4] in MACHO_FAT:
        return True
    order = "little" if header[:4] in MACHO_LITTLE else "big"
    return int.from_bytes(header[12:16], order) in MACHO_TYPES


def machine_files(tree: Path, directories: Sequence[str] = BINARY_DIRECTORIES) -> list[Path]:
    """Every ELF or Mach-O in the tree the loader will load, in a stable order, symlinks skipped.

    *directories* is where to look, and a tree whose payload sits at its own root has to say so —
    ``machine_files(tree, ("",))``. The default finds nothing in such a tree, which would make
    `verify` return no problems and `floor` return no floor, both of them for the reason that
    neither looked: a check that passes by asking nothing is the failure this argument exists to
    prevent. Caddy is the first archive here shaped that way and the reason it is an argument.
    """
    found = []
    for directory in directories:
        root = tree / directory if directory else tree
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            if kind(path) and loadable(path) and path not in found:
                found.append(path)
    return found


# --------------------------------------------------------------------------------------- ELF ---


def elf_dependencies(path: Path, search: Sequence[Path] = ()) -> list[tuple[str, Path | None]]:
    """``(soname, resolved path)`` for each library *path* needs, unresolved ones as ``None``.

    ``ldd`` is used rather than ``objdump -p`` because the resolution is the point: after the rewrite
    the same call, run from the moved tree, is what proves the rewrite worked.

    *search* is what the process will have on its library path that this file does not carry itself
    — see `loader_search`. It is passed as ``LD_LIBRARY_PATH``, which is not quite where the loader
    would find those directories, but the only thing that changes is *which* in-tree copy answers a
    name that is in the tree twice, and there is no such name.
    """
    environment = None
    if search:
        environment = dict(os.environ)
        environment["LD_LIBRARY_PATH"] = os.pathsep.join(str(directory) for directory in search)
    output = run("ldd", str(path), check=False, environment=environment)
    dependencies = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        # `ldd` answers in prose when the file is not something it can resolve for — a statically
        # linked executable, or anything `machine_files` should already have filtered out. Reading
        # that as a library name produces "X needs not a dynamic executable", which is a sentence
        # this repository printed once and should not be able to print again.
        if line in ("not a dynamic executable", "statically linked"):
            return []
        if "=>" in line:
            soname, _, target = line.partition("=>")
            soname = soname.strip()
            target = target.strip()
            if target.startswith("not found"):
                dependencies.append((soname, None))
                continue
            target = re.sub(r"\s*\(0x[0-9a-f]+\)$", "", target)
            dependencies.append((soname, Path(target) if target else None))
        else:
            # The loader itself and the vDSO, printed without an arrow. Both are system by
            # definition and named here only so `is_system` sees them.
            name = re.sub(r"\s*\(0x[0-9a-f]+\)$", "", line)
            dependencies.append((Path(name).name, Path(name) if name.startswith("/") else None))
    return dependencies


def elf_set_rpath(path: Path, rpath: str) -> None:
    run("patchelf", "--set-rpath", rpath, str(path))


def elf_rpaths(path: Path) -> list[str]:
    """The ``DT_RPATH`` and ``DT_RUNPATH`` entries written in *path*, as spelled.

    ``objdump -p`` rather than ``patchelf --print-rpath``, which prints one of the two and says
    nothing about which. Both matter here, and the difference between them is the whole point of
    `loader_search`.
    """
    output = run("objdump", "-p", str(path), check=False)
    entries = []
    for line in output.splitlines():
        words = line.split()
        if len(words) == 2 and words[0] in ("RPATH", "RUNPATH"):
            entries.append(words[1])
    return entries


def loader_search(tree: Path) -> list[Path]:
    """The in-tree directories the loader will search for **everything** this tree loads.

    A plugin does not have to carry a search path of its own, and CPython's compiled modules do not:
    `_tkinter.cpython-312-x86_64-linux-gnu.so` needs `libtcl9.0.so`, has neither ``DT_RPATH`` nor
    ``DT_RUNPATH``, and the library sits in the tree's own `lib/` where nothing in that file points.
    It still loads, because the *interpreter* that `dlopen`s it carries ``DT_RPATH
    $ORIGIN/../lib``, and glibc searches the ``DT_RPATH`` of the whole chain that led to a load —
    the main executable included — not only the object being resolved.

    So `ldd` on such a module alone asks a question the loader never asks, and answers "not found"
    about a library that is right there. Reading the executables' own ``DT_RPATH`` is how that gets
    corrected without assuming a layout: a tree that arranges itself some other way says so in its
    binaries, and one that arranges itself the way `bundle` does names `$ORIGIN` and is unaffected.

    ``DT_RUNPATH`` is deliberately read here as well even though the loader does *not* inherit it,
    because the only thing this list is used for is `verify`, and a `verify` that resolved *less*
    than the loader would reject archives that work.
    """
    binaries = tree / "bin"
    if sys.platform in ("darwin", "win32") or not binaries.is_dir():
        return []
    found: list[Path] = []
    for path in sorted(binaries.iterdir()):
        if path.is_symlink() or not path.is_file() or kind(path) != "elf":
            continue
        for entry in elf_rpaths(path):
            for element in entry.split(":"):
                expanded = Path(os.path.normpath(
                    element.replace("${ORIGIN}", str(path.parent)).replace("$ORIGIN", str(path.parent))
                ))
                if expanded.is_dir() and inside(expanded, tree) and expanded not in found:
                    found.append(expanded)
    return found


# ------------------------------------------------------------------------------------ Mach-O ---


def macho_id(path: Path) -> str | None:
    output = run("otool", "-D", str(path), check=False).splitlines()
    return output[1].strip() if len(output) > 1 and output[1].strip() else None


def macho_rpaths(path: Path) -> list[str]:
    output = run("otool", "-l", str(path), check=False)
    rpaths, pending = [], False
    for line in output.splitlines():
        line = line.strip()
        if line.endswith("cmd LC_RPATH"):
            pending = True
        elif pending and line.startswith("path "):
            rpaths.append(line[len("path "):].split(" (offset")[0].strip())
            pending = False
    return rpaths


def macho_dependencies(path: Path, executable_dir: Path) -> list[tuple[str, Path | None]]:
    """``(install name as written, resolved path)`` for each dylib *path* loads.

    The spelling matters as much as the target: ``install_name_tool -change`` matches the string
    that is actually in the load command, so a resolved path alone cannot rewrite anything.
    """
    identity = macho_id(path)
    rpaths = macho_rpaths(path)
    dependencies = []
    for line in run("otool", "-L", str(path), check=False).splitlines()[1:]:
        spelling = line.strip().split(" (compatibility")[0].strip()
        if not spelling or spelling == identity:
            continue
        dependencies.append((spelling, macho_resolve(spelling, path, rpaths, executable_dir)))
    return dependencies


def macho_resolve(
    spelling: str, referrer: Path, rpaths: list[str], executable_dir: Path
) -> Path | None:
    candidates = []
    if spelling.startswith("@loader_path/"):
        candidates.append(referrer.parent / spelling[len("@loader_path/"):])
    elif spelling.startswith("@executable_path/"):
        candidates.append(executable_dir / spelling[len("@executable_path/"):])
    elif spelling.startswith("@rpath/"):
        tail = spelling[len("@rpath/"):]
        for rpath in rpaths:
            base = rpath.replace("@loader_path", str(referrer.parent))
            base = base.replace("@executable_path", str(executable_dir))
            candidates.append(Path(base) / tail)
    else:
        candidates.append(Path(spelling))
    for candidate in candidates:
        resolved = Path(os.path.normpath(candidate))
        if resolved.exists():
            return resolved
    return None


def macho_sign(path: Path) -> None:
    """Re-sign ad-hoc. See the module docstring: on arm64 this is not optional."""
    run("codesign", "--force", "--sign", "-", "--timestamp=none", str(path))


def absolutise(libdir: Path) -> list[Path]:
    """Give the dylibs in *libdir* install names dyld can actually resolve.

    A build system that predates ``@rpath`` may set a library's install name to its bare file name,
    with no directory at all — ICU's Darwin makefile is one, and it is not the only one. Such a
    library links perfectly: ``-L`` tells the *linker* where it is. It then cannot be loaded, because
    the bare name is copied into everything that links it and dyld has nowhere to look.

    That failure is invisible in the worst way. `configure` link probes keep passing while every
    *run* probe fails to launch, so autoconf writes "this platform cannot do that" for feature after
    feature and stops on whichever one it cannot live without. What was actually wrong is that a
    library installed twenty minutes earlier is unloadable.

    So each install name here is made absolute, along with every reference between them. That is
    only a build-time arrangement: whatever ends up in the artifact is rewritten again by `bundle`,
    which cannot see these libraries until they are loadable in the first place.
    """
    if sys.platform != "darwin":
        return []
    repaired = []
    for path in sorted(libdir.glob("*.dylib")):
        if path.is_symlink() or kind(path) != "macho":
            continue
        identity = macho_id(path)
        changed = False
        if identity and "/" not in identity:
            run("install_name_tool", "-id", str(libdir / identity), str(path))
            changed = True
        for spelling, _ in macho_dependencies(path, libdir):
            if "/" in spelling or not (libdir / spelling).exists():
                continue
            run("install_name_tool", "-change", spelling, str(libdir / spelling), str(path))
            changed = True
        if changed:
            macho_sign(path)
            repaired.append(path)
    return repaired


# ---------------------------------------------------------------------------------------- PE ---
#
# **Read out of the file rather than asked of a tool, and that is the difference that matters.**
# `ldd` and `otool` are on every machine that could run the recipes that need them. The Windows
# equivalent is `cygcheck`, which exists only where Cygwin is installed — that is, only on the build
# machine — and it answers with what *this* machine's `PATH` happens to offer rather than with what
# the file requires. A verify that consulted it would be the "build machine is the one machine where
# a broken artifact works" failure in its purest form: a run that bundled nothing at all reported
# success, because the loader found cygwin1.dll in the Cygwin installation that was still on `PATH`.
#
# So the import table is parsed here. It is about sixty lines of offsets and it depends on nothing.


def pe_imports(path: Path) -> list[str]:
    """Every DLL named in *path*'s import and delay-load import directories.

    PE names a dependency by bare file name — no path, no version, no soname. All the meaning is in
    how the loader then searches, and :func:`pe_resolve` is that half.

    Both directories are read. A delay-load import is not resolved until the first call through it,
    so a missing one does not fail the load; it crashes the program later, somewhere unrelated to
    the library that was actually absent, which is a worse failure than the one this file exists to
    prevent. Only the modern RVA form is understood — the pre-2002 linkers wrote absolute addresses
    there and set no flag for it, and nothing in this table is that old.
    """
    with path.open("rb") as handle:
        offset = pe_signature(handle)
        if offset is None:
            return []
        handle.seek(0)
        data = handle.read()

    coff = offset + 4
    sections_count = int.from_bytes(data[coff + 2:coff + 4], "little")
    optional_size = int.from_bytes(data[coff + 16:coff + 18], "little")
    optional = coff + 20
    magic = int.from_bytes(data[optional:optional + 2], "little")
    # The only thing the two shapes disagree about here is where the data directories start: PE32
    # carries a `BaseOfData` field and a 32-bit `ImageBase`, PE32+ carries neither and a 64-bit one.
    directories = optional + {0x10B: 96, 0x20B: 112}.get(magic, 0)
    if directories == optional:
        return []

    sections = []
    for index in range(sections_count):
        entry = optional + optional_size + index * 40
        sections.append((
            int.from_bytes(data[entry + 12:entry + 16], "little"),   # VirtualAddress
            int.from_bytes(data[entry + 16:entry + 20], "little"),   # SizeOfRawData
            int.from_bytes(data[entry + 20:entry + 24], "little"),   # PointerToRawData
        ))

    def offset_of(rva: int) -> int | None:
        """An address as the loader sees it, turned into a position in the file on disk."""
        for virtual, size, raw in sections:
            if virtual <= rva < virtual + size:
                return raw + (rva - virtual)
        return None

    def string_at(rva: int) -> str | None:
        where = offset_of(rva)
        if where is None:
            return None
        end = data.find(b"\0", where)
        return data[where:end if end != -1 else None].decode("ascii", "replace") or None

    names: list[str] = []
    # (directory index, descriptor size, where the name RVA sits in a descriptor). Both tables end
    # with an all-zero descriptor rather than stating a count.
    for index, stride, name_field in ((1, 20, 12), (13, 32, 4)):
        table = int.from_bytes(data[directories + index * 8:directories + index * 8 + 4], "little")
        if not table:
            continue
        cursor = offset_of(table)
        if cursor is None:
            continue
        while cursor + stride <= len(data):
            descriptor = data[cursor:cursor + stride]
            if not any(descriptor):
                break
            name = string_at(int.from_bytes(descriptor[name_field:name_field + 4], "little"))
            if name and name not in names:
                names.append(name)
            cursor += stride
    return names


def pe_resolve(
    name: str, beside: Path, executable_dir: Path, search: Sequence[Path]
) -> Path | None:
    """Where the loader will find *name*, searched roughly the way it searches.

    **The application's directory comes first, and that is the whole of the relocation story on
    Windows.** There is no rpath to set, no install name to rewrite and no signature to repair: a
    DLL copied next to the executable *is* the fix, and `$ORIGIN` is emulating a behaviour Windows
    already has by default. It is also why `bundle` is called with ``libdir="bin"`` here where every
    other platform uses ``lib``.

    *executable_dir* is that application directory, and it is not the same as *beside* — this is
    `loader_search`'s problem in the other dialect. A plugin in `lib/` loaded by `bin/postgres.exe`
    imports from the executable and from the libraries beside it, and the loader resolves those
    against the **process's** directory rather than the plugin's. Asking only where the plugin sits
    reports as missing a file that is right there in the tree, which is a false failure and not a
    strict check: PostgreSQL and MariaDB both ship Windows trees shaped exactly that way.

    *search* is where the build put libraries this tree does not carry yet — Cygwin's own `bin` —
    and it is deliberately absent from `verify`, so that a tree which failed to bundle resolves
    nothing and says so.
    """
    for directory in (beside, executable_dir, *search, *SYSTEM_DIRECTORIES):
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def pe_dependencies(
    path: Path, executable_dir: Path, search: Sequence[Path] = ()
) -> list[tuple[str, Path | None]]:
    return [
        (name, pe_resolve(name, path.parent, executable_dir, search)) for name in pe_imports(path)
    ]


# ------------------------------------------------------------------------------------ shared ---


def is_system(spelling: str, resolved: Path | None) -> bool:
    if sys.platform == "darwin":
        target = str(resolved) if resolved else spelling
        return target.startswith(SYSTEM_PREFIXES)
    if sys.platform == "win32":
        # By location, because a PE import is a bare name and carries nothing else to judge it by.
        # An unresolved name is *not* system: it is a library this machine does not have either,
        # which `bundle` refuses and `verify` reports — the same treatment ELF gives a "not found".
        if Path(spelling).name.lower().startswith(API_SET_PREFIXES):
            return True
        return resolved is not None and inside(resolved, WINDOWS_ROOT)
    name = Path(spelling).name
    return name in SYSTEM_SONAMES or name.startswith("ld-linux")


def inside(path: Path, tree: Path) -> bool:
    try:
        path.resolve().relative_to(tree.resolve())
        return True
    except ValueError:
        return False


def bundle(tree: Path, libdir: str = "lib", search: Sequence[Path] = (),
           directories: Sequence[str] = BINARY_DIRECTORIES) -> dict[str, Path]:
    """Copy every non-system dependency into ``tree/libdir`` and rewrite the tree to use it.

    Returns ``{name: where it came from}`` — the origin matters to the caller, which has to collect
    each bundled library's licence and cannot ask the copy where it was packaged from. Raises rather
    than continuing when two different libraries want the same file name, because one would silently
    overwrite the other and the result would load whichever won.

    *search* is where the build put the libraries it compiled itself, and it is needed for the same
    reason `loader_search` exists one step later: **a library does not have to carry a search path,
    and the one asking for it usually does.** A Ruby linked with ``-Wl,-rpath,<deps>/lib`` resolves
    ``libssl.so.3`` perfectly, and ``libssl.so.3`` then names ``libcrypto.so.3`` with no path of its
    own — so asking *it* what it needs answers "not on this machine" about a library sitting beside
    it. Left out, the bundling stops on a dependency that was never missing.

    *directories* is where the payload is, and it exists here for the reason it exists on
    :func:`machine_files` and :func:`verify`: a tree whose binary sits at its own root has to say so
    or this walks nothing and bundles nothing, which reads exactly like a tree that needed nothing.
    """
    library_directory = tree / libdir
    executable_dir = tree / "bin"
    bundled: dict[str, Path] = {}

    queue = deque(machine_files(tree, directories))
    seen: set[Path] = set()
    while queue:
        current = queue.popleft()
        if current in seen:
            continue
        seen.add(current)
        for spelling, resolved in dependencies(current, executable_dir, search):
            if is_system(spelling, resolved):
                continue
            if resolved is None:
                raise Unbundleable(
                    f"{current.name} needs {spelling}, which is not on this machine either. "
                    "Bundling cannot invent it; the build is missing a dependency."
                )
            if inside(resolved, tree):
                continue
            name = Path(spelling).name
            if name in bundled:
                if bundled[name].resolve() != resolved.resolve():
                    raise Unbundleable(
                        f"two different libraries are both called {name}: {bundled[name]} and "
                        f"{resolved}. One would overwrite the other."
                    )
                continue
            library_directory.mkdir(parents=True, exist_ok=True)
            destination = library_directory / name
            # `copy2` rather than a link, and the source is followed through its symlinks: what is
            # wanted is the real file under the name the loader asks for.
            shutil.copy2(resolved.resolve(), destination)
            destination.chmod(destination.stat().st_mode | 0o644)
            bundled[name] = resolved
            # The *original* goes back on the queue, not the copy. A library's dependencies are
            # often written relative to itself — Homebrew's libwebp asks for
            # `@rpath/libsharpyuv.0.dylib` and finds it beside itself — and asking the copy resolves
            # those against a directory that does not have them yet, which reads as a dependency
            # missing from the machine rather than as one not copied over yet.
            queue.append(resolved)

    rewrite(tree, libdir, set(bundled), executable_dir, directories)
    return dict(sorted(bundled.items()))


def dependencies(
    path: Path, executable_dir: Path, search: Sequence[Path] = ()
) -> list[tuple[str, Path | None]]:
    if sys.platform == "darwin":
        return macho_dependencies(path, executable_dir)
    if sys.platform == "win32":
        return pe_dependencies(path, executable_dir, search)
    return elf_dependencies(path, search)


def rewrite(tree: Path, libdir: str, bundled: set[str], executable_dir: Path,
            directories: Sequence[str] = BINARY_DIRECTORIES) -> None:
    """Point every load at the copy beside it, relative to whoever is doing the loading.

    Nothing to do on Windows, and it is worth saying so rather than leaving the reader to infer it
    from an empty branch. A PE image names its dependencies by bare file name and the loader looks
    in the image's own directory first — so the copy `bundle` just made *is* the redirection, with
    no search path to set and no signature to repair. That is also why the caller passes
    ``libdir="bin"``: on Windows the library directory has to be the executable's own.
    """
    if sys.platform == "win32":
        return
    library_directory = tree / libdir
    for path in machine_files(tree, directories):
        relative = os.path.relpath(library_directory, path.parent).replace(os.sep, "/")
        if sys.platform == "darwin":
            anchor = "@loader_path" if relative == "." else f"@loader_path/{relative}"
            # An install name is a property of a *dylib*, and not every Mach-O under a library
            # directory is one: Ruby's compiled extensions live in `lib/ruby/<version>/<arch>/` and
            # are MH_BUNDLE, which has no `LC_ID_DYLIB` to set. `macho_id` answering None is how
            # they are told apart — asking `install_name_tool -id` anyway makes it refuse the file,
            # which would fail the whole relocation over something that was never wanted.
            if inside(path, library_directory) and macho_id(path):
                run("install_name_tool", "-id", f"@rpath/{path.name}", str(path))
            for spelling, resolved in macho_dependencies(path, executable_dir):
                # A reference into /usr/lib is never redirected, even when a bundled library happens
                # to share its file name. macOS ships its own `libiconv.2.dylib`, and Homebrew ships
                # one too under the same name but exporting GNU's `_libiconv` rather than the
                # system's `_iconv` — so matching on the file name alone quietly points the system's
                # own gettext at the wrong library, and the whole binary aborts on startup with a
                # missing symbol.
                if is_system(spelling, resolved):
                    continue
                if Path(spelling).name in bundled and not spelling.startswith("@rpath/"):
                    run("install_name_tool", "-change", spelling,
                        f"@rpath/{Path(spelling).name}", str(path))
            # Every search path the build left behind is removed, not merely added to. They are
            # absolute — a Homebrew cellar, a temporary build prefix — and on the machine that built
            # this they all still exist, so `@rpath/libzip.5.dylib` would go on resolving to the
            # builder's copy and the archive would look correct here and load a stranger's library
            # there. Everything the tree needs is in one directory, so one search path is enough.
            for existing in macho_rpaths(path):
                if existing != anchor:
                    run("install_name_tool", "-delete_rpath", existing, str(path), check=False)
            if anchor not in macho_rpaths(path):
                run("install_name_tool", "-add_rpath", anchor, str(path))
            macho_sign(path)
        else:
            anchor = "$ORIGIN" if relative == "." else f"$ORIGIN/{relative}"
            elf_set_rpath(path, anchor)


def verify(tree: Path, directories: Sequence[str] = BINARY_DIRECTORIES,
           executable_dir: Path | None = None) -> list[str]:
    """Re-resolve every dependency and complain about anything outside the tree.

    Meant to be run on a *copy of the tree in a directory it has never seen*. Running it where the
    tree was built proves nothing: the original build directory is still there, so a reference that
    escaped the rewrite still resolves and the check passes for a reason that will not exist on a
    user's machine.

    Each file is resolved the way the loader will resolve it — including through the search path the
    tree's own executables carry, which is how a plugin with no ``DT_RPATH`` of its own finds a
    library that is nonetheless in the tree. See `loader_search`.

    *executable_dir* is where the **process** will be running from, which is not where the file being
    checked sits — `pe_resolve` and ``@executable_path`` both need it and neither can derive it from
    the file. It used to be ``tree / "bin"`` unconditionally, and that is a fact about MariaDB's tree
    rather than about trees. A Windows Python keeps ``python.exe`` and ``python314.dll`` at the top
    and its extension modules in ``DLLs\\``, so asking ``tree/bin`` there names a directory that does
    not exist and reports **34 modules as missing a DLL that is one level above them** — measured on
    the published 3.14.7 archive, whose 34 modules then all imported from a moved tree on a cut-down
    ``PATH``. A check that fails what works is not a strict check; it is a check nobody can leave on.

    Derived from the tree rather than defaulted, because the tree already knows: ``bin`` if it has
    one, its own root if it does not. Still an argument, so that a tree which is neither can say so.
    """
    problems = []
    if executable_dir is None:
        executable_dir = tree / "bin" if (tree / "bin").is_dir() else tree
    search = loader_search(tree)
    for path in machine_files(tree, directories):
        for spelling, resolved in dependencies(path, executable_dir, search):
            if is_system(spelling, resolved):
                continue
            if resolved is None:
                problems.append(f"{path.name}: {spelling} does not resolve")
            elif not inside(resolved, tree):
                problems.append(f"{path.name}: {spelling} resolves outside the tree, to {resolved}")
    return problems


# ------------------------------------------------------------------------------------- floors ---


def _highest(versions: set[tuple[int, ...]]) -> str | None:
    return ".".join(str(part) for part in max(versions)) if versions else None


def glibc_floor(paths: list[Path]) -> str | None:
    """The oldest glibc these binaries will start on, read off the binaries themselves.

    Every glibc symbol a program imports carries the version it was introduced in, so the highest of
    those *is* the requirement — a measurement, unlike "whatever the build machine had", which is a
    guess that happens to be conservative until the day it is not.
    """
    if sys.platform != "linux":
        return None
    versions: set[tuple[int, ...]] = set()
    for path in paths:
        try:
            symbols = subprocess.run(
                ["objdump", "-T", str(path)], capture_output=True, text=True, timeout=120
            ).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        versions |= {
            tuple(int(part) for part in match.split("."))
            for match in re.findall(r"GLIBC_(\d+\.\d+(?:\.\d+)?)", symbols)
        }
    return _highest(versions)


def macos_floor(paths: list[Path]) -> str | None:
    """The oldest macOS these binaries will load on, read off their load commands.

    Bundling makes this a property of the *archive* rather than of the compiler flags: a dependency
    copied in from Homebrew was built for the runner's macOS and carries its own minimum, which is
    usually higher than anything this recipe asked for.

    Which field to read depends on which load command it sits in, so the command has to be tracked
    rather than the whole dump grepped: ``LC_BUILD_VERSION`` states the minimum as ``minos`` and then
    goes on to list the *tools* that built the file, each with a ``version`` of its own — the
    linker's ``1115.7.3`` is not a macOS release, but it is the largest number in the file and would
    win every comparison. Only the older ``LC_VERSION_MIN_MACOSX`` spells its minimum ``version``.
    """
    if sys.platform != "darwin":
        return None
    fields = {"LC_BUILD_VERSION": "minos", "LC_VERSION_MIN_MACOSX": "version"}
    versions: set[tuple[int, ...]] = set()
    for path in paths:
        command: str | None = None
        for line in run("otool", "-l", str(path), check=False).splitlines():
            words = line.split()
            if len(words) == 2 and words[0] == "cmd":
                command = words[1]
            elif len(words) == 2 and command in fields and words[0] == fields[command]:
                if re.fullmatch(r"\d+(?:\.\d+)*", words[1]):
                    versions.add(tuple(int(part) for part in words[1].split(".")))
                command = None      # one minimum per load command; ignore whatever follows it
    return _highest(versions)


def floor(tree: Path, directories: Sequence[str] = BINARY_DIRECTORIES) -> tuple[str, str] | None:
    """``(key, value)`` for the manifest's ``requires``, or None where the OS has no such notion.

    None is also the honest answer for a statically linked binary: a Go build imports no glibc
    symbol at all, so there is no version to be the floor rather than a floor of zero.
    """
    files = machine_files(tree, directories)
    if sys.platform == "darwin":
        measured = macos_floor(files)
        return ("macos", measured) if measured else None
    measured = glibc_floor(files)
    return ("glibc", measured) if measured else None


# ---------------------------------------------------------------------------------- licences ---
#
# **Beside `bundle`, because `bundle` is what creates the obligation.** These lived in `mariadb.py`
# and were called by three MariaDB recipes; nothing in them is about MariaDB, and the moment a
# fourth kind bundled its first system library the choice was to import a database module from a
# database module or to put them where they belong. `bundle` already answers with *where each
# library came from* for no other purpose than this.

# Where a licence text sits once it is installed, in the two shapes this repository meets: at the root
# of a Homebrew keg, and under `share/doc/<anything>/` — Debian's spelling and also where several
# formulas put theirs. Globbed rather than named because projects disagree about `LICENSE`, `LICENCE`,
# `COPYING` and every suffix of each.
LICENCE_GLOBS = ("LICENSE*", "LICENCE*", "COPYING*", "COPYRIGHT*", "copyright")

# Where Cygwin publishes the source of everything it ships. Written *into the tree* by
# `cygwin_source_note` rather than only stated here, because LGPLv3 asks that whoever receives the
# binary be able to obtain the library's source, and a reader holding the archive has the tree and
# not this file.
CYGWIN_SOURCE = "https://cygwin.com/pub/cygwin/x86_64/release"


def dpkg_owner(path: Path) -> str | None:
    """Which Debian package installed this file, if the machine can say."""
    try:
        result = subprocess.run(["dpkg", "-S", str(path)], capture_output=True, text=True,
                                timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or ":" not in result.stdout:
        return None
    # `libssl3:arm64: /usr/lib/...` on a multi-arch system; the qualifier is not part of the name,
    # and `/usr/share/doc` is filed under the bare one.
    return result.stdout.split(":", 1)[0].strip() or None


def cygwin_posix(root: Path, path: Path) -> str:
    r"""*path* spelled the way Cygwin spells it, asked of Cygwin's own mount table.

    **``cygcheck -f`` looks its answer up in a database keyed by POSIX paths**, so a Windows spelling
    finds nothing in it — and says so by printing nothing and exiting zero, which is the shape of
    failure this repository keeps meeting. Redis 8.10.0 shipped because of it: its archive bundles
    ``cyggcc_s-seh-1.dll`` and carries the Cygwin runtime's licence and not libgcc's, because the
    owner lookup answered ``None`` and the only two documents left are the ones found by path.

    Through ``cygpath`` rather than by string surgery, because the answer is the mount table and not
    a rule: ``<root>\bin`` is mounted at ``/usr/bin`` and not at ``/bin``, and a drive letter is not
    always ``/cygdrive``. By absolute path, because the caller may be running with Cygwin
    deliberately kept off ``PATH``.
    """
    try:
        answered = subprocess.run(
            [str(root / "bin" / "cygpath.exe"), "-u", str(path)],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return str(path)
    return answered.stdout.strip() or str(path)


def cygwin_package(root: Path, path: Path) -> str | None:
    """Which Cygwin package installed this file, exactly as ``cygcheck -f`` names it.

    ``cygwin-3.6.10-1``, ``libgcc1-14.4.0-1`` — version and release included, which is the spelling
    that identifies *the* source tarball rather than the project it came from.

    A failure here prints what the tool actually said. The previous version returned ``None`` for
    every reason alike, so a lookup that had never once worked looked exactly like a library that
    happened to have no package — and that is precisely what it was doing.
    """
    spelling = cygwin_posix(root, path)
    try:
        result = subprocess.run([str(root / "bin" / "cygcheck.exe"), "-f", spelling],
                                capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as refusal:
        print(f"cygcheck -f {spelling} could not be run: {refusal}", file=sys.stderr)
        return None
    named = [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]
    if result.returncode != 0 or not named:
        said = result.stderr.strip() or "(nothing on stderr)"
        print(f"cygcheck -f {spelling} exited {result.returncode} and named no package: {said}",
              file=sys.stderr)
        return None
    return named[0]


def cygwin_owner(root: Path, path: Path) -> str | None:
    """The same package with its version cut off, which is how ``/usr/share/doc`` files it.

    This is `dpkg_owner` in the other dialect.
    """
    package = cygwin_package(root, path)
    return re.sub(r"-\d.*$", "", package) if package else None


def cygwin_licences(real: Path) -> list[tuple[str, Path]]:
    """What has to travel with a DLL taken out of a Cygwin installation.

    Two documents, and the first is not optional: **cygwin1.dll is LGPLv3**, so an archive that
    redistributes it has to carry its terms. ``/usr/share/doc/Cygwin/`` holds `COPYING` and
    `CYGWIN_LICENSE` — the second states the runtime exception, which is the part a reader actually
    needs and which no glob in :data:`LICENCE_GLOBS` would have matched. The package's own directory
    is added when it has one, so `libgcc1` is licensed as libgcc rather than as Cygwin.

    The root is the parent of the directory the library was taken from — ``<root>/bin/cygwin1.dll``
    — which is the same walk the Homebrew branch does, for the same reason: nothing else on a
    Windows runner can say where the installation is once `PATH` has been cut down.
    """
    root = real.parent.parent
    shared = root / "usr" / "share" / "doc"
    found: list[tuple[str, Path]] = []

    for name in ("COPYING", "CYGWIN_LICENSE"):
        text = shared / "Cygwin" / name
        if text.is_file():
            found.append(("cygwin", text))

    owner = cygwin_owner(root, real)
    if owner:
        for pattern in LICENCE_GLOBS:
            found.extend((owner, text) for text in sorted((shared / owner).glob(pattern))
                         if text.is_file())
        # Cygwin's per-package note, which states where the source came from and under what terms.
        readme = shared / "Cygwin" / f"{owner}.README"
        if readme.is_file():
            found.append((owner, readme))
    return found


def cygwin_source_note(licences: Path, bundled: dict[str, Path]) -> None:
    """Say where the source of every bundled Cygwin DLL is published, in the archive itself.

    **LGPLv3 asks for more than its text, and this is the half that a licence file does not cover.**
    The recipient has to be able to obtain the library's source and relink against a modified one.
    The second half answers itself on Windows — the DLL is a separate file beside the ``.exe`` and
    can be replaced with another, which is the whole reason `bundle` puts it there — so what is left
    is a route to the source, and naming the exact package is enough of one because nothing here is
    patched: each file is the one its Cygwin package installs, copied over unmodified.

    A package that cannot be named is a failure rather than an ``unknown`` in a table. The archive
    would be redistributing an LGPL library while pointing at nothing, and a file that says so
    politely is worse than a build that stops.
    """
    rows, unnamed = [], []
    for name, origin in sorted(bundled.items()):
        real = origin.resolve()
        # `<root>/bin/cygwin1.dll` — the same walk `cygwin_licences` makes, and for the same reason:
        # once `PATH` has been cut down, nothing else on the runner can say where Cygwin is.
        package = cygwin_package(real.parent.parent, real)
        if not package:
            unnamed.append(f"{name} (from {origin})")
            continue
        rows.append(f"{name}\t{origin}\t{package}")
    if unnamed:
        raise SystemExit(
            "cygcheck cannot say which package installed a bundled DLL, so this archive cannot "
            "state where its source is published: " + "; ".join(unnamed)
        )

    (licences / "CYGWIN-SOURCE.txt").write_text(
        "This archive links the Cygwin runtime, which is LGPLv3. Nothing in it is patched: each DLL "
        "below is the file the named Cygwin package installs, copied beside the binary unmodified.\n"
        "\nfile\tinstalled from\tpackage\n" + "\n".join(rows) + "\n\n"
        "The corresponding source for each package is published by Cygwin at\n"
        f"  {CYGWIN_SOURCE}/<package>/\n"
        "as a <package>-<version>-src.tar.xz beside the binary package, and can also be fetched with "
        "Cygwin's own setup program using --download --include-source. The runtime's history is at\n"
        "  https://sourceware.org/git/?p=newlib-cygwin.git\n",
        encoding="utf-8",
    )


def licence_texts(origin: Path) -> list[tuple[str, Path]]:
    """The licence files belonging to a library at ``origin``, as ``(who it belongs to, file)``."""
    real = origin.resolve()
    places: list[Path] = []
    owner: str | None = None

    if sys.platform == "win32":
        return cygwin_licences(real)

    if "/Cellar/" in str(real):
        # **The keg, not the formula directory.** Walking up until the parent is `Cellar` stops at
        # `/opt/homebrew/Cellar/snappy`, which holds version directories and no files — which is how
        # six Homebrew libraries came to be bundled into the macOS artifacts with `licenses/` holding
        # nothing but MariaDB's own. One level lower is `.../snappy/1.2.2`, where the files are.
        keg = real
        while keg.parent.parent.name != "Cellar" and keg.parent != keg:
            keg = keg.parent
        owner = keg.parent.name
        places = [keg] + sorted(p for p in keg.glob("share/doc/*") if p.is_dir())
    else:
        owner = dpkg_owner(real) or dpkg_owner(origin)
        if owner:
            places = [Path("/usr/share/doc") / owner]

    found: list[tuple[str, Path]] = []
    for place in places:
        for pattern in LICENCE_GLOBS:
            for text in sorted(place.glob(pattern)):
                if text.is_file():
                    found.append((owner or "unknown", text))
    return found


def bundled_licences(tree: Path, bundled: dict[str, Path]) -> None:
    """Ship the licence of every library :func:`bundle` put beside the payload.

    **Decided once for the same reason each recipe's `prune` is.** :func:`bundle` already answers
    with where each library came from precisely so that its caller can do this, and only the macOS
    recipe tried — with a walk that stopped one directory too high, so it collected nothing and said
    nothing. The Linux recipes bundle between eighteen and twenty-two system libraries apiece and
    never looked at all: OpenSSL, PCRE2, lz4, lzo, snappy and zstd travel inside these artifacts, and
    several of those licences require their text to travel with them. That is a condition of
    redistributing the archive, not tidiness, so a library whose licence cannot be found is a failure
    and not a warning — a warning is what the last one was.

    On Windows there is a second obligation and one licence that carries it, so
    :func:`cygwin_source_note` runs as well. See its docstring for what a text alone does not answer.
    """
    if not bundled:
        return
    licences = tree / "licenses"
    licences.mkdir(exist_ok=True)

    rows, unlicensed = [], []
    for name, origin in sorted(bundled.items()):
        rows.append(f"{name}\t{origin}")
        texts = licence_texts(origin)
        if not texts:
            unlicensed.append(f"{name} (from {origin})")
            continue
        for owner, text in texts:
            shutil.copy2(text, licences / f"{owner}-{text.name}")

    (licences / "BUNDLED.tsv").write_text(
        "library\tbuilt from\n" + "\n".join(rows) + "\n", encoding="utf-8"
    )
    if sys.platform == "win32":
        cygwin_source_note(licences, bundled)
    if unlicensed:
        raise SystemExit(
            "no licence text found for a bundled library, and the archive may not be redistributed "
            "without one: " + "; ".join(unlicensed)
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tree", type=Path, help="the directory to make self-contained")
    parser.add_argument("--libdir", default="lib")
    parser.add_argument("--verify-only", action="store_true",
                        help="check an already-bundled tree instead of bundling it")
    arguments = parser.parse_args()

    if sys.platform not in ("darwin", "win32") and not sys.platform.startswith("linux"):
        raise SystemExit(f"nothing to relocate on {sys.platform}; this is for ELF, Mach-O and PE")

    if not arguments.verify_only:
        bundled = bundle(arguments.tree, arguments.libdir)
        print(f"bundled {len(bundled)} librar{'y' if len(bundled) == 1 else 'ies'}")
        for name, origin in bundled.items():
            print(f"  {name} <- {origin}")

    problems = verify(arguments.tree)
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if problems:
        raise SystemExit(f"{len(problems)} reference(s) still point outside the tree")

    measured = floor(arguments.tree)
    if measured:
        print(f"needs {measured[0]} {measured[1]} or newer ({platform.machine()})")


if __name__ == "__main__":
    main()
