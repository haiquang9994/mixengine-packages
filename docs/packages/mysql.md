# MySQL, and the lines upstream stopped building

*Part of [mixengine-packages](../../README.md), which holds the table of what is packaged.*

[MariaDB](mariadb.md) is the other database with this name in its programs, and it is not the same
product to anybody maintaining an application against one of them. Five lines are packed, 5.6 through
9.7, and the shape of the table is upstream's rather than a preference.

| OS / arch | 5.6, 5.7 | 8.0, 8.4, 9.7 |
| --- | --- | --- |
| macOS aarch64 | **built** | **borrowed** — `macos15-arm64.tar.gz` |
| macOS x86_64 | **built** | **borrowed** — `macos15-x86_64.tar.gz` |
| Linux x86_64 | **built** | **borrowed** — `linux-glibc2.28-x86_64.tar.xz` |
| Linux aarch64 | **built** | **borrowed** — `linux-glibc2.28-aarch64.tar.xz` |
| Windows x86_64 | **borrowed** — `winx64.zip` | **borrowed** — `winx64.zip` |
| Windows aarch64 | — | — |

**Oracle withdrew macOS from the 5.x lines while they were alive.** `5.7.31` offers
`macos10.14-x86_64`, `5.7.20` offers `macos10.12-x86_64`, and `5.7.44` — the last release of the line
— offers no macOS asset of any kind and lists no macOS entry in its own operating-system menu. 5.6
does the same thing earlier, and neither line ever had an ARM build on any system. So the *newest*
patch of a line is less portable than one from the middle of it, and a recipe reading a release's
asset list the way `caddy.py` does would quietly produce fewer cells for a newer version.

Six things follow, and each of them is a thing a reader would otherwise read as a packing fault.

**All four Unix cells of 5.6 and 5.7 are compiled, and that is a decision rather than a shortage.**
Upstream still publishes `linux-glibc2.12-x86_64` for both lines, so that cell *could* be borrowed. It
is not. The ARM cell has to be compiled — there is nothing to borrow — which means a 2026 toolchain
against an OpenSSL this repository supplies, while the borrowed tarball is Oracle's 2021 build at a
glibc floor of 2.12 against the built cell's 2.28. Two Linux artifacts of `5.6.51` would be two
different databases, and `parity.py` compares finished artifacts precisely because that difference is
invisible in two green builds. This is the first row here where
[borrow before you build](../borrow-before-you-build.md) loses to
[one version means one thing](../one-version-means-one-thing.md), and it is worth stating in those
terms: borrowing is cheaper per cell, and it is not cheaper than having the six cells of a version
mean one thing.

**`provides` is shorter on newer versions, and that is legal.** `mysqlpump` is in 5.7 and 8.0 and
gone from 8.4; `mysql_upgrade` is in 5.6, 5.7 and 8.0 and gone from 8.4; 5.6 alone has
`mysql_install_db`. `parity.py` compares the six cells of *one* version, so none of this is a parity
failure — but somebody reading a shorter command list on 9.7 than on 5.6 needs to be told that before
filing it.

**MySQL 5.6 ships an OpenSSL nobody patches.** Its own `cmake/ssl.cmake` sets `OPENSSL_FOUND` only
when `OPENSSL_MAJOR_VERSION STREQUAL "1"`; 5.7.44 accepts `"1" OR "3"`. So 5.7 compiles against the
OpenSSL a machine already has and 5.6 gets a 1.1.1 built and bundled by the recipe — a release that
stopped receiving public security fixes in September 2023. `smoke.openssl` in each 5.6 manifest names
exactly which library that artifact loads. It is not a reason to refuse the version: a version whose
own build system rejects a maintained OpenSSL cannot be given one, and the person maintaining an
application against MySQL 5.6 is exactly who a local development environment is for.

**Neither 5.x line will configure under a current CMake, so the recipe brings its own.** Both trees
ask for OLD behaviour on `CMP0018`, `CMP0022`, `CMP0042` and `CMP0045` by name, and CMake 4 answers
that the policy *may not be set to OLD behavior because this version of CMake no longer supports it*
— four errors before a single file is compiled, on macOS and inside the manylinux container alike.
`-DCMAKE_POLICY_VERSION_MINIMUM=3.5` answers a different question, about a `CMAKE_MINIMUM_REQUIRED`
that is too old, and does not help with this one. The alternative to an old tool was editing
upstream's `CMakeLists.txt`, which means guessing what four compatibility settings from a 2013-era
build system should become; `tools/mysql_build.py` pins **CMake 3.31.12** instead — the last 3.x
line — by URL and SHA-256, and fetches it per build the same way it fetches 5.6's OpenSSL.

