# One version means one thing, and no more than is needed

*Part of [mixengine-packages](../README.md), which holds the table of what is packaged.*

Two rules that go together, because each without the other produces an artifact somebody regrets.

**The artifacts of a version must be as alike as the platforms allow.** A user pins `mariadb 11.8`
in a blueprint and hands it to a colleague on another operating system; what they get has to be the
same database, not whatever that platform's publisher happened to compile. Upstreams do not help
here — MariaDB's Linux bintar carries every storage engine its maintainers can build, its own `.deb`
packages carry none of them because each is a separate package, and a source build carries whatever
was configured. Left alone, one version means three different feature sets. So the set of features
is **chosen here, once, for all cells of a kind**, and where two recipes express that choice
separately — a packer deleting, a compiler configuring — each says so beside the other.

Parity may be resolved in either direction, and which one is a judgement about the feature rather
than about convenience. Something a local development environment does not do — a cluster engine, an
ODBC bridge, PAM authentication — is dropped everywhere. Something it would reach for is *enabled*
everywhere, even where that means compiling it on the targets nobody publishes a binary for; that is
how `mariabackup` ended up in all six MariaDB cells after first being dropped from all six.

**And an artifact contains what it takes to run, nothing else.** Not debug symbols — upstream's
Linux bintar hides 700 MB of them inside its binaries and Windows keeps 74 MB in a single `.pdb`,
while Debian and Windows both publish theirs separately for whoever wants them. Not test suites, not
benchmarks, not headers or import libraries: MixEngine installs a database or a runtime, not an SDK.
Every user downloads this once per version per machine, and none of that is ever read.

*None of that is ever read* is a claim about the runtime, though, and one row answers it the other
way. `pip install` of a source distribution compiles a C extension on the machine doing the
installing, so CPython's headers and its Windows import library are read there routinely — and are
kept, on every cell, named in a top-level `keeps` that says which path and why. An exemption that
has to be written into the artifact is an exemption somebody argued for; the rule is what makes the
argument necessary.

Whatever is taken out, put in, deliberately kept, admitted missing or **shipped at upstream's path
and not in upstream's bytes** is recorded in `mixengine-artifact.json` — `upstream.removed`,
`upstream.added`, `upstream.changed`, `keeps`, `lacks` — so that "borrowed" keeps meaning something a
reader can check against what the publisher shipped, and "no more than is needed" keeps meaning a
list somebody wrote down rather than a habit somebody remembers. `upstream.changed` is the one
hardest to do without: a file that was modified rather than added or deleted is indistinguishable
from a corrupted download unless the artifact says what was done to it. There was briefly a sixth,
`upstream.stripped`, which said in a sentence what `changed` says as a mapping of path to command —
and two spellings of one fact is the shape of thing this rule exists to remove, so it survives only
in artifacts published before the check below was written.

**Both rules are checked by comparing finished artifacts, because intent is not evidence.** The first
audit of a green MariaDB run — six cells, all six proven against a running server — found the two
Linux artifacts three times apart in size and each of the four asymmetries below, none of which any
recipe knew it had:

* the same feature list applied by three recipes, and only one of them running it: the `.deb` cell
  kept PAM plugins and Galera scripts, the compiled cells kept 21 MB of test binaries, a 14 MB import
  library and eighteen demonstration plugins;
* a feature present on five cells and missing on the sixth *because that cell was smaller* — the
  compression providers InnoDB loads for `innodb_compression_algorithm`, which upstream splits into
  five more `.deb` packages;
* a pattern that had excluded `mysql_ldb` since the first round and never once removed it, because
  deleting its target first made the symlink invisible to `Path.exists`;
* and one difference that is not ours to fix and is written down instead: upstream's own bintar ships
  the client half of the PARSEC authentication plugin without the server half, which its `.deb`
  packages have.

Most of that is invisible in a passing build. A smoke test proves an artifact *runs*; only a diff
proves six of them are the same thing.

That diff is now [`tools/parity.py`](../tools/parity.py), and it runs where every artifact of a version
is on one disk at once — the index workflow, which is the only place in the repository that can see
more than one cell. Across the cells it compares feature sets: `extensions.static ∪
extensions.enabled` for PHP, the commands in `provides` for every kind. Within one artifact it
refuses any path matching what the second half of the rule throws out. **Every difference it is not
told about is a defect**, which is what makes the two ways of telling it worth having — `lacks` for
something a cell cannot do at any price, `keeps` for something it carries on purpose, both written
into the artifact with the reason attached, and [`tools/php_parity.py`](../tools/php_parity.py) for the
handful of differences that belong to a whole row rather than to one cell.

Pointed at the catalogue as it stands, it reports 370 differences on the PHP row and none on any
other, and every one of them is against an archive packed before the task that closes it. That is
the answer a check like this is supposed to give the first time: not *nothing is wrong*, and not
something new either, but *here is the backlog, and here is the thing that will notice if it comes
back*.
