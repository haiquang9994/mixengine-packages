#!/usr/bin/env python3
"""Where every date in ``data/eol.json`` came from, and the thing that keeps it true.

``data/eol.json`` is the only claim this repository makes that is not about bytes. Everything else
in an artifact is measured — a digest, a load command, a version a binary printed about itself — and
can be re-measured from the archive years later. "Upstream stops patching this line on the 30th of
April 2027" cannot. It is a transcription, and until this file existed four of the six kinds were
transcribed **by hand from a web page, with nothing checking them**.

That is not a hypothetical worry. The first run of :func:`check` found, in Ruby alone:

* **3.2 was wrong** — written 2026-03-31, upstream says 2026-04-01. Off by one day, from a schedule
  page that says "March" in prose and 1 April in its data.
* **3.4 and 4.0 were invented.** Upstream states no end date for either. Both had been extrapolated
  from Ruby's habit of ending a line on 31 March about four years on, which is a good guess and is
  still this repository's opinion printed in a field that means "upstream says".
* **3.3 was right by luck** — the number matches upstream's, but upstream files it under
  ``expected_eol_date`` rather than ``eol_date``, which is a different claim.

PHP, Node.js, Python, MariaDB and PostgreSQL were all correct to the day. That is the shape of this
class of bug: it is not that hand-transcription is usually wrong, it is that nothing tells you which
of the forty-four entries is the one that is.

**Six publishers, six machine-readable documents, and no mirror.** The roadmap expected two of these
to need ``endoflife.date``. None of them do:

===========  =====================================================================================
``php``      ``php.net/releases/branches.php`` — JSON, no query string needed, 24 branches with
             ``security_support_end``. php.net renders ``supported-versions.php`` from the same data
             and this is the machine half of it.
``node``     ``nodejs/Release``'s ``schedule.json`` — the file the Node.js release working group
             edits when it moves a date, keyed ``v22`` with an ``end``.
``python``   ``peps.python.org/api/release-cycle.json`` — the document the developer's guide itself
             consumes; ``devguide/_tools/generate_release_cycle.py`` reads this exact URL to draw the
             chart on ``devguide.python.org/versions/``.
``ruby``     ``ruby/www.ruby-lang.org``'s ``_data/branches.yml`` — the data file the branches page is
             generated from.
``mariadb``  ``downloads.mariadb.org/rest-api/mariadb`` — already the catalogue ``mariadb.py``
             resolves a version against; ``release_eol_date`` per series.
``postgres`` ``postgresql.org/versions.json`` — already the catalogue ``postgres.py`` resolves a
             version against; ``eolDate`` per major.
===========  =====================================================================================

Four decisions this file is answerable for.

*The check runs on a clock, not on a pack, because the MariaDB pattern does not generalise.*
``mariadb.py`` prints the end-of-life date it saw on every run, and that works because the date
arrives in the same document the download does — it costs nothing and it catches a moved schedule
the next time that series is packed. But an end-of-life date does not change when a version is
packed; it changes on a calendar. And the lines closest to their date are precisely the lines nobody
is packing any more: Ruby 3.2 reached its end in April 2026 and would never have been repacked, so
the wrong date above would have sat in the index until a human happened to look. So the check is a
scheduled workflow over the whole file, and per-recipe printing (:func:`announce`) is kept as the
cheap half — it reads what is written down, makes no network call, and cannot fail a build.

*The whole of each publisher's document is transcribed, not the lines this repository offers.* The
file used to hold a curated 38 entries and the curation was the problem: a subset cannot be checked,
because nothing distinguishes a line deliberately left out from one forgotten. Transcribing the
document in full makes the check an **equality**, which is the only kind of check that catches an
omission. It also costs nothing — ``mkindex.py`` reads the lines it needs and ignores the rest — and
it dates an artifact of PHP 5.6 correctly if one is ever republished, without anybody deciding to.

*Ruby falls back to ``expected_eol_date``, and the fallback is named in the output.* Ruby is the only
one of the six that does not state a future date: ``eol_date`` is filled in when a branch actually
ends, and a branch in security maintenance carries ``expected_eol_date`` instead. Refusing the
expectation would leave Ruby 3.3 undated while PHP 8.5 carries a date four years further out — and
PHP's, Node's and Python's future dates are *also* plans that can move, which is what this check is
for. So an expectation is transcribed and the field it came from is printed beside it. What is not
transcribed is the case upstream is silent on: Ruby 3.4 and 4.0 have no date here, per line rather
than per kind, for the reason ``data/eol.json`` gives about Caddy.

*Ruby's document is YAML and there is a twenty-line reader for it rather than a dependency or a
mirror.* Python's standard library has no YAML, this repository installs nothing on a runner, and
the alternative — ``endoflife.date`` — is a third party restating what ruby-lang.org already
publishes. The file it publishes is a flat list of ``key: value``, which is a shape that can be read
honestly; :func:`_yaml` reads exactly that shape and **raises on anything else**, so the day
ruby-lang.org nests something is the day this stops rather than the day it starts guessing.

Python 3 stdlib only, by policy: this runs on a GitHub runner with nothing installed.
"""

