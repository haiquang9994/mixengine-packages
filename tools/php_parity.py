"""Which extensions a PHP version offers, decided once for all six of its cells.

Three recipes produce the PHP row and each of them arrives at an extension set a different way:
`static-php-cli` compiles one in, `php_legacy_unix.py` builds one with ``phpize``, and
`php_windows.py` unpacks whatever windows.php.net put in the zip. Left to themselves the three
answers are the publishers' answers, not MixEngine's — which is how PHP 8.3 came to be missing
``redis`` and ``mongodb`` on Windows while the Unix recipes *fail a build* without them, and how the
Windows archive came to carry PHP's own ``dl_test`` and ``zend_test`` test extensions that no Unix
cell has ever had.

So the decision lives here and the three recipes read it. That is the same argument `borrow.py`
makes about mechanics and this file makes about policy: **a decision three producers take separately
will drift, and the drift is invisible because they agree on the name of the extension.** The rule
this serves is [*One version means one thing, and no more than is needed*][rule], whose first
half is about this file and whose second half is about `php_windows.py`'s pruning.

Nothing here downloads, builds or loads anything. It answers three questions — what a branch owes,
what is on by default, and whether a finished set is short of either — and it is deliberately
readable end to end, because it is the list somebody will want to argue with.

[rule]: ../README.md#one-version-means-one-thing-and-no-more-than-is-needed
"""

from __future__ import annotations

import sys

# Where `php_unix.py` takes over from `php_legacy_unix.py`, and therefore where the extension set a
# version can offer changes. Imported by both, so a move cannot be made in one and forgotten in the
# other.
FLOOR = (8, 1)

# Beyond what PHP itself compiles in, every cell of every version offers these. The order is
# load order, not alphabetical: `redis` links against `igbinary` when it can find it, and stores a
# serialisation nothing else reads when it cannot.
#
# `xdebug` is here for the same reason it is *not* in `ENABLED_BY_DEFAULT` below: a debugger has to
# be present on every cell or a blueprint that turns it on is a blueprint that works on some
# machines, and it must not be running on a cell where nobody asked for it.
UNIVERSAL = ("igbinary", "redis", "mongodb", "xdebug")

# And these two from 8.1 upwards only. Not an oversight and not a floor anybody chose: PECL
# publishes no Windows build of `zstd` for 7.0 or 7.1 at all, so parity on those two lines can only
# be reached downwards. Since the rule compares the cells of *one version* rather than one version
# against another, 7.4 without yaml and 8.3 with it are both conformant; 8.3 with it on four cells
# and without it on two is not.
MODERN = ("yaml", "zstd")

# Missing one of these is a failed build rather than a warning, on every cell. MixEngine offers them
# on every version it ships, so an artifact without one lies about what it can run.
#
# The rest of `UNIVERSAL` warns instead, and that is a division of labour rather than a softer rule:
# a brand-new PHP branch can outrun PECL's Windows builder by a few weeks, and the check that turns
# a missing `igbinary` into a failed *release* is the cross-cell one in P6 — it can see the five
# cells that do have it, and this cannot.
REQUIRED = frozenset({"redis", "mongodb"})

# Present in `ext/` on every cell, and switched on by nobody unless asked. Everything else that ends
# up in `ext/` is expected to be enabled — see `enabled_by_default`.
OFF_BY_DEFAULT = frozenset({"xdebug"})

# What PHP is asked to carry beyond its own always-on core, in `static-php-cli`'s spelling because
# that is the recipe that has to name them to a tool. Chosen as the set a local development
# environment for Laravel, Symfony or WordPress would otherwise have the user install by hand.
#
# It is a *keep-list*, and after this task it is one on Windows too: `php_windows.py` throws away
# every `php_*.dll` whose name is not in here or in `expected`, rather than working from a list of
# things to delete. The difference matters more than it looks. A delete-list is written against the
# archive somebody measured — 8.3's, in this case — and says nothing about the `php_interbase.dll`
# in 7.3's or whatever ships in 8.6; a keep-list answers for every branch, including the ones nobody
# has looked at.
COMPILED_IN = (
    "bcmath", "bz2", "calendar", "ctype", "curl", "dba", "dom", "exif", "fileinfo", "filter",
    "ftp", "gd", "gmp", "iconv", "intl", "mbregex", "mbstring", "mysqli", "mysqlnd", "opcache",
    "openssl", "pcntl", "pdo", "pdo_mysql", "pdo_pgsql", "pdo_sqlite", "pgsql", "phar", "posix",
    "readline", "session", "shmop", "simplexml", "soap", "sockets", "sodium", "sqlite3",
    "sysvmsg", "sysvsem", "sysvshm", "tokenizer", "xml", "xmlreader", "xmlwriter", "xsl", "zip",
    "zlib",
)

