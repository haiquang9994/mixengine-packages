#!/usr/bin/env python3
"""Check that every asset the published index names is still there, and still the bytes it claims.

The index is a promise about the past. A blueprint pinning PHP 8.1.29 has to keep installing years
after 8.1 stopped being built, which makes every release asset this repository has ever uploaded
load-bearing — not archived, not historical, *in use*. Nothing in GitHub knows that. A release can be
deleted from a web page in two clicks, and until this file existed the only thing that would have
noticed was a user whose install failed.

**Deleting one is not recoverable, and that is the part worth being clear about.** The obvious reading
is that a lost artifact can be rebuilt from the recipe that made it, and it can — but not to the same
bytes. These are compressed archives packed at a different minute, by a different runner, from
sources that may themselves have moved; the sha256 in the index will not match, and the index is
signed. Restoring means publishing a *different* artifact under the same version and re-signing an
index that now describes it differently, which every client that pinned the old hash is entitled to
read as tampering. So the recovery is a break, and the only real defence is to find out fast.

Two claims, and they cost three orders of magnitude apart:

* **Still there.** One ``HEAD`` per asset. Four hundred requests, a few seconds, no bandwidth.
* **Still the same bytes.** A download of the whole archive. Six gigabytes today, and one more
  version of one more runtime adds six cells to that.

So the first is done for everything, every run, and the second for a fixed *fraction* of the archive
— which is the choice worth explaining. A count ("hash twenty of them") keeps the run cheap and lets
coverage decay as the archive grows; a fraction keeps coverage constant and lets the run grow. The
promise being checked is about coverage, so coverage is what is held fixed: with the default eight
slices, every asset is hashed within eight weeks no matter how many there are, and the bill grows the
same way the archive does. Which slice an asset falls in is derived from its URL rather than from its
position in the index, so a version published mid-cycle joins one fixed slice and is hashed within
the cycle instead of shuffling everything else out of the one it was in.

The manifests are checked too, and they are the half nothing else looks at: ``publish-index.yml``
does not rebuild the index from this repository's memory, it downloads every release asset and reads
the ``<archive>.json`` beside each one. A deleted sidecar leaves the archive perfectly intact and
quietly removes that version from every index generated afterwards. It is not named in the index —
it is the input the index is made *from* — so it is derived here rather than read.

Separate from ``verify.py`` on purpose: that one checks a candidate index before it is signed, and a
failure there means "do not publish this". This one checks the index that is already published and
already trusted, and a failure means "something outside this repository changed under us".

Python 3 stdlib only, like everything else here.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PUBLISHED = "https://github.com/haiquang9994/mixengine-packages/releases/download/index/index.json"
SLICES = 8
TIMEOUT = 300


class _KeepHead(urllib.request.HTTPRedirectHandler):
    """Follow a redirect without turning a ``HEAD`` into a ``GET``.

    Every asset URL is a redirect: ``github.com/.../releases/download/...`` answers 302 and the bytes
    come from a signed ``release-assets.githubusercontent.com`` URL. ``urllib`` does not forward the
    original request, it builds a new one, and whether the method survives that is a property of the
    interpreter — recent CPython carries ``HEAD`` across, older ones rebuild it as a ``GET``. The
    difference between those two is the difference between a check that costs nothing and a check
    that downloads six gigabytes, silently, while reporting exactly the same thing. Reasserting the
    method is a no-op where it was already right and the whole point where it was not.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and req.get_method() == "HEAD":
            redirected.method = "HEAD"
        return redirected


_OPENER = urllib.request.build_opener(_KeepHead)


def reach(url: str, method: str = "HEAD", attempts: int = 3):
    """Open *url*, retrying the answers that are not answers about permanence.

    ``borrow.fetch`` retries the network and never retries a status, because there a status is
    upstream stating a fact about what it publishes. Here the rule has to be different in one place:
    a 503 or a 429 from a CDN is not GitHub saying the asset is gone, it is GitHub saying *ask
    later*, and a weekly job that cries deletion over a rate limit is a weekly job people stop
    reading. A 404 is still an answer and is still not retried — it is the answer this exists to
    find.
    """
    for attempt in range(1, attempts + 1):
        try:
            return _OPENER.open(urllib.request.Request(url, method=method), timeout=TIMEOUT)
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == attempts:
                raise
            said = f"HTTP {error.code}"
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
            if attempt == attempts:
                raise
            said = str(error)
        print(f"{url}: {said} (attempt {attempt} of {attempts})", file=sys.stderr)
        time.sleep(5 * attempt)
    raise AssertionError("unreachable")


def load(source: str) -> dict:
    if source.startswith(("http://", "https://")):
        with urllib.request.urlopen(source, timeout=60) as response:
            return json.loads(response.read())
    return json.loads(Path(source).read_text(encoding="utf-8"))


def assets(index: dict) -> list[dict]:
    """Every URL the archive has to keep answering, which is more than the index lists.

    Two per artifact. The archive is named by the index and carries a size and a hash to be checked
    against; the manifest is named by nothing and carries neither, so all that can be asked of it is
    whether it is still there — which is the only question it fails at.
    """
    found = []
    for package in index["packages"]:
        for artifact in package["artifacts"]:
            what = f"{package['kind']} {package['version']} {artifact['os']}/{artifact['arch']}"
            found.append({
                "what": what,
                "url": artifact["url"],
                "size": artifact["size"],
                "sha256": artifact["sha256"],
            })
            found.append({
                "what": f"{what} manifest",
                "url": artifact["url"] + ".json",
                "size": None,
                "sha256": None,
            })
    return found


