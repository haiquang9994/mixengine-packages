# mixengine-packages build plan

This repository releases on its own clock, so it needs its own order of work. What is here is that
order: what is packed, what the rules say about it that is not true yet, and what has not been packed
at all.

Legend: `[ ]` todo · `[~]` in progress · `[x]` done · **(rule)** = a conformance debt against
[*One version means one thing, and no more than is needed*](one-version-means-one-thing.md).

---

## Where we are

Every row of the runtime table is packed, and every service row that has been evaluated is packed.
Every recipe now conforms to the rule and there is a program that says so. What is *not* done is the
**repack**: the artifacts on the releases page were packed before P2–P5, and `tools/parity.py` names
every one of those differences on every run. P6 below is what it says and what it does not.
PostgreSQL is the first row packed *after* it, and the difference shows three times: the rule caught
an asymmetry inside EDB's own release before anything was published; when a second publisher was
added for the same version it had something to be checked against rather than only a recipe's word;
and when the macOS cells were cut from 362 MB to 82 MB it was the rule that said what the smaller
tree still had to offer, so a saving of that size cost nothing anybody could argue about.

| Kind | Cells | Recipes | Conforms |
| --- | --- | --- | --- |
| PHP | 7.0 – newest, 6 targets | `php_windows`, `php_unix`, `php_legacy_unix` | yes — P2 |
| Node.js | 16 – newest, 6 targets | `node` | yes — P3 |
| Python | 3.10 – newest, 6 targets | `python` | yes — P4, P4a, P4b and P4c |
| Ruby | 3.2 – newest, 6 targets | `ruby`, `ruby_unix` | yes — P5, P5a and P5b |
| Caddy | 2.0 – newest, 6 targets | `caddy` | yes |
| MariaDB | 10.6 – newest, 6 targets | `mariadb`, `mariadb_deb`, `mariadb_build` | yes — it is where the rule came from |
| PostgreSQL | 14 – newest, 5 of 6 targets | `postgres`, `postgres_deb` | yes — P7, P7a and P7b, and it is the first row packed under the rule |