from __future__ import annotations

import argparse
import calendar
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import borrow  # noqa: E402  — siblings, and this directory is not importable as a package

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "eol.json"

# raw.githubusercontent serves the two documents whose publisher keeps them in a git repository
# rather than behind a page. Both are the publisher's own repository — nodejs/Release is the release
# working group's, ruby/www.ruby-lang.org is the site's — which is the same trade as reading
# php.net's endpoint, not a mirror.
PHP = "https://www.php.net/releases/branches.php"
NODE = "https://raw.githubusercontent.com/nodejs/Release/main/schedule.json"
PYTHON = "https://peps.python.org/api/release-cycle.json"
RUBY = "https://raw.githubusercontent.com/ruby/www.ruby-lang.org/master/_data/branches.yml"
MARIADB = "https://downloads.mariadb.org/rest-api/mariadb"
POSTGRES = "https://www.postgresql.org/versions.json"

# php.net answers 403 to `Python-urllib/3.x` on some paths, the way download.redis.io does, and the
# fix is the same one `borrow.fetch` already takes an argument for.
AGENT = {"User-Agent": "mixengine-packages (+https://github.com/haiquang9994/mixengine-packages)"}


def _get(url: str) -> bytes:
    return borrow.fetch(url, timeout=60, headers=AGENT)


def _json(url: str):
    return json.loads(_get(url))


def _day(text: str, *, last: bool = True) -> str:
    """Normalise a publisher's date to ``YYYY-MM-DD``.

    Two shapes arrive. PHP's and MariaDB's are timestamps and the time is noise. Python's are
    ``YYYY-MM`` for any line that has not ended yet, and the rounding is not this file's invention:
    ``devguide/_tools/generate_release_cycle.py`` resolves the same field to the last day of the
    month before drawing it, so a line stated as ``2030-10`` is supported through 31 October 2030.
    """
    text = text.strip()
    if len(text) == len("YYYY-MM"):
        year, month = (int(part) for part in text.split("-"))
        day = calendar.monthrange(year, month)[1] if last else 1
        return f"{year:04d}-{month:02d}-{day:02d}"
    return text[:10]


