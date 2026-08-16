# mixengine-packages build plan

This repository releases on its own clock, so it needs its own order of work. What is here is that
order: what is packed, what the rules say about it that is not true yet, and what has not been packed
at all.

Legend: `[ ]` todo · `[~]` in progress · `[x]` done · **(rule)** = a conformance debt against
[*One version means one thing, and no more than is needed*](../README.md#one-version-means-one-thing-and-no-more-than-is-needed).

---

## Where we are

Every row of the runtime table is packed, and both service rows that have been evaluated are packed.
What is *not* done is the rule itself.

| Kind | Cells | Recipes | Conforms |
| --- | --- | --- | --- |
| PHP | 7.0 – newest, 6 targets | `php_windows`, `php_unix`, `php_legacy_unix` | **no** — P2 |
| Node.js | 16 – newest, 6 targets | `node` | **no** — P3 |
| Python | 3.10 – newest, 6 targets | `python` | partly — P4 |
| Ruby | 3.2 – newest, 6 targets | `ruby`, `ruby_unix` | unknown — P5 |
| Caddy | 2.0 – newest, 6 targets | `caddy` | yes |
| MariaDB | 10.6 – newest, 6 targets | `mariadb`, `mariadb_deb`, `mariadb_build` | yes — it is where the rule came from |

The rule was written **after** MariaDB, because MariaDB is what taught it: three routes to one
version produced three different feature sets, and fixing that is what
[`10d4e81`](https://github.com/haiquang9994/mixengine-packages/commit/10d4e81) and
[`6c344d0`](https://github.com/haiquang9994/mixengine-packages/commit/6c344d0) began. They did not
finish it: a later audit of the six *finished* artifacts of a green run found four more asymmetries
no recipe knew it had, and closing those took seven further commits; what that audit found is
written down in [the README](../README.md#one-version-means-one-thing-and-no-more-than-is-needed),
because it is the argument for P6. The rule has never been applied backwards to the four runtime
rows that were packed before it existed, and P1–P6 are that work. Measured against upstream's own
archives, the gaps are not marginal: **PHP 8.3 on Windows is missing two extensions this repository
fails a Unix build over**, and one Node.js version is 106 MB on Windows and 198 MB on Linux.

Nothing below is a rewrite. Every recipe already downloads, verifies, relocates, proves and packs
correctly; what they do not do is *choose*, and choosing once is the whole of the rule.

---

## Conformance — apply the rule to the rows packed before it

### [x] P1 — Give the borrowed recipes somewhere to say what they took out **(rule)**

`upstream.removed`, `upstream.added` and `upstream.stripped` are what keep "borrowed" checkable
against what the publisher shipped. `tools/python.py`, `tools/mariadb.py` and `tools/mariadb_deb.py`
wrote them — the three recipes written against the rule, or corrected under it. `node.py`,
`php_windows.py` and `ruby.py` had no parameter for it at all, so even a correct decision made in
P2–P5 would have had nowhere to be declared. `borrow.publish` already carries whatever the manifest
holds, so this was a `describe(…, added, removed)` signature in three recipes and nothing more.

**First, because P2–P5 each end in a manifest field that does not exist yet.** `php_windows.py` does
not use `borrow.py` at all — it predates it — so it either grows the two arguments itself or is moved
onto `borrow` in passing; moving it is the larger change and is not required to close this.

Closed as `borrow.declare`, which is one function rather than four copies of six lines, and which
does one thing the fields did not do before: **it checks the claim against the tree before writing
it.** A path in `added` has to be there, a path in `removed` has to be gone — by `os.path.lexists`,
because a dangling symlink is still a file in the archive and `exists` follows the link and answers
no, which is precisely how `mysql_ldb` survived four rounds of being excluded. `python.py` gave up
its own copy of the two fields to it; `php_windows.py` imports `borrow` for this one function and
keeps fetching, hashing and packing by itself, because moving the rest is still the larger change.
The MariaDB recipes are the remaining writers of `upstream.*` outside `declare`, and they also write
`stripped`, which nothing else does — folding them in belongs with P6, where the check that reads
these fields is written.

### [ ] P2 — PHP: one extension set, chosen once **(rule)**

The widest gap here, and the only one that contradicts something this repository already enforces
elsewhere.

Measured on `php-8.3.33-nts-Win32-vs16-x64.zip` — 88 entries, 90.9 MB unpacked, 40 DLLs in `ext/`
totalling 28.0 MB — against `php_unix.py`'s `STATIC_EXTENSIONS` and `php_legacy_unix.py`'s `PECL`:

*Absent from the Windows archive in any form:* **`redis`, `mongodb`**, `igbinary`, `xdebug`, `yaml`,
`zstd`. The first two are the pair `php_legacy_unix.py` fails a build over — *"MixEngine offers
{package} on every version it ships, so an artifact without it is not one worth publishing"* — and
the Windows leg publishes without them and says nothing. They exist as official Windows DLLs
(xdebug.org publishes its own; PECL publishes Windows builds of the rest), so this is a download and
a load-check per branch, not a compile.

*Present only on Windows:* `odbc`, `pdo_odbc` — an ODBC bridge is the README's own example of what a
local development environment does not do — plus `oci8_19`, `pdo_oci`, `pdo_firebird`, `snmp`,
`imap`, `enchant`, `tidy`, `gettext`, `com_dotnet`, and **`dl_test` and `zend_test`, which are PHP's
own test extensions**. Also `dev/php8.lib`, 892 KB of import library in a runtime that is not an SDK.

*Different in kind rather than in presence:* `curl`, `openssl`, `mbstring`, `intl`, `gd`, `zip`,
`sodium`, `sqlite3` and `fileinfo` are compiled in on Unix and are loadable modules on Windows. That
one is **not** a defect to fix here — a static extension cannot be turned off, and Windows publishes
no build with them static — but it means the Windows artifact only behaves like its Unix twin if
whoever installs it enables that set. `extensions.shared` already names them; what this task owes is
the artifact saying that the set is *expected to be enabled* rather than merely available.

Resolve each difference in one direction for all six cells, and **say so beside the other recipe** —
a deletion in the packer and a `--with-` in the compiler are one decision written twice, and the rule
is explicit that each has to name the other.

### [ ] P3 — Node.js: decide what `include/node` is for **(rule)**

One recipe, six cells, and it chooses nothing — so the cells differ by whatever upstream happened to
put in each archive. Node 24.19.0, measured:

| | files | unpacked |
| --- | --- | --- |
| `win-x64.zip` | 1,989 | 106.1 MB |
| `linux-x64.tar.gz` | 4,708 | 198.5 MB |

The 92 MB is almost entirely **`include/node/**` — 2,726 headers, 59.0 MB, 29.7% of the Linux
archive**, down to OpenSSL headers for `solaris-x86-gcc`. The Windows zip has no such directory, so
one version already means two things. Both halves also carry ~3.0 MB of `npm/docs` and `npm/man`.

The decision is genuinely a decision rather than an obvious deletion, which is why it is a task:
`node-gyp` wants those headers, and fetches them from nodejs.org itself when they are absent. So
either they are dropped everywhere and `node-gyp` keeps working over the network, or they are kept
everywhere — which means *adding* them to the Windows cell, from upstream's own
`node-v<version>-headers.tar.gz`. What is not allowed is the status quo, where the answer is
whichever one the publisher chose per platform.

### [ ] P4 — Python: tkinter, and the same two questions **(rule)**

`tools/python.py` is the recipe that already does this properly — `install_only_stripped`, `_crypt`
removed with the reason written down, `upstream.added` and `upstream.removed` both populated. Two
things are left.

**tkinter is described as excluded and is not excluded.** `MODULES` says *"tkinter is deliberately
not here — upstream ships it, it needs a display library on Linux, and nothing a local web
development environment does touches it"*, which is exactly the rule's own test for *dropped
everywhere* — but the comment only removes it from the smoke test. cpython 3.13.15 still ships
`tcl/`, `tcl/tcl86t.lib` and `tcl/tk86t.lib` on Windows and `lib/itcl4.3.8/*.a` on Linux. Either
delete it on all six cells and record it in `upstream.removed`, or delete the claim.

**Headers and `libs/python313.lib` stay, and should say why.** 1.8 MB of `include/` on both systems
plus 563 KB of import libraries on Windows — the same shape the README rules out — except that here
they are load-bearing: `pip install` of an sdist with a C extension links `python313.lib`. That is a
decision the rule permits ("something it would reach for is enabled everywhere"), and what it is
missing is being stated rather than merely being true. 62.7 MB Windows against 98.3 MB Linux is a gap
that should be explainable line by line after this task.

### [ ] P5 — Ruby: make the two recipes answer the same questions **(rule)**

`ruby_smoke.py` exists precisely so that a borrowed Ruby and a compiled Ruby make the same claim, and
it works. What sits outside it does not:

- `ruby_unix.py` prunes `share/man`, `share/doc`, `share/ri`; `ruby.py` prunes nothing. This is the
  packer/compiler pair the rule names, with neither side mentioning the other.
- `--enable-yjit`, `--enable-libedit` (chosen over GNU readline for the **licence**, not the API) and
  `--disable-shared` are decisions taken for four cells out of six. Nobody has asked what
  RubyInstaller does about any of the three.
- `ruby_unix.py` proves two things `ruby.py` does not: that YJIT turns on, and that
  `gem install bigdecimal` compiles a native extension in the moved tree. Those are claims about
  *Ruby*, so by `ruby_smoke.py`'s own argument they belong in it — or their absence on Windows
  belongs in the manifest.

Unpacking a `.7z` needs 7-Zip, which the machine this was audited on does not have, so the Windows
half is *unknown* rather than *wrong*. Measure it first; the answer decides whether this task is a
prune, a note, or nothing.

### [ ] P6 — Make the rule something CI can fail on **(rule)**

P2–P5 are one-time corrections; this is what keeps them. `verify.py` already validates each artifact
against the schema. What it cannot do is compare the artifacts of *one version to each other*, which
is the whole of the rule's first half. This is not a hypothetical check: MariaDB's four asymmetries
were found by doing precisely that by hand, on a run whose six cells had all passed their smoke
tests. What this task automates has already caught something once.

Two checks, both cheap because every fact they need is already in `mixengine-artifact.json`:

*Across cells* — for one `(kind, version)`, the feature sets must match. For PHP that is
`extensions.static ∪ extensions.shared` modulo a small, **named** per-OS exemption list (`pcntl` and
`posix` have no Windows build; `com_dotnet` has no Unix one); for the rest it is `provides`.

*Within one artifact* — no path matching what the second half of the rule forbids and the manifest
does not declare: `.pdb`, `.dSYM`, `*.lib`/`*.a`, `include/`, `share/man`, `share/doc`, `test/`. A
recipe that legitimately keeps one of them (P4's `python313.lib`) declares it, and the declaration is
what the check reads — so "no more than is needed" becomes a list somebody wrote down rather than a
habit somebody remembers.

Run it in `publish-index.yml`, where every artifact of a version is visible at once. An empty cell is
not a failure — a target upstream never built is already an `exit 75`, and this must keep that
distinction or it will block the release of the five cells that do exist.

---

## Services still to pack

Caddy and MariaDB are in. The remaining three are the same shape, and each owes the same two things:
an evaluation — **borrow before you build**, asking the catalogue rather than assuming it, which is
how MariaDB's row turned out to be wrong in three cells — and a smoke test that exercises *run,
configure, health-check, stop* rather than `--version`.

### [ ] P7 — PostgreSQL

The evaluation question is Windows and macOS. EDB publishes a Windows binary archive and macOS
builds; Linux has no relocatable upstream tarball worth the name, and the `.deb`-rearrangement route
MariaDB's aarch64 cell uses is the obvious candidate. The smoke test's own terms are `initdb`, a
server started against a rendered `postgresql.conf`, `pg_isready`, a row written and read back
through a real table, and `pg_ctl stop -m fast` — the `mariadb-install-db` / `mariadb-admin ping` /
`mariadb-admin shutdown` trio, one database over.

### [ ] P8 — Redis and Memcached

**The evaluation here is likely to answer "build", and on the platform that usually borrows.**
Neither project publishes an official Windows binary; what circulates is a Microsoft fork abandoned
at 3.0 and community rebuilds. So this task decides between compiling both natively on a Windows
runner and declaring the cell empty — and an empty cell is a real answer, not a failure, as long as
the index says so instead of a user finding out.

The smoke test is the cheapest in the table: start, `PING`, `SET`/`GET`, `SHUTDOWN NOSAVE`.

### [ ] P9 — nginx

nginx publishes a Windows zip itself and nothing relocatable for macOS or Linux, so the shape is
PHP's rather than Caddy's. The proof has one thing Caddy's does not: nginx has no admin endpoint, so
reload is `-s reload` against a running master and health is a request actually served.

---

## The index

### [ ] P10 — End-of-life dates for every kind, not only MariaDB

`data/eol.json` carries what the index promises about a version's support window. MariaDB's entry is
transcribed from a publisher API and reprinted on every run; the runtime entries are transcribed from
schedule pages by hand and nothing checks them. PHP and Node.js both publish machine-readable
schedules (`endoflife.date` mirrors the rest); reading them the way `mariadb.py` reads MariaDB's
turns four hand-maintained entries into four transcriptions with a source.

### [ ] P11 — Prove the archive is permanent

The index promises that a blueprint pinning PHP 8.1.29 keeps working forever, which makes every
release asset load-bearing and an accidental deletion unrecoverable. Nothing states that today: no
protection on the releases, no periodic check that every URL in the published index still answers,
no line in the README saying which assets may never be deleted. A scheduled workflow that `HEAD`s
every artifact the current index names is the smallest thing that would notice.

---

## Working on this file

- Tick the task here; one file, not a phase per section, because this repository is one pipeline.
- New work goes **where it belongs in the order**, with the next free suffix on the task it follows
  (`P2a`, `P2b`) rather than at the end.
- **One note, one place.** Why a recipe does what it does belongs in its docstring, beside the code
  it is about; what a packaging decision settled for the whole repository belongs in
  [`../README.md`](../README.md); what building something the hard way taught belongs in
  [`building-from-source.md`](building-from-source.md). What this file carries is only what none of
  those can: what has not been done yet, and what has to be decided before it can be.