# Extensions of `COMPILED_IN` that Windows cannot have, with why — the exemption list a cross-cell
# comparison has to be given or it will report four asymmetries nobody can close. Measured against
# `php-8.3.33-nts-Win32-vs16-x64.zip`: none of these is in the archive as a DLL and none is reported
# by `get_loaded_extensions()` on the official build.
#
# `sysvshm` is deliberately absent from this list. Windows does ship `php_sysvshm.dll`, which is why
# the exemption is per extension rather than "System V is Unix's".
NO_WINDOWS_BUILD = {
    "pcntl": "fork and friends are POSIX; PHP has never built pcntl for Windows",
    "posix": "the POSIX API layer, likewise",
    "sysvmsg": "System V message queues; Windows ships sysvshm and neither of these",
    "sysvsem": "System V semaphores, likewise",
}

# Names in `COMPILED_IN` that PHP never reports as extensions, so a comparison of finished artifacts
# will find them missing on every cell including the ones that have them. `mbregex` is
# static-php-cli's name for mbstring's regex engine, which is a configure option: a build with it
# answers `mbstring` and nothing else.
NOT_REPORTED = {"mbregex"}

# Extensions that did not exist for the whole range, with the branch they arrived in. `sodium` came
# in 7.2 and `php_legacy_unix.py` already guards `--with-sodium` on that same number; without this
# the check below would ask 7.0 and 7.1 for something PHP had not written yet.
SINCE = {"sodium": (7, 2)}

# What the DLL is called where that is not ``php_<extension>.dll``. One entry, and it cost an
# artifact to find: every Windows build from 7.0 to 7.4 ships GD as ``php_gd2.dll`` and 8.0 renamed
# it to ``php_gd.dll``, so a keep-list matched against file stems threw GD out of five branches —
# and threw it out *silently*, because a smoke test that loads what is in `ext/` cannot miss what is
# no longer there. It was caught by running the recipe against 7.0 rather than by reading it, which
# is the same lesson the README draws from MariaDB and the reason P6 exists.
DLL_NAMES = {"gd2": "gd"}

# Where the official Windows build started carrying something the Unix cells have had all along,
# measured across the eleven branches this row covers rather than assumed. Neither of these is a
# decision anybody here took and neither can be closed by borrowing — PECL has no build of either —
# so they are named, which is what the rule asks of a difference the platform imposes:
#
#   readline  absent from 7.0 and in every build from 7.1; `php -a` has no line editing there
#   dba       absent from 7.0 and 7.1, present from 7.2
#
# The direction the rule would otherwise pick is available and worse: dropping readline from the
# four Unix cells of 7.0 to match, which costs those cells a working interactive shell and buys one
# fewer line in this file.
WINDOWS_SINCE = {"readline": (7, 1), "dba": (7, 2)}

# Dropped from the Windows archive by the keep-list above, listed here with the reason because the
# reason is a decision about the whole row rather than about one recipe — a reader of `php_unix.py`
# wondering why nothing configures LDAP should find the answer without reading a packer. Measured on
# 8.3.33; older branches drop more (`interbase`, `wddx`, `xmlrpc`, `mcrypt`, `recode`, `pspell`) and
# the keep-list catches those without being told about them.
#
# `php_windows.py` asserts that none of these survived, so this is a check and not a caption: a
# keep-list that grew an entry by accident is exactly the kind of edit that reads as harmless.
#
# Two more are surplus and stay, because they are compiled into the publisher's build and this row
# does not compile anything on Windows: `mcrypt` on 7.0 and 7.1, and `wddx` on 7.0 through 7.3. PHP
# removed both itself — mcrypt in 7.2, wddx in 7.4 — and no Unix cell of those branches has either.
# A cross-cell check will find them and has to be told; it is the same shape as `NO_WINDOWS_BUILD`
# read from the other end, and the same answer: name it, because nothing here can close it.
#
# Every one of them is the README's own test applied twice — *something a local development
# environment does not do is dropped everywhere* — and the two at the end are not even that:
#
#   odbc, pdo_odbc          an ODBC bridge is the README's own example of what this does not do
#   oci8_19, pdo_oci        Oracle, which additionally wants a client library nobody ships
#   pdo_firebird            Firebird, likewise
#   snmp, ldap, imap        bridges to infrastructure a local web development environment does not
#                           run, and Unix has never built any of the three
#   enchant                 spell checking, and 3.0 MB of glib behind it
#   tidy, gettext           real libraries and real use cases, and no Unix cell has ever had them;
#                           the direction is chosen downwards because adding them means compiling
#                           two more libraries into four built cells forever
#   com_dotnet              a COM bridge, which is Windows-only by nature — the roadmap expected
#                           this one to *stay* as a named exemption, and it is dropped instead
#                           because "the platform has no equivalent" is a reason to look at the
#                           feature, not a reason to keep it
#   ffi                     loads arbitrary native libraries by absolute path, which is the one
#                           thing a relocatable tree cannot promise anything about
#   dl_test, zend_test      PHP's own test extensions, shipped by the publisher for its test suite
SURPLUS_ON_WINDOWS = (
    "com_dotnet", "dl_test", "enchant", "ffi", "gettext", "imap", "ldap", "oci8_19", "odbc",
    "pdo_firebird", "pdo_oci", "pdo_odbc", "snmp", "tidy", "zend_test",
)


