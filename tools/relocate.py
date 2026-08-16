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

ELF_MAGIC = b"\x7fELF"
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
    """``elf``, ``macho`` or None, decided by the file's own first four bytes.

    Asking ``file(1)`` would mean depending on it being installed and on its wording; the magic
    number is the thing itself.
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
    if sys.platform == "darwin" or not binaries.is_dir():
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


# ------------------------------------------------------------------------------------ shared ---


def is_system(spelling: str, resolved: Path | None) -> bool:
    if sys.platform == "darwin":
        target = str(resolved) if resolved else spelling
        return target.startswith(SYSTEM_PREFIXES)
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
    return elf_dependencies(path, search)


def rewrite(tree: Path, libdir: str, bundled: set[str], executable_dir: Path,
            directories: Sequence[str] = BINARY_DIRECTORIES) -> None:
    """Point every load at the copy beside it, relative to whoever is doing the loading."""
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


def verify(tree: Path, directories: Sequence[str] = BINARY_DIRECTORIES) -> list[str]:
    """Re-resolve every dependency and complain about anything outside the tree.

    Meant to be run on a *copy of the tree in a directory it has never seen*. Running it where the
    tree was built proves nothing: the original build directory is still there, so a reference that
    escaped the rewrite still resolves and the check passes for a reason that will not exist on a
    user's machine.

    Each file is resolved the way the loader will resolve it — including through the search path the
    tree's own executables carry, which is how a plugin with no ``DT_RPATH`` of its own finds a
    library that is nonetheless in the tree. See `loader_search`.
    """
    problems = []
    executable_dir = tree / "bin"
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


def licence_texts(origin: Path) -> list[tuple[str, Path]]:
    """The licence files belonging to a library at ``origin``, as ``(who it belongs to, file)``."""
    real = origin.resolve()
    places: list[Path] = []
    owner: str | None = None

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

    if sys.platform not in ("darwin",) and not sys.platform.startswith("linux"):
        raise SystemExit(f"nothing to relocate on {sys.platform}; this is for ELF and Mach-O")

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