def _yaml(text: str) -> list[dict[str, str]]:
    """Read exactly the shape ``ruby/www.ruby-lang.org``'s ``_data/branches.yml`` is written in.

    A list of mappings, one scalar per key, two-space indentation, ``#`` comments on their own line.
    Anything else raises: this is a reader for one known file, not a YAML parser, and the difference
    matters because a parser that guesses would silently mis-date every Ruby in the index.
    """
    entries: list[dict[str, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("- "):
            entries.append({})
            body = line[2:]
        elif line.startswith("  ") and entries:
            body = line[2:]
            if body.startswith(" "):
                raise SystemExit(f"{RUBY}:{number}: nested — this reader only knows flat entries")
        else:
            raise SystemExit(f"{RUBY}:{number}: not a flat list of mappings: {line!r}")
        key, separator, value = body.partition(":")
        if not separator or not key.strip():
            raise SystemExit(f"{RUBY}:{number}: not a `key: value` line: {line!r}")
        entries[-1][key.strip()] = value.strip()
    return entries


# ---------------------------------------------------------------------------------------------
# One reader per publisher. Each returns {line: (date or None, the field the date came from)}, and
# the two halves of that are both load-bearing.
#
# The *field* is carried through so the check can print what was read, which is the whole difference
# between a transcription and a number somebody typed.
#
# The *None* is the distinction that caught Ruby 3.4. A line a publisher lists and states no date
# for, and a line a publisher has never heard of, look identical once the undated ones are dropped —
# and they mean opposite things. The first is upstream saying "this line has not ended"; a date
# written against it can only have come from this repository, and is deleted. The second is upstream
# having pruned a dead line from its document; the date written against it is the last thing
# upstream ever said, an artifact of that line may still be in the index, and it is kept.
# ---------------------------------------------------------------------------------------------


def _read_php() -> dict[str, tuple[str | None, str]]:
    return {
        branch["branch"]: (_day(branch["security_support_end"])
                           if branch.get("security_support_end") else None, "security_support_end")
        for branch in _json(PHP)
    }


def _read_node() -> dict[str, tuple[str | None, str]]:
    return {
        line.lstrip("v"): (_day(schedule["end"]) if schedule.get("end") else None, "end")
        for line, schedule in _json(NODE).items()
    }


def _read_python() -> dict[str, tuple[str | None, str]]:
    return {
        line: (_day(cycle["end_of_life"]) if cycle.get("end_of_life") else None, "end_of_life")
        for line, cycle in _json(PYTHON).items()
    }


def _read_ruby() -> dict[str, tuple[str | None, str]]:
    found: dict[str, tuple[str | None, str]] = {}
    for branch in _yaml(_get(RUBY).decode("utf-8")):
        name = branch.get("name")
        if not name:
            raise SystemExit(f"{RUBY}: an entry has no name")
        found[name] = (None, "eol_date")
        for field in ("eol_date", "expected_eol_date"):
            if branch.get(field):
                found[name] = (_day(branch[field]), field)
                break
    return found


def _read_mariadb() -> dict[str, tuple[str | None, str]]:
    return {
        series["release_id"]: (_day(series["release_eol_date"])
                               if series.get("release_eol_date") else None, "release_eol_date")
        for series in _json(MARIADB)["major_releases"]
    }


def _read_postgres() -> dict[str, tuple[str | None, str]]:
    return {
        str(major["major"]): (_day(major["eolDate"]) if major.get("eolDate") else None, "eolDate")
        for major in _json(POSTGRES)
    }


SOURCES = {
    "php": (PHP, _read_php),
    "node": (NODE, _read_node),
    "python": (PYTHON, _read_python),
    "ruby": (RUBY, _read_ruby),
    "mariadb": (MARIADB, _read_mariadb),
    "postgres": (POSTGRES, _read_postgres),
}


def published(kind: str) -> dict[str, tuple[str | None, str]]:
    """Every line *kind*'s publisher lists today, dated where the publisher dates it."""
    url, reader = SOURCES[kind]
    stated = reader()
    if not any(date for date, _ in stated.values()):
        raise SystemExit(f"{url} stated no dates at all; that is a format change, not an answer")
    return stated


# ---------------------------------------------------------------------------------------------
# The written-down half, and the two things that read it.
# ---------------------------------------------------------------------------------------------


def read(path: Path = DATA) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def order(line: str) -> tuple:
    """Sort ``3.9`` before ``3.10``, and put anything unnumbered last rather than crashing."""
    parts = line.split(".")
    return (0, tuple(int(part) for part in parts)) if all(p.isdigit() for p in parts) \
        else (1, (), line)


def lines(version: str) -> tuple[str, ...]:
    """The spellings a version's release *line* can have, narrowest first.

    PHP's line is 8.3 and Node.js's is 22, so both are tried and each publisher's own spelling wins.
    Shared with ``mkindex.py`` rather than written twice, because a lookup rule implemented in two
    places is a lookup rule that will one day disagree with itself over exactly one runtime.
    """
    parts = version.split(".")
    return (".".join(parts[:2]), parts[0])


def dated(table: dict, kind: str, version: str) -> str | None:
    stated = table.get(kind, {})
    for line in lines(version):
        if line in stated:
            return stated[line]
    return None


def announce(kind: str, version: str, path: Path = DATA) -> str | None:
    """Print what is written down about *version*'s line. No network, and never raises.

    This is the half of the job that belongs in a recipe: a build log should say whether the thing
    being packed is still patched upstream. It deliberately does *not* check the publisher — that is
    :func:`check`'s job, on a schedule — so a packaging run never fails over a web request.
    """
    try:
        date = dated(read(path), kind, version)
    except (OSError, ValueError) as error:  # a broken data file must not fail a build
        print(f"could not read {path}: {error}", file=sys.stderr)
        return None
    if date is None:
        print(f"{kind} {version}: upstream states no end-of-life date for this line")
        return None
    today = datetime.date.today().isoformat()
    if date < today:
        print(f"{kind} {version}: upstream stopped patching this line on {date}")
    else:
        print(f"{kind} {version}: upstream supports this line until {date}")
    return date


# ---------------------------------------------------------------------------------------------
# check and update
# ---------------------------------------------------------------------------------------------


def check(table: dict, kinds: list[str]) -> list[str]:
    """Compare every written date against its publisher. Returns the problems, worst first."""
    problems, notes = [], []
    for kind in kinds:
        url, _ = SOURCES[kind]
        upstream = published(kind)
        written = table.get(kind, {})
        dated_upstream = sum(1 for date, _ in upstream.values() if date)
        print(f"\n{kind}: {url}")
        print(f"  {len(upstream)} line(s) listed, {dated_upstream} of them dated")

        for line in sorted(set(written) | set(upstream), key=order):
            mine = written.get(line)
            theirs, field = upstream.get(line, (None, ""))
            if line not in upstream:
                # Not a failure. A publisher that prunes a dead line from its document has not made
                # the date it once stated untrue, and an artifact of that line may still be in the
                # index — which is the reason these dates are written down rather than fetched.
                notes.append(f"{kind} {line}: written {mine}, upstream no longer lists this line")
            elif mine and theirs and mine == theirs:
                print(f"  {line:8} {mine}  ({field})")
            elif mine and theirs:
                problems.append(
                    f"{kind} {line}: written {mine}, upstream states {theirs} ({field})"
                )
            elif theirs:
                problems.append(
                    f"{kind} {line}: upstream states {theirs} ({field}) and nothing is written down"
                )
            elif mine:
                problems.append(
                    f"{kind} {line}: written {mine}, and upstream lists this line with no {field} "
                    f"— that date is this repository's guess, not a transcription"
                )
            else:
                print(f"  {line:8} {'—':10}  (upstream states no {field} yet)")

        for line, date in written.items():
            try:
                datetime.date.fromisoformat(date)
            except (TypeError, ValueError):
                problems.append(f"{kind} {line}: {date!r} is not a YYYY-MM-DD date")

    for note in notes:
        print(f"\nkept  {note}")
    return problems


def update(table: dict, kinds: list[str]) -> dict:
    """Rewrite each kind's dates from its publisher, in place.

    In place matters twice over: ``data/eol.json`` carries a prose block per kind explaining what
    the entries mean and why some of them are there, and a rewrite that reordered or dropped those
    would throw away the part of the file a person actually reads. Dict order is insertion order, so
    replacing a value leaves the block above it exactly where it was. A line upstream has stopped
    listing is kept, and a line upstream lists with no date is dropped, for the reasons the readers
    above give.
    """
    for kind in kinds:
        upstream = published(kind)
        written = table.get(kind, {})
        merged = {line: date for line, date in written.items() if line not in upstream}
        merged.update({line: date for line, (date, _) in upstream.items() if date})
        for line in sorted(set(merged) - set(written), key=order):
            print(f"  + {kind} {line} {merged[line]}")
        for line in sorted(set(written) - set(merged), key=order):
            print(f"  - {kind} {line} {written[line]} (upstream states no date for this line)")
        for line in sorted(set(written) & set(merged), key=order):
            if written[line] != merged[line]:
                print(f"  ~ {kind} {line} {written[line]} -> {merged[line]}")
        table[kind] = {line: merged[line] for line in sorted(merged, key=order)}
    return table


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--kind", action="append", choices=sorted(SOURCES),
                        help="check one kind rather than all six; repeatable")
    parser.add_argument("--update", action="store_true",
                        help="rewrite the dates from their publishers instead of checking them")
    arguments = parser.parse_args()

    kinds = arguments.kind or sorted(SOURCES)
    table = read(arguments.data)

    if arguments.update:
        before = json.dumps(table, indent=2, ensure_ascii=False)
        table = update(table, kinds)
        after = json.dumps(table, indent=2, ensure_ascii=False)
        if before == after:
            print(f"{arguments.data} already matches every publisher")
            return
        # newline="\n" and not the platform's: this file is committed, and a rewrite from a Windows
        # machine that turned every line ending over would bury three changed dates in a diff of
        # four hundred untouched ones.
        arguments.data.write_text(after + "\n", encoding="utf-8", newline="\n")
        print(f"rewrote {arguments.data} from {len(kinds)} publisher(s)")
        return

    problems = check(table, kinds)
    for problem in problems:
        print(f"\nFAIL  {problem}")
    if problems:
        raise SystemExit(
            f"\n{len(problems)} date(s) disagree with their publisher. If upstream moved a "
            f"schedule, `python tools/eol.py --update` transcribes it again; commit the diff so "
            f"the next index carries it."
        )
    print(f"\nok: every date in {arguments.data} is the one its publisher states")


if __name__ == "__main__":
    main()
