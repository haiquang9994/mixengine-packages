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
| PHP | 7.0 – newest, 6 targets | `php_windows`, `php_unix`, `php_legacy_unix` | yes — P2 |
| Node.js | 16 – newest, 6 targets | `node` | yes — P3 |
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
archives, the gaps were not marginal: PHP 8.3 on Windows was missing two extensions this repository
fails a Unix build over, which P2 closed, and Node.js 24.19.0 was 106 MB on Windows against 198 MB
on Linux, which P3 closed.

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

### [x] P2 — PHP: one extension set, chosen once **(rule)**

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

Closed, and not beside the other recipe but *inside* it: the set is `tools/php_parity.py` and all
three recipes read it, because "say so beside" is a comment and comments do not fail a build. The
extensions this repository adds are one list now, reached three ways — compiled in on 8.1+,
`phpize`d on 7.x, downloaded from php.net's own PECL builds on Windows, where all six of them exist
for every branch except `zstd` before 7.2, so 7.0 and 7.1 do without it on all six cells rather than
on four.

The surplus is thrown out by a **keep-list rather than a delete-list**, which is the one decision
here worth arguing with. A delete-list is written against the archive somebody measured, 8.3's,
and says nothing about 7.3's `php_interbase.dll` or about 8.6; the keep-list answered for all
eleven branches without being told about any of them, dropping `xmlrpc`, `interbase`,
`phpdbg_webhelper` and `oci8_12c` on the old ones as a matter of course. Two consequences came with
it. `com_dotnet` goes too — P6 below expected it to stay as a named exemption, and "the platform
has no equivalent" turned out to be a reason to look at the feature rather than to keep it. And
4.7 MB left with it: `prune` deletes libraries **by reachability**, computed with the publisher's
own `deplister.exe` and then deleting that too, so dropping `enchant` takes 3.0 MB of GLib with it on every branch without a
table saying which library belongs to which extension.

Three things the task found that reading could not:

* **PHP 7.0 through 7.4 call GD `php_gd2.dll`**, and 8.0 renamed it. A keep-list matched on file
  stems threw GD out of five branches *silently* — nothing loads what is no longer there — and it
  was caught by running the recipe against 7.0. The file is renamed to the extension inside it
  now, and `php_parity.check` reads the whole compiled-in set rather than only what this
  repository adds, which is the check that would have failed.
* **`oci8`, `pdo_oci` and `pdo_firebird` cannot load at all** in the archive as published: they want
  client libraries the publisher does not ship. `snmp` loads and creates `C:\usr\snmp\persist` on
  the way up. Three of the fifteen dropped extensions were never usable.
* **Windows 7.0 has no `readline` and no `dba`**, and 7.0–7.1 carry `mcrypt` compiled in with
  7.0–7.3 carrying `wddx`. None of the four is closeable by borrowing, so they are named in
  `php_parity` beside `pcntl` and `posix` — the exemption list P6 needs, measured not guessed.

`extensions.enabled` is the third half of this. `shared` said "available" about `curl` and about
`odbc` alike, so nine extensions that are compiled into every Unix cell and are loadable modules on
Windows had no way to be described as *expected*. `static ∪ enabled` is now what a cell does and
`static ∪ shared` is what it could be asked to do, which is the comparison P6 wants. The Windows
smoke test also stopped proving one extension and started proving all of them, which is both the
drift `php_smoke` exists to name and the only thing standing between a reachability sweep and a
library deleted in error.

Proven on 7.0, 7.4 and 8.3 end to end, on Windows: every extension in each archive loads from a
relocated tree, and the 8.3 artifact is *smaller* than upstream's zip while carrying six extensions
it did not have.

### [x] P3 — Node.js: decide what `include/node` is for **(rule)**

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