**And the compiler is told which standard, because both trees ask and only one of them manages to
keep the answer.** InnoDB's `univ.i` opens with `#define byte unsigned char`, harmless while a
compiler's default was C++98 and not since: `<cstddef>` declares `enum class byte : unsigned char`
from C++17 on, the macro rewrites that declaration, and GCC 14 stops with `unnamed scoped enum is
not allowed` inside a standard header. 5.6's own `compiler_options.cmake` computes `-std=gnu++03`
for GCC 6 and newer and then overwrites the variable on the very next line; 5.7 fixed that file by
prepending instead of setting. So the flag is passed on the command line, where an overwrite cannot
reach it, on clang as well as GCC — the clash follows the compiler's *default standard*, and Apple's
has moved too.

**On macOS the compiled cells use the system zlib rather than the one 5.6 carries.** That copy
dates from 2013 and its `zutil.h` still has a branch for *classic* Mac OS, taken whenever
`TARGET_OS_MAC` is defined — which is 1 on every Apple platform today. The branch does
`#define fdopen(fd,mode) NULL`, so the next `#include <stdio.h>` meets `FILE *fdopen(int, const
char *)` with `fdopen` already a macro and clang stops inside Apple's own header. It is not even
consistent between the two cells: measured, x86_64 on SDK 15.5 fails and arm64 on SDK 14.5 does
not, so what compiles depends on which runner image is current. macOS has shipped a maintained zlib
in `/usr/lib` for as long as there has been a macOS, `relocate` leaves anything there alone, and a
2013 zlib is not something to ship on purpose. Linux keeps the bundled one, where it compiles.

**Both compiled lines are built from modified source, and the source travels with them.** Two
changes, and each one is a change Oracle itself made in a later version.

*Every cell of both lines* has the file `VERSION` at the root of the tree renamed to
`MYSQL_VERSION`, with `cmake/mysql_version.cmake` reading the new name. The tree's root is on the
include path, macOS is case-insensitive unless somebody went out of their way, and libc++'s own
`iosfwd` does
`#include <version>` — which therefore opens MySQL's version file and stops clang at
`MYSQL_VERSION_MAJOR=5` with `expected unqualified-id`. Neither file is wrong; the names collided
years after both were written, and **MySQL 8.0.28 renamed the same file for the same reason.**

*The 5.6 cells* additionally have **two** blocks deleted from `include/my_global.h`, and the second
is the same mistake as the zlib one above, made in the other direction.
`#if defined(TARGET_OS_LINUX) || defined(__GLIBC__)` turns `_GNU_SOURCE` on — and it asks whether
Apple's `TARGET_OS_LINUX` *exists*, meaning to ask whether it is 1. Apple's
`TargetConditionals.h` has since grown that macro, defined as 0, so on SDK 15.5 a macOS build
declares itself GNU, `mysys/my_error.c` takes its
`#elif defined _GNU_SOURCE` branch, and `char *r = strerror_r(...)` meets the POSIX `strerror_r`
that returns `int`. Xcode 16.4 refuses it; Xcode 15.4 compiled it with a warning, into a build whose
`my_strerror` would have read a pointer that was really a zero. **5.7.44 does not have the block**,
and on Linux `_GNU_SOURCE` comes from `my_config.h`, which 5.6 generates too.

The first block is the older one. Written for PowerPC-era universal binaries, it undoes the
`SIZEOF_*` values CMake has just detected and hardcodes them from
`__i386__ / __ppc__ / __x86_64__ / __ppc64__`, ending in `#error Building FAT binary for an unknown
architecture.` On Apple Silicon that `#error` is the whole of the failure. 5.7.44 does not have that
one either — Oracle deleted it and let the detected values stand. So every change made here is a
change upstream itself made a line later, rather than a port invented in this repository.