def slice_of(url: str, slices: int) -> int:
    """Which week of the rotation hashes this asset — a fact about the URL, and nothing else.

    Not ``hash()``: that is salted per process and would pick a different set every run, so an asset
    could go years without being hashed while the log claimed a complete cycle.
    """
    return int(hashlib.sha256(url.encode()).hexdigest()[:8], 16) % slices


def present(asset: dict) -> str | None:
    """Ask whether the asset is there, and whether it is still the size the index promised.

    The size is free — a ``HEAD`` answers with it — and it is worth reading rather than discarding.
    It cannot prove the bytes, but it is the one thing that separates "this URL answers" from "this
    URL answers with what we published", and the realistic corruption here is not cosmic rays: it is
    a build workflow re-run against an existing tag, uploading a rebuilt archive over the old one
    with ``--clobber``. That artifact is a different file at the same URL, and its length almost
    never lands on the byte the index recorded.
    """
    try:
        with reach(asset["url"]) as response:
            stated = response.headers.get("Content-Length")
    except urllib.error.HTTPError as error:
        return f"{asset['what']}: HTTP {error.code} — {asset['url']}"
    except Exception as error:  # noqa: BLE001 — unreachable is unreachable, however it got there
        return f"{asset['what']}: {type(error).__name__}: {error} — {asset['url']}"

    if asset["size"] is None or stated is None:
        return None
    if int(stated) != asset["size"]:
        return (f"{asset['what']}: the index says {asset['size']:,} bytes and the release now "
                f"holds {int(stated):,} — {asset['url']}")
    return None


def unchanged(asset: dict) -> str | None:
    """Download the whole thing and check it hashes to what was signed.

    Streamed and discarded rather than saved: the largest artifact is 78 MB today, and a runner that
    kept a slice of the archive on disk would start failing on space for a reason that has nothing to
    do with what is being checked.
    """
    digest = hashlib.sha256()
    read = 0
    try:
        with reach(asset["url"], method="GET") as response:
            for block in iter(lambda: response.read(1 << 20), b""):
                digest.update(block)
                read += len(block)
    except Exception as error:  # noqa: BLE001 — same as above, and `present` already said which
        return f"{asset['what']}: {type(error).__name__}: {error} — {asset['url']}"

    if digest.hexdigest() != asset["sha256"]:
        return (f"{asset['what']}: hashes to {digest.hexdigest()}, the signed index says "
                f"{asset['sha256']} — {asset['url']}")
    if read != asset["size"]:
        return f"{asset['what']}: {read:,} bytes downloaded, the index says {asset['size']:,}"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", default=PUBLISHED,
                        help="path or URL of the index to check the archive against")
    parser.add_argument("--slices", type=int, default=SLICES,
                        help="how many runs a full pass over the hashes takes; 1 hashes everything "
                             "in one run, 0 hashes nothing and only checks that the assets are there")
    parser.add_argument("--slice", type=int, default=None,
                        help="which slice to hash (default: this ISO week, so a weekly job walks "
                             "the whole archive on its own)")
    parser.add_argument("--jobs", type=int, default=8,
                        help="how many assets to ask about at once")
    arguments = parser.parse_args()

    index = load(arguments.index)
    everything = assets(index)
    if not everything:
        raise SystemExit(f"{arguments.index} names no artifacts at all; that is not an archive to "
                         f"check, it is a generator bug")

    print(f"{arguments.index}")
    print(f"generated {index.get('generated_at', 'at an unstated time')}, "
          f"{len(index['packages'])} package(s), {len(everything)} asset(s) to account for")

    with concurrent.futures.ThreadPoolExecutor(max_workers=arguments.jobs) as pool:
        # `map` keeps the order of the input, which is what lets the answers be matched back to the
        # assets they are about rather than read out of the messages.
        answers = list(pool.map(present, everything))
    missing = [problem for problem in answers if problem]
    gone = {asset["url"] for asset, problem in zip(everything, answers) if problem}
    print(f"\npresent: {len(everything) - len(missing)} of {len(everything)}")

    drifted: list[str] = []
    if arguments.slices > 0:
        week = datetime.datetime.now(datetime.timezone.utc).isocalendar().week
        chosen = (week if arguments.slice is None else arguments.slice) % arguments.slices
        # Only the archives: a manifest has no hash in the index to be checked against, and
        # `present` has already asked it the only question it can answer. Nothing already reported
        # missing is downloaded again either — it would fail twice for one reason, and the count at
        # the end is meant to say how many things are wrong, not how many checks noticed.
        due = [asset for asset in everything
               if asset["sha256"] and asset["url"] not in gone
               and slice_of(asset["url"], arguments.slices) == chosen]

        print(f"\nhashing slice {chosen} of {arguments.slices} (ISO week {week}): "
              f"{len(due)} archive(s), "
              f"{sum(asset['size'] for asset in due) / (1 << 30):.2f} GiB")
        for number, asset in enumerate(due, start=1):
            print(f"  [{number}/{len(due)}] {asset['what']}", flush=True)
            problem = unchanged(asset)
            if problem:
                drifted.append(problem)

    problems = missing + drifted
    for problem in problems:
        print(f"FAIL {problem}")
    if problems:
        raise SystemExit(
            f"{len(problems)} problem(s): {len(missing)} asset(s) the index names and the releases "
            f"do not hold, {len(drifted)} that are no longer the bytes they were signed as"
        )
    # Said separately because they are different amounts of knowledge, and a run that hashed nothing
    # claiming everything is unchanged is the sentence a reader would quote back later.
    print("\nevery asset the index names is present" + (
        ", and every one hashed this run is the bytes it was signed as"
        if arguments.slices > 0 else "; nothing was hashed this run"
    ))


if __name__ == "__main__":
    main()
