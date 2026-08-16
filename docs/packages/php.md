# PHP

*Part of [mixengine-packages](../../README.md), which holds the table of what is packaged.*

Windows borrows; everything else is built, by two recipes with the join at 8.1:

| OS / arch | Range | How |
| --- | --- | --- |
| Windows x86_64 | 7.0 – newest | **borrowed** — official windows.php.net builds, repacked |
| macOS aarch64, x86_64 | 8.1 – newest | **built** — [`static-php-cli`](https://github.com/crazywhalecc/static-php-cli) |
| macOS aarch64, x86_64 | 7.0 – 8.0 | **built** — from source, dependencies bundled beside the binary |
| Linux x86_64, aarch64 | 8.1 – newest | **built** — `static-php-cli` |
| Linux x86_64, aarch64 | 7.0 – 8.0 | **built** — from source inside AlmaLinux 8, dependencies bundled |

The last two rows are the range `static-php-cli` does not build. They are affordable because those
six branches are *final* — 7.0.33 through 8.0.30 will never have another release — so the pipeline
runs a handful of times rather than being kept current for every security release. `php_legacy_unix.py`
compiles them and `relocate.py` makes the result carry its own libraries. On macOS both
architectures are built on a runner of their own: nothing is cross-compiled and nothing runs under
Rosetta, so a branch that will not compile natively for an architecture is simply not offered there.

Getting those two rows green took ten rounds of CI, and almost all of it went on builds that exited
zero and shipped something wrong rather than on code that failed to compile.
[`docs/building-from-source.md`](../building-from-source.md) is what that was worth keeping — read
it before opening any other **built** cell, because little of it is about PHP.

**Three recipes, one extension set.** Borrowing the Windows build meant borrowing its publisher's
idea of what PHP is for, and that idea is not this one: 8.3 arrived without `redis` and `mongodb`,
the two the Unix legs *fail a build* over, and with an ODBC bridge, an Oracle client, a Firebird
driver, and PHP's own `dl_test` and `zend_test`. So the set is chosen in
[`tools/php_parity.py`](../../tools/php_parity.py) and the three recipes reach it three ways: compiled in
by `static-php-cli`, `phpize`d on 7.x, downloaded from php.net's own PECL builds on Windows. What is
surplus there is deleted, along with the libraries it was the only user of, and both directions are
named in `upstream.added` and `upstream.removed`.

Two of those differences could not be resolved by choosing, and are written down instead. Windows
has never had `pcntl`, `posix`, `sysvmsg` or `sysvsem`, and its 7.0 build has no `readline` and no
`dba` where every Unix cell of 7.0 does; 7.0 and 7.1 carry `mcrypt` compiled in, and 7.0 through 7.3
carry `wddx`, which cannot be removed from a build nobody here compiles. Naming them is what lets a
cross-cell check exist at all — everything it does *not* name is then a defect.

One more field came out of this. `extensions.shared` says what an artifact can load and says nothing
about what it will; on Windows nine extensions that are compiled into every Unix cell are loadable
modules, because no Windows build exists with them static. `extensions.enabled` is the set the
daemon is expected to switch on, so `static ∪ enabled` is what a cell actually does, and that is
the thing six cells have to agree about.