MySQL Community is GPLv2, so the complete corresponding source is published as
`mysql-<version>-patched-src.tar.gz` beside the binaries, `licenses/SOURCE.md` inside each artifact
names it, and that asset is under [the archive's permanence promise](../the-archive.md)
like every other — a deleted source tarball here is a licence violation rather than a missing
convenience.

**Every key that has ever signed MySQL is expired, and the check is not weaker for it.** The 2013 key
expired in February 2022, the 2022 key in December 2023, the 2023 key in October 2025 — and the five
lines packed here are signed by all three. A signature made while a key was valid stays valid; gpg
says so by emitting `EXPKEYSIG` beside `VALIDSIG`, and the trust in these recipes comes from a
fingerprint pinned in `tools/mysql.py` rather than from a keyring's opinion about dates. 5.6 is
signed with DSA over SHA-1, which is why `--allow-weak-digest-algos` is passed explicitly: what a
recipe does must not depend on which gpg a runner happens to carry.

**8.0 is packed at 8.0.44 rather than 8.0.45, and upstream is why.** 8.0.45 published its Linux
tarballs with **no detached signature at all** — the CDN answers 404 and the archive's own gpg
endpoint answers `200` with a one-byte body — while its macOS and Windows assets are signed and
8.0.44's Linux ones are. The page states an MD5, which is not something this repository writes into
`upstream.verified_against`. So the whole line is resolved once, against every asset every cell needs,
and what was refused is printed in the run: a per-leg resolution would have put three cells of 8.0.45
and two of 8.0.44 into two releases, each with a table three-fifths empty.

Two more, smaller, and both measured rather than assumed. **The Windows runtime a version needs is
read off its own binaries**: `mysql-5.6.51-winx64.zip` imports `msvcr100.dll`, which is Visual Studio
2010 and not the 2013 its documentation implies, while `5.7.44` — a 2023 rebuild of a 2015-era line —
imports `vcruntime140_1.dll` and needs the newest redistributable. And **upstream ships libraries it
did not finish**: `mysql-5.7.44-winx64.zip` carries a `bin/saslSCRAM.dll` that imports
`libcrypto-3-x64.dll`, and that zip contains no OpenSSL DLL at all, so the file cannot load on any
machine including Oracle's. It is deleted, named in `upstream.removed`, and the alternative was
shipping a tree that fails its own relocation check.

**The Linux artifacts were five times the size of the others, and the same pruning made both.**
MySQL 9.7.1 packed to 109 MB on macOS and 118 MB on Windows and to **609 MB on Linux** — one
server, one compression, the same fifteen paths named in `upstream.removed` on every cell. What
differs is inside the files. A borrowed Linux bintar carries `.debug_*` in `bin/mysqld` and in every
plugin, where Oracle ships macOS already stripped and files Windows' symbols in separate `.pdb` that
`NOT_SHIPPED` drops. The compiled cells reach the same place without anyone's help: DWARF is linked
into an ELF executable and stays behind in the object files of a Mach-O one, so 5.6 came out at
131 MB on Linux against 76 MB on macOS from a single set of flags. So `strip.debug` runs on Linux
and nowhere else, over both halves of the row — the one operation every recipe here now shares, and
where the same `strip --strip-debug` took a MariaDB bintar from 371 MB to 27 MB. `--strip-debug` and not `--strip-all`, which is what
`strip.IMAGES` would have asked for: `lib/plugin/*.so` is opened by `dlopen`,
`lib/libmysqlclient.so` is what a client extension links against, and the dynamic symbol table is
not what makes these files large anyway. Every file that changed is named in `upstream.changed`,
mapped to the command that changed it, and `strip.symbols` refuses to return unless the loader's
and the linker's whole view of it survived.

**There are no end-of-life dates**, and [that absence has its own reason written down](../end-of-life-dates.md):
Oracle publishes MySQL's schedule in a support-policy PDF and announces an EOL on a page written
after the fact, neither of which `tools/eol.py` can re-read and compare. For the record, and stated
here rather than in a file that promises to be checkable: 5.6 went out of support in February 2021,
5.7 in October 2023, and 8.0 in April 2026.

The proof is the same shape as MariaDB's and asks the questions a *database* raises: the artifact is
moved somewhere it has never been, a data directory is bootstrapped from scratch, the server starts
against a rendered `my.cnf`, `mysqladmin ping` answers, a row is written and read back **through
InnoDB** — checked in `information_schema`, because a server whose storage engine failed to initialise
falls back without failing — and it is stopped through `mysqladmin shutdown`, with the clean-shutdown
line looked for in the log afterwards.

Bootstrapping is where the five lines disagree most, and `mysql_smoke.bootstrap` is a table of three
routes rather than a version test: 5.7 and newer use `mysqld --initialize-insecure`; 5.6 on Unix uses
`scripts/mysql_install_db`, which does not quote `$basedir` and so has to be reached through a
space-free symlink; 5.6 on Windows has neither, and upstream's zip ships a `data/` directory with
the system tables already built.

Two things about that middle route are worth stating because both read as broken artifacts when they
are not. **In a tree compiled here `mysql_install_db` is Perl, not shell** — 5.6's
`scripts/CMakeLists.txt` configures `mysql_install_db.pl.in` on every platform and only appends
`.pl` to the name on Windows — so what runs it is read off its own first line. And
**`support-files` is otherwise not shipped, but `support-files/my-default.cnf` is kept**: the
script looks for that
template in four places, refuses to run without it, and checks for it before it looks at
`--keep-my-cnf`. One file, and the 5.6 cells cannot bootstrap without it.

One platform difference this turned up that no documentation states: **MariaDB's installer creates
`root@127.0.0.1` and MySQL's `--initialize-insecure` creates only `root@localhost`**, so the
`skip-name-resolve` that MariaDB's smoke test sets would leave every client here refused by a server
whose own log says it is ready for connections.