The rule was written **after** MariaDB, because MariaDB is what taught it: three routes to one
version produced three different feature sets, and fixing that is what
[`10d4e81`](https://github.com/haiquang9994/mixengine-packages/commit/10d4e81) and
[`6c344d0`](https://github.com/haiquang9994/mixengine-packages/commit/6c344d0) began. They did not
finish it: a later audit of the six *finished* artifacts of a green run found four more asymmetries
no recipe knew it had, and closing those took seven further commits; what that audit found is
written down in [that rule](one-version-means-one-thing.md),
because it is the argument for P6. The rule had never been applied backwards to the four runtime
rows that were packed before it existed, and P1–P6 were that work. Measured against upstream's own
archives, the gaps were not marginal: PHP 8.3 on Windows was missing two extensions this repository
fails a Unix build over, which P2 closed; Node.js 24.19.0 was 106 MB on Windows against 198 MB on
Linux, which P3 closed; CPython 3.13.15 shipped Tk 8.6 to Windows and Tk 9.0 to Unix under one
version number, which P4 closed by shipping neither; and Ruby 4.0.6 on Windows was 276 MB of which
225 MB was documentation the four compiled cells are configured never to build, which P5 closed.
Four times now a task has come out somewhere other than where it was pointed — P3 kept npm's manual
pages after going and reading npm, P4a keeps 30 MiB of a shared library nothing in the archive
loads, P4b found its own title wrong and two of its stated reasons for existing wrong with it, and
P5a was written to close an asymmetry that a look at the six trees says is not there. That is what a
rule is for, and it is the argument for measuring the artifacts rather than the release notes: every
one of those four was written from what the publisher says and corrected by what the publisher ships.
P5b then said the same thing about a check rather than an artifact — the comparison it added was
correct on paper and wrong on the first real archive it was pointed at. P6 closed the sequence by
turning the audit that started it into a program, and its first run says the same thing a sixth
time: every difference it reports is real, and every one is against an archive packed before the
task that already closes it.

*Packed* above means the recipe is written and runs, which is not the same as published, and the two
had quietly come apart: the releases page holds PHP, Node.js, Python, Ruby, Caddy and MariaDB, while
PostgreSQL, Redis, Memcached and nginx have recipes and nothing to install. P12 is that gap, found by
P11 while counting the archive.

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

### [x] P4 — Python: tkinter, and the same two questions **(rule)**

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

Both closed, and the first one turned out not to be an argument about size. **Upstream ships a
different tkinter to each half of the table**: Windows carries Tk 8.6 with Tix 8.4.3, Unix carries
Tk 9.0 with itcl 4.3.8 and the Tcl Thread package, and 3.14 replaces the Windows DLLs with Tcl 9
and moves the entire Tcl script library inside them. So `python 3.13.15` meant one toolkit on one
machine and another on the next before anything here chose, which is the rule's first half failing
in a way the *"needs a display library on Linux"* comment never described. Dropped rather than
levelled up, by the rule's own test: nothing a local web development environment does draws a
window, and the two things in the standard library that need a display are the toolkit and IDLE.

**A keep-list twice more**, in the two places the surplus versions itself in its own name — `lib/`
on Unix keeps `python3.X`, `libpython3*` and `pkgconfig` and nothing else, `libs/` on Windows keeps
`python3*.lib` — and names elsewhere. It earned itself twice, again: `libs/` on 3.10 and 3.11 holds
**31 per-extension import libraries** (`_socket.lib`, `_tkinter.lib`, one per built-in module) that
3.12 onwards does not, which exist to link a module *into* a Python being compiled and are opened
by nothing that installs a wheel; and `lib/itcl4.3.8` and `lib/thread3.0.6` carry their versions in
their names, so a delete-list would have been out of date at the next Tcl release. `share/man` goes
with them on Unix — one manual page whose Windows twin has never had one.

**And then a sweep that fails the pack if any part of any path still matches Tcl or Tk**, which is
what makes the drop a rule rather than a list. It is not decoration: 3.14 renamed `tcl86t.dll` and
`tk86t.dll` to `tcl90.dll` and `tcl9tk90.dll`, and a recipe written against 3.13 would have shipped
both in silence — the `php_gd2.dll` failure from P2 exactly, one runtime over. The sweep also found
the one path in either tree that looks like the toolkit and is not, `share/terminfo/t/tkterm`, an
ncurses description of a terminal emulator; it is named in `NOT_TOOLKIT` rather than dodged by
narrowing the sweep to where Tcl lives today, since moving Tcl is precisely what 3.14 did.

The second question closed the other way, and the reversal is the useful part. `include/` and
`libs/python3XX.lib` are kept — the opposite of P2, which deleted `dev/php8.lib` as *"892 KB of
import library in a runtime that is not an SDK"* and was right to. A PHP extension reaches a
developer as a DLL somebody else compiled; a Python extension frequently does not, because
`pip install` of any sdist without a matching wheel compiles C on the installing machine against
`Python.h`. So **"no more than is needed" is a question about the runtime, not about the file
type**, and the answer now travels in the artifact: a top-level `keeps` mapping each exempt path to
why, written by `borrow.declare`, which refuses a `keeps` naming something the tree does not have.
The schema carries it and P6 is what will read it. The smoke test proves it from the other side —
it asks the relocated interpreter where its headers are and requires `Python.h` to be there, and
requires `import tkinter` to fail, because a deletion checked only against the file system is
checked only in the direction that cannot go wrong twice.

**A latent bug the simulation caught**, which was in `site_packages` before this task and would have
bitten on macOS rather than here: `tree / "Lib"` is answered *yes* by a case-insensitive file system,
and a default macOS install has one. Deciding "is this a Unix layout" by looking for `lib/python3.*`
first cannot be answered by accident; asking for `Lib` first pointed every caller one level above the
standard library on two of the six cells.

Proven on 3.10.21, 3.13.15 and 3.14.7 — three different Tcl layouts — packed end to end on Windows,
and `prune` run against the real Linux *and* macOS tarballs of each, which is as far as a Windows
machine reaches. All three manifests validate against the schema. What the gap looks like now, on
3.13.15: Windows 59.8 MB → 48.1, Linux 93.8 → 83.5, macOS 61.4 → 52.2. What is left of it is P4a.

### [x] P4a — Python: Unix ships the interpreter twice **(rule)**

P4 owed an explanation of the Windows/Linux gap line by line, and produced one. After it, 35.4 MB
of the remaining 35.4 MB is two things, both Unix-only, and neither is a feature:

**`lib/libpython3.13.so.1.0` is a second complete copy of the interpreter — 30.3 MB on Linux,
16.7 MB on macOS — and nothing in the tree loads it.** `bin/python3.13` links CPython statically:
read out of the ELF's own `DT_NEEDED` it asks for `libpthread`, `libdl`, `libutil`, `librt`, `libm`
and `libc` and for no `libpython` at all, and the macOS binary's `LC_LOAD_DYLIB` list says the same.
Nor does anything else: of the twelve dynamically linked ELF files in a 3.13.15 Linux tree, **zero**
name it. Windows carries `python313.dll` once and that one is the interpreter. This is the same
shape as `include/node` — weight one publisher gives one platform, that the platform's own
executable never opens — with one difference that makes it a task rather than a line in P4:
deleting it is what stops `python3-config --libs --embed` from linking, so it is a decision about
whether a MixEngine Python is ever *embedded* in another process, and that deserves being asked
rather than assumed. `_sysconfigdata__*.py` names the file and would need to stop.

**`share/terminfo` is ~1.9 MB and 2,800 files, and whether the bundled ncurses can even reach it is
not measurable from here.** The interpreter has `readline` and `curses` compiled in, so a terminfo
database is genuinely wanted; the question is whether these builds look for it beside themselves or
at the `/tools/deps` prefix they were built under — in which case the copy in the archive is
unreachable, the system's own database answers, and this is 1.9 MB of nothing. Settling it needs a
Linux machine with `TERM` set and the system database moved out of the way. Windows has no `share/`.

**The terminfo half needed no Linux machine after all, and the answer is that the database is
unreachable.** ncurses does not guess where its database is; the search order is compiled into it,
and reading the strings out of `bin/python3.13` itself gives the whole of it: `$TERMINFO`, then
`$TERMINFO_DIRS`, then `~/.terminfo`, then the three absolute paths built in at configure time —
`/etc/terminfo:/lib/terminfo:/usr/share/terminfo`. **Not one of them is inside the tree**, and no
`<prefix>/share/terminfo` string exists in the binary to be found. So a MixEngine Python on Linux
reads the machine's own database, as it would have with or without this directory, and the 2.2 MB
and 1,833 files it ships are answered by nobody. It is also a directory *four of the six cells do
not have* — macOS ships `share/man` and nothing else, Windows ships no `share/` at all — so this is
the rule's first half failing quietly on one platform. The whole of `share/` goes on Unix now rather
than `share/man` alone, and the deletion took the sweep's exemption list with it: `share/terminfo`
was the only entry in `NOT_TOOLKIT`, the one path in either tree matching `TOOLKIT` without being
the toolkit, and an exemption for a directory that no longer ships is exactly the stale claim
`borrow.declare` was taught to refuse. Better to delete the directory than to keep explaining it.

**The library half closed the other way, and the reversal is the point of the task.**
`lib/libpython3.13.so.1.0` stays, on all four Unix cells, and the reason is not that the measurement
was wrong — it is right, nothing in the archive loads that file. It is that *the archive says the
file is there for something outside itself to link*, in three separate places, and all three are
upstream's own words rather than an inference:

* `lib/libpython3.13.so` is a **symlink** to it. The runtime loader resolves libraries by `SONAME`
  and never looks at that name; the only program on a Unix machine that opens `libfoo.so` is a
  linker. Upstream shipped a file whose sole consumer is `ld`.
* `bin/python3.13-config` is written to survive relocation — it computes `prefix_real` from its own
  path with `dirname`/`readlink` before substituting it into every variable — and `--ldflags
  --embed` prints `-L<tree>/lib -lpython3.13`. Not a build-time path left behind; a path deliberately
  recomputed for the tree it finds itself in.
* `PY_ENABLE_SHARED="1"` sits in that same script and in `_sysconfigdata`, and it is the flag that
  *suppresses* the fallback to the config directory. With it set there is nowhere else to look.

Deleting the library would leave an artifact describing a file it does not carry, on three counts,
and the only repair would be rewriting `python3-config` and `_sysconfigdata` — editing the
publisher's record of its own build, which is a worse thing to ship than 30 MiB. Windows settles the
parity question in the same direction with no choice at all: `python3XX.dll` **is** the interpreter
there (`python.exe` is a 91 KB launcher) and `libs/python3XX.lib`, kept by P4 so a C extension can
compile, is the same file an embedder links. Two cells can embed a Python whatever this repository
decides, so dropping the Unix library would not be a saving but a way of making one version mean two
things again — the failure P4 had just finished closing.

So it is declared instead. `keeps` gains three entries per Linux cell and one per macOS cell — the
library, its linker symlink, and `lib/libpython3.so`, the 20 KB stable-ABI forwarder that is what
`python3.dll` is on Windows — each with the reason travelling in the manifest. **This is the case
the field was worth building for**, and it is not the one P4 built it for: no per-artifact check
would ever flag a `.so` in `lib/`, so nothing but a written reason could stop the next person from
noticing 30 MiB of apparent duplication and deleting it. The smoke test proves it from the far side,
the way it proves `Python.h`: it computes the path `python3-config` is about to hand a linker and
requires the file to be there, after the tree has moved.

That check earned itself on its first run, and on the platform it does not apply to. It first asked
whether `sysconfig` reports an `LDLIBRARY` at all, on the assumption that Windows reports none —
and Windows reports `python313.dll`, a real file that lives at the root rather than under `lib/`,
described by a variable meaning *"what a POSIX linker is given"* on a platform with neither a POSIX
linker nor a `python3-config` to hand anything over. The Windows pack failed on a file it was never
missing. It asks about the platform now. Two other things worth keeping: `sysconfig`'s own `LIBDIR`
is still `/install/lib`, the build machine's path, so the check has to reconstruct the directory the
way the shell script does rather than believe what the interpreter reports; and there is no
`libpython*.a` in any `install_only` tree, so a static link was never on the table.

**And the gap P4 promised to explain line by line is now explained.** Pruned, on 3.13.15: Windows
48.1 MiB, macOS 52.2, Linux 81.4. Linux is 33.3 MiB larger than Windows and **30.3 MiB of that is
the library this task decided to keep**; macOS is 4.1 MiB larger while carrying a 16.7 MiB duplicate
of its own, which is to say every other part of the macOS tree is *smaller* than its Windows twin.
What is left over on Linux after the library — about 3 MiB — is not surplus but shape: OpenSSL,
SQLite and the rest are compiled into the interpreter there and shipped as separate DLLs on Windows,
which is the same feature set spelled two ways, the way P2's static-versus-shared PHP extensions
are. There is one thing left that *is* surplus, and it is in the binaries themselves: P4b.

Run against every tarball of 3.10, 3.11, 3.12, 3.13 and 3.14 on all three operating systems —
fourteen cells, every one clean through `prune`, `keeps` and `borrow.declare` — and packed end to
end on Windows for 3.10.21, 3.13.15 and 3.14.7, all three validating against the schema.

### [x] P4b — Python: the stripped variant is not stripped **(rule)**

`tools/python.py` takes `install_only_stripped` and says why at length: *"the same tree without the
debug symbols, and the saving is not marginal"*. Measured on the tarballs rather than on the
release notes, that is true of the tree and **not true of the two largest files in it**. Reading
the ELF section headers of Linux 3.13.15:

| | file | in sections the loader never maps |
| --- | --- | --- |
| `lib/libpython3.13.so.1.0` | 31,801,480 | **11,748,357** |
| `bin/python3.13` | 31,372,864 | **2,790,993** |

14.5 MB of an 85.3 MB tree, in sections with no `SHF_ALLOC` bit — nothing in a running process ever
reads them. Most of it is one section: `.rela.text`, 6.5 MB, which is a *relocatable object's*
table appearing in a finished shared library, the signature of a link done with `--emit-relocs` so
that a post-link optimiser could run. It arrived with 3.12 — 3.10 and 3.11 have none of it and only
1.6 MB of non-allocated weight altogether — and 3.14 carries 12.2 MB, so this grows rather than
settles. macOS is milder and the same in kind: `LC_SYMTAB` is 1.5 MB of the 3.13.15 dylib.

The obvious fix is the platform's own `strip`, and it is available: `build-python.yml` runs each
cell on its own native runner, so binutils is on the Linux legs and the Xcode tools on the macOS
ones. Two things make it a task rather than a line in P4a. It would be the second recipe to shell
out to a toolchain binary — PHP's `deplister.exe` is the first — and it would be the first to
*modify* a borrowed executable rather than delete files around it, which moves the smoke test from
"proves the archive is complete" to "is the only thing standing behind a mutated interpreter".
And the smoke test cannot cover what P4a just kept `libpython` for: it runs the interpreter, it
does not link anything against the library, so a `strip` that damaged the dynamic symbol table
would pass every check in the file and fail in somebody's `pip install`. Settle what proves a
stripped library still links before stripping one.

**The title is wrong and the variant is honestly named**, which is the first thing reading it as a
diff against `install_only` shows. Upstream's step removed every `.debug_*` section and every
`.rela.debug_*` beside it and touched nothing else: 80 MB out of `bin/python3.13`, 207 MB out of
`lib/libpython3.13.so.1.0`, a tarball of 118.5 MB becoming one of 34.8 MB. What it left is what
`--strip-debug` was never going to take. So the recipe's own sentence — *"the same tree without the
debug symbols"* — was exact, and the 14.5 MB measured above is not debug information at all.

It is two things with different owners, and the archive names the second one itself:

- **A symbol table**, on every version from 3.10 on and on both Unix formats: `.symtab` and
  `.strtab` on Linux, `LC_SYMTAB` on macOS. 1.6 MB per binary on 3.10, 2.4–2.8 MB on 3.13.
- **8.8 MB of relocations that were an input to an optimisation that never ran on the file holding
  them.** `.note.bolt_info` — a note llvm-bolt writes and nothing else does — is in `bin/python3.X`
  on 3.12, 3.13 and 3.14 and in no library; `.rela.text` is in the library on those same three
  versions and in no executable. So BOLT ran on the executable and consumed its relocations, while
  the library was linked with the same `--emit-relocs` and then left alone. That is why the weight
  appears in 3.12 and grows to 12.2 MB by 3.14, and it is why no amount of `strip -d` finds it.

Both come out together whether one wants them to or not: `.rela.text` names its symbols by index
into `.symtab`, so a tool that removes the table removes the relocations with it.

**Windows decides the direction, from the far side.** Its `install_only_stripped` ships **no `.pdb`
at all** — zero files, of 3,303 — so a Windows cell has already had its symbols taken and no
levelling-up is available. Taking the other two down to it is the move `prune` made with tkinter, in
the same direction and for the same clause of the rule.

#### The proof, which is what the task actually asked for

The check is `python.mapped`, and it is structural rather than empirical because the empirical proof
does not reach: the file most at risk is the one P4a kept *because nothing in the archive loads it*,
so a smoke test may start the interpreter a thousand times without touching it. Instead — if every
byte the loader maps and every table the linker reads is identical before and after, the two files
cannot behave differently, and running either one would say no more than that. On ELF the line is
drawn by the hardware: `SHF_ALLOC` is exactly the set `strip` may not touch, and `.dynsym`,
`.dynstr`, `.gnu.hash`, `.dynamic` and the loader's `.rela.dyn`/`.rela.plt` are all inside it.
Program headers are compared whole and separately. Measured across the operation on ten cells: 26
allocated sections identical, 9 program headers identical, byte for byte, including on the BOLTed
executable whose layout was rewritten after the linker was done.

**The flag is not the same on both Unix halves, and that is the finding, not a portability wrinkle.**
ELF keeps two symbol tables and a linker reads only the allocated one. Mach-O keeps *one*, whose
exported range `LC_DYSYMTAB` indexes — and `--strip-all` there empties it: measured on
`libpython3.13.dylib`, 1,755 exported symbols become 0 and `_Py_Initialize` stops existing in the
file, to save 66 KB more than `-x` saves. So `--strip-all` on Linux and `-x` on macOS.

Two more things only measuring found:

- **The arm64 cells are ad-hoc signed and the kernel answers a stale signature with `SIGKILL`.** Not
  an error a caller prints — the process does not start. And it would pass every other check here,
  for the same reason the library is unreachable. So `python.countersigned` recomputes the
  CodeDirectory's 4,230 page hashes rather than trusting the tool to have re-signed; corrupting one
  byte makes it name page 1420. `x86_64-apple-darwin` carries no `LC_CODE_SIGNATURE` at all, so the
  check is real on two cells of six and answers `None` on the rest rather than calling that a fault.
- **`mapped` refused its own first run, and was right to.** Comparing Mach-O segments whole reported
  `__TEXT` as changed on every *successful* strip, because `__TEXT` starts at file offset 0 and
  therefore contains the header and every load command — including the `LC_SYMTAB` and
  `LC_CODE_SIGNATURE` offsets a strip is *supposed* to move. It compares sections within segments
  now, which is the granularity the ELF branch always used.

#### Two claims in the paragraph above this one were wrong

This is not the second recipe to shell out to a toolchain binary and it is not the first to modify a
borrowed executable. `mariadb.strip_debug` has run `strip --strip-debug` over every machine file in a
borrowed bintar since before this repository had a rule — it is the reason `upstream.stripped`
exists. What is new here is not the operation but the *checking*, and MariaDB is the argument for it:
that function passes `capture_output=True` without reading the exit code, keeps whatever came out of
the tool, and declines to claim a saving only if the tree got bigger. Reconciling it with what P4b
built belongs where P1 already put it — with P6, which is where the check that reads these fields is
written.

`upstream.changed` is the fourth kind of difference and the one `borrow.declare` was written without,
because for three tasks there was no such thing: a recipe added files, removed files, or left them
alone. A file that ships at upstream's path and is not upstream's bytes is the difference a reader
comparing two archives is least able to account for, because it looks like a corrupted download. It
maps each path to the command that made it — `strip --strip-all` — rather than to prose: the reader
holding both archives wants to know what was done, and the argument lives here.

#### What it comes to, on 3.13.15

| | after P4a | after P4b | |
| --- | --- | --- | --- |
| Windows | 48.1 MiB | 48.1 MiB | nothing to do |
| macOS | 52.2 MiB | **49.5 MiB** | 2.7 MiB, 3 binaries |
| Linux | 81.4 MiB | **66.7 MiB** | 14.7 MiB, 4 binaries |

Four binaries, because `lib-dynload` holds three entries and one of them is `.empty`: everything
except `_dbm` and `_tkinter` is compiled into the interpreter, `prune` deletes `_tkinter`, and `_dbm`
is 2.38 MB on Linux with 834 KB of symbol table in it because gdbm is linked in statically.

The gap between Linux and Windows was 33.3 MiB after P4a and is **18.6 MiB** now — and
`lib/libpython3.13.so.1.0` is 19.1 MiB of that, which is to say all of it and more. Without the
second copy of the interpreter P4a decided to keep, the Linux artifact would be *smaller* than the
Windows one. macOS is within 1.4 MiB of Windows while carrying a 15.3 MiB duplicate dylib of its own.
Nothing is left in this row that one cell has and another does not.

Run on all ten Unix cell-versions available — 3.10, 3.11, 3.12, 3.13, 3.14 on `x86_64-unknown-linux-gnu`
plus 3.13.15 on the other three Unix triples — every one clean through `prune`, `strip_symbols`,
`keeps` and `borrow.declare`, and packed end to end on Windows for 3.10.21, 3.13.15 and 3.14.7, whose
archives came out byte-for-byte the size they were before P4b, which is what "no-op" has to mean.
Two honest limits on that. The tool driving it here was `llvm-objcopy` (as `rust-objcopy`), because
it reads ELF and Mach-O from any host and a runner's own `strip` cannot be run from this machine —
`mapped` is precisely what makes the substitution acceptable, since whatever the tool, the artifact
is not published unless the mapped image is identical. And extracting a Unix tarball on Windows turns
symlinks into copies, so the local run stripped `bin/python` and `bin/python3` as well; on a runner
`relocate.machine_files` skips them and the four paths above are what `upstream.changed` will name.

### [x] P4c — Python: what P4b's first run on CI found **(rule)**

P4b was ticked on 2026-08-16 and its workflow was not run again until the next day. When it was, all
four Unix cells were red, for two unrelated reasons, and the second one is the reason this section is
not a footnote to P4b.

#### `machine_files` was answering for three loaders at once

Three of the four cells died on `t32.exe is neither a 64-bit ELF nor a thin 64-bit Mach-O`. That file
is one of six PE launcher stubs `pip` vendors from `distlib` and ships on *every* platform, because
`pip` copies one to disk whenever it writes a Windows console script. They are in
`lib/python3.X/site-packages/pip/_vendor/distlib/`, which is under `lib`, so they were in
`machine_files` on Unix as well.

They had been invisible until P8a taught `relocate.kind` to read a PE, and after it the two callers
of `machine_files` disagreed without either saying so. `dependencies` dispatches on the **host**, not
on the file, so on Linux it asked `ldd` about a Windows stub, `ldd` answered "not a dynamic
executable", and `verify` passed over six files in silence — the same failure as P6a, in the opposite
direction and on the cells P6a was not looking at. `strip.mapped` is not silent: it refuses to write
over a file it cannot read, which is how this surfaced at all.

Fixed in `relocate.LOADED`, one table, at the only place both callers get their list. Nothing here
packs a tree for an operating system it is not running on — `ldd`, `otool` and `cygcheck` each answer
for one — so the format is not an argument. On linux-x86_64 the scan goes from 15 files to **9**,
identically under the default directories and under `("",)`; the six that go are the six stubs. The
comment P6a left in `python.py` claiming 15 was right about the count and wrong about what was in it,
and now says so.

#### GNU strip destroys the x86_64 interpreter, and P4b caught it

`linux-x86_64` died earlier, in `strip`, on `bin/python3.14`: *changed 2 thing(s) a loader or a
linker can see — segment 1, segment 2*. That is not a false alarm and the check is not too strict.
Run on ubuntu-24.04 with binutils 2.42, upstream's own binary, and one variable at a time:

| | |
| --- | --- |
| as upstream published it | `runs: 3.14.7 \| OpenSSL 3.5.7`, exit 0 |
| after `strip --strip-all bin/python3.14` | `undefined symbol: , version`, **exit 127** |
| the same file restored, nothing else changed | exit 0 |
| after `strip --strip-all lib/libpython3.14.so.1.0` | exit 0 |

python-build-standalone runs BOLT over this one cell, and BOLT moves `.dynstr`'s 45 KB to the end of
the file while leaving its `sh_addr` at `0x3ff5a0` — inside the first `PT_LOAD`, where the bytes are
not. An ELF says what it holds twice and nothing enforces that the two agree. GNU `strip` works from
the section table and writes a new program header table out of it, so it does not preserve that
disagreement, it resolves it: the first `PT_LOAD` shrinks from `0x1000` to `0x5a0`, `DT_STRTAB` ends
up pointing at unmapped memory, every dynamic symbol name reads back as the empty string, and the
process dies before `main`. `strip` says so itself — ``allocated section `.dynstr' not in segment`` —
in a warning going to stderr that nobody was reading.

**P4b's own stated limit is what hid this.** Its validation ran `llvm-objcopy`, because a runner's
`strip` cannot be run from a Windows machine, and llvm-objcopy carries the program header table
across instead of rebuilding it. The reasoning offered for the substitution — that `mapped` makes the
tool irrelevant, since the artifact is not published unless the mapped image is identical — was
sound, and it is exactly what happened: the artifact was not published. It just took until the real
tool ran to find out which tool that sentence was protecting us from.

So `strip.unmapped` asks first, out of the file rather than out of the warning, and the file is left
alone when the answer is yes. Skipped and named, not refused: upstream's binary is the one that
works, the archive ships either way, and this is the same trade `strip.IMAGES` already makes by
having no `windows` row. It costs 2.99 MB of a 100.8 MB tree. `lib/libpython3.14.so.1.0`, whose
layout is consistent, still strips and is worth 12.2 MB — four times as much — and the two macOS
cells and linux-aarch64 are unaffected.

Verified by running the recipe itself, `tools/python.py --version 3.14`, on Ubuntu 24.04: pruned,
stripped three files, left `bin/python3.14` alone with the reason printed, passed `verify`, passed
the smoke test through `pip` and a live TLS chain, and packed.

### [x] P5 — Ruby: make the two recipes answer the same questions **(rule)**

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

**The blocker had already been removed by somebody fixing something else.** `borrow.seven_zip` falls
back to `%SystemRoot%\System32\tar.exe` — bsdtar, which ships with Windows and reads 7-Zip — a
fallback added because 7-Zip itself failed on a `windows-2022` runner. So the audit ran here after
all, on 3.2.11, 3.4.10 and 4.0.6. It is **a prune and a note**, and the prune is the largest single
thing this repository has thrown out.

#### The prune: half the archive, and four fifths of one of them

| | tree | after | zip | after |
| --- | --- | --- | --- | --- |
| 3.2.11 | 86.3 MB | 39.2 MB | 28.8 MB | **14.7 MB** |
| 3.4.10 | 108.0 MB | 47.7 MB | 34.0 MB | **17.2 MB** |
| 4.0.6 | 276.1 MB | 51.1 MB | 53.5 MB | **18.8 MB** |

`share/doc` and `share/ri` — RDoc's HTML rendering of Ruby's own manual, and the `ri` database — are
60.3 MB of the 3.4.10 tree and **224.9 MB of the 4.0.6 tree**, which is 81% of an artifact of a
programming language. It grew four and a half times in one line while the language grew by 8%: 1,334
HTML files averaging 160 KB where 3.4 had 1,502 averaging 32 KB.

**The direction was decided by the Unix half rather than by the size**, which matters because the
size alone would have been a reason to do something hasty. `ruby_unix.py` does not merely delete
those directories, it passes `--disable-install-doc` so they are never generated — a positive choice
with an argument written beside it, that this repository has no business shipping four copies of
Ruby's manual on four targets for every version of every line. Nothing on the Windows side argues
back; the `.7z` carries them because RubyInstaller is a general-purpose distribution of Ruby.

`share/ri` was checked rather than assumed, and it is **not** the CPython terminfo case: it is
genuinely reachable, `RDoc::RI::Paths.path` names it inside the tree and `RDoc::RI::Driver` answers
`String#upcase` out of it. What it is not is reachable through anything either recipe publishes —
neither `provides` has ever held `ri` or `rdoc`, and IRB's own `help` in 3.4 routes to its command
table rather than to RDoc. A working feature of a Ruby installation that no MixEngine command
reaches, on two cells of six, against four that were configured never to build it.

The list is `tools/ruby_parity.py`, read by both recipes, for the reason P2 built `php_parity.py`:
"say so beside the other recipe" is a comment, and this is precisely the drift a comment did not
prevent — `ruby_unix.PRUNE` carried an argument about "four targets" while the other two carried
225 MB nobody had weighed.

#### The note: two things the six cells cannot be made to share

Asked what RubyInstaller does about the three flags the Unix build passes — the second bullet above
— a 3.2.11, a 3.4.10 and a 4.0.6 all answer the same way:

- **YJIT is not there.** `RubyVM::YJIT` is undefined and `ruby --yjit` answers *"warning: Ruby was
  built without YJIT support"*. CRuby does not build YJIT for `x64-mingw-ucrt`. `ruby_unix.py`
  configures `--enable-yjit` and its smoke test **refuses to publish** a Ruby that answers false to
  `RubyVM::YJIT.enabled?`, on the grounds that the flag warns rather than fails without a Rust
  compiler — so this is a capability four cells are checked for and two cannot have.
- **No native gem compiles.** `gem install bigdecimal` exits 1 with *"MSYS2 could not be found.
  Please run `ridk install`"*. The toolchain is a separate ~1 GB MSYS2 that RubyInstaller publishes
  as its own installer, which is why the archive borrowed here is the one without it. `ruby_unix.py`
  proves the opposite claim from the moved tree and calls it a claim about Ruby.
- **libedit is a difference on one line only.** Windows is built `--without-ext=readline` and
  `require "readline"` resolves to the pure-Ruby Reline on every line. `ruby_unix.py`'s own comment
  says the same is true of its 3.3+ cells, so only 3.2 differs — there the four Unix cells carry
  real libedit, ncurses and tinfo beside them. Recorded rather than acted on: the `Readline` API is
  the same either way and what differs is how a line editor behaves inside `irb`.

The first two are written into a new top-level `lacks`, a mapping of capability to reason. It is the
only field in the schema that is an admission rather than a decision, and it exists because the
alternative is an artifact quietly smaller than its version number promises — a daemon can read it
and refuse to enable a feature the cell has not got, and a blueprint asking for a native gem can
fail where it is written. The four cells that lack nothing ask and write no field, which is not the
same as never having asked.

Packed end to end on Windows for all three lines, every manifest validating against the schema with
`upstream.removed` naming both directories and `lacks` naming both capabilities. What is left is
P5a, which is a question this audit raised rather than one it was sent to answer — and which, on
being asked properly, turned out to be two answers already in the trees and one task nobody had
written down.

### [x] P5a — Ruby: the shared library, and the linker's half of the archive **(rule)**

Two questions P5 raised and did not answer, both written down as *needs the compiled half changed,
and this machine cannot build one*. Neither did. The first was a misreading, the second follows from
correcting it, and what actually needs a rebuild is a third thing the measurement turned up.

#### The asymmetry was read off a config variable and is not in the trees

`RbConfig::CONFIG['ENABLE_SHARED']` is `yes` on the two borrowed cells and `no` on the four compiled
ones, and P5a was written from that: *two cells can be embedded in a program and four cannot*. What
this repository is supposed to do at that point is go and look, and looking says `--disable-shared`
does not mean no libruby — it means libruby is a **static archive**. `lib/libruby-static.a` is
41.4 MB on Linux and `lib/libruby.3.4-static.a` is 28.3 MB on macOS, on 3.4.10; either is the
largest file in its tree, larger than the `bin/ruby` that already contains it linked.

The half that decides it is what names them. `rbconfig.rb` opens with

```ruby
TOPDIR = File.dirname(__FILE__).chomp!("/lib/ruby/3.4.0/x86_64-linux")
```

— that is `--enable-load-relative`, the flag this whole row turns on, so `libdir` follows the tree
wherever it is put — and then publishes `CONFIG["LIBRUBYARG"]` as `-Wl,-rpath,$(libdir)
-lruby-static $(MAINLIBS)` on the compiled cells and `-l$(RUBY_SO_NAME)` on the borrowed ones, which
is what `lib/libx64-ucrt-ruby340.dll.a` resolves. `lib/pkgconfig/ruby-3.4.pc` restates it with
`prefix=${pcfiledir}/../..`. So every one of the six hands an embedder a link line naming a file
inside the artifact, and deleting either file leaves the artifact describing something it does not
carry — the failure P4a kept CPython's unloaded `libpython` to avoid, here applying to six cells
rather than four. `--disable-shared` stays, and its comment is right for a better reason than it
gives: a static libruby is one fewer file for `bin/ruby` to find after the move, and the embedder is
still served.

The one thing that *is* uneven is smaller than the question. Windows' `LIBRUBYARG_STATIC` names a
`lib…-static.a` RubyInstaller does not ship, so the other of the two spellings is the empty one
there. Nothing follows it unless an embedder asks for it by name, and there would be nothing to ship
that answered.

#### So the second question answers itself, and it is two entries in `keeps`

The import library is not dead weight, and `include/` — 2.0 MB, the same on all six — is what a gem
with a C extension is compiled against. Both stay on the Windows cells although `lacks` has just
said no compiler is present, for the reason P4 kept `libs/python3XX.lib` on a Windows shipping no
compiler either: *cannot today* and *cannot ever* are different artifacts, `ridk install` is the
supported way a user closes the gap, and headers deleted here cannot be.

`ruby_parity.keeps(tree, os)` reads both off the finished tree — a glob, because the middle of the
name is the build's `RUBY_SO_NAME` and it is spelled three ways — and **refuses to describe a tree
that has neither**, since a build that stopped installing the static library is the decision
changing under the recipe rather than a file to shrug at. `ruby_unix.py` is the first *built*
artifact in this repository to go through `borrow.declare`, which took one fix: `declare` used
`setdefault` and would have hung an empty `upstream` on a manifest with nothing borrowed, and the
schema requires `url` and `sha256` in any `upstream` that exists.

#### What actually needs a rebuild is the thing neither question asked about

Weighing the static archive to argue about it is how this turned up: **63% of `libruby-static.a` is
debug information.** By ELF section, on 3.4.10 for Linux — 9.6 MB of `.rela.debug_info`, 4.3 MB of
`.debug_str`, 4.2 MB of `.debug_info`, against 3.9 MB of `.text`. And the binaries beside it are in
the same state, which is the part P4b would recognise:

| | tree | `bin/ruby` | its DWARF | static libruby | its DWARF |
|---|---|---|---|---|---|
| linux-x86_64 | 106 MB | 20.5 MB | 11.7 MB | 41.4 MB | 26.1 MB |
| macos-aarch64 | 81 MB | 6.9 MB | 0.9 MB | 28.3 MB | 19.0 MB |

macOS is lighter only because `assemble` already deletes the `.dSYM` bundles, which is where the
compiler put the executable's debug information and is not where it put the archive's. Windows has
none of this: RubyInstaller links with `-s`, visible in the `DLDFLAGS` its own `.pc` publishes. That
is P4b's finding on another row — Windows already stripped, the other four levelled down to it — and
it is **P5b**, not this task, because it changes what a compiled cell contains and nothing here can
compile one.

#### Verified

All three Windows lines packed end to end (14,717,349 / 17,177,020 / 18,833,034 bytes — the manifest
grew by its `keeps` block and nothing else did), each manifest validating with
`keeps: ['include', 'lib/libx64-ucrt-ruby3X0.dll.a']`. The Unix half cannot be built here, so what
was checked is the part P5a changed, against the published `ruby-3.4.10` artifacts for
`linux-x86_64` and `macos-aarch64`: `keeps` finds `include` and the right static library in each,
`declare` writes them, no `upstream` block appears on a built manifest, both validate, and a tree
with no static libruby is refused.

### [x] P5b — Ruby: the four compiled cells ship their debug information **(rule)**

Measured under P5a and left there: `bin/ruby` and `lib/libruby*-static.a` carried 37.8 MB of DWARF
on the Linux tree and 19.9 MB on the macOS one, where RubyInstaller ships none, having linked with
`-s`. P4b's task on another row, settled in P4b's direction — level down to the cell whose publisher
already did it — and separate from P5a for two reasons, of which the first turned out to be the easy
half.

#### The proof moved before it grew

`mapped()` and `countersigned()` are now `tools/strip.py`, with the flag tables and the driver that
runs the operation and refuses it. `python.strip_symbols` keeps its docstring and is four lines. The
argument for hoisting is the one this repository makes about `php_parity` and `ruby_parity`: two
recipes stripping their own binaries by their own rules are two opinions about one file, and nothing
outside either recipe could notice them diverging.

#### An `ar` archive is not an image, and that difference is the rest of the task

`strip --strip-debug` over a static library is a different claim from stripping a binary nothing
links, and the wrong instruction is *silently* wrong. `--strip-all` over `libruby-static.a` takes it
from 41.4 MB to 7.9 MB and leaves a file that resolves nothing — an artifact no test here would
catch, because nothing inside the tree links against that file either. So `strip.ARCHIVES` is
`--strip-debug` on Linux and `-S` on macOS, against `strip.IMAGES`'s `--strip-all` and `-x`, and the
four are not interchangeable in either direction.

`strip.resolvable()` is the check, and it compares what a linker resolves rather than what a loader
maps: the archive's own symbol index, then per member the globals, the bytes of every section that
will end up in somebody else's binary, and the relocations — **by name**. By name because a
successful strip renumbers both tables underneath them, so comparing them as bytes would report
every run that worked as a failure. Two things were learned by running it rather than by writing it:

- **`-S` means opposite things to the two tools.** To macOS's `strip` it is `--strip-debug`, which
  is why `strip.ARCHIVES` spells it that way; to `objcopy`, which is what stood in for the platform
  tool here, it is `--strip-all`. The rehearsal refused the macOS archive on the first run,
  correctly, for a reason that was about the rehearsal.
- **`ARM64_RELOC_ADDEND` puts a literal where every other relocation type puts a symbol or a section
  number**, so read alike, an addend of 12 becomes "the twelfth section" — and on 50 of the 116
  members of `libruby.3.4-static.a` the twelfth section is one of the `__DWARF` ones being removed.
  The check reported the strip it had just performed correctly as having damaged the archive.
  Nothing short of a real archive would have shown it.

One key is compared for loss rather than for equality, and it is the archive index. A common symbol
— what `VALUE rb_cArray;` compiles to — is in every member and in none of the `__.SYMDEF SORTED`
Apple's archiver wrote, and llvm's archiver puts all 165 of them back. Nothing that resolved stopped
resolving, which is the whole of the question, so the direction is enforced and the growth is
printed.

#### What it weighs

| | tree before | tree after | out | `bin/ruby` | static libruby |
|---|---|---|---|---|---|
| linux-x86_64 | 104.4 MB | 63.1 MB | 41.3 MB (40%) | 20.5 → 7.5 MB | 41.4 → 15.3 MB |
| macos-aarch64 | 78.4 MB | 55.3 MB | 23.2 MB (30%) | 6.9 → 5.4 MB | 28.3 → 9.3 MB |

More than P5a's DWARF figures because `--strip-all` takes the symbol tables of the images as well,
across 106 files on Linux and 104 on macOS rather than the two big ones alone.

#### Verified

Rehearsed against the published `ruby-3.4.10` artifacts for both cells, with `llvm-objcopy` standing
in for the platform `strip` this machine has not got, through `strip.symbols` itself rather than
around it. Every one of the 107 Linux and 105 macOS files came through clean, `countersigned()`
included, with one allowance stated: seven of the bundled Linux shared libraries differ in
`p_offset` and in nothing else — same `p_vaddr`, `p_filesz`, `p_memsz`, same section bytes — because
llvm-objcopy repacks an ELF where GNU strip leaves the hole. The recipe calls GNU strip, and P4b's
CI runs are the evidence that the identical comparison passes there. The negative: `--strip-all`
over the static library is refused on both cells, naming the symbols that stopped resolving.

And unlike P4b, this row's own smoke test reaches the file being stripped. `smoke()` compiles
`bigdecimal` from the relocated tree, and `rbconfig.rb` sends that link at `-lruby-static` — so the
archive P5a declared in `keeps` is exercised by a compiler after the fact, on the machine that
published it.

The row has to be repacked for any of this to take effect, and only the four compiled cells change.

### [x] P5c — Ruby: what P5b's first run on CI found **(rule)**

That last sentence is the whole of it. P5b was ticked on 2026-08-16 and the workflow was not run
again until the next day, and when it was, both Unix halves were red — the fourth time in two days
that a section was ticked on *the recipe is right* and nobody ran it. The last green Ruby build,
2026-08-15, printed no `stripped` line at all.

Three separate things came out, and only the first was the recipe's.

**It was stripping libraries it had not built.** `strip_symbols` took `relocate.machine_files(tree)`,
which is every binary in the tree — including the ten `relocate.bundle` had just copied off the build
machine. A distribution strips before it packages: the debug information is in a separate
`-debuginfo` package and there is nothing left in the file. The log said so in the line above the
failure, `stripped lib/libcrypt.so.1 (2,101,249 -> 2,105,768, -4,519 of symbol table)` — a file that
**grew** under `--strip-all`. The recipe now subtracts what it did not build, told apart by whether
`bundle` found it inside the work directory, which is the line `collect_licences` was already
drawing. OpenSSL, libyaml and libffi are built here and still stripped.

**`bin/ruby` lost its code signature to the strip.** It is rewritten by `install_name_tool` and then
stripped, and `countersigned` reported the ad-hoc signature no longer matching *at page 0* — the
header and the load commands. CPython's macOS cells never asked this: the x86_64 one carries no
signature and the arm64 one comes through Apple's `strip` still valid. `strip.resign` puts an ad-hoc
signature back and `countersigned` is asked again, which is only reachable after `mapped` has proved
every loadable byte identical.

#### And the check was wrong about `p_offset`

`lib/libcrypto.so.3` — OpenSSL 3.5.7, compiled by this recipe, so no subtraction would have saved it
— was refused with *changed 3 thing(s) … segment 7, segment 8, segment 9*. That message named a
segment and not a field, so the first thing done was to make it name fields, and the answer arrived
in one run:

> segment 7 [p_offset 7204864 -> 6369280], segment 8 [p_offset 7206784 -> 6371200],
> segment 9 [p_offset 7376528 -> 6540944]

Only `p_offset`, on three segments, each moved down by exactly 835,584 bytes. Same `p_vaddr`, same
`p_filesz`, same `p_memsz`, same flags: a strip that removed a non-allocated section lying earlier in
the file and compacted the rest. `p_offset` is where a segment *reads from*, and a loader maps
`p_filesz` bytes at `p_vaddr` without caring. The artifact was correct and the check called it a
failure — the same shape as the hard-coded `tree/bin` P6a deleted, and a check nobody can leave on.

Hashing the bytes at that offset was tried first, because *what* is there is the real question, and
it does not work. The program header table is itself inside the first `PT_LOAD`, so an offset that
moves changes the contents of `PT_PHDR` and of the segment holding it; that version failed an
ordinary `strip --strip-all` of an ordinary shared library, which is the check calling its own
subject a failure for the third time in this file. What takes `p_offset`'s place is
`strip.unmapped`, asked **again after the operation**: every allocated section must still lie inside
some `PT_LOAD`. The precondition P4c wrote and the postcondition P5c needs are the same question, and
together they tie the segment table back to the section table that `p_offset` was tying it to badly.

Checked four ways on Ubuntu 24.04 with binutils 2.42, because a comparison that had just been
loosened is one nobody should take on trust: the BOLT interpreter is still refused, an ordinary
shared library and an ordinary executable now pass and still load and run, and a `PT_LOAD` pointed
deliberately somewhere else is caught by name.

### [x] P6 — Make the rule something CI can fail on **(rule)**

P2–P5 are one-time corrections; this is what keeps them. `verify.py` already validates each artifact
against the schema. What it cannot do is compare the artifacts of *one version to each other*, which
is the whole of the rule's first half. This is not a hypothetical check: MariaDB's four asymmetries
were found by doing precisely that by hand, on a run whose six cells had all passed their smoke
tests, and P2 then packed five branches of PHP without GD for the same reason — a file renamed
between eras, invisible to every per-artifact check because what is missing cannot fail a load test.
Twice now, and both times by comparing rather than by reading.

Closed as `tools/parity.py`, run in `publish-index.yml` before the index is generated rather than
after — a version that fails this is a version that should not be described to anybody.

#### Across cells

For one `(kind, version)`, the feature sets must match. For PHP that is `extensions.static ∪
extensions.enabled` — the set a cell actually runs with, which is what P2 added `enabled` for;
`shared` is only what it could be asked to do, and on Windows it says the same word about `curl` and
about a debugger. For every kind, including PHP, it is also the commands in `provides`, which is
where the cheapest asymmetry of all lives and nothing was looking for it.

The comparison is **a cell against the union of its siblings**, not against a list of what a version
owes. That is deliberate and it is the half `php_parity.check` cannot do: a recipe sees one cell and
cannot know the other five have something. It is also why an empty cell costs nothing — a row of
three is compared as a row of three, and a target upstream never built is still an `exit 75` in its
own workflow.

Three things exempt a difference, and they are three different kinds of thing on purpose:

* **`lacks`**, written by the recipe into the artifact, for something *this cell* cannot do at any
  price. P5 invented the field for Ruby on Windows — no YJIT, no compiler for native gems — and this
  is what reads it. The reason travels with the archive, where a reader holding it can find it.
* **`php_parity.exempt`**, written here, for a difference that belongs to the whole row: the four
  extensions Windows has never had, the two its old builds gained late, and the two its 7.x builds
  compile in and cannot drop. That last pair used to be a paragraph of comment and is now
  `WINDOWS_UNTIL`, because a sentence is not something a program can be told.
* **`php_parity.SERVES`**, the one place two commands are one capability: a site is served through
  `php-fpm` on Unix and `php-cgi.exe` behind FastCGI on Windows, which has never had an FPM SAPI.
  Deliberately not a general mechanism — a table of "these two names mean the same thing" that grew
  would be a table for explaining differences away.

Everything outside those three is a defect this fails on.

#### Within one artifact

No path matching what the second half of the rule throws out and the manifest does not declare:
`*.pdb`, `*.dSYM`, `*.lib`, `*.a`, `include/`, `share/man`, `share/doc`, `share/ri`, `test/`.
Extensions match anywhere in the tree, directories only at its root — a `test` at the root is a
suite for testing the thing, while `lib/ruby/3.4.0/test/unit.rb` is Ruby's standard library.

A recipe that legitimately keeps one of them declares it, and the declaration is what the check
reads — so "no more than is needed" becomes a list somebody wrote down rather than a habit somebody
remembers. That field exists: P4 added a top-level `keeps`, a mapping of path to reason rather than
a list, because a check reading a bare path can only report *declared* and the argument is the part
worth keeping. CPython was its first writer — `include/` on six cells, `libs/` on two and the shared
interpreter on four — and P5a made Ruby the second, on all six at once and from both recipes, which
is the case that matters most here: `libx64-ucrt-ruby340.dll.a` and `libruby-static.a` are what this
would otherwise read as two rows disagreeing, and they are one decision spelled by two toolchains.
Ruby is also the first *built* artifact to write the field, so nothing here assumes a `keeps` implies
an `upstream` beside it. The Unix entries are the reminder that this will never be the whole of the
field: no pattern above would flag a `.so` in `lib/`, and P4a declared 30 MiB of one anyway, because
the reason it is kept is not reconstructable from the file. What is enforced is that everything
matching the patterns is declared; what the field is *for* is larger, and stays larger.

Two more checks came free, and neither repeats `borrow.declare`. That runs against the tree a recipe
is about to pack; this runs against the archive that came out, which is the only place a path lost
between the two can be caught — so a `keeps` naming something the archive does not contain fails,
and so does an `upstream.removed` naming something it still does. A removal that did not survive
packing is exactly the `mysql_ldb` shape, one stage later.

#### The fold-in

`upstream.stripped` was a sentence MariaDB wrote about an unchecked `strip --strip-debug`;
`upstream.changed` is a mapping P4b writes about a strip that refuses to publish unless the loader's
and the linker's whole view of the file came through identical. The fold-in is not a rename: it is
`mariadb.strip_debug` reading its own exit code and answering to `strip.mapped`, which is worth more
to that row than to this one — MariaDB strips 371 MB of bintar down to 27 and nothing checked what
came out. It asks for `--strip-debug` in the recipe rather than from one of `strip`'s two tables,
because `lib/libmariadb.so` is an image that *is* linked against and neither `IMAGES` nor `ARCHIVES`
means that; naming it at the call site is what keeps those two tables meaning what they say.

One thing had to change in `strip.symbols` for the fold-in to be honest. It now names a file in
`changed` only if the bytes actually moved, read from a digest rather than from a size — because
`mariadb_deb.py` calls the same function *expecting to find nothing*, Debian having stripped its own
binaries before packaging them, and a mapping that named 412 unchanged files would send a reader
holding both archives looking for a difference nobody made.

`stripped` stays in the schema, described as what it was, because artifacts published before this
carry it. That is the same call `loaded_extension` got in P2.

#### What it found

Pointed at the whole published catalogue — 194 manifests, 35 versions, six kinds — plus one archive
per kind opened and walked:

| | across cells | within artifacts |
| --- | --- | --- |
| caddy, mariadb, node, python, ruby | **0** | node: `include/` (3,324 paths), `share/man`, `share/doc` · python: `include/` (270), `share/man`, two `libitclstub*.a` |
| php | **370**, all against the Windows cell | `dev/php8.lib`, `php8embed.lib` |

**Not one of those is a defect a landed task has not already closed.** Node's three are P3's
`upstream.removed`; python's `include/` is P4's `keeps` and its `share/man` and itcl stubs are what
P4 deletes with the whole of `share/` and the Tk sweep; PHP's two `.lib` files are named in
`php_windows.prune`; and of the 370, `php-win` and `phpdbg` are deleted by that same function while
the rest are the extensions P2 wrote `extensions.enabled` for. Every one of them is an artifact
packed before the task that fixes it, which is the standing caveat every task since P2 has ended on,
now stated once by a program instead of six times by a person.

So the check's first verdict is the useful one: the rule is enforced from here, and the catalogue
has a repack owing. `publish-index.yml` is red until that happens, which is the correct behaviour
for a gate and not a reason to soften it — there is no flag to turn this off, because a fallback
that succeeds is the hardest kind of difference to notice.

#### Verified

The two archives P5a produced against the two it was measured on: `ruby-3.4.10` on Windows passes
both halves — 195 paths under `include/` and one `.a`, every one declared — while the linux-x86_64
and macos-aarch64 cells of the *published* 3.4.10 fail on exactly those paths, because they were
packed before `keeps` existed. Same version, same check, opposite answers, and the difference
between them is the task that landed in between. Formats: `.zip` through `zipfile`, `.tar.gz` and
`.tar.zst` through `tar`, since the 3.12 the index workflow installs cannot read zstd itself.

### [x] P6a — The Windows cells `relocate.verify` was never allowed to look at

P6 made the rule something CI can fail on. This is the discovery that on Windows it could not fail,
and that the second half of the reason survived the first being fixed.

`relocate.kind` used to judge a file by its magic and answer `None` for `MZ`, so `verify` had nothing
to say about a Windows tree. Every recipe written in that era therefore guards the call with
`sys.platform != "win32"` — honestly, because the call did nothing there. P8a taught `relocate` to
parse the PE import table out of the file itself, which quietly turned all of those guards from
accurate into stale.

**Removing a guard is not the whole of opening a cell, and Node is the proof.** With the guard gone
`verify` still reported no problems, because it looks in `BINARY_DIRECTORIES` and a Windows Node tree
has no `bin`: `node.exe` sits at the root. Measured on the packed 24.19.0 archives — 0 machine files
against 1 for `("",)` on both `windows/x86_64` and `windows/aarch64`, and *the same single file*
either way on `linux/x86_64` and `macos/aarch64`, so naming the root costs Unix nothing. A cell
opened without checking where its payload actually sits reports the same green as a cell still shut,
which is the failure this whole thread has now produced twice.

* **[x] `node.py`** — `verify(elsewhere, directories=("",))`, unguarded. Both Windows cells now
  genuinely read `node.exe`: eleven imports, every one resolving into System32, no MSVC runtime
  beside it. Upstream's build is self-contained and this is the first time anything here said so.
* **[x] `caddy.py`** — already unguarded and already `("",)`; confirmed by parsing a packed
  `caddy.exe` rather than by reading the source.
* **[x] `nginx.py`** — only the guard was wrong; `BINARIES = ("",)` had been right since the recipe
  was written. One binary, every import into System32, which is the claim its docstring already made
  about a statically linked build and could not check on the platform it was made about.
* **[x] `mariadb_build.py` and `mariadb.py`** — only the guard, and the default `directories` needs
  no help: `bin` and `lib` hold everything, 85 machine files in the x86_64 cell and 75 in the
  aarch64 one, the same either way as a root scan of the published 12.3.2. **Two files, because the
  first pass at this changed only `mariadb_build.py` and that is not the recipe that packs the
  Windows cell people install** — it compiles aarch64, while x86_64 is borrowed through
  `mariadb.py`. A list of recipes written from `grep relocate.verify` found five; the sixth was
  found by reading which script the green CI leg had actually run.
* **[x] `python.py`** — the sharpest case, below.
* **[x] `postgres.py`** — shipped a binary that cannot load, below.
* **[x] `php_windows.py`** — had no `verify` call **at all**, guarded or otherwise, so it never
  appeared in a search for one. Also below.

#### The third question, which is where the process runs from

Two recipes needed more than a guard and a directory, and both needed the same third thing.

**`verify` had `executable_dir = tree / "bin"` written into it as a constant.** That is a fact about
MariaDB's tree, not about trees. The Windows Python keeps `python.exe` and `python314.dll` at the
root with its extension modules in `DLLs\`, so the loader resolves their imports against the root —
and `verify` asked a `bin` that does not exist and called **34 modules broken**. They are not: all 34
import from a tree moved somewhere new on a cut-down `PATH`, run rather than reasoned about. A check
that fails what works is not a strict check, it is a check nobody can leave on. It derives the
directory from the tree now — `bin` if there is one, the root if not — and still takes it as an
argument for a tree that is neither.

**Python's default scan was not looking at nothing. It was looking at pip's binaries.** None of
`DLLs\`, `python.exe` or `python314.dll` is in `BINARY_DIRECTORIES`. What *is*, on a case-insensitive
file system, is `lib` — which matches `Lib\`, the Python **source library**. So the check read
exactly eight files: six vendored `distlib` launcher stubs (two of them ARM64, in an x86_64 archive)
and two venv launchers, and passed. Naming the root takes it from 8 to 53.

**PostgreSQL was shipping `bin/stackbuilder.exe`, which cannot load.** `UNWANTED` keeps StackBuilder's
own directory out at unpack time and `NOT_SHIPPED` removes the eight wxWidgets DLLs that EDB scatters
into `bin/` — and the comment explaining the second says, in as many words, that removing the
directory alone leaves the toolkit behind. Nobody wrote the sentence pointing the other way. The
executable stayed, its three wx imports resolve to nothing, and it was in every archive this recipe
had ever made. It reached no user only because P12 is still open and PostgreSQL has no release; the
check found it in the first minute it was allowed to look. Both halves are one entry now.

**PHP had no `verify` to guard, and its Windows tree was stranding a plugin.** Loading every
extension out of the relocated tree is a strong test and it is not this one: it exercises `ext/` and
what `ext/` imports, while `php8.dll`, the eleven ICU and OpenSSL DLLs beside it and
`extras/ssl/legacy.dll` are loaded by nothing in it. Pointed at the root, the check failed on the
first run — `lib/enchant/libenchant2_hunspell.dll`, 0.8 MB, importing `libenchant2.dll` and
`glib-2.dll`, neither of which is in the archive. `unreachable` sweeps the *root* for libraries
nothing imports, deliberately, because a reachability sweep cannot see a plugin loaded by name at run
time. The half nobody had written was the mirror image: a run-time plugin whose **owner** is being
removed. Dropping `ext/php_enchant.dll` orphans `libenchant2.dll`, and enchant's hunspell backend —
which nothing imports, being a plugin — stayed behind pointing at it. `stranded` is that half, to a
fixed point because a provider can sit on a provider.

Measured, not argued: every claim above comes from a packed archive — the published 3.14.7, 12.3.2
and 1.30.4, and an 18.6, an 8.5.9 and a 1.30.4 built on this machine — and each recipe was re-run
with its cell open before the change was written down.

Two things this turned up that are **not** P6a. Both are the same shape as the gap itself — work that
landed and was ticked without the workflow ever being run again — and both became first-class open
items: P4b's strip check had never once run in CI and failed on all four Unix Python cells, which is
**P4c** and is fixed; and every published PHP archive predates P2, so `pdo_firebird` is in them,
declared in `extensions.shared` and unable to load, which is **P13**. The PostgreSQL smoke test also
cannot pass on a GitHub Windows runner at all, which runs as an administrator; that is **P12a**.

---

## Services still to pack

Caddy, MariaDB and PostgreSQL are in. The remaining two are the same shape, and each owes the same
two things: an evaluation — **borrow before you build**, asking the catalogue rather than assuming
it, which is how MariaDB's row turned out to be wrong in three cells and how PostgreSQL's turned out
to be wrong in four — and a smoke test that exercises *run, configure, health-check, stop* rather
than `--version`.

### [x] P7 — PostgreSQL: EDB's two archives, and what is actually inside them

The evaluation question was Windows and macOS, and the answer moved three of the four cells it
touched. Closed as `tools/postgres.py` and `tools/postgres_smoke.py`. The workflow it added ran three
legs; P7a below took it to five.

#### What the catalogue actually says

Every line of this was measured against upstream rather than read off a download page, and the page
would have been wrong about most of it.

* **The PostgreSQL project publishes no binaries at all**, only source with a `.sha256` beside it.
  What `postgresql.org/download` points at for Windows and macOS is EnterpriseDB's.
* **Windows x86_64** — `postgresql-<version>-<n>-windows-x64-binaries.zip`, 344 MB. Borrowed.
* **macOS** — one `osx-binaries.zip`, and `bin/postgres` inside it is a **fat Mach-O carrying x86_64
  and arm64**. One download is the build for two cells, which nothing else in this repository can
  say. Read out of the file's own header, four bytes at a time, over an HTTP range request — the
  archive is 445 MB and this cost 64 KB. What each cell *ships* is one slice of it, which is P7b.
* **Linux** — `…-linux-x64-binaries.tar.gz` answers 403 for every version tried. EDB stopped. That
  became P7a, which packs both Linux cells from the project's own `.deb` packages instead.
* **Windows on ARM** — nothing, from anybody, and P7b found out why: PostgreSQL does not compile
  there before 19. The cell is empty until it does, and P7c is what opens it.
* **The version catalogue and the end-of-life dates are one document**, `versions.json`, which states
  every major, its newest minor, whether it is supported and the day support ends. The same trade
  `mariadb.py` makes with the MariaDB REST API — and `data/eol.json` gets a `postgres` block all the
  same, which that file had already promised to whichever service branched its support next. Read
  from a publisher rather than a schedule page is a fact about where the dates came from; the index
  still has to be rebuildable from release assets years later, and a generator that called the API
  would answer differently depending on the day it ran.

**The floor is 14, and it is where the archive changes shape rather than a preference.** EDB's macOS
zip for 13 is a *thin x86_64* Mach-O; from 14 on it is universal. A 13 packed here would mean Intel
on a row where every other version means both architectures — one version meaning two things,
decided by which cell a user installed from. PostgreSQL 13 also went out of support in November
2025, so upstream's answer and the catalogue's shape agree on where to stop.

#### Most of the download is not a database, and it is never written to disk

The Windows zip unpacks to 914 MB of which **717 MB is pgAdmin 4**, an Electron desktop application
with its own Python; the macOS zip is 1,215 MB with pgAdmin and StackBuilder inside. So `UNWANTED` is
applied *while unpacking* rather than after, and the second reason is stronger than saving a minute
of I/O per run: `pgsql/pgAdmin 4/python/Lib/site-packages/azure/mgmt/rdbms/…` is past `MAX_PATH`, and
extracting this archive whole dies half way with a `FileNotFoundError` naming a file whose only
problem is the length of its name. Every root skipped is named in `upstream.removed` all the same —
"never unpacked" and "deleted" are the same difference to a reader holding both archives.

That meant not using `borrow.unpack`, which meant doing what `zipfile` does not: **permission bits
and symlinks**. A zip stores the Unix mode in the top half of `external_attr` and `extractall`
discards it, so a macOS tree unpacked that way has a `bin/postgres` nobody can execute and a
`lib/libpq.5.dylib` that is a copy of its target instead of a link to it.

What else is not shipped is the rule rather than the size: headers, static and import libraries,
PGXS and its `pkgconfig`, `pg_config` and `ecpg` — without `include/` there is nothing for either to
compile, which is the argument that removed `mariadb_config` — the test modules that sit beside the
real ones, and 14 MB of wxWidgets DLLs in `bin/` that exist for StackBuilder's window.

**And the procedural languages that are not PostgreSQL's own.** `plperl`, `plpython3u` and `pltcl`
each need an interpreter *installed on the user's machine*: EDB's `plperl.dll` links a Perl this
archive does not carry, so `CREATE EXTENSION plperl` on a clean machine fails with a message about a
missing library. Debian packages each as its own `postgresql-plperl-N`, so P7a's cells could not have
had them either. `plpgsql` is compiled into the server and stays. 344 MB becomes a 38 MB artifact
with 46 extensions in it.

#### Two things the rule caught before anything was published

**EDB's own two archives disagree.** 18.6 ships `system_stats.control` on macOS and not on Windows —
an extension of EDB's own, present in one cell of a version and absent from the other, inside a
single publisher's release. It is removed from both rather than added to one, because a third route
from Debian's packages could never have it.

**P6 only compared extensions for PHP, and this is the row that says why that was the exception.**
`parity.offered` took `static ∪ enabled` and ignored `shared`, for a reason the schema states: on
Windows PHP's `shared` says the same word about `curl` and about a debugger. Nothing else here has
that problem — a PostgreSQL extension is created in a database by whoever wants it, never switched on
in configuration, so `shared` *is* what a cell offers. So it now counts for every kind but PHP, and
the 46 extensions of this artifact are compared rather than skipped.

#### What is not checked, and it is said out loud

**EDB publishes no checksum.** Every other borrow here is checked against a digest its publisher
states — nodejs.org's `SHASUMS256.txt`, Caddy's `checksums.txt`, MariaDB's REST API,
python-build-standalone's `SHA256SUMS` — and `get.enterprisedb.com` answers 403 to `.sha256` and
`.md5` beside every archive it serves. What is left is TLS to the publisher's own host with no mirror
redirector between, which is strictly what `ruby.py` records when RubyInstaller publishes no checksum
file. `upstream.verified_against` says *"HTTPS to get.enterprisedb.com; EDB publishes no checksum for
these archives"*, in those words, because an artifact implying otherwise would be making the one
claim its reader cannot check. The archive's own SHA-256 is still computed and published, so the next
person to download it can compare with this one.

The relocation check is a check and not a correction, for a related reason: EDB's `lib/` already
carries OpenSSL, ICU, krb5, libxml2 and lz4, so the expected answer is that nothing reaches outside
the tree. `relocate.bundle` would *make* that true by rewriting load commands — and a rewritten file
is no longer the bytes EDB published, which is a difference the recipe would then have to declare.
Asking first is how a recipe finds out whether it needs to.

#### The proof

MariaDB's shape, one database over: the tree is moved somewhere it has never been, `initdb`
bootstraps a cluster whose superuser has a password and answers scram-sha-256, the server starts
against a rendered `postgresql.conf` — passed with `--config-file`, never written into the data
directory, so generated configuration stays disposable and the data directory stays sacred —
`pg_isready` answers, a row is written and read back, and `pg_ctl stop -m fast` stops it with the
clean-shutdown line looked for in the log afterwards.

`CREATE EXTENSION hstore` and `pgcrypto` are the part that is about *this* recipe rather than about
PostgreSQL. An extension needs a module in the library directory **and** a control file and SQL
script in the share tree, the two live in different halves of the archive, and this recipe deletes
from both — so a pruning that took one half would pass every other check here and fail on a user's
first `CREATE EXTENSION`. `pgcrypto` earns its place twice: `digest()` is computed by the OpenSSL
travelling inside the archive.

**One finding that is invisible from a build log.** Run without a stated locale on a machine whose
system locale is Vietnamese, `initdb` reports *could not find suitable text search configuration for
locale "Vietnamese_Vietnam.1252"*, quietly sets the default text search configuration to `simple` —
a cluster where full-text search does not stem — and **exits zero**. The same artifact on two
developers' machines produces two databases that answer differently, and no packaging check could
see it. The smoke test states `--locale=C -E UTF8` so that it is reproducible; what it teaches the
daemon is that it has to *choose*, because the default is whatever the machine happens to be.

#### Verified

The Windows x86_64 cell was run end to end on a real Windows 11 machine before any of this was
written down: 344 MB borrowed, 137 paths not shipped, `initdb` through `pg_ctl stop -m fast`, and a
38 MB artifact out. `tools/parity.py` passes it on both halves — nothing matching the surplus
patterns, and all 137 `upstream.removed` paths absent from the archive it actually produced.
`mkindex.py` builds an index from it that dates the package 2030-11-14 out of the new `eol.json`
block, and `verify.py` accepts that index, including its own new check that a database provides both
a server and the first-run job that gives the server something to start against.

Running `parity.py` over the whole published catalogue still reports exactly the 370 problems P6
found, which is the answer wanted from the `shared` change: only PHP manifests carry that field
today, and PHP is the kind it deliberately does not count for.

The macOS legs are written and unrun — this machine is not a Mac — and two things there are
predicted rather than proven. The first is arithmetic and can be checked now: both archives lose
their procedural languages and EDB's two additions, 61 − 15 on Windows and 62 − 16 on macOS, and both
land on **46**, which is what the Windows artifact actually shipped. The second cannot: whether
`extract`'s permission bits and symlinks come out right, and whether a universal build's arm64 slice
starts on an arm64 runner.

### [x] P7a — PostgreSQL on Linux, from the project's own packages

The two cells P7's evaluation moved rather than filled, and it turned out to be one task rather than
two: `apt.postgresql.org` builds `amd64` and `arm64` alike, so a single recipe covers both. Closed as
`tools/postgres_deb.py`, with `build-postgres.yml` grown from three legs to five.

#### It is the best-checked download in this repository, and P7 is the worst

EDB publishes no digest at all — P7 says so in `verified_against` because there was nothing else
honest to say. This route has two links and follows both: the suite's `Release` file states the
SHA256 of the `Packages` index, and `Packages` states the SHA256 of every `.deb` in it. Nothing is
taken on trust but TLS to the project's own host, and a broken link is a stopped run rather than a
quiet substitution. That the *same database* arrives one way with a chain and the other way with
none is worth leaving visible in the two manifests rather than smoothing over.

**And it is `apt-archive.postgresql.org`, not `apt.postgresql.org`.** The live repository keeps
roughly the last three minors of a major and drops the rest — at the time of writing its `jammy-pgdg`
suite offers 18.3, 18.4 and 18.6 and nothing before them, which would make this index's promise that
a blueprint pinning 18.4 keeps working true for about four months. The archive host keeps every build
ever pushed, twenty-five of PostgreSQL 14 alone, and is still written to daily, so using it for
current versions costs nothing. The same trade `mariadb_deb.py` makes between `deb.mariadb.org` and
`archive.mariadb.org`, and it is the second time the live/archive distinction has decided a recipe.

#### The layout may not be rearranged, and MariaDB's answer is the opposite one

This is the finding, and the roadmap had it backwards: it said the Debian layout "puts the server
under `lib/postgresql/<major>/bin` and the client tools in `bin`", which is wrong — all thirty-six
programs are under `lib/postgresql/<major>/bin` — and it assumed the tree would be rearranged into
one shape the way `mariadb_deb.py` rearranges its packages into upstream's bintar layout.

It cannot be. MariaDB is *told* where it lives, through `--basedir`, so its payload can be moved
anywhere and still resolve. PostgreSQL is told nothing and works it out, in `make_relative_path` in
`src/port/path.c`: it takes the `bindir` and `sharedir` compiled into the binary, strips the prefix
they share, and then requires the directory it is **actually running from to end in what is left**.
Debian configures `--bindir=/usr/lib/postgresql/18/bin --datadir=/usr/share/postgresql/18`, so what
is left is `lib/postgresql/18/bin`; a `postgres` moved to a plain `bin/` does not end in that, the
tail match fails, and the binary silently falls back to the absolute `/usr/share/postgresql/18` that
no artifact has. It would still start. `initdb` would work on the packager's machine and fail on
every other one — the exact failure shape this repository packs artifacts to avoid.

So the tree keeps Debian's `/usr` shape exactly, and `bin` is laid over it as a **symlink**, which
`find_my_exec` resolves before it measures anything. All five cells now report `bin/postgres` and
upstream's binaries still find their own share directory. Two consequences worth having written down:

* **The symlink goes on after `relocate.bundle`, not before**, and that order is a finding rather
  than a style. `$ORIGIN` in an ELF search path is the *resolved* directory of the object being
  loaded, so a `bin` link laid first makes `relocate.rewrite` compute `$ORIGIN/../lib` from a path
  the loader never uses — pointing at `lib/postgresql/<major>/lib`, which holds the extension modules
  and no bundled library at all. Everything resolves on the packing machine, where the distribution's
  own copies are still installed, and nothing resolves on a user's.
* **`postgres_smoke` learned to glob.** `MODULES` and the new `CONTROLS` are patterns rather than
  paths because one of the three routes puts the major version in the middle of both, and a table
  cannot name a number it will only learn at run time. `postgres.PRUNE` became patterns for the same
  reason, with `**` so that one entry covers all three depths — writing them out is how that list
  came to have two spellings of `man` and not the third.

#### Two things left out that no check could have caught

`postgresql-<major>-jit` is PostgreSQL's LLVM expression compiler, and Debian is the only publisher
here that offers one: EDB's Windows archive has no JIT provider and its macOS archive has only the
headers, which this repository drops with the rest of the SDK. Taking it on Linux alone would make
`jit = on` — PostgreSQL's own default — mean *compile the query* on two cells of a version and not on
the other three, with no error and no log line either way. `sepgsql` is the same shape one size
smaller: built on Linux only, useless without an SELinux policy loaded, and the one module in
Debian's set neither EDB archive has.

Neither is a command nor an extension, so `parity.py` is blind to both. That is the point of writing
them down here: the program covers what it covers, and a row is still allowed to need a decision the
program cannot make.

#### One thing this cell needs that its siblings carry

Debian builds `--with-system-tzdata`, so the 646 files of timezone data are not in the archive — and
cannot be put there, because the compiled-in `/usr/share/zoneinfo` is the one path PostgreSQL never
relocates. Copying the files in would produce a directory the server does not open. It is named in
`requires` beside the glibc floor, which is where `php_windows.py` already puts `vcredist`: a
dependency a user already has is a fact to state, not a reason to refuse a cell.

#### The proof, and the part of it that ran here

The claim is P7's, unchanged and now exercised by a second producer: `initdb` through
`pg_ctl stop -m fast`, with `hstore` and `pgcrypto` created and used. Only a Linux runner can run it.

What *was* run here is the half that decides whether the artifact is the same PostgreSQL. The three
packages were downloaded, the rearrangement and `postgres.prune` were run against them offline, and
the resulting tree was read by the same `describe` and `extensions` the EDB route uses: **15 commands
and 46 extensions, and against the published Windows cell the difference is empty in both directions
and both ways round.** `parity.across` over the three cells reports no problems. Two packagers who
share no build system arriving at the same 46 is the closest this repository gets to a second opinion
on what a version means — and it is also the strongest evidence yet that P7's own pruning was right,
since Debian threw out the procedural languages by never putting them in the package.

The same refactor was re-run against the EDB archive P7 packed from, and it produces P7's answer
exactly: the same 137 paths in `upstream.removed`, the same 15 `provides`, the same 46 extensions,
the same `extension_dir`. `parity.py` over the whole published catalogue still reports its 370.

#### Where the licence collection now lives

`bundled_licences` and `licence_texts` moved from `mariadb.py` to `relocate.py`. Nothing in them was
ever about MariaDB; they were there because MariaDB was the first kind to bundle a system library,
and the moment a second one did the choice was to import a database module from a database module or
to put them beside `relocate.bundle` — the function that creates the obligation and that already
answers with *where each library came from* for no other purpose than this. Three call sites moved
with them and nothing else changed.

### [x] P7b — PostgreSQL: Windows on ARM, and the second slice of a universal build

Two loose ends of P7, together because both are about an architecture that is carried rather than
built. Both were to be measured before being written, and the measurements answered the two halves
in opposite directions: one was worth more than the task expected, and the other cannot be done at
all — by upstream, not by this repository.

#### The archive is written down three times, and only one of the copies is inherent

The task asked how much of the macOS archive is machine code. The answer is **essentially all of
it**, and the measuring turned up a second duplication nobody had gone looking for.

After the unwanted roots are skipped and `prune` has run, the macOS tree is **362 MB** — nine times
the Windows artifact of the same version, which nothing in P7 had remarked on. 199 MB of that is the
same bytes written more than once, in two different ways:

* **161 MB is the universal build**, x86_64 and arm64 in one file, of which each cell can execute
  one. That is what the task was about.
* **38 MB is EDB shipping each dylib's version chain as whole copies.** `libicudata.dylib`,
  `libicudata.77.dylib` and `libicudata.77.1.dylib` are three identical 64 MB files where an ordinary
  ICU install is one file and two links, and 23 groups in `lib/` are that shape. The archive is not
  incapable of storing a link — it holds 78 of them, every one inside pgAdmin.

So `link_versions` puts the chains back and `thin` keeps the slice the cell can run: **362 MB
becomes 82 MB on arm64 and 80 MB on x86_64**, with 15 provides, 46 extensions and
`extension_dir` unchanged, which is the whole of what `parity.py` compares. The three cells that
exist offline agree on both fields with nothing on either side of the difference.

#### Re-signing is what the task expected and what the finding removed

The task assumed a `lipo` over the tree means re-signing every Mach-O on arm64. It does not, and the
reason is worth writing down: **a fat file's slices are complete Mach-O files whose internal offsets
are slice-relative**, so lifting one out changes nothing inside it, its `LC_CODE_SIGNATURE`
included. The operation is therefore a byte-range copy read from the file's own header, in Python,
with no `lipo` involved.

Which turns the signature from an obstacle into the proof. All 173 binaries carry an ad-hoc
signature whose CodeDirectory holds a SHA-256 of each 4 KB page; `strip.countersigned` — written for
P4b, where a strip *did* resize the file — recomputes every page against the bytes now on disk, and
all 173 verify. An extraction off by one byte fails there rather than on a user's machine, where
arm64 answers a bad signature with `SIGKILL` and nothing printed. `upstream.changed` names all 173
paths: 35 links and 138 slices.

#### Every macOS measurement had been made about two machines at once

The quieter half, and the reason this belongs before anything else in `main` rather than at the end.
`otool` reports a universal binary's load commands **once per architecture**, so `relocate.verify`
was reading both dependency lists and `relocate.floor` was taking the higher of the two builds'
minimum macOS versions and calling it this cell's. Neither was wrong to do; both were being handed a
file that answers for two machines. Thinning first means each cell measures what it ships.
`relocate.loadable`'s note that "nothing in this table ships one" was a fact about the archives when
it was written and is now a fact this repository keeps.

#### Windows on ARM is upstream's empty cell, and the reason is six lines of Perl

The task's premise was that the row is one `mariadb_build.py` already walks — compile natively,
PostgreSQL builds with meson and MSVC. Asked instead of assumed, **PostgreSQL does not build on
Windows/ARM64 on any version MixEngine offers**, and the evidence is upstream's own buildfarm.

Two Windows/ARM64 MSVC animals exist, approved 2025-12-12 and 2026-03-16. The newer reports on
`master` only. The older tried the stable branches once each: on 18 and 17 the build stops at
**target 1206 of 2047**, with 1205 objects already compiled for `/MACHINE:ARM64`, at
`src/tools/msvc_gendef.pl` — the Perl script that generates the export file the server's own
extensions link against — whose usage line reads `arch: x86 | x86_64` and which exits rather than
accept `aarch64`. `master` and `REL_19_STABLE` accept it. 16's single run failed at configure for an
unrelated reason in the owner's own settings, and 14 and 15 have no meson build at all.

Carrying the two-line patch was considered and refused, on the evidence's own terms: **nobody knows
what target 1207 does**, because upstream has never got past 1206 on those branches. A patch here
would be this repository claiming a platform its publisher does not test, on evidence that stops
precisely where the evidence stops — and the first user to find the next blocker would find it as a
broken artifact rather than as an empty cell. The cell stays empty and the index says why.

### [ ] P7c — PostgreSQL on Windows/ARM64, when 19 makes it possible

Blocked on upstream rather than on anything here, and unblocked by a release: PostgreSQL 19 accepts
`aarch64` where 18 does not. When `versions.json` lists it, this is `postgres_build.py` — meson and
MSVC on `windows-11-arm`, the shape `mariadb_build.py` already has, with vcpkg for the ICU, OpenSSL,
libxml2, lz4 and zstd the borrowed cells carry — and the row it has to match is not a matter of
taste: 15 provides and 46 extensions, which `parity.py` will check against the other five cells.

Two conditions before it starts, and they are cheap to re-ask. The buildfarm animal has to be green
on the branch, not merely past target 1206 — the last run on `REL_19_STABLE` failed in
`pg_amcheckCheck`. And EDB has to still not publish one, because a borrow beats a build here as
everywhere else.

#### Re-asked 2026-08-16: still shut, and one of the conditions was watching the wrong machine

**The release.** `versions.json` names 18 as the newest major and lists no 19 at all — 19 is at
**Beta 3**, released 2026-08-13, the same day as the five minors, with no RC yet and GA planned for
September. `series()` reads that document, so until it gains a 19 row there is nothing for a recipe
to offer. This is a wait of weeks, not of anything anybody here has to do.

**The borrow.** Still none: `postgresql-18.0-1-windows-arm64-binaries.zip` answers 403 where the
`windows-x64` name of the same version answers 200. A build is still the route.

**The buildfarm, which is the condition that needed correcting.** The two animals have names, and
writing them down is most of what makes this cheap to ask again — `unicorn` (approved 2025-12-12,
Windows 11 Pro 10.0.26100, MSVC 19.50.35718, arch `aarch64`) and `hoatzin` (2026-03-16, Windows 11,
msvc 19.50.35725, arch **`ARM`** — which is why a search of the member list for `arm64` finds only
one of the two).

The condition as written points at `unicorn`, and **`unicorn` is not a fair witness**. Its last green
run on any branch was 208 days ago; the 49 runs since have all failed, 44 of them at
`pg_amcheckCheck`, on `master` exactly as on `REL_19_STABLE`. And each takes 13 to 17 hours where
`hoatzin` does the same work in 45 minutes. A failure that behaves identically on every branch and
takes twenty times as long as the same platform needs is measuring the machine, not the branch.

`hoatzin` is what the platform actually looks like: **44 of 53 `master` runs green**, ~45 minutes
each, the newest 16 days ago. So Windows/ARM64 MSVC compiles PostgreSQL *and passes its whole test
suite* — a stronger statement than P7b could make, and the reason to expect 19 to work rather than
merely to hope it. `msvc_gendef.pl` accepting `aarch64` was the gate; on `master` it is open and
everything behind it is green too.

But `hoatzin` has never reported on `REL_19_STABLE`, or on any stable branch. So the condition as
written **cannot be met by evidence that exists** — not because 19 is broken there but because the
only animal reporting on 19 is the broken one. The question to re-ask is therefore not "is `unicorn`
green on 19" but **"is there a green `REL_19_STABLE` run from an animal that is also green on
`master`"** — `hoatzin` picking up the branch, or `unicorn` recovering. If 19 ships and neither has,
then the first green run of `postgres_build.py` is itself the evidence, and it costs one
`workflow_dispatch` to find out.

### [x] P8 — Redis and Memcached

**The evaluation here is likely to answer "build", and on the platform that usually borrows.**
Neither project publishes an official Windows binary; what circulates is a Microsoft fork abandoned
at 3.0 and community rebuilds. So this task decides between compiling both natively on a Windows
runner and declaring the cell empty — and an empty cell is a real answer, not a failure, as long as
the index says so instead of a user finding out.

The smoke test is the cheapest in the table: start, `PING`, `SET`/`GET`, `SHUTDOWN NOSAVE`.

#### It answered "build" on four cells and refused the question on two

**The decision this task was written to make does not exist.** It assumed a choice between compiling
on a Windows runner and declaring the cell empty, and there is no first option: Redis 8.10 ships no
`CMakeLists.txt`, no `win32/` directory and no project file at all — a `src/Makefile` around POSIX
`fork()`, `epoll` and `kqueue`, with a README naming Linux, OSX, OpenBSD, NetBSD and FreeBSD.
memcached is autotools with a privilege-dropping source file per Unix (`linux_priv.c`,
`darwin_priv.c`, `freebsd_priv.c`, `openbsd_priv.c`, `solaris_priv.c`) and none for Windows. That is
a stronger finding than "hard", and it is the same shape as P7c's above: the empty cell belongs to
upstream, not to this repository, and it does not open by trying harder. It is a *harder* shape than
P7c's, in fact — PostgreSQL's Windows/ARM64 cell has a date it opens on, and this one has none.

The three alternatives MixEngine's own table named were each asked. **Valkey** is a fork of the same
POSIX program, unsupported on Windows, and its install page sends a Windows user to WSL — excluded by
ADR 0003. **Memurai** is proprietary and cannot be redistributed. **The community rebuilds** are the
unmaintained fork the plan already refused. So both Windows legs run and exit 75, as Caddy's do for a
release with no archive.

What the four Unix cells got, and what each one settled:

* **`tools/redis.py`, floor 7.2, resolved against `redis/redis-hashes`** — upstream's own one-line-per-tarball
  catalogue with a SHA-256 and a URL, so what exists and what it should hash to come from one
  document. The floor is a licence decision as much as an age one: 7.2 is the last BSD-3 line, 7.4 is
  RSALv2/SSPLv1 and 8.0 adds AGPLv3, and upstream still patches all of them. 58 stable releases
  across eight lines resolve today.
* **Core Redis only.** From 8.0 the tarball vendors RediSearch, RedisJSON, RedisTimeSeries,
  RedisBloom and vector-sets — 6,671 files, and why 8.10.0 is 21 MB where 7.2.15 is 3.4 MB. Building
  them wants LLVM 21, Rust 1.94 and a CMake pinned to ≤ 3.31.6, on four cells, forever, and it would
  make the 7.2 cells of this row a different thing from the 8.x ones. `make -C src all` is what
  upstream's own `scripts/build.sh redis` runs for the core and is the one path that serves both
  lines.
* **`tools/memcached.py`, floor 1.6**, catalogue from the GitHub *tags* because memcached publishes
  no releases at all, tarball and digest from `memcached.org/files/<name>.tar.gz{,.sha1}`. libevent
  2.1.13-stable is pinned with its SHA-256, compiled static and its licence shipped, because after a
  static link nothing in the tree names it. No `--enable-shutdown`: it would put an unauthenticated
  `shutdown` verb on a loopback port, and ADR 0008 already says stopping this service without a
  signal costs nothing.
* **No TLS on either.** These are the only built rows here that bundle *no* libraries: with TLS off
  both binaries import nothing outside the C runtime, which `relocate.verify` states rather than the
  build flags.

Two things found on the way that are worth more than the row itself.

**`download.redis.io` answers 403 to `Python-urllib/3.x` and 200 to any other `User-Agent`**, same
URL, same second. A recipe using `borrow.fetch` resolves a version perfectly and then dies on the
download with a status that reads like the release was withdrawn. `borrow.fetch` gained a `headers`
argument — defaulted to none, so the other eight recipes are untouched — and `redis.py` names itself.
Caught here rather than on the first CI run because the resolvers were exercised against the live
catalogues before anything was committed, which is the cheapest test in this repository and the one
most easily skipped.

**redis.io's published lifecycle table is for a different product.** Searching for Redis Open
Source's end-of-life dates leads to a detailed, dated schedule for **Redis Software**, the commercial
cluster product, whose majors overlap the open-source numbering without meaning the same thing — it
has a 7.22 and a 7.8 that Redis Open Source has never had, beside a 7.2 and a 7.4 that it has.
Transcribing it would date every artifact here against another product's support window with nothing
looking wrong. `data/eol.json` records the trap for P10, which is where the dates belong; neither
kind gets an entry now.

Left for whoever runs it: **nothing here has been through CI**. The resolvers, the licence collection
across both Redis lines, and the refusal of an unnamed `deps/` directory were all exercised locally;
the compile, the relocation check and the smoke tests need a `workflow_dispatch` on each of
`build-redis.yml` and `build-memcached.yml` to be anything more than an intention.

### [x] P8a — Redis on Windows, because P8 asked one word wrong

**P8 concluded from "there is no Windows build system" that there is no Windows Redis, and those are
different claims.** The first is true and was re-read on a runner rather than trusted: no tag of
`redis/redis` between 2.6 and 8.10 has ever contained a `CMakeLists.txt`, a `win32/` or an `msvs/`,
and the Windows support that once existed lived in `microsoftarchive/redis` — a separate fork with
its own `src/Win32_Interop`, stopped at 3.0.504 in 2016. The second does not follow from it. Whether
a program runs somewhere is a question about the interfaces it calls, and compiled *against* a POSIX
runtime rather than ported to Win32, the unmodified upstream tarball builds and runs.

That was not a theory to test: `redis-windows/redis-windows` has published exactly that for 6.2,
7.2, 7.4, 8.2, 8.4, 8.6, 8.8 and 8.10 — every line offered here, the newest of them a fortnight old.
What this task borrows from it is the **method** and not the binaries, which stay out for the reason
every fork's binaries do: there is no digest published by anyone upstream to check them against, and
the archive's permanence promise would come to rest on one person's repository.

Settled by a throwaway workflow on a branch before any of it reached `tools/`, and the ledger is the
argument for doing that: **eight runs, of which three findings were about Redis and the rest were
about the spike's own harness.** They are written up in
[building-from-source.md](building-from-source.md#windows-and-what-a-spike-is-for),
because none of them is about Redis and all of them are about the next Windows cell.

#### What landed

*Cygwin, not MSYS2, and the reason is redistribution rather than which one compiles.* MSYS2's own
documentation says its runtime is for its build tools rather than for programs to be shipped;
Cygwin publishes `CYGWIN_LICENSE` and `COPYING` as documents, so an archive carrying `cygwin1.dll`
under LGPLv3 can carry its terms. `cygwin_root()` proves which shell it has by asking `uname -s`
rather than by finding a `bash.exe`, because every Windows runner here already has an MSYS2 one on
`PATH` and the two would produce different artifacts from the same recipe.

*Nothing in the source is patched.* Two compiler flags — `-D_GNU_SOURCE` for `dlfcn.h`, and
`-Wno-char-subscripts` for hiredis's own `-Werror` meeting a newlib `isspace()` that does not cast.
Goals are named instead of `all`, and `make install` is not called at all: both end in
`module_tests`, which links a shared object against symbols an executable exports, and a PE image
has no undefined symbol resolved at load time.

*`relocate.py` learned PE*, read out of the import table in the file rather than asked of
`cygcheck`, which exists only on a build machine and answers with what that machine's `PATH`
offers. Bundling on Windows is a copy into `bin/`: the loader searches the image's own directory
first, so the copy *is* the redirection — no rpath, no install name, no re-signing.

*One constraint left this repository.* `getAbsolutePath()` in `server.c` decides a path is absolute
with `relpath[0] == '/'`, so no Windows spelling of the config path survives. The supervisor has to
set a working directory and name `redis.conf` relatively — which the smoke test now does on all five
cells, so it is one rule rather than a Windows branch. **T35 has to honour it.**

*What the cell is worth, stated in the release notes and the manifest rather than discovered later.*
The event loop is `select`, `maxclients` settles near 3168, and Cygwin invents a POSIX root from
wherever `cygwin1.dll` sits — so `CONFIG GET dir` answers in a namespace that moves with the
artifact. Writing a path to Redis is fine; reading one back and treating it as a Windows path is not.

*`windows/aarch64` stays empty*, and for a reason with no date on it: neither Cygwin nor MSYS2 has
an ARM64 build. Running x86_64 under emulation would work and is refused, because an archive
labelled `aarch64` may not hold binaries that are not.

**Memcached was said to be untouched by this, and that sentence did not survive being checked.** It
read: its Windows cells stay shut, because the privilege-dropping layer has a source file per Unix
and none to write for Windows — a decision about what the artifact *is* rather than about whether it
links. The source tree says otherwise. Every `*_priv.c` sits behind an `AM_CONDITIONAL` and is off
by default: optional hardening reached through `--enable-seccomp` or a platform probe, not a
component the program needs. Under Cygwin `config.h` carries `/* #undef HAVE_DROP_PRIVILEGES */` and
the build completes. So `windows/x86_64` opened on Redis's route with nothing added to the
`configure` line and nothing patched, and `windows/aarch64` stays shut for the reason that has no
date on it: Cygwin has no aarch64 port.

Two things came out of doing it, and both were about this repository rather than about memcached.
The first Windows build failed on twenty-seven `api-ms-win-*` imports, which are virtual names the
loader resolves from a schema — `relocate` already knew that and `memcached.py`'s own `cygcheck`
copy of the same check did not, because the two had been written a year of lessons apart and one
day of calendar apart. The recipe now calls `relocate.bundle` and `relocate.verify` like `redis.py`,
and unifying them surfaced the second: `CYGWIN-SOURCE.txt` had been written for memcached alone,
while Redis shipped `cygwin1.dll` under the identical LGPLv3 obligation with its licence text and no
route to the source. It lives in `relocate.bundled_licences` now and both archives carry it.

### [x] P8b — The licence check that could not fail

The other half of that, found by reading `redis-8.10.0-windows-x86_64.zip` instead of the code.
`licenses/` holds Redis, its eight bundled deps, and — for **both** Cygwin DLLs together —
`cygwin-COPYING` and `cygwin-CYGWIN_LICENSE`. Nothing for `libgcc1`, which is what installed
`cyggcc_s-seh-1.dll`, and libgcc is **GPLv3 with the GCC Runtime Library Exception**: a different
licence, from a different project, making a different promise. The archive did not fail to state
terms. It stated the wrong ones, which is worse, and it is the kind of wrong a reader trusts.

`bundled_licences` has stopped a build over a missing licence since P7a, and it says so: *a library
whose licence cannot be found is a failure and not a warning*. It could not stop this one.
`cygwin_licences` returned Cygwin's two runtime documents for **every** DLL, and those two files are
in every Cygwin installation, so `if not texts` was answered by somebody else's licence before it
could ever be reached. The same shape as P6a and P4c, a third time in two days: the check ran, the
check passed, and the check was not looking at its subject.

Now `cygwin_licences` answers for the owning package alone, Cygwin's two documents are shipped once
by `cygwin_runtime_terms` as the runtime's terms rather than as every library's, and a DLL with
nothing of its own stops the build.

**Which it immediately did, and the two runs it took to settle are the point.** `libgcc1` installs
no documentation at all — `cygcheck -l libgcc1-14.4.0-1` lists nothing under `/usr/share/doc`,
because Cygwin packages the GCC runtime apart from the compiler that carries GCC's licence texts. So
the failure was taught to list the doc directories matching the package's stem, and the next run
answered `gcc` — not the `gcc-core` the package is installed from, which is exactly the near-miss a
derived spelling would have shipped. `CYGWIN_LICENCE_ELSEWHERE` names it, one row, the way
`redis.DEPS_LICENCES` names its deps.

One more thing was tried and undone by measuring it. Taking the whole doc directory looked like the
answer that could not go stale; the archive it produced carried 1.2 MB of GCC's `NEWS` and 685 KB of
its `ChangeLog`, and `RUNTIME.LIBRARY.EXCEPTION` — the document that reasoning was built on — is not
in that directory at all. `COPYING` and `COPYING.LIB` are, and `LICENCE_GLOBS` already matched them.

`redis-8.10.0-windows-x86_64.zip` now ships 16 licence files, 225 KB, `libgcc1-COPYING` and
`libgcc1-COPYING.LIB` among them. Six green cells. Memcached, which bundles only `cygwin1.dll`, was
green through all of it.

### [x] P9 — nginx

nginx publishes a Windows zip itself and nothing relocatable for macOS or Linux, so the shape is
PHP's rather than Caddy's. The proof has one thing Caddy's does not: nginx has no admin endpoint, so
reload is `-s reload` against a running master and health is a request actually served.

#### The borrowed binary turned out to be the specification for the built ones

Both halves of the shape were right, and the thing worth writing down is what happened when the two
were made to mean the same thing. `nginx -V` on upstream's own Windows zip prints the configure line
it was built with, and that line is not a curiosity — it is a **specification a recipe can compile
against**. The four Unix cells are configured from it: the same twenty-two `--with-` flags, the same
three libraries, the same empty prefix. Which modules a version of nginx has here is upstream's
decision transposed rather than anybody's taste, and `check_modules` holds *both* sides to the
constant, so an upstream that changes its build stops the recipe rather than quietly publishing a row
that means two things. A check that looked only at the compiled side could not tell those apart.

Five cells, and both the floor and the empty one were measured rather than chosen:

* **Floor 1.26.0.** Every Windows zip from 1.26.0 to 1.31.3 carries exactly those twenty-two flags;
  1.24.0 carries twenty, missing `stream_realip` and `stream_ssl_preread`. A 1.24 row would mean one
  thing on Windows and another on the compiled cells. Worse, 1.24.0's zip is linked against **OpenSSL
  1.1.1t** — public security fixes ended September 2023 — and a published zip is frozen.
* **Windows/aarch64 is empty**, and for a new reason. Upstream publishes **one** Windows build and it
  is 32-bit x86; there is no `-win64` asset in any of its 331 zips, at any version. On x86_64 that
  runs natively under WOW64 and ships. On ARM64 it would be an i386 payload in an archive whose
  manifest says `arch: aarch64`, which is a lie in the index. Unlike Redis, nginx *has* an MSVC build
  system, so this cell could be compiled — that is a Windows-on-ARM pipeline with three vendored
  libraries maintained for every security release to fill one cell, which is the trade *Borrow before
  you build* refuses. Its leg still runs and exits 75.
* **`lacks` earns its keep for the first time since Ruby.** nginx.org calls its own Windows build a
  beta: `select()`/`poll()` only, several workers of which one does any work, no UDP. That is
  upstream's admission and the borrowed artifact carries it, so a daemon can decline to render
  `worker_processes auto;` there instead of being mysteriously slower on one platform. **HTTP/3 is off
  everywhere** for the matching reason — QUIC is UDP, and a `listen ... quic` that parses on four
  cells and not two is the asymmetry the module table exists to prevent.

**nginx publishes no digest of anything** — 594 tarballs, 331 zips, a detached PGP signature beside
each and nothing else. Keeping the property every other recipe here has meant verifying signatures,
so seven fingerprints are pinned in `tools/nginx.py`, each key file is checked against its pin
*before* import, and a signature is accepted only on a `VALIDSIG` naming one of them. Two things fell
out of building that: `nginx_signing.key` is **three** public keys in one file, which is where an
extra key would go unnoticed and is why all three are pinned individually; and the gpg on a Windows
runner is Git's MSYS build, which reads `--homedir C:\…` as *relative* and prepends its own working
directory, so the homedir has to be `.` with the cwd set instead.

Two smaller things, both found by running it rather than by reading. **`--prefix=` does not mean the
same on both platforms**: it stops `NGX_PREFIX` being defined at all, which is upstream's own answer
to relocation, but the sub-paths still get joined onto it, so `conf/nginx.conf` becomes
`/conf/nginx.conf` — absolute on Unix, prefix-relative on Windows where nginx wants a drive letter.
Nothing relies on the compiled-in defaults; the contract, proven on every cell, is
`nginx -p <instance> -c conf/nginx.conf -e stderr`. And **an instance prefix needs `logs/` and
`temp/` created before nginx starts** — `logs/` because nginx never creates it, `temp/` because nginx
creates `temp/client_body_temp` with one `mkdir` rather than a chain, so a missing parent is
`[emerg] … CreateDirectory() failed (3)` one line after `nginx -t` passed. That is a note for
whoever writes the daemon's nginx recipe, and it is what the first run of this smoke test did.

Left for whoever runs it: **the four compiled cells have not been through CI**. The Windows cell was
run end to end on a real Windows machine — signature verified against a pinned key, repacked, module
set checked, `nginx -t`, a request served, the configuration rewritten and `-s reload` proven by the
new body coming back, `-s quit` — and the Unix path was exercised with the compiler stubbed out, so
the downloads, the pinned library digests, the licence collection and the assembled tree are known
good and the compile itself is not. It needs a `workflow_dispatch` on `build-nginx.yml`.

---

## The index

### [x] P10 — End-of-life dates for every kind, not only MariaDB

`data/eol.json` carries what the index promises about a version's support window. MariaDB's entry is
transcribed from a publisher API and reprinted on every run; the runtime entries are transcribed from
schedule pages by hand and nothing checks them. PHP and Node.js both publish machine-readable
schedules (`endoflife.date` mirrors the rest); reading them the way `mariadb.py` reads MariaDB's
turns four hand-maintained entries into four transcriptions with a source.

#### Nothing had ever checked them, and Ruby was wrong in three different ways

The task assumed the fix was four transcriptions and the interesting part would be finding the
documents. The documents were easy — **all six kinds publish one, and `endoflife.date` turned out
not to be needed for anything**, which is worth recording because the plan above budgeted for it:

| kind | document | field |
| --- | --- | --- |
| php | `php.net/releases/branches.php` | `security_support_end` |
| node | `nodejs/Release`'s `schedule.json` | `end` |
| python | `peps.python.org/api/release-cycle.json` | `end_of_life` |
| ruby | `ruby/www.ruby-lang.org`'s `_data/branches.yml` | `eol_date`, else `expected_eol_date` |
| mariadb | `downloads.mariadb.org/rest-api/mariadb` | `release_eol_date` |
| postgres | `postgresql.org/versions.json` | `eolDate` |

The interesting part was the first run of `tools/eol.py`. PHP, Node.js, Python, MariaDB and
PostgreSQL were correct to the day — thirty-nine entries, no drift. **Ruby was wrong three separate
ways in the five it had:**

- **3.2 was off by a day.** Written 2026-03-31, upstream says 2026-04-01. Ruby's page says "March"
  in prose and 1 April in its data.
- **3.4 and 4.0 were invented.** Upstream states no end date for either, and both had been
  extrapolated from Ruby's habit of ending a line on 31 March about four years on. A good guess,
  printed in a field that means "upstream says". They are gone.
- **3.3 was right by luck.** The number matches, but upstream files it under `expected_eol_date`
  rather than `eol_date`.

That is the shape of the class: hand-transcription is not usually wrong, and nothing tells you which
of the forty-four entries is the one that is.

Ruby's `expected_eol_date` is transcribed rather than refused. Ruby is the only one of the six that
does not state a future date at all — `eol_date` is filled in when a branch actually ends — and
refusing the expectation would leave 3.3 undated while PHP 8.5 carries a date four years further
out, when PHP's, Node's and Python's future dates are *also* plans that can move. So it is taken,
and `--check` prints the field every date came from, which makes an expectation visible as one. What
is not taken is the case upstream is silent on: 3.4 and 4.0 are undated, per line rather than per
kind.

#### Three things this settled that the plan did not ask about

**The MariaDB pattern is the wrong pattern, and P10 is where that became measurable.** Reading the
date at pack time works for MariaDB because it arrives in the same document the download does — it
costs nothing and catches a moved schedule the next time that series is packed. But an end-of-life
date does not change when a version is packed; it changes on a calendar, and the lines closest to
their date are exactly the ones nobody is packing any more. Ruby 3.2 ended in April 2026 and will
never be repacked. So the check is `.github/workflows/check-eol.yml`, weekly and on any push
touching the data or the tool, and the recipes keep only the free half: `eol.announce` prints what
is written down, makes no network call and cannot fail a build. It is wired into all seven runtime
recipes.

**A subset cannot be checked, so each document is transcribed in full.** The file used to hold a
curated 44 entries and the curation was the bug: nothing distinguishes a line deliberately left out
from one forgotten. All 117 lines the six publishers state are now written down — PHP 4.3 and
PostgreSQL 6.3 included — which turns the check into an *equality*, the only kind that catches an
omission. It costs nothing: `mkindex.py` reads the lines it needs. Two consequences are recorded in
`data/eol.json` rather than left to be rediscovered — PostgreSQL 13 now has a date and still cannot
be packed (P7's Intel-only macOS archive), and Ruby's `1.9.3` and `2.0.0` are named with three parts
and so can never match `mkindex.py`'s two lookups.

**And a correction could not previously reach the index.** `mkindex.py` applied `data/eol.json` only
to the artifacts a run had just added, so the fix to Ruby 3.2 would have made the file right and
left the published index wrong forever. It now re-dates every package on every run, and *removes* a
date the file no longer states — un-saying is half of a correction, and the invented 3.4 date would
otherwise have outlived the entry it came from. Proven against a synthetic previous index: 3.2.9
picked up 2026-04-01 without being rebuilt, 3.4.2's date was dropped, 22.11.0 gained one it never
had, and Caddy stayed undated.

One thing deliberately not solved: the four undated kinds. Caddy, nginx, Redis and Memcached publish
no schedule, so `tools/eol.py` has six sources and not ten, and it is the one drift the check cannot
see — if Caddy starts publishing a schedule tomorrow, nothing here will say so. Re-ask it when a
kind's packaging is next touched, the way P8 and P9 both did.

Left for whoever runs it: **`check-eol.yml` has never fired.** The check itself was run repeatedly
against all six live publishers from a developer machine — it found the Ruby entries, `--update`
fixed them, a second `--check` came back clean and a third `--update` was a no-op — so the tool is
known good and only the workflow around it is not. The first scheduled Monday will say.

### [x] P11 — Prove the archive is permanent

The index promises that a blueprint pinning PHP 8.1.29 keeps working forever, which makes every
release asset load-bearing and an accidental deletion unrecoverable. Nothing states that today: no
protection on the releases, no periodic check that every URL in the published index still answers,
no line in the README saying which assets may never be deleted. A scheduled workflow that `HEAD`s
every artifact the current index names is the smallest thing that would notice — and P10 left one to
copy the shape from: `check-eol.yml` is a weekly job whose whole output is a pass or a failure with
instructions attached, which is what this wants to be too.

#### One of the three gaps cannot be closed, and saying so is the deliverable

"No protection on the releases" was written as work. It is not: **GitHub has no way to protect a
release asset.** Tag protection exists, has to be turned on by hand in the repository settings, and
guards the wrong thing — a protected tag does not stop the release under it, or one asset of that
release, from being deleted, which is exactly the accident this task is about. So prevention was
never available and the task is honestly two thirds of what it says: a rule written down, and a job
that notices it has been broken. The README says which assets may never be deleted; the enforcement
is `check-archive.yml`, weekly, running `tools/permanence.py`.

That makes *how fast* the only variable, and the reason is worth being precise about because it also
decides what the failure message has to say. A lost artifact can be rebuilt from the recipe that made
it — and not to the same bytes. These are compressed archives packed at a different minute by a
different runner from sources that may themselves have moved, so the sha256 in the index will never
match again, and the index is signed. Recovery means publishing a *different* artifact under the same
version and re-signing an index that describes it differently, which anyone who pinned the old hash
is entitled to read as tampering. There is no quiet repair, only a week's worth of not knowing.

#### Half the archive is not in the index

The plan says "`HEAD`s every artifact the current index names", and following it literally would have
left the more insidious deletion invisible. The index names 194 archives; the releases hold 388
assets, because every archive has a `<archive>.json` manifest beside it — and `publish-index.yml`
does not rebuild the index from anything in this repository, it downloads every release asset and
reads the manifest next to each archive. **A deleted sidecar leaves the archive perfectly intact and
quietly drops that cell out of every index generated afterwards.** Nothing before this named those
files as load-bearing. They are not in the index, so their URLs are derived rather than read, and all
that can be asked of them is whether they are there — which is the only question they fail at.

#### A fraction of the hashes, not a number of them

Presence is a `HEAD`: 388 requests, fourteen to thirty seconds from a home connection at eight at a
time, no bandwidth. The bytes are a different order
of cost — 6.13 GiB today, and one more version of one more runtime adds six cells to that. Hashing
everything weekly is affordable *now*, which is the trap: it is the reading that quietly stops being
true. So a fixed **fraction** is hashed each run rather than a fixed count. A count keeps the weekly
bill flat and lets coverage rot as the archive grows; a fraction keeps coverage flat and lets the
bill grow with the thing it insures. The promise is about coverage, so coverage is what is held: at
the default of eight slices every asset is hashed within eight weeks however many there are, which
measured out at 21–32 archives and 0.57–0.99 GiB per week across the current 194.

Which slice an asset falls in is a digest of its URL, not its position in the index. Position would
mean a version published mid-cycle reshuffles every other asset into a different week, so "hashed
within eight weeks" would stop being true exactly when the archive is growing. It is `hashlib` and
not `hash()` for the same kind of reason: the built-in is salted per process, so a rotation built on
it would pick a different set every run and could leave an asset unhashed for years while the log
claimed a complete cycle.

The `Content-Length` a `HEAD` answers with is compared against the size the index recorded, because
it is free and it is the difference between "this URL answers" and "this URL answers with what we
published". It catches the second-likeliest accident after deletion: a build workflow re-run against
an existing tag, uploading a rebuilt archive over the old one with `--clobber`. Same URL, same name,
a different file — and the length almost never lands on the byte the index wrote down.

#### The trap that would have made the cheap check expensive and silent

Every asset URL is a redirect: `github.com/.../releases/download/…` answers 302 and the bytes come
from a signed `release-assets.githubusercontent.com` URL. `urllib` does not forward the original
request across that, it builds a new one — and whether the method survives is a property of the
interpreter rather than of the calling code. Recent CPython carries `HEAD` across; older ones rebuild
it as a `GET`. On this archive that is the difference between 388 free requests and a 6 GiB download,
**reporting exactly the same result either way**. The redirect handler reasserts the method, which is
a no-op where it was already right and the whole of the check where it was not.

#### What the first run found

388 of 388 present, and the twenty-two archives of this week's slice all hashed to what the index
says — a whole weekly run, end to end, in 86 seconds from a home connection. The published index's
signature is from the key in the tree
(`9439248f2eebafe0`, matching `minisign.pub`) — which is checked in the workflow before the archive
is, since checking an archive against an index nobody can show was ours is checking a stranger's
list. Every refusal was exercised against the live releases: an asset deleted from a release that
still exists, a release deleted whole, a manifest deleted with its archive left alone, a length that
moved, a hash that disagrees, and an index that names nothing at all. The rotation was checked to
cover all 194 archives across its eight weeks with no asset in two slices and none in none, and to
pick the same slice in three separate processes.

Two things the run said that were not the question:

- **The published index still carries the three wrong Ruby dates.** P10 fixed `data/eol.json` and
  `mkindex.py` now re-dates every package, but neither reaches anybody until `publish-index.yml` runs
  — the live index says ruby 3.2.11 ends 2026-03-31 and gives 3.4.10 and 4.0.6 the two invented
  dates. The correction is a workflow run away, and nothing will do it on its own.
- **The archive is six kinds, not ten.** PHP, Node.js, Python, Ruby, Caddy and MariaDB are published;
  PostgreSQL, Redis, Memcached and nginx have recipes, green tables in this file and *no releases*.
  So P7–P9 are done as recipes and undone as artifacts, and the 6.13 GiB above is somewhere under
  half of what this job will eventually be watching.

Left for whoever runs it: **`check-archive.yml` has never fired**, the same gap P10 left. The tool
underneath it was run repeatedly against the live releases from a developer machine, including the
hash path, so what is unproven is the workflow — the `minisign -V` step and the summary — and not the
check.

### [ ] P12 — Four kinds exist as recipes and not as artifacts

Found by P11 while counting what the archive holds, and it is a gap in the *releases* rather than in
any code here. PostgreSQL, Redis, Memcached and nginx have finished recipes, green sections in this
file and **no release at all** — the published index is PHP, Node.js, Python, Ruby, Caddy and
MariaDB, 35 packages and 194 archives, and nothing else. P7, P7a, P7b, P8 and P9 are done in the
sense they were written to be: the recipe conforms, the smoke test passes on the runner, the empty
cells are stated. They are not done in the sense a user cares about, because there is nothing to
install.

So this is running the four `workflow_dispatch` builds and then `publish-index.yml`, not designing
anything — but it is worth a line here because everything about the way this file is written hides
it. A section is ticked when the recipe is right, the table above counts cells a recipe covers, and
`tools/parity.py` and `tools/permanence.py` both only ever look at artifacts that exist. Nothing in
the repository can tell the difference between a kind that was never built and a kind whose build
was never run.

Two things to do while running them, both of which get easier the fewer versions exist:

- **`publish-index.yml` afterwards, once.** It regenerates from every release that exists, so one run
  picks up all four kinds *and* carries P10's corrected Ruby dates into the published index, which is
  otherwise still wrong there.
- **Then `check-archive.yml` by hand with `slices: 1`**, which hashes the whole archive including
  everything just published. It is the one moment when a full sweep is cheap and the one time the
  artifacts have never been read back from the releases page by anything.

### [x] P12a — PostgreSQL cannot be smoke-tested on a runner that is an administrator

One of the four could not be built at all, and it is the only one of P12's blockers that was a
defect rather than an errand. `build-postgres` has run twice in its life and been red both times, on
every leg, at the same line:

> Execution of PostgreSQL by a user with administrative permissions is not permitted. The server
> must be started under an unprivileged user ID…

Nothing is wrong with the archive. `relocate.verify` passes on the runner immediately before this,
and no user ever reaches it: an interactive Windows account gets a *filtered* token, so
`pgwin32_is_admin` answers no even for someone in the Administrators group. GitHub's `windows-2022`
image runs its steps **elevated**, which is the one configuration PostgreSQL refuses.

`initdb` and `pg_ctl` never hit it because they re-execute themselves under a restricted token —
`src/common/restricted_token.c` — so the only casualty is the smoke test's own line, which starts
`postgres --config-file=` directly with `subprocess.Popen`.

Two fixes were available and both cost the thing being tested. Starting through `pg_ctl start` works
and is one line, but the claim this smoke test makes is that the server runs as a **direct,
supervised child**, which is how MixEngine will run it and which `pg_ctl` deliberately is not.
Skipping Windows would leave unpublished the one cell no smoke test had ever covered. So the runner
is corrected instead of the check: `postgres_smoke.unelevated` disables the same two SIDs
PostgreSQL's own code disables — Administrators and Power Users — in a restricted copy of this
process's token, and starts the server with it. `CreateProcessAsUser` needs no privilege for that,
because the token is a restricted version of the caller's own, which is the same special case
`initdb` relies on. It runs only when `elevated()` is true, so a developer's machine takes the
`Popen` path unchanged.

Verified locally for everything except the privilege drop — spawn, log redirection, working
directory, environment, an exit code of 7 read back, `poll()` before exit, `TimeoutExpired` at the
timeout the caller catches, and `kill()`. The drop itself cannot be shown from an unelevated shell,
where UAC has already marked Administrators deny-only in both children; the runner is what answers
that, and it is why this is ticked against a build rather than a local run. The runner answered:
`pack (18, windows-2022, windows, x86_64, postgres.py)` is green.

#### And the version banner, which had never been read on the two Linux cells

Unblocking Windows let both Linux legs run to the same line and fail there:

> postgres reports 'postgres (PostgreSQL) 18.6 (Ubuntu 18.6-1.pgdg22.04+2)', expected a 18.6 build

The pattern was `(\d+\.\d+)\s*$`, under a comment claiming that a build carrying a packager's suffix
would still match on a prefix. Anchored at the end, a suffix was the one thing that could not match,
and `postgres_deb` — which packs two of the six cells and appends exactly such a suffix — had never
been run far enough to say so. Read after the product name instead. Both Linux legs are green.

#### What is left, and it is not this

Two of six still fail, and they are the same failure on the two macOS cells:

> error: libpq-oauth-18.dylib: @rpath/libcurl.4.dylib does not resolve

That is `verify` working. PostgreSQL 18 adds `libpq-oauth`, which links libcurl, and EDB's macOS
archive does not carry one — so the answer the recipe wrote down for this case in P7 has come true:
*EDB's archive is not self-contained after all, and this recipe would have to bundle and re-sign it.*

**Dropped, and what decided it was the other two cells rather than the argument about signing.** The
module's only `LC_RPATH` is `@loader_path`, so `@rpath/libcurl.4.dylib` can mean exactly one file,
`lib/libcurl.4.dylib`, and that file is not in the archive — read out of the published
`postgresql-18.6-1-osx-binaries.zip` over HTTP ranges, both slices of the universal binary alike, for
4.6 MB of a 445 MB download. Then the comparison nobody had made: the Windows archive carries
`bin/libcurl.dll` and **no `libpq-oauth` at all**, and the Linux cells, built from the project's own
`.deb`s, carry neither. macOS was the only one of the six cells with the module and the only one that
could not load it. Removing it makes the row say what two thirds of it already said, which is the
opposite of the parity cost that made this look like a product decision.

So it is `stackbuilder.exe` again, in another operating system's spelling, and settled the same way.
The alternative was never cheap: bundling libcurl rewrites load commands, invalidates EDB's
signature and makes this repository the signer of every macOS binary it ships — the thing `thin`
exists to avoid — and Apple's libcurl is not a file on disk to copy, so it would have meant
Homebrew's, with its OpenSSL behind it.

What a user loses is the OAuth 2.0 device-authorization flow for libpq, which activates only when the
*server* names `oauth` in `pg_hba.conf` and loads a validator module. MixEngine's own `initdb` writes
scram-sha-256, and anyone on Windows or Linux never had it.

**All five cells green**, which is the first time `build-postgres` has finished: it had run twice in
its life before today and been red both times, on every leg.

### [x] P13 — Every published PHP archive predates P2

Found by P6a and confirmed against the artefacts rather than argued: all eleven PHP releases were
published on 2026-08-14 and P2 landed after them, so every one declares `pdo_firebird` in
`extensions.shared` and none can load it — the DLL needs an `fbclient.dll` that is not in the
archive. Measured on the published manifests of 7.4.33, 8.3.33 and 8.5.9: 40, 40 and 36 shared
extensions, `pdo_firebird` in all three. The same recipe today builds 8.5.9 with **30** and without
it.

Nothing to design. It was eleven `build-php.yml` runs with `release=true`, at the exact versions
already published so the existing releases were replaced rather than joined by a twelfth. It is here
as a task because the repository cannot see it: `parity.py` and `permanence.py` read what a manifest
says, and this manifest said something the archive could not do.

Eleven runs, every leg green, and checked afterwards by reading the **published** manifests back
rather than the ones the builds uploaded:

| | 7.0.33 | 7.1.33 | 7.2.34 | 7.3.33 | 7.4.33 | 8.0.30 | 8.1.34 | 8.2.33 | 8.3.33 | 8.4.24 | 8.5.9 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| shared extensions | 26 | 26 | 28 | 28 | 28 | 28 | 30 | 31 | 31 | 31 | 30 |
| `pdo_firebird` | — | — | — | — | — | — | — | — | — | — | — |

8.5.9 is the one with a before to compare against: 36 shared extensions and `pdo_firebird` among
them, now 30 and gone.

---

## Working on this file

- Tick the task here; one file, not a phase per section, because this repository is one pipeline.
- New work goes **where it belongs in the order**, with the next free suffix on the task it follows
  (`P2a`, `P2b`) rather than at the end.
- **One note, one place.** Why a recipe does what it does belongs in its docstring, beside the code
  it is about; what a packaging decision settled for one kind belongs on that kind's page in
  [`packages/`](packages/), and what it settled for the whole repository belongs beside the rule it
  is part of — [borrowing](borrow-before-you-build.md), [parity](one-version-means-one-thing.md),
  [layout](repack-do-not-rearrange.md), [dates](end-of-life-dates.md),
  [the archive](the-archive.md); what building something the hard way taught belongs in
  [`building-from-source.md`](building-from-source.md). [`../README.md`](../README.md) carries the
  table of what is packaged and links to all of it, and nothing else. What this file carries is only
  what none of those can: what has not been done yet, and what has to be decided before it can be.