Dropped, on all six cells, and the argument that settled it is not the one above. **`node-gyp` does
not read those headers.** It looks inside the runtime it is running under only when the build set
`use_prefix_to_find_headers` — a flag distributions pass so their `-dev` package can compile
offline — and every official build has it false, read out of the `process.config` baked into the
Linux 24.19.0 and 26.7.0 binaries rather than assumed. With it false there is no choice left to
make: `configure.js` downloads `process.release.headersUrl` into `~/.node-gyp/<version>` and
compiles against that. Which is why native modules have always built on Windows against an archive
carrying no `include/` at all — the platform that already answers the question is the one this
copies, and "dropped everywhere and `node-gyp` keeps working over the network" is not a trade so
much as a description of what already happens on three of the six cells.

*Keep them everywhere* was also not reachable, which the task did not know. `node-gyp --nodedir`
pointed at an installed tree on Windows links against `<nodedir>/$(Configuration)/node.lib`, a path
out of a **build** tree; the headers tarball has never contained one and upstream publishes it
separately, per architecture. The symmetric option was never "add 59 MB to three cells" but "add
59 MB and a fourth download, and still be told the two halves are not the same thing".

**A keep-list again**, for the reason P2 gives, and it earned itself twice over on a row where a
delete-list would have looked sufficient: measured across 16.20.2, 20.19.5, 24.19.0 and 26.7.0 on
both platforms, one naming `include` and `share/{doc,man}` would have shipped Node 16's
`share/systemtap` and its `node_etw_provider.man`. Neither had been seen by anything here. What
`tools/node.py` keeps at the root is now the interpreter, its libraries, the launchers already
named in `LAYOUT`, and `LICENSE`; everything else there goes, including `install_tools.bat` — which
is not a tool but a Chocolatey install of Python and the VC build tools onto the whole machine —
and `CHANGELOG.md`, the only file whose *contents* differed between the two cells for a reason that
is not line endings.

The one thing the task expected to delete and this kept: npm's 2.7 MB of `docs/` and `man/`. All
three parts are read by a documented command — `npm help-search` reads `docs/content`, `npm help`
opens `docs/output/*.html` on Windows and runs `man` against `man/man[1-7]` on Unix — and both
cells already carried all three, so the rule's answer is keep. "No more than is needed" is a claim
about need, and the way to settle it was to go and read npm rather than to weigh the directory.

Proven on 16.20.2, 24.19.0 and 26.7.0: packed end to end on Windows, and `prune` run against the
real Linux tarball of each, which is as far as a Windows machine can take the other half. Node
24.19.0 on Linux goes from 4,708 files and 198.5 MB to 1,977 and 138.8 MB — and once both cells are
pruned, **the entire remaining difference between the Windows and Linux trees is `node.exe` against
`bin/node` and the per-shell launchers beside it.** Every other path matches.

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
tests, and P2 then packed five branches of PHP without GD for the same reason — a file renamed
between eras, invisible to every per-artifact check because what is missing cannot fail a load test.
Twice now, and both times by comparing rather than by reading.

Two checks, both cheap because every fact they need is already in `mixengine-artifact.json`:

*Across cells* — for one `(kind, version)`, the feature sets must match. For PHP that is
`extensions.static ∪ extensions.enabled` — the set a cell actually runs with, which is what P2
added `enabled` for; `shared` is only what it could be asked to do, and on Windows it says the same
word about `curl` and about a debugger. The exemption list is no longer a guess: `php_parity` names
the four extensions Windows has never had, the two its old builds gained late, and the two its 7.x
builds compile in and cannot drop. Everything outside those names is a defect this check fails on.
For the other kinds the comparison is `provides`.

*Within one artifact* — no path matching what the second half of the rule forbids and the manifest
does not declare: `.pdb`, `.dSYM`, `*.lib`/`*.a`, `include/`, `share/man`, `share/doc`, `test/`. PHP
on Windows is where to point it first, because P2 already deletes every one of those there: a check
that finds nothing in the row that was just cleaned is a check that has been tested. Node.js is the
second, and it is the row that shows the check has to read `upstream.removed` rather than only the
tree — `include/`, `share/man` and `share/doc` are on that list because P3 named what it dropped,
and a check reading the tree alone would say nothing about a cell that never had them. A
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
