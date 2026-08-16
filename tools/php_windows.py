#!/usr/bin/env python3
"""Borrow a PHP build from windows.php.net, make it agree with the other five cells, and repack it.

Nothing here compiles PHP. The official Windows builds are already relocatable — T20a extracted one,
moved it to an unrelated directory and ran it from there — so the whole job is to fetch the right
zip, prove it runs after being moved, describe what is inside it, and repack.

What it does *not* borrow is the extension set. Until P2 this recipe packed whatever the publisher
put in the zip, and the publisher's answer is not MixEngine's: PHP 8.3 on Windows shipped without
``redis`` and ``mongodb``, which the Unix recipes fail a build over, and with an ODBC bridge, an
Oracle client, ``dl_test`` and ``zend_test``. So the set is now chosen — `php_parity` holds the
choice for all three recipes — and this one reaches it in two moves the Unix recipes make at
configure time: **what is missing is downloaded** from PECL's own Windows builds, and **what nothing
here needs is thrown away**, along with the libraries that were only in the archive to serve it.

Three things this deliberately does not do:

*It does not rearrange the directory.* ``php.exe`` resolves its DLLs from its own directory, so
normalising the tree into ``bin``/``lib`` would produce an archive that only fails at run time. The
layout stays as its publisher shipped it and ``mixengine-artifact.json`` says where things are.

*It does not use the TS build.* ``php-cgi.exe`` behind FastCGI is how a Windows site is served, and
that wants the non-thread-safe build; the thread-safe one exists for an in-process module SAPI
MixEngine does not use.

*It does not mix compiler tags.* A PECL extension built for a different ``vc``/``vs`` tag than the
PHP it is loaded into is a different C runtime, not a version to warn about, so an extension with no
build for this exact tag is treated as one that does not exist.

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
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Not a migration. This recipe predates `borrow` and still fetches, hashes, unpacks and packs by
# itself; moving that over is a separate change with a separate risk. What is taken from elsewhere
# is the part that must not be implemented twice — `borrow.declare` for the two manifest fields,
# `php_parity` for which extensions a version owes, `php_smoke.loads` for what loading one means —
# because the whole reason those modules exist is that two producers of one claim drift apart
# invisibly, precisely because they agree on the name of the thing.
import borrow  # noqa: E402  — siblings, and this directory is not importable as a package
import php_parity  # noqa: E402
import php_smoke  # noqa: E402

RELEASES = "https://windows.php.net/downloads/releases"
ARCHIVES = f"{RELEASES}/archives"

# PECL's own Windows builder, which publishes a DLL per (package, version, branch, thread safety,
# compiler tag, architecture). It is the same set of extensions the Unix recipes compile or
# `phpize`, from the same source releases, built by php.net rather than by a third party.
PECL = "https://windows.php.net/downloads/pecl/releases"

# How far down a package's release list to walk before giving up. Deep on purpose, and for the same
# reason `php_legacy_unix.py` walks 250: the newest `mongodb` with a Windows build for PHP 7.0 is
# 1.5.3, some sixty releases below the current 2.3.3. A shallow walk does not fail — it reports "no
# Windows build for this branch" and ships an artifact missing an extension the Unix cells have.
PECL_DEPTH = 250

# Nothing here publishes a hash. windows.php.net states one for a current PHP release in
# `releases.json` and states nothing for anything under `pecl/`, so what a downloaded extension is
# checked against is HTTPS to php.net and the load test further down — the same assurance an
# archived PHP build gets, and the manifest says so in `recipe` rather than implying more.
PECL_VERIFIED_AGAINST = "nothing — php.net publishes no hash under pecl/; HTTPS to the publisher"

# The compiler tag in the filename, mapped to the redistributable a user needs. Read from the
# filename rather than from a branch table because the spelling is not consistent across branches
# (``VC15`` on 7.2, ``vc15`` on 7.4) and the branch table would have to be extended by hand forever.
VCREDIST = {"vc14": "2015", "vc15": "2017", "vs16": "2019", "vs17": "2022"}

# The two SAPIs this artifact is for, and the two `-v` is run against. They used to be a subset of
# what the archive held: `php-win.exe` is the GUI-subsystem build, which writes nothing to a console
# at all — asking it for a version banner produces an empty string and an exit code of zero, the
# most confusing possible way for a check to fail — and `phpdbg.exe` is an interactive debugger.
# Both are now deleted rather than merely not run, because the Unix recipes pass `--disable-phpdbg`
# and build only `cli` and `fpm`: a SAPI nobody starts is one more thing this version means here
# and nowhere else. See `prune`.
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


def rename_aliases(tree: Path) -> tuple[list[str], list[str]]:
    """Give every extension DLL the name of the extension inside it, and say which were renamed.

    One file in one era: every Windows build from 7.0 to 7.4 ships GD as ``php_gd2.dll`` and 8.0
    renamed it to ``php_gd.dll``. Five branches out of eleven, and everything downstream of this
    assumes the other spelling — the keep-list in :func:`prune` matches file stems, so GD was thrown
    out of all five without a word; ``extensions.shared`` would have advertised ``gd2``, which is not
    an extension name; and the daemon composes ``extension=php_<name>.dll`` from that list.

    Renaming rather than teaching each of those three about the exception, because the exception is
    the publisher's and it is the only one: after this, the file that provides ``gd`` is called
    ``php_gd.dll`` on every Windows cell this repository publishes. It costs one line in each of
    ``upstream.added`` and ``upstream.removed``, which is exactly what those fields are for.
    """
    added, removed = [], []
    for alias, name in php_parity.DLL_NAMES.items():
        source = tree / "ext" / f"php_{alias}.dll"
        if not source.exists():
            continue
        source.rename(tree / "ext" / f"php_{name}.dll")
        print(f"renamed ext/php_{alias}.dll to ext/php_{name}.dll, which is the extension in it")
        added.append(f"ext/php_{name}.dll")
        removed.append(f"ext/php_{alias}.dll")
    return added, removed


def pecl_release(package: str, branch: str, compiler: str, zip_arch: str) -> tuple[str, str] | None:
    """The newest stable *package* that publishes a build for exactly this PHP, or ``None``.

    "Exactly" includes the compiler tag, which is the one part of this that is not a preference. A
    ``vs16`` extension loaded into a ``vc15`` PHP is a second C runtime in one process rather than a
    version mismatch to warn about, and the way that fails is a heap corruption several minutes
    later. PECL's Windows builder uses the same tag the official build of a branch uses, so
    requiring equality costs nothing and rules out a failure nobody could read.

    Release candidates are refused on the version string rather than on PECL's stability field: the
    field is what a packager typed, and this listing carries ``0.9.0RC1`` directories beside stable
    ones. A runtime manager does not ship a release candidate of an extension it chose for the user.
    """
    try:
        listing = fetch(f"{PECL}/{package}/").decode("utf-8", "replace")
    except urllib.error.HTTPError:
        return None
    stable = {
        found for found in re.findall(r'href="([0-9][0-9A-Za-z.]*)/"', listing)
        if re.fullmatch(r"[0-9.]+", found)
    }

    def numeric(found: str) -> tuple[int, ...]:
        # `0.9.0` is newer than `0.18.0` only if these are read as numbers, and `zstd` is exactly
        # that listing. Sorting the strings would pick a release four years older.
        return tuple(int(piece) for piece in found.split("."))

    ordered = sorted(stable, key=numeric, reverse=True)

    for candidate in ordered[:PECL_DEPTH]:
        asset = f"php_{package}-{candidate}-{branch}-nts-{compiler}-{zip_arch}.zip"
        try:
            files = fetch(f"{PECL}/{package}/{candidate}/").decode("utf-8", "replace")
        except urllib.error.HTTPError:
            continue
        if asset in files:
            return candidate, f"{PECL}/{package}/{candidate}/{asset}"
    return None


def install_extensions(tree: Path, branch: str, compiler: str, zip_arch: str,
                       work: Path) -> tuple[dict[str, str], list[str]]:
    """Put the extensions this branch owes into ``ext/``, and say where each one came from.

    ``php_parity.expected`` decides *which*; this decides nothing and downloads. What it takes out
    of each zip is the DLL and the licence texts, and deliberately not the ``.pdb`` beside it: those
    are 13.7 MB against 4.0 MB of extension for the six packages of 8.3, and debug symbols are the
    first thing the second half of the rule names. The licences are not optional in the same way:
    ``mongodb`` is Apache-2.0 and ``zstd`` is BSD, and both require the text to travel with the
    binary, which is why the Unix recipes ship a ``licenses/`` directory and why this now does too.
    """
    versions: dict[str, str] = {}
    added: list[str] = []
    (tree / "ext").mkdir(exist_ok=True)

    for package in php_parity.expected(tuple(int(p) for p in branch.split("."))):
        if (tree / "ext" / f"php_{package}.dll").exists():
            print(f"{package}: already in the publisher's archive")
            continue

        found = pecl_release(package, branch, compiler, zip_arch)
        if found is None:
            missing = (
                f"PECL publishes no {compiler} nts {zip_arch} build of {package} for PHP {branch}, "
                f"within {PECL_DEPTH} releases"
            )
            if package in php_parity.REQUIRED:
                raise SystemExit(
                    f"{missing}. MixEngine offers {package} on every version it ships, so an "
                    "artifact without it is not one worth publishing."
                )
            print(f"warning: {missing}; this cell will be short of what the others carry",
                  file=sys.stderr)
            continue

        release, url = found
        print(f"borrowing {url}")
        archive = work / f"{package}.zip"
        urllib.request.urlretrieve(url, archive)
        with zipfile.ZipFile(archive) as zipped:
            for member in zipped.infolist():
                if member.is_dir():
                    continue
                name = member.filename.rsplit("/", 1)[-1]
                if name == f"php_{package}.dll":
                    (tree / "ext" / name).write_bytes(zipped.read(member))
                    added.append(f"ext/{name}")
                elif name.startswith(("LICENSE", "COPYING")) or name == "THIRD_PARTY_NOTICES":
                    # Flattened under the package's name: two of these zips carry a licence for a
                    # library they vendored (`liblzf/LICENSE`, `zstd/COPYING`) as well as their own,
                    # and both have to arrive somewhere a reader can tell them apart.
                    label = member.filename.replace("/", "-")
                    (tree / "licenses").mkdir(exist_ok=True)
                    (tree / "licenses" / f"{package}-{label}").write_bytes(zipped.read(member))
                    added.append(f"licenses/{package}-{label}")
        versions[package] = release
        print(f"{package} {release}")

    return versions, added


def imports(deplister: Path, binary: Path) -> set[str]:
    """Which DLLs *binary* names in its import table, lowercased, whether or not they were found."""
    result = subprocess.run(
        [str(deplister), str(binary)], capture_output=True, text=True, timeout=120
    )
    return {
        line.split(",")[0].strip().lower()
        for line in result.stdout.splitlines() if line.strip()
    }


def unreachable(tree: Path, deplister: Path) -> list[Path]:
    """The DLLs beside ``php.exe`` that nothing left in the archive imports, directly or otherwise.

    Deleting an extension leaves its library behind, and that library is not small: dropping
    ``enchant`` orphans ``libenchant2.dll`` and the three GLib DLLs underneath it, 3.0 MB, and
    dropping ``ldap`` and ``imap`` orphans ``libsasl.dll``. A hand-written list of what belongs to
    what would be right for the archive somebody measured and wrong for every other branch, so this
    is computed instead — from ``deplister.exe``, which the publisher ships for exactly this purpose
    and which is itself deleted once it has answered.

    Only the root is swept. ``extras/ssl/legacy.dll`` is in nobody's import table because OpenSSL
    loads its providers by name at run time, and a reachability sweep is simply the wrong instrument
    for that kind of dependency — so it is pointed at the one directory where every DLL is there to
    be linked against. The guard for the rest is the smoke test, which loads **every** extension out
    of the relocated tree: a library deleted in error takes its extension down with it, there,
    rather than on a user's machine.
    """
    local = {path.name.lower(): path for path in tree.glob("*.dll")}
    queue = [path for path in tree.glob("*.exe") if path != deplister]
    queue += sorted((tree / "ext").glob("*.dll"))

    reached: set[str] = set()
    while queue:
        for name in imports(deplister, queue.pop()):
            if name in local and name not in reached:
                reached.add(name)
                queue.append(local[name])
    return [path for name, path in sorted(local.items()) if name not in reached]


def prune(tree: Path, branch: str) -> list[str]:
    """Throw away everything this artifact does not need, and answer with what went.

    Three kinds of thing go, and they are three different arguments:

    *Extensions nobody chose*, by keep-list rather than by delete-list — see
    :data:`php_parity.COMPILED_IN` for why that distinction is the whole robustness of this. Three
    of them cannot even load: ``oci8_19``, ``pdo_oci`` and ``pdo_firebird`` need client libraries
    the publisher does not ship, and a fourth, ``snmp``, creates ``C:\\usr\\snmp\\persist`` as it
    starts.

    *Things that are not part of running PHP*: the import libraries the rule names outright
    (``dev/php8.lib``, ``php8embed.lib``, 1.8 MB), any debug symbols, ``deplister.exe``, the two
    SAPIs above, and the publisher's SBOM — which is the only one of these that would be *wrong*
    rather than merely surplus, because it inventories the archive as published and this recipe is
    about to change it.

    *Libraries left with nothing to serve*, which is a consequence of the first rather than a
    decision of its own.
    """
    keep = set(php_parity.COMPILED_IN) | set(
        php_parity.expected(tuple(int(part) for part in branch.split(".")))
    )
    removed: list[Path] = []

    for module in sorted((tree / "ext").glob("php_*.dll")):
        if module.stem.removeprefix("php_") not in keep:
            removed.append(module)

    survivors = [
        name for name in php_parity.SURPLUS_ON_WINDOWS
        if (tree / "ext" / f"php_{name}.dll").exists()
        and (tree / "ext" / f"php_{name}.dll") not in removed
    ]
    if survivors:
        raise SystemExit(
            f"the keep-list would have shipped {', '.join(survivors)}, which php_parity says this "
            f"row does not carry — one of the two lists has moved and they no longer agree"
        )

    removed += [tree / name for name in ("phpdbg.exe", "php-win.exe") if (tree / name).exists()]
    removed += [
        path for path in sorted(tree.rglob("*"))
        if path.is_file() and path.suffix.lower() in (".lib", ".pdb", ".exp")
    ]
    removed += sorted(path for path in (tree / "extras" / "sbom").rglob("*") if path.is_file())

    for path in removed:
        path.unlink()

    deplister = tree / "deplister.exe"
    if deplister.exists():
        orphaned = unreachable(tree, deplister)
        for path in orphaned:
            print(f"nothing left in the archive imports {path.name}")
            path.unlink()
        removed += orphaned
        deplister.unlink()
        removed.append(deplister)
    else:
        print(
            "warning: no deplister.exe in this archive, so the libraries orphaned by the "
            "extensions above stay in it and the artifact is larger than it should be",
            file=sys.stderr,
        )

    freed = sorted(path.relative_to(tree).as_posix() for path in removed)
    print(f"dropped {len(freed)} file(s): {', '.join(freed)}")
    return freed


def describe(tree: Path, version: str, arch: str, url: str, upstream_hash: str | None,
             pecl: dict[str, str] | None = None,
             added: list[str] | tuple[()] = (), removed: list[str] | tuple[()] = ()) -> dict:
    """What is in the archive, as the daemon will read it.

    Called after :func:`install_extensions` and :func:`prune`, never before, because everything here
    is *measured*: ``provides`` is whatever executables survived, ``extensions`` is whatever is in
    ``ext/`` once the choosing is over. A manifest written from the intent rather than from the tree
    is the thing that made ``upstream.removed`` worth checking in the first place.

    ``extensions.enabled`` is the field this row could not do without and the other kinds do not
    need. Nine extensions are compiled into the Unix builds and are loadable modules here, and no
    Windows build exists with them static — so listing them under ``shared`` beside ``odbc`` said
    "available" about both and "expected" about neither. See :func:`php_parity.enabled_by_default`.
    """
    provides = {path.stem: path.name for path in sorted(tree.glob("*.exe"))}
    if "php" not in provides:
        raise SystemExit("no php.exe in the archive")

    static = json.loads(
        php(tree / "php.exe", "-n", "-r", "echo json_encode(get_loaded_extensions());")[0]
    )
    shared = sorted(p.stem.removeprefix("php_") for p in (tree / "ext").glob("php_*.dll"))
    enabled = php_parity.enabled_by_default(shared)
    print(f"{len(static)} static extension(s), {len(shared)} loadable module(s) in ext/, "
          f"{len(enabled)} of them expected to be on")
    if not shared:
        raise SystemExit(
            f"no php_*.dll under {tree / 'ext'} — the archive is not laid out the way this recipe "
            f"expects, and every dynamic extension would be missing. Contents: "
            f"{sorted(p.name for p in tree.iterdir())[:20]}"
        )
    php_parity.check(
        tuple(int(part) for part in version.split(".")[:2]), static, shared, windows=True
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
        "extensions": {"static": sorted(static), "shared": shared, "enabled": enabled},
    }
    if requires:
        manifest["requires"] = requires
    if pecl:
        # The same field `php_legacy_unix.py` writes for the same reason: which PECL release each
        # extension came from. There it is the release that was compiled and here it is the release
        # that was downloaded, and a reader comparing two cells of one version wants both spelled
        # the same way rather than one in a manifest and one in a build log.
        manifest["recipe"] = "windows.php.net build, repacked; " + ", ".join(
            f"{name} {release}" for name, release in sorted(pecl.items())
        ) + f" from PECL, verified against {PECL_VERIFIED_AGAINST}"
    return borrow.declare(tree, manifest, added, removed)


def smoke(tree: Path, version: str, manifest: dict) -> dict:
    """Run the artifact from somewhere it has never been, and make it load everything it carries.

    ``php -v`` on its own would pass even with a broken ``extension_dir`` — the built-in extensions
    are static and never consult it — and the official build bakes ``C:\\php\\ext``, an absolute path
    that exists on nobody's machine. So the test that matters is the second one.

    **Every** extension, not the first one that loads. This half used to stop at the first, which
    was enough to prove ``extension_dir`` was honoured and nothing else, while the Unix half proved
    all of them — the exact drift `php_smoke` exists to name, with `smoke.loaded_extension` meaning
    a weaker thing on one cell than on the other five. It is load-bearing rather than tidy, too:
    :func:`prune` deletes libraries by reachability, and an extension whose DLL went with them is
    only visible here.
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

    # Through a generated php.ini rather than -d, because that is the mechanism the daemon will use
    # and a smoke test that proves a different one proves nothing. `php_smoke.loads` writes it, and
    # every value in it is quoted — not a style choice. PHP's ini parser refuses ``~`` in an
    # unquoted value -- ``syntax error, unexpected '~'`` -- and Windows puts one in every 8.3 short
    # path: ``RUNNER~1``, ``PROGRA~1``, and the profile directory of any user whose name is not
    # plain ASCII. Unquoted, the file is rejected from that line on and *every* extension silently
    # fails to load while ``php -v`` keeps answering perfectly. Quoted, it loads. This holds for
    # MixEngine's generated config too, not only for this test.
    loaded, refused = [], []
    ini = elsewhere.parent / "php.ini"
    for candidate in manifest["extensions"]["shared"]:
        ok, said, complaint = php_smoke.loads(
            elsewhere / "php.exe", elsewhere / "ext", candidate, ini, windows=True
        )
        if ok:
            loaded.append(candidate)
            continue
        refused.append(candidate)
        print(f"{candidate} did not load: {said!r}", file=sys.stderr)
        for line in complaint.splitlines():
            print(f"  {line}", file=sys.stderr)
    print(f"loaded {len(loaded)} extension(s) from the relocated ext/, through a generated php.ini")

    if refused:
        raise SystemExit(
            f"in the archive and cannot be loaded: {', '.join(refused)}. Either extension_dir is "
            "not being honoured — in which case every dynamic extension would fail on a user's "
            "machine — or pruning took a library one of these needed."
        )

    shutil.rmtree(elsewhere.parent.parent, ignore_errors=True)
    return {"relocated": True, "ran": ran, "loaded_extensions": loaded}


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

    # The branch and the compiler tag come out of the archive that was actually downloaded rather
    # than out of a table, because both are things the publisher decides per release: the tag is
    # `vc14` on 7.1 and `vs17` on 8.4, and it is spelled inconsistently enough across branches
    # (`VC15` on 7.2, `vc15` on 7.4) that a table would have to be extended by hand forever.
    branch = ".".join(args.version.split(".")[:2])
    tag = re.search(r"-(vc\d+|vs\d+)-", url, re.IGNORECASE)
    if not tag:
        raise SystemExit(f"no compiler tag in {url}, so nothing can be matched against it on PECL")
    zip_arch = {"x86_64": "x64"}[args.arch]

    renamed, was = rename_aliases(tree)
    pecl, added = install_extensions(tree, branch, tag.group(1).lower(), zip_arch, work)
    removed = prune(tree, branch) + was
    added += renamed

    manifest = describe(tree, args.version, args.arch, url, upstream_hash, pecl, added, removed)
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
        f"{len(manifest['extensions']['shared'])} shared, "
        f"{len(manifest['extensions']['enabled'])} expected to be enabled; "
        f"all {len(manifest['smoke']['loaded_extensions'])} loaded after relocation"
    )


if __name__ == "__main__":
    main()
