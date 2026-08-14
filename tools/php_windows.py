#!/usr/bin/env python3
"""Borrow a PHP build from windows.php.net and repack it as a MixEngine artifact.

Nothing here compiles PHP. The official Windows builds are already relocatable — T20a extracted one,
moved it to an unrelated directory and ran it from there — so the whole job is to fetch the right
zip, prove it runs after being moved, describe what is inside it, and repack.

Two things this deliberately does not do:

*It does not rearrange the directory.* ``php.exe`` resolves its DLLs from its own directory, so
normalising the tree into ``bin``/``lib`` would produce an archive that only fails at run time. The
layout stays as its publisher shipped it and ``mixengine-artifact.json`` says where things are.

*It does not use the TS build.* ``php-cgi.exe`` behind FastCGI is how a Windows site is served, and
that wants the non-thread-safe build; the thread-safe one exists for an in-process module SAPI
MixEngine does not use.

Python 3 stdlib only, by policy: this runs on a GitHub runner with nothing installed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

RELEASES = "https://windows.php.net/downloads/releases"
ARCHIVES = f"{RELEASES}/archives"

# The compiler tag in the filename, mapped to the redistributable a user needs. Read from the
# filename rather than from a branch table because the spelling is not consistent across branches
# (``VC15`` on 7.2, ``vc15`` on 7.4) and the branch table would have to be extended by hand forever.
VCREDIST = {"vc14": "2015", "vc15": "2017", "vs16": "2019", "vs17": "2022"}

# Every extension this build can load, tried in turn until one loads. It only has to prove that
# ``extension_dir`` is honoured after relocation, which is the half ``php -v`` cannot prove.
SMOKE_EXTENSIONS = ("openssl", "curl", "mbstring", "sqlite3")

# ``-v`` is run against these and no others. ``php-win.exe`` is the GUI-subsystem build and writes
# nothing to a console at all — asking it for a version banner produces an empty string and an
# exit code of zero, which is the most confusing possible way for a check to fail. ``phpdbg.exe``
# is an interactive debugger and is not asked either. Both stay in ``provides`` because they are in
# the box; neither is something MixEngine will ever start.
VERSION_CHECKED = ("php", "php-cgi")


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def newest_patch(branch: str, arch: str) -> str:
    """Turn ``8.3`` into the newest patch of it there is, supported or not.

    ``releases.json`` answers for supported branches. An archived branch is not in it, and used to
    be refused here on the reasoning that it has no "newest" — only a last — so naming it was the
    caller's job. That reasoning stopped holding the moment the Unix recipes reached back to 7.0:
    they resolve a branch to its final patch by themselves, so one dispatch of ``7.0`` produced four
    artifacts of 7.0.33 and one error from this leg alone.

    A frozen branch is in fact the easier of the two to answer for. "Newest" of a supported branch
    is a moving claim; the last patch of a branch that will never have another is a fact, and the
    archive listing states it. So it is read from there.
    """
    releases = json.loads(fetch(f"{RELEASES}/releases.json"))
    current = releases.get(branch)
    if current:
        return current["version"]

    zip_arch = {"x86_64": "x64"}[arch]
    listing = fetch(f"{ARCHIVES}/").decode("utf-8", "replace")
    # Exactly three numeric parts: `php-7.0.0RC1-nts-…` is in there too, and a release candidate is
    # not a patch of anything.
    pattern = re.compile(
        rf"php-({re.escape(branch)}\.\d+)-nts-Win32-[A-Za-z]+\d+-{zip_arch}\.zip"
    )
    found = {m.group(1) for m in pattern.finditer(listing)}
    if not found:
        raise SystemExit(
            f"php {branch} is in neither releases.json nor archives/ as an nts {zip_arch} build. "
            f"Name an exact version if it is somewhere else."
        )
    return max(found, key=lambda version: tuple(int(part) for part in version.split(".")))


def resolve(version: str, arch: str) -> tuple[str, str | None]:
    """Return the download URL for *version*, and its published SHA-256 if there is one.

    Currently-supported branches are described by ``releases.json``, which carries a hash. Older ones
    live in ``archives/`` and carry nothing at all — no hash, no signature, not even a
    ``sha256sum.txt``. That is a real gap and it is recorded in the manifest rather than papered
    over: for those versions the only assurance is HTTPS to the publisher, plus the build reporting
    the version it was asked for when it is run.
    """
    zip_arch = {"x86_64": "x64"}[arch]
    branch = ".".join(version.split(".")[:2])

    releases = json.loads(fetch(f"{RELEASES}/releases.json"))
    current = releases.get(branch)
    if current and current.get("version") == version:
        for key, entry in current.items():
            if not isinstance(entry, dict) or "zip" not in entry:
                continue
            if key.startswith("nts-") and key.endswith(f"-{zip_arch}"):
                return f"{RELEASES}/{entry['zip']['path']}", entry["zip"].get("sha256")
        raise SystemExit(f"no nts-{zip_arch} build in releases.json for {version}")

    listing = fetch(f"{ARCHIVES}/").decode("utf-8", "replace")
    pattern = re.compile(
        rf"php-{re.escape(version)}-nts-Win32-([A-Za-z]+\d+)-{zip_arch}\.zip"
    )
    names = sorted({m.group(0) for m in pattern.finditer(listing)})
    if not names:
        raise SystemExit(f"php {version} nts {zip_arch} is in neither releases.json nor archives/")
    return f"{ARCHIVES}/{names[0]}", None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def php(binary: Path, *args: str) -> tuple[str, str]:
    """Run *binary* and return its output and its complaints, separately.

    Both halves are returned because PHP answers a question on stdout and explains a refusal on
    stderr, and a check that reads only the first can report "it said no" without ever saying why.
    """
    result = subprocess.run(
        [str(binary), *args], capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        raise SystemExit(f"{binary.name} {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout.strip(), result.stderr.strip()


def describe(tree: Path, version: str, arch: str, url: str, upstream_hash: str | None) -> dict:
    binaries = {p.stem: p.name for p in sorted(tree.glob("*.exe"))}
    provides = {name: path for name, path in binaries.items() if name != "deplister"}
    if "php" not in provides:
        raise SystemExit("no php.exe in the archive")

    static = json.loads(
        php(tree / "php.exe", "-n", "-r", "echo json_encode(get_loaded_extensions());")[0]
    )
    shared = sorted(p.stem.removeprefix("php_") for p in (tree / "ext").glob("php_*.dll"))
    print(f"{len(static)} static extension(s), {len(shared)} loadable module(s) in ext/")
    if not shared:
        raise SystemExit(
            f"no php_*.dll under {tree / 'ext'} — the archive is not laid out the way this recipe "
            f"expects, and every dynamic extension would be missing. Contents: "
            f"{sorted(p.name for p in tree.iterdir())[:20]}"
        )

    compiler = re.search(r"-(vc\d+|vs\d+)-", url, re.IGNORECASE)
    requires = {}
    if compiler:
        redist = VCREDIST.get(compiler.group(1).lower())
        if redist:
            requires["vcredist"] = redist

    manifest = {
        "schema": 1,
        "kind": "php",
        "version": version,
        "os": "windows",
        "arch": arch,
        "source": "borrowed",
        "upstream": {
            "url": url,
            "sha256": upstream_hash or sha256(tree.parent / "upstream.zip"),
            "verified_against": (
                "windows.php.net releases.json"
                if upstream_hash
                else "nothing — archived builds publish no hash; HTTPS to the publisher is all there is"
            ),
        },
        "provides": provides,
        "extension_dir": "ext",
        "extensions": {"static": sorted(static), "shared": shared},
    }
    if requires:
        manifest["requires"] = requires
    return manifest


def smoke(tree: Path, version: str, manifest: dict) -> dict:
    """Run the artifact from somewhere it has never been, and make it load something.

    ``php -v`` on its own would pass even with a broken ``extension_dir`` — the built-in extensions
    are static and never consult it — and the official build bakes ``C:\\php\\ext``, an absolute path
    that exists on nobody's machine. So the test that matters is the second one.
    """
    elsewhere = Path(tempfile.mkdtemp(prefix="mixengine-smoke-")) / "moved here" / "php"
    elsewhere.parent.mkdir(parents=True)
    shutil.copytree(tree, elsewhere)

    ran = []
    for name in VERSION_CHECKED:
        relative = manifest["provides"].get(name)
        if relative is None:
            raise SystemExit(f"the archive provides no {name}")
        banner = php(elsewhere / relative, "-v")[0].splitlines()[0]
        if version not in banner:
            raise SystemExit(f"{relative} reports {banner!r}, expected {version}")
        print(f"{relative}: {banner}")
        ran.append(f"{relative} -v")

    loaded, refused = None, []
    for candidate in SMOKE_EXTENSIONS:
        if candidate not in manifest["extensions"]["shared"]:
            continue
        # Through a generated php.ini rather than -d, because that is the mechanism the daemon will
        # use and a smoke test that proves a different one proves nothing.
        #
        # Every value is quoted, and that is not a style choice. PHP's ini parser refuses ``~`` in
        # an unquoted value -- ``syntax error, unexpected '~'`` -- and Windows puts one in every 8.3
        # short path: ``RUNNER~1``, ``PROGRA~1``, and the profile directory of any user whose name is
        # not plain ASCII. Unquoted, the file is rejected from that line on and *every* extension
        # silently fails to load while ``php -v`` keeps answering perfectly. Quoted, it loads. This
        # holds for MixEngine's generated config too, not only for this test.
        ini = elsewhere.parent / "php.ini"
        ini.write_text(
            f'display_errors=stderr\n'
            f'extension_dir="{elsewhere / "ext"}"\n'
            f'extension="php_{candidate}.dll"\n',
            encoding="utf-8",
        )
        answer, complaint = php(
            elsewhere / "php.exe",
            "-n", "-c", str(ini),
            "-r", f"echo extension_loaded({candidate!r}) ? 'yes' : 'no';",
        )
        if answer == "yes":
            loaded = candidate
            print(f"loaded {candidate} from the relocated ext/, through a generated php.ini")
            break
        refused.append(f"{candidate}: {answer!r} {complaint.splitlines()[:2]}")
    if loaded is None:
        raise SystemExit(
            "no shared extension could be loaded after relocation, so extension_dir is not being "
            "honoured and every dynamic extension would fail on a user's machine.\n  "
            + "\n  ".join(refused or ["none of the candidates is in this build"])
        )

    shutil.rmtree(elsewhere.parent.parent, ignore_errors=True)
    return {"relocated": True, "ran": ran, "loaded_extension": loaded}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version", required=True,
        help="exact PHP version (8.3.33), or a branch (8.3) for its newest patch",
    )
    parser.add_argument("--arch", default="x86_64", choices=["x86_64"])
    parser.add_argument("--out", default="dist", type=Path)
    args = parser.parse_args()

    if sys.platform != "win32":
        raise SystemExit("this recipe smoke-tests the binaries it packs, so it runs on Windows")

    if args.version.count(".") == 1:
        args.version = newest_patch(args.version, args.arch)
        print(f"branch resolved to {args.version}")

    url, upstream_hash = resolve(args.version, args.arch)
    print(f"borrowing {url}")

    work = Path(tempfile.mkdtemp(prefix="mixengine-php-"))
    upstream = work / "upstream.zip"
    urllib.request.urlretrieve(url, upstream)

    actual = sha256(upstream)
    if upstream_hash and actual != upstream_hash:
        raise SystemExit(f"sha256 mismatch: got {actual}, releases.json says {upstream_hash}")
    print(f"sha256 {actual} ({'verified' if upstream_hash else 'unverifiable, archived build'})")

    tree = work / "tree"
    with zipfile.ZipFile(upstream) as archive:
        archive.extractall(tree)

    manifest = describe(tree, args.version, args.arch, url, upstream_hash)
    manifest["smoke"] = smoke(tree, args.version, manifest)
    (tree / "mixengine-artifact.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    args.out.mkdir(parents=True, exist_ok=True)
    packed = args.out / f"php-{args.version}-windows-{args.arch}.zip"
    with zipfile.ZipFile(packed, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(tree.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(tree))

    (args.out / f"{packed.name}.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    shutil.rmtree(work, ignore_errors=True)

    print(f"packed {packed} ({packed.stat().st_size:,} bytes)")
    print(f"sha256 {sha256(packed)}")
    print(f"provides {', '.join(sorted(manifest['provides']))}")
    print(
        f"extensions {len(manifest['extensions']['static'])} static, "
        f"{len(manifest['extensions']['shared'])} shared; "
        f"loaded {manifest['smoke']['loaded_extension']} after relocation"
    )


if __name__ == "__main__":
    main()
