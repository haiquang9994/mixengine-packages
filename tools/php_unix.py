#!/usr/bin/env python3
"""Build a relocatable PHP for macOS or Linux with static-php-cli, and pack it as an artifact.

This is the "built" half of the PHP row. It exists because no publisher ships a relocatable PHP for
these two systems: Homebrew's is bound to its prefix and the distro packages are bound to ``/usr``.
`static-php-cli`_ already solved that, is MIT, and covers **PHP 8.1 upwards** — which is why the
version policy floors macOS and Linux at 8.1 while Windows reaches back to 7.0.

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

SPC_VERSION = "2.8.5"
SPC_URL = "https://github.com/crazywhalecc/static-php-cli/releases/download/{v}/spc-{target}.tar.gz"

# Compiled in, and therefore always present. Chosen as the set a local development environment for
# Laravel, Symfony or WordPress would otherwise have the user install by hand — including the two
# MixEngine was told it must carry across the whole version range, redis and mongodb.
STATIC_EXTENSIONS = [
    "bcmath", "bz2", "calendar", "ctype", "curl", "dba", "dom", "exif", "fileinfo", "filter",
    "ftp", "gd", "gmp", "iconv", "igbinary", "intl", "mbregex", "mbstring", "mongodb", "mysqli",
    "mysqlnd", "opcache", "openssl", "pcntl", "pdo", "pdo_mysql", "pdo_pgsql", "pdo_sqlite",
    "pgsql", "phar", "posix", "readline", "redis", "session", "shmop", "simplexml", "soap",
    "sockets", "sodium", "sqlite3", "sysvmsg", "sysvsem", "sysvshm", "tokenizer", "xml",
    "xmlreader", "xmlwriter", "xsl", "yaml", "zip", "zlib", "zstd",
]

# Built as loadable modules instead: a debugger nobody wants running by default is exactly the case
# the compiled-in set cannot serve, because it could never be turned off again.
SHARED_EXTENSIONS = ["xdebug"]

SAPIS = ["cli", "fpm"]


def host() -> tuple[str, str, str]:
    machine = {"x86_64": "x86_64", "amd64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}
    arch = machine.get(platform.machine().lower())
    if arch is None:
        raise SystemExit(f"unsupported machine {platform.machine()}")
    if sys.platform == "darwin":
        if arch != "aarch64":
            raise SystemExit(
                "MixEngine offers no Intel-only PHP on macOS — the version policy is arm64 or "
                "nothing there, so an x86_64 mac build would be an artifact nobody can install"
            )
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


def build(spc: Path, work: Path, branch: str) -> Path:
    extensions = ",".join(STATIC_EXTENSIONS)
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

    run(str(spc), "doctor", "--auto-fix", cwd=work, env=env)
    # The download has to cover the shared extensions too. `--build-shared` does not fetch anything
    # of its own: it links what `download` already put in place, and refuses at the very end of a
    # build otherwise, which is the most expensive moment to find out.
    everything = ",".join(STATIC_EXTENSIONS + SHARED_EXTENSIONS)
    if not env.get("GITHUB_TOKEN"):
        print(
            "warning: no GITHUB_TOKEN. static-php-cli resolves two dozen libraries through "
            "api.github.com, which allows 60 unauthenticated requests an hour per IP — expect a "
            "403 that surfaces as a type error inside its downloader.",
            file=sys.stderr,
        )
    run(str(spc), "download", f"--with-php={branch}", f"--for-extensions={everything}",
        "--retry=5", "--ignore-cache-sources=php-src", cwd=work, env=env)

    arguments = [str(spc), "build", extensions]
    arguments += [f"--build-{sapi}" for sapi in SAPIS]
    if SHARED_EXTENSIONS:
        arguments.append(f"--build-shared={','.join(SHARED_EXTENSIONS)}")
    run(*arguments, cwd=work, env=env)

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


def glibc_floor(binary: Path) -> str | None:
    """The oldest glibc this binary will start on, read off the binary itself.

    Every glibc symbol a program imports carries the version it was introduced in, so the highest
    of those *is* the requirement — which is a measurement, unlike "whatever the runner had", which
    is a guess that happens to be conservative until the day it is not.
    """
    if sys.platform != "linux":
        return None
    try:
        symbols = subprocess.run(
            ["objdump", "-T", str(binary)], capture_output=True, text=True, timeout=120
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    versions = {
        tuple(int(part) for part in match.split("."))
        for match in re.findall(r"GLIBC_(\d+\.\d+(?:\.\d+)?)", symbols)
    }
    if not versions:
        return None
    return ".".join(str(part) for part in max(versions))


def smoke(tree: Path, provides: dict[str, str], shared: list[str]) -> tuple[str, dict]:
    """Exercise the build from a directory it has never seen.

    A build tested where it was produced proves nothing about relocatability, which is the single
    property this whole repository exists to guarantee.
    """
    elsewhere = Path(tempfile.mkdtemp(prefix="mixengine-smoke-")) / "moved here" / "php"
    elsewhere.parent.mkdir(parents=True)
    shutil.copytree(tree, elsewhere)

    banner = run(str(elsewhere / "bin" / "php"), "-v").splitlines()[0]
    version = re.search(r"PHP (\d+\.\d+\.\d+)", banner)
    if not version:
        raise SystemExit(f"could not read a version out of {banner!r}")

    ran = [f"{path} -v" for path in provides.values()]
    for name, path in provides.items():
        if name != "php":
            run(str(elsewhere / path), "-v")

    # Through a generated php.ini with every value quoted, exactly as the Windows recipe does and
    # for the same reason: this is the mechanism the daemon will use, so it is the one worth
    # proving, and a check that goes through -d proves a different one.
    loaded = None
    for candidate in shared:
        name = candidate.removeprefix("php_")
        ini = elsewhere.parent / "php.ini"
        ini.write_text(
            f'display_errors=stderr\n'
            f'extension_dir="{elsewhere / "ext"}"\n'
            f'zend_extension="{elsewhere / "ext" / (candidate + ".so")}"\n',
            encoding="ascii",
        )
        answer = run(
            str(elsewhere / "bin" / "php"), "-n", "-c", str(ini),
            "-r", f"echo extension_loaded({name!r}) ? 'yes' : 'no';",
        ).strip()
        if answer.endswith("yes"):
            loaded = candidate
            print(f"loaded {name} from the relocated ext/, through a generated php.ini")
            break
        print(f"{name} did not load: {answer!r}")

    proof = {"relocated": True, "ran": ran}
    if loaded:
        proof["loaded_extension"] = loaded
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

    if tuple(int(part) for part in args.branch.split(".")) < (8, 1):
        raise SystemExit(
            f"static-php-cli builds PHP 8.1 and newer; {args.branch} on this OS is T27a's problem"
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

    manifest = {
        "schema": 1,
        "kind": "php",
        "version": version,
        "os": operating_system,
        "arch": arch,
        "source": "built",
        "recipe": f"static-php-cli {SPC_VERSION}",
        "provides": provides,
        "extensions": {"static": sorted(static), "shared": sorted(shared)},
        "smoke": proof,
    }
    if shared:
        manifest["extension_dir"] = "ext"
    floor = glibc_floor(tree / "bin" / "php")
    if floor:
        manifest["requires"] = {"glibc": floor}
        print(f"needs glibc {floor} or newer")
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
