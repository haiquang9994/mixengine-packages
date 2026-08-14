#!/usr/bin/env python3
"""Check an index before it is signed.

A signature says "this is the file we published". It says nothing about whether the file is right,
and once signed a wrong index is a wrong index every client will happily trust. So the checks that
matter run first.

Three of them cannot be expressed in JSON Schema and are the ones most worth having:

1. **No version was lost.** The index is cumulative by promise. Comparing against the published one
   is the only thing that can catch a generator bug that silently drops a package.
2. **Every URL is ours.** An upstream URL passes every structural check and then breaks years later,
   for exactly the old pinned versions the promise was made about.
3. **Every artifact is reachable and hashes to what is claimed** — optional, because it costs a
   download of the whole index, and worth it before a release.

Needs ``jsonschema`` for the structural half; the invariants above run without it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

OURS = "github.com/haiquang9994/mixengine-packages"


def load(source: str) -> dict:
    if source.startswith(("http://", "https://")):
        with urllib.request.urlopen(source, timeout=60) as response:
            return json.loads(response.read())
    return json.loads(Path(source).read_text(encoding="utf-8"))


def structural(index: dict, schema: Path) -> list[str]:
    try:
        import jsonschema
    except ImportError:
        print("jsonschema not installed; skipping the structural half", file=sys.stderr)
        return []
    validator = jsonschema.Draft202012Validator(
        json.loads(schema.read_text(encoding="utf-8"))
    )
    return [
        f"{'/'.join(str(p) for p in error.path)}: {error.message}"
        for error in validator.iter_errors(index)
    ]


def invariants(index: dict, previous: dict | None) -> list[str]:
    problems = []
    seen = set()

    for package in index["packages"]:
        key = (package["kind"], package["version"])
        if key in seen:
            problems.append(f"{key[0]} {key[1]} appears twice")
        seen.add(key)

        platforms = set()
        for artifact in package["artifacts"]:
            where = (artifact["os"], artifact["arch"])
            if where in platforms:
                problems.append(f"{key[0]} {key[1]} has two {where[0]}/{where[1]} artifacts")
            platforms.add(where)

            if OURS not in artifact["url"]:
                problems.append(
                    f"{key[0]} {key[1]} {where[0]}/{where[1]} points outside our mirror: "
                    f"{artifact['url']}"
                )
            if "php" in package["kind"] and artifact["os"] != "windows":
                if "php-fpm" not in artifact["provides"]:
                    problems.append(
                        f"php {key[1]} {where[0]} provides no php-fpm; a site could not be served"
                    )

    if previous:
        lost = {(p["kind"], p["version"]) for p in previous["packages"]} - seen
        for kind, version in sorted(lost):
            problems.append(f"{kind} {version} was in the published index and is not in this one")

    return problems


def reachable(index: dict) -> list[str]:
    problems = []
    for package in index["packages"]:
        for artifact in package["artifacts"]:
            try:
                with urllib.request.urlopen(artifact["url"], timeout=300) as response:
                    digest = hashlib.sha256()
                    for block in iter(lambda: response.read(1 << 20), b""):
                        digest.update(block)
            except Exception as error:  # noqa: BLE001 — any failure is the same failure here
                problems.append(f"{artifact['url']}: {error}")
                continue
            if digest.hexdigest() != artifact["sha256"]:
                problems.append(
                    f"{artifact['url']}: hashes to {digest.hexdigest()}, index says "
                    f"{artifact['sha256']}"
                )
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("index", help="path or URL of the index to check")
    parser.add_argument("--previous", help="the published index it must not have lost anything from")
    parser.add_argument("--schema", type=Path, default=Path("schema/index.schema.json"))
    parser.add_argument("--fetch", action="store_true",
                        help="download every artifact and check its hash")
    args = parser.parse_args()

    index = load(args.index)
    previous = load(args.previous) if args.previous else None

    problems = structural(index, args.schema) + invariants(index, previous)
    if args.fetch:
        problems += reachable(index)

    for problem in problems:
        print(f"FAIL {problem}")
    if problems:
        raise SystemExit(f"{len(problems)} problem(s)")

    artifacts = sum(len(p["artifacts"]) for p in index["packages"])
    print(f"ok: {len(index['packages'])} package(s), {artifacts} artifact(s)")


if __name__ == "__main__":
    main()
