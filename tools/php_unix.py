#!/usr/bin/env python3
"""Build a relocatable PHP for macOS or Linux with static-php-cli, and pack it as an artifact.

This is the "built" half of the PHP row, for **PHP 8.1 and newer**. It exists because no publisher
ships a relocatable PHP for these two systems: Homebrew's is bound to its prefix and the distro
packages are bound to ``/usr``. `static-php-cli`_ already solved that, is MIT, and covers 8.1
upwards; everything older is compiled by ``php_legacy_unix.py``, which is a different recipe because
it is a different problem — no dependency solver, an era's toolchain, and libraries bundled beside
the binary rather than linked into it.

Two settings here are load-bearing rather than preferences:

*Never the musl target.* A fully static musl build cannot load a dynamic extension at all, which
would make every optional extension impossible on Linux. The glibc build can, so that is the one
MixEngine ships, and it is why ``--with-libc`` is not offered as an option below.

*``fpm`` is built, not just ``cli``.* A PHP site is served by php-fpm on these systems, so an
artifact with only the CLI would install fine and then be unable to run anything.

What is compiled in is present forever — a static extension cannot be turned off. So the compiled-in
set is the one nobody would want disabled, and anything optional or heavy is built shared instead
and shipped as a loadable module beside it.

.. _static-php-cli: https://github.com/crazywhalecc/static-php-cli
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

import php_parity
import php_smoke
import relocate

SPC_VERSION = "2.8.5"
SPC_URL = "https://github.com/crazywhalecc/static-php-cli/releases/download/{v}/spc-{target}.tar.gz"

# Built as loadable modules rather than compiled in: a debugger nobody wants running by default is
# exactly the case the compiled-in set cannot serve, because it could never be turned off again.
# `php_parity` holds that decision now, because the Windows recipe has to make the same one about a
# DLL it downloads and the 7.x recipe about one it builds with `phpize`.
SHARED_EXTENSIONS = sorted(php_parity.OFF_BY_DEFAULT)

SAPIS = ["cli", "fpm"]


def static_extensions(branch: str) -> list[str]:
    """What is compiled in, and therefore always present.

    Two lists in one, and the split is the whole of P2. :data:`php_parity.COMPILED_IN` is the set a
    local development environment for Laravel, Symfony or WordPress would otherwise have the user
    install by hand, and it is a set every cell of every version carries however it has to get it —
    on Windows most of these are loadable modules, because no Windows build exists with them static.
    :func:`php_parity.expected` is the handful this repository *adds* to what PHP ships, ``redis``
    and ``mongodb`` among them, which are compiled in here, `phpize`d on 7.x and downloaded from
    PECL on Windows.

    Neither list lives in this file any more. The reason is not tidiness: this recipe used to name
    ``redis``, ``mongodb``, ``igbinary``, ``yaml`` and ``zstd`` in a list of its own, the 7.x recipe
    named four of them in a list of its own, and the Windows recipe named none of them anywhere —
    which is exactly how a row ends up meaning three things.
    """
    parts = tuple(int(piece) for piece in branch.split("."))
    return list(php_parity.COMPILED_IN) + [
        name for name in php_parity.expected(parts) if name not in php_parity.OFF_BY_DEFAULT
    ]


def host() -> tuple[str, str, str]:
    machine = {"x86_64": "x86_64", "amd64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}
    arch = machine.get(platform.machine().lower())
    if arch is None:
        raise SystemExit(f"unsupported machine {platform.machine()}")
    if sys.platform == "darwin":
        # Both architectures, each built on a runner of its own. The policy used to be arm64 or
        # nothing here, on the argument that a version with no arm64 build should not be offered at
        # all; that argument survives, but it never implied the reverse. Intel Macs run the daemon
        # (MixEngine ships a universal binary) and PHP 7 is disproportionately what they are kept
        # around for, so an Intel row that exists for 7.x and stops at 8.0 would be the odder
        # matrix. Nothing is cross-compiled and nothing runs under Rosetta.
        return "macos", arch, f"macos-{arch}"
    if sys.platform.startswith("linux"):
        return "linux", arch, f"linux-{arch}"
    raise SystemExit("this recipe is for macOS and Linux; Windows borrows instead of building")


def run(*command: str, cwd: Path | None = None, env: dict | None = None) -> str:
    print("$", " ".join(command), flush=True)
    result = subprocess.run(
        command, cwd=cwd, env=env, text=True, capture_output=True, timeout=7200
    )
    sys.stdout.write(result.stdout)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise SystemExit(f"{command[0]} exited {result.returncode}")
    return result.stdout


def install_spc(work: Path, target: str) -> Path:
    url = SPC_URL.format(v=SPC_VERSION, target=target)
    print(f"fetching {url}")
    tarball = work / "spc.tar.gz"
    urllib.request.urlretrieve(url, tarball)
    with tarfile.open(tarball) as archive:
        archive.extractall(work)
    spc = work / "spc"
    spc.chmod(0o755)
    return spc


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def run_spc(command: list[str], work: Path, env: dict) -> str:
    """Run static-php-cli, and make a failure of it legible.

    It sends compiler output to log files of its own rather than to stdout, so a build that dies in
    `make` reaches CI as `make exited 2` and nothing else — a line that names no file, no symbol and
    no reason. The logs sit in the work directory, which is deleted on the way out, so the only
    moment they can be read is this one.
    """
    try:
        return run(*command, cwd=work, env=env)
    except SystemExit:
        for name in ("spc.output.log", "spc.shell.log"):
            log = work / "log" / name
            if not log.exists():
                continue
            tail = log.read_text(errors="replace").splitlines()[-300:]
            print(f"\n===== last {len(tail)} lines of {name} =====", file=sys.stderr)
            print("\n".join(tail), file=sys.stderr)
        raise


def build(spc: Path, work: Path, branch: str) -> Path:
    static = static_extensions(branch)
    extensions = ",".join(static)
    env = {**os.environ}

    if sys.platform.startswith("linux"):
        # static-php-cli defaults to musl on Linux, and a statically linked musl has no `dlopen` at
        # all — it refuses the build outright the moment a shared extension is asked for. The choice
        # is therefore forced rather than preferred: MixEngine ships loadable extensions, so it
        # links glibc.
        #
        # The price is a floor. A glibc binary will not start on a distribution older than the one
        # that built it, where the musl build would have run anywhere. That floor is measured off
        # the finished binary and recorded in the manifest rather than assumed, so the index can
        # state it and a client can refuse the install instead of producing a loader error.
        env["SPC_LIBC"] = "glibc"

    # No PHP release here was written for C23, and C23 removed the old-style function definition —
    # `bc_add(n1, n2, result, scale_min)` with the parameter types on the lines below it, which is
    # how libbcmath is written all through 8.1. Nothing asks for C23: `AC_PROG_CC` probes for the
    # newest standard the compiler will accept and puts it in `CC` itself, so the standard a build
    # gets is decided by how new the runner's clang happens to be. That is why 8.1 compiled on
    # macos-14 and failed on macos-15-intel with the same source and the same flags — a difference
    # in Xcode, read as a difference in architecture.
    #
    # Answering the probe here rather than fighting its result afterwards: `-std=` appended later
    # would win at `make` time but leave `configure` measuring the compiler as something the build
    # then isn't. Set for every branch, because a version that builds today would otherwise break
    # the first time a runner image ships a newer clang, and it would break the same way.
    env["ac_cv_prog_cc_c23"] = "no"

    run_spc([str(spc), "doctor", "--auto-fix"], work, env)
    # The download has to cover the shared extensions too. `--build-shared` does not fetch anything
    # of its own: it links what `download` already put in place, and refuses at the very end of a
    # build otherwise, which is the most expensive moment to find out.
    everything = ",".join(static + SHARED_EXTENSIONS)
    if not env.get("GITHUB_TOKEN"):
        print(
            "warning: no GITHUB_TOKEN. static-php-cli resolves two dozen libraries through "
            "api.github.com, which allows 60 unauthenticated requests an hour per IP — expect a "
            "403 that surfaces as a type error inside its downloader.",
            file=sys.stderr,
        )
    run_spc([str(spc), "download", f"--with-php={branch}", f"--for-extensions={everything}",
             "--retry=5", "--ignore-cache-sources=php-src"], work, env)

    arguments = [str(spc), "build", extensions]
    arguments += [f"--build-{sapi}" for sapi in SAPIS]
    if SHARED_EXTENSIONS:
        arguments.append(f"--build-shared={','.join(SHARED_EXTENSIONS)}")
    run_spc(arguments, work, env)

    buildroot = work / "buildroot"
    if not (buildroot / "bin" / "php").exists():
        raise SystemExit("static-php-cli produced no buildroot/bin/php")
    return buildroot


def assemble(buildroot: Path, work: Path) -> tuple[Path, dict[str, str], list[str]]:
    """Lay the build out as the archive, and report what it provides.

    Unlike the Windows recipe this *does* choose a layout, because there is no publisher's layout to
    preserve — static-php-cli's buildroot is a build directory, not a distribution.
    """
    tree = work / "tree"
    (tree / "bin").mkdir(parents=True)
    provides = {}
    for name in ("php", "php-fpm"):
        binary = buildroot / "bin" / name
        if not binary.exists():
            continue
        shutil.copy2(binary, tree / "bin" / name)
        provides[name] = f"bin/{name}"

    shared = []
    modules = buildroot / "modules"
    if modules.is_dir():
        (tree / "ext").mkdir(exist_ok=True)
        for module in sorted(modules.glob("*.so")):
            shutil.copy2(module, tree / "ext" / module.name)
            shared.append(module.stem)

    # static-php-cli leaves a *directory* of licences, one per library it linked in — and that is
    # the right shape, because a static binary carries all of their code. Several of those licences
    # require the text to travel with the binary, so shipping the whole directory is a condition of
    # redistributing the artifact at all rather than tidiness.
    licences = buildroot / "license"
    if licences.is_dir():
        shutil.copytree(licences, tree / "licenses")
    elif (buildroot / "LICENSE").is_file():
        shutil.copy2(buildroot / "LICENSE", tree / "LICENSE")
    return tree, provides, shared


def smoke(tree: Path, provides: dict[str, str], shared: list[str]) -> tuple[str, dict]:
    """Exercise the build from a directory it has never seen.

    A build tested where it was produced proves nothing about relocatability, which is the single
    property this whole repository exists to guarantee.

    The same four things are proven here as in the 7.0–8.0 recipe, and for the same reasons: that
    nothing in the tree still reaches outside it, that the SAPIs start, that the bundled libraries
    are *called* rather than merely linked, and that every shared extension loads through a
    generated ini. This half used to prove two of them, which made `smoke.relocated` mean something
    different depending on which branch produced it.
    """
    elsewhere = Path(tempfile.mkdtemp(prefix="mixengine-smoke-")) / "moved here" / "php"
    elsewhere.parent.mkdir(parents=True)
    shutil.copytree(tree, elsewhere, symlinks=True)

    problems = relocate.verify(elsewhere)
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if problems:
        raise SystemExit("the relocated tree still reaches outside itself")

    banner = run(str(elsewhere / "bin" / "php"), "-v").splitlines()[0]
    version = re.search(r"PHP (\d+\.\d+\.\d+)", banner)
    if not version:
        raise SystemExit(f"could not read a version out of {banner!r}")

    ran = [f"{path} -v" for path in provides.values()]
    for name, path in provides.items():
        if name != "php":
            run(str(elsewhere / path), "-v")

    answer = php_smoke.libraries(elsewhere / "bin" / "php", elsewhere.parent / "smoke.php")
    if not answer.endswith("OK"):
        raise SystemExit(f"the relocated build cannot use its own libraries: {answer}")
    print("every bundled library answered from the relocated tree")

    # Every shared extension, not the first one that loads. There is only one of them today, which
    # is exactly why stopping at the first was survivable and would not have stayed so.
    loaded, refused = [], []
    ini = elsewhere.parent / "php.ini"
    php = elsewhere / "bin" / "php"
    for candidate in shared:
        ok, said, error = php_smoke.loads(php, elsewhere / "ext", candidate, ini)
        if ok:
            loaded.append(candidate)
            print(f"loaded {candidate} from the relocated ext/, through a generated php.ini")
            continue
        refused.append(candidate)
        print(f"{candidate} did not load: {said!r}", file=sys.stderr)
        for line in error.splitlines():
            print(f"  {line}", file=sys.stderr)

    if refused:
        # `--build-shared` was asked for these by name, so one that will not load is a build that
        # did not produce what it was told to. Publishing it would put an `ext/` in the archive
        # holding a file the daemon can offer and PHP will refuse.
        raise SystemExit(
            f"built but cannot be loaded: {', '.join(refused)}"
        )

    proof = {"relocated": True, "ran": ran, "loaded_extensions": loaded}
    shutil.rmtree(elsewhere.parent.parent, ignore_errors=True)
    return version.group(1), proof


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--branch", required=True,
        help="PHP branch, e.g. 8.3 — static-php-cli picks the newest patch and the recipe records "
             "whichever one it got, rather than pretending it can pin a patch it cannot ask for",
    )
    parser.add_argument("--out", default="dist", type=Path)
    args = parser.parse_args()

    if tuple(int(part) for part in args.branch.split(".")) < php_parity.FLOOR:
        raise SystemExit(
            f"static-php-cli builds PHP 8.1 and newer; {args.branch} is php_legacy_unix.py's"
        )

    operating_system, arch, target = host()
    work = Path(tempfile.mkdtemp(prefix="mixengine-php-"))

    spc = install_spc(work, target)
    buildroot = build(spc, work, args.branch)
    tree, provides, shared = assemble(buildroot, work)
    version, proof = smoke(tree, provides, shared)

    static = json.loads(
        run(str(tree / "bin" / "php"), "-n", "-r", "echo json_encode(get_loaded_extensions());")
    )

    # Measured, then checked against what the branch owes. The check is here rather than after the
    # build because `--build-shared` can be given a name static-php-cli then quietly does not
    # produce, and an artifact short of `redis` is one this repository has always said it would
    # rather not publish — it just never said so on this half.
    php_parity.check(tuple(int(piece) for piece in args.branch.split(".")), static, shared)

    manifest = {
        "schema": 1,
        "kind": "php",
        "version": version,
        "os": operating_system,
        "arch": arch,
        "source": "built",
        "recipe": f"static-php-cli {SPC_VERSION}",
        "provides": provides,
        "extensions": {
            "static": sorted(static),
            "shared": sorted(shared),
            # Empty here, and that is the answer rather than a missing one: everything this build
            # carries is compiled in, and the only loadable module is the debugger. On Windows the
            # same field names nine extensions, because that is where the same set is loadable
            # rather than static. See `php_parity.enabled_by_default`.
            "enabled": php_parity.enabled_by_default(shared),
        },
        "smoke": proof,
    }
    if shared:
        manifest["extension_dir"] = "ext"
    # Measured off the finished archive rather than assumed from the runner, and on macOS as well as
    # Linux: a build produced on macos-14 does not start on macos-13, and an index that says nothing
    # about that hands the user a loader error instead of a refusal with a reason.
    measured = relocate.floor(tree)
    if measured:
        manifest["requires"] = {measured[0]: measured[1]}
        print(f"needs {measured[0]} {measured[1]} or newer")
    (tree / "mixengine-artifact.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    args.out.mkdir(parents=True, exist_ok=True)
    packed = args.out / f"php-{version}-{operating_system}-{arch}.tar.zst"
    try:
        run("tar", "--zstd", "-cf", str(packed), "-C", str(tree), ".")
    except SystemExit:
        # A tar that died half way leaves a truncated archive behind, and `dist/` is uploaded
        # wholesale — so it is removed rather than left for something downstream to find.
        packed.unlink(missing_ok=True)
        packed = packed.with_suffix("").with_suffix(".tar.gz")
        run("tar", "-czf", str(packed), "-C", str(tree), ".")

    (args.out / f"{packed.name}.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    shutil.rmtree(work, ignore_errors=True)

    print(f"packed {packed} ({packed.stat().st_size:,} bytes)")
    print(f"sha256 {sha256(packed)}")
    print(f"php {version} on {operating_system}/{arch}: {', '.join(sorted(provides))}")
    print(f"{len(static)} static extensions, {len(shared)} shared")


if __name__ == "__main__":
    main()