def expected(branch: tuple[int, int]) -> tuple[str, ...]:
    """The extensions this branch owes on all six cells, in the order they should be loaded."""
    return UNIVERSAL + (MODERN if branch >= FLOOR else ())


def enabled_by_default(shared: list[str]) -> list[str]:
    """Which of the loadable modules in an archive the daemon is expected to switch on.

    Every one of them except the ones deliberately off, and that is the whole rule rather than a
    per-cell list, because after this task nothing reaches `ext/` by accident: a Unix archive holds
    what its recipe built shared and a Windows archive holds what `php_windows.py` chose to keep.

    The field exists because Windows and Unix cannot express the same decision the same way.
    ``curl``, ``openssl``, ``mbstring``, ``intl``, ``gd``, ``zip``, ``sodium``, ``sqlite3`` and
    ``fileinfo`` are compiled into the Unix builds and are loadable modules on Windows, and no
    Windows build exists with them static — so the Windows artifact only behaves like its Unix twin
    if whoever installs it enables that set. Saying which extensions are *present* never said that.
    """
    return sorted(set(shared) - OFF_BY_DEFAULT)


def _reported(names: list[str]) -> set[str]:
    """Extension names as this file spells them, out of names as PHP and the recipes spell them.

    Two mismatches, both of which make a present extension look absent. ``get_loaded_extensions()``
    answers with PHP's own capitalisation — ``PDO``, ``Phar``, ``SimpleXML`` — while a file stem and
    a configure flag are lower case; and opcache answers ``Zend OPcache``, which is the one name in
    the set that is not a variant of the extension's own.
    """
    return {"opcache" if name.lower() == "zend opcache" else name.lower() for name in names}


def check(branch: tuple[int, int], static: list[str], shared: list[str],
          windows: bool = False) -> None:
    """Refuse an artifact that is short of what its branch owes, whichever way it would have got it.

    One check for three recipes, run against the finished tree rather than against the intent that
    produced it — a compiled-in extension, a `phpize`d module and a downloaded DLL are three
    mechanisms and one claim, and it is the claim that has to hold.

    It reads the whole of :data:`COMPILED_IN` and not only the handful this repository adds, because
    the failure it was written for was in the other half: the Windows recipe keeps a DLL by matching
    its file name, PHP 7.0 calls GD ``php_gd2.dll``, and two branches were packed without GD at all
    while every check passed. What is missing cannot fail a load test.

    What is *not* checked here is the six cells against each other, because a recipe only ever sees
    one of them. That is P6's, and it reads `extensions` out of the six finished manifests — where
    it would also have caught the GD one, from the other direction.
    """
    have = _reported(static) | _reported(shared)
    owed = [
        name for name in COMPILED_IN
        if name not in NOT_REPORTED
        and branch >= SINCE.get(name, (0, 0))
        and not (windows and name in NO_WINDOWS_BUILD)
        and not (windows and branch < WINDOWS_SINCE.get(name, (0, 0)))
    ]
    missing = [name for name in list(expected(branch)) + owed if name not in have]
    if not missing:
        return
    fatal = [name for name in missing if name in REQUIRED]
    if fatal:
        raise SystemExit(
            f"php {branch[0]}.{branch[1]} has no {', '.join(fatal)}. MixEngine offers "
            f"{'them' if len(fatal) > 1 else 'it'} on every version it ships, so an artifact "
            f"without {'them' if len(fatal) > 1 else 'it'} is not one worth publishing."
        )
    print(
        f"warning: this archive has no {', '.join(missing)}, which the other cells of "
        f"{branch[0]}.{branch[1]} are expected to carry — one version would mean two things",
        file=sys.stderr,
    )
