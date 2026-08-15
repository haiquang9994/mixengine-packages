#!/usr/bin/env python3
"""Generate the signed index from the artifacts that exist.

The index is **cumulative and never loses a version**: a blueprint pinning PHP 8.1.29 has to keep
working years after 8.1 stopped being built. So this reads whatever index is already published,
merges the new artifacts into it, and writes the union. A run that produced nothing still writes a
valid index — the same one, with a new timestamp.

That is also why it refuses to emit an upstream URL. Every ``url`` points at a release asset of this
repository, because upstream hosts prune and the promise above would then be a lie for exactly the
old versions it was made about.

Python 3 stdlib only. Signing is minisign's job, not this script's — see ``publish-index.yml``.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

SCHEMA = 1
ARCHIVE_SUFFIXES = (".zip", ".tar.zst", ".tar.gz")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_previous(source: str | None) -> dict:
    """Read the index this run is extending, from a path or a URL.

    A missing one is the first run and not an error. A *malformed* one is an error and must stay
    one: silently starting over would drop every version already published, which is the single
    thing this file promises never to happen.
    """
    if not source:
        return {"schema": SCHEMA, "packages": []}
    try:
        if source.startswith(("http://", "https://")):
            with urllib.request.urlopen(source, timeout=60) as response:
                raw = response.read()
        else:
            path = Path(source)
            if not path.exists():
                print(f"no previous index at {source}; this is the first one", file=sys.stderr)
                return {"schema": SCHEMA, "packages": []}
            raw = path.read_bytes()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            print(f"no previous index at {source}; this is the first one", file=sys.stderr)
            return {"schema": SCHEMA, "packages": []}
        raise
    previous = json.loads(raw)
    if previous.get("schema") != SCHEMA:
        raise SystemExit(
            f"the published index is schema {previous.get('schema')}, this tool writes {SCHEMA}; "
            "merging across a schema change has to be done deliberately"
        )
    return previous


def collect(directory: Path, base_url: str) -> list[dict]:
    """Turn every ``<archive>`` + ``<archive>.json`` pair in *directory* into an index artifact."""
    found = []
    for manifest_path in sorted(directory.glob("*.json")):
        archive = manifest_path.with_suffix("")
        if not archive.name.endswith(ARCHIVE_SUFFIXES) or not archive.exists():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        artifact = {
            "os": manifest["os"],
            "arch": manifest["arch"],
            "url": f"{base_url.rstrip('/')}/{manifest['kind']}-{manifest['version']}/{archive.name}",
            "sha256": sha256(archive),
            "size": archive.stat().st_size,
            "provides": manifest["provides"],
        }
        for optional in ("requires", "extension_dir", "extensions"):
            if optional in manifest:
                artifact[optional] = manifest[optional]

        if not manifest.get("smoke", {}).get("relocated"):
            raise SystemExit(
                f"{archive.name} was never run from a directory it had been moved to. "
                "Nothing goes in the index that has not been proven to relocate."
            )
        found.append((manifest["kind"], manifest["version"], artifact))
    return found


def merge(index: dict, found: list, eol: dict, channel: str) -> dict:
    """Add the new artifacts, replacing an artifact of the same kind/version/os/arch in place.

    Replacing is allowed — a rebuild of the same version is how a broken artifact gets fixed.
    Removing is not, which is why nothing here ever deletes.
    """
    packages = {(p["kind"], p["version"]): p for p in index.get("packages", [])}

    for kind, version, artifact in found:
        package = packages.setdefault(
            (kind, version), {"kind": kind, "version": version, "channel": channel, "artifacts": []}
        )
        # What a release *line* is differs per runtime, and it is the line that has an end-of-life
        # date: PHP's is 8.3, Node.js's is 22. Both spellings are tried, narrowest first, so
        # `data/eol.json` states each runtime's lines in the shape that runtime actually uses rather
        # than in a shape this file imposes on all of them.
        lines = (".".join(version.split(".")[:2]), version.split(".")[0])
        for line in lines:
            if line in eol.get(kind, {}):
                package["eol"] = eol[kind][line]
                break

        package["artifacts"] = [
            existing
            for existing in package["artifacts"]
            if (existing["os"], existing["arch"]) != (artifact["os"], artifact["arch"])
        ] + [artifact]
        package["artifacts"].sort(key=lambda a: (a["os"], a["arch"]))

    def order(item):
        (kind, version) = item
        return (kind, [int(part) if part.isdigit() else part for part in version.split(".")])

    return {
        "schema": SCHEMA,
        "generated_at": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "packages": [packages[key] for key in sorted(packages, key=order)],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, default=Path("dist"),
                        help="directory of <archive> and <archive>.json pairs")
    parser.add_argument("--previous", help="path or URL of the index being extended")
    parser.add_argument("--base-url", required=True,
                        help="where release assets live, e.g. "
                             "https://github.com/haiquang9994/mixengine-packages/releases/download")
    parser.add_argument("--eol", type=Path, default=Path("data/eol.json"))
    parser.add_argument("--channel", default="stable", choices=["stable", "rc", "beta"])
    parser.add_argument("--out", type=Path, default=Path("dist/index.json"))
    args = parser.parse_args()

    if "windows.php.net" in args.base_url or "nodejs.org" in args.base_url:
        raise SystemExit("the index must point at our own mirror; upstreams prune")

    eol = json.loads(args.eol.read_text(encoding="utf-8")) if args.eol.exists() else {}
    found = collect(args.artifacts, args.base_url) if args.artifacts.is_dir() else []
    index = merge(load_previous(args.previous), found, eol, args.channel)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(index, indent=2, sort_keys=False) + "\n", encoding="utf-8")

    artifacts = sum(len(p["artifacts"]) for p in index["packages"])
    print(f"added {len(found)} artifact(s)")
    print(f"wrote {args.out}: {len(index['packages'])} package(s), {artifacts} artifact(s)")


if __name__ == "__main__":
    main()
