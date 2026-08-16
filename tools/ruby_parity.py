#!/usr/bin/env python3
"""What a Ruby version offers, and what it costs, decided once for all six of its cells.

Two recipes produce the Ruby row and they arrive at a tree from opposite directions: ``ruby.py``
unpacks whatever RubyInstaller put in a ``.7z``, ``ruby_unix.py`` runs ``configure`` and ``make``
against the source tarball. Left to themselves each answers the question its own way, and the
answers had drifted in both halves of the rule at once.

*"No more than is needed"* had drifted **by 225 MB**. ``ruby_unix.py`` passes
``--disable-install-doc`` and then deletes ``share/man``, ``share/doc`` and ``share/ri`` for good
measure; ``ruby.py`` pruned nothing, because nothing had measured what it was carrying. Measured:
``share/doc`` and ``share/ri`` are 60.3 MB of a 108 MB tree on 3.4.10 and **224.9 MB of a 276 MB
tree on 4.0.6**, which is 81% of an artifact of a programming language being RDoc's HTML rendering
of that language's own manual. Nothing serves it, no ``ri`` or ``rdoc`` command is published by
either recipe, and the four Unix cells have never had it.

*"One version means one thing"* had drifted in a way no amount of pruning fixes, which is what
:data:`LACKS` is for. Two of the things ``ruby_unix.py`` **fails a build over** are not available on
Windows at any price, so the honest artifact is one that says so rather than one that is quietly
less than its siblings.

And one apparent drift turned out not to be one, which is :func:`keeps`. Both halves ship the file a
program embedding Ruby links against and both name it from a build record that follows the moved
tree; they spell it differently because the toolchains do, and reading ``ENABLE_SHARED`` — ``yes`` on
Windows, ``no`` on the other four — says the opposite of what the six trees contain.

This is the same argument ``php_parity.py`` makes for its own row and ``borrow.py`` makes about
mechanics: a decision two producers take separately will drift, and the drift is invisible because
they agree on the name of the thing. The rule served is [*One version means one thing, and no more
than is needed*][rule].

Nothing here downloads, unpacks or runs anything; :func:`keeps` reads a finished tree and no more.

[rule]: ../README.md#one-version-means-one-thing-and-no-more-than-is-needed
"""

from __future__ import annotations

from pathlib import Path

# Installed or shipped and then thrown away, on every cell of every version. Named as directories
# rather than as a pattern because that is what both recipes delete and what `upstream.removed`
# records — a directory removed whole is named by its root, so six cells stay comparable field to
# field instead of one of them listing 15,415 files.
#
# `share/man` exists only on the compiled cells; RubyInstaller ships no manual pages at all. It stays
# in the list because the list is the decision rather than an inventory of one publisher's archive,
# and because a recipe deleting a path that is not there is the cheap half of keeping them identical.
#
# The direction was decided by the Unix half and not by the size. `ruby_unix.py` does not merely
# delete the documentation, it passes `--disable-install-doc` so it is never generated — a positive
# choice with an argument behind it, that MixEngine has no business shipping four copies of Ruby's
# manual on four targets for every version of every line. Nothing on the Windows side argues back:
# the `.7z` carries the docs because it is a general-purpose distribution of Ruby, not because
# somebody decided a development environment needs them.
SURPLUS = ("share/man", "share/doc", "share/ri")

# What a cell cannot do that its siblings can, with the reason, written into the artifact.
#
# **This is an admission and is meant to read as one.** Everything else in this repository resolves
# an asymmetry by moving one side; these two cannot be moved. `ruby_unix.py` passes `--enable-yjit`
# and then *fails the build* if `RubyVM::YJIT.enabled?` comes back false, and proves that
# `gem install bigdecimal` compiles a native extension inside the moved tree. Asked the same two
# questions, a RubyInstaller 3.2, 3.4 and 4.0 answer:
#
#   ruby --yjit  ->  "warning: Ruby was built without YJIT support"; `RubyVM::YJIT` is not defined
#   gem install  ->  "MSYS2 could not be found. Please run `ridk install`", exit 1
#
# Neither is upstream being careless. YJIT is not built for `x64-mingw-ucrt` by CRuby itself, and
# the toolchain that compiles a native gem is a separate 1 GB MSYS2 that RubyInstaller publishes as
# its own installer — shipping it would make the Ruby row's Windows artifact larger than every other
# artifact in this repository put together, to provide a compiler MixEngine does not otherwise
# offer on that platform.
#
# So it is declared. A daemon reading this can refuse to enable YJIT on a cell that has none instead
# of passing a flag that warns, and a blueprint asking for a gem with a native extension can fail
# where it is written rather than on somebody's machine. An absence nothing states is an absence a
# reader has to discover.
LACKS = {
    "windows": {
        "yjit": (
            "RubyInstaller builds x64-mingw-ucrt, which CRuby does not build YJIT for: "
            "`RubyVM::YJIT` is undefined and `ruby --yjit` warns rather than fails. The four "
            "compiled cells are configured `--enable-yjit` and their smoke test refuses to publish "
            "a Ruby that answers false to `RubyVM::YJIT.enabled?`, so this is the one capability "
            "the six cells of a version cannot be made to share."
        ),
        "native gems": (
            "`gem install` of a gem with a C extension answers `MSYS2 could not be found. Please "
            "run 'ridk install'` and exits 1. The compiler lives in a separate ~1 GB MSYS2 "
            "RubyInstaller publishes as its own installer, which is why the archive borrowed here "
            "is the one without it. Gems that publish an `x64-mingw-ucrt` binary install normally; "
            "the compiled cells prove `gem install bigdecimal` builds from source in the moved tree "
            "and this one cannot make that claim."
        ),
    },
}


def lacks(operating_system: str) -> dict[str, str]:
    """What this cell cannot offer, as the manifest's ``lacks`` field.

    A function rather than a lookup so that the empty case is a value and not a `KeyError`, and so
    the four cells that lack nothing write nothing — an artifact with an empty `lacks` and one whose
    recipe never thought about it would otherwise be indistinguishable.
    """
    return dict(LACKS.get(operating_system, {}))


# What the file a program embedding Ruby links against is called, per toolchain. Two globs rather
# than six names because the middle of the name is the build's `RUBY_SO_NAME`, which is
# `ruby` on Linux, `ruby.3.4` on macOS and `x64-ucrt-ruby340` on Windows — the same file, spelled
# by whoever configured it.
LINKER = {"windows": "*.dll.a", "macos": "lib*-static.a", "linux": "lib*-static.a"}


def keeps(tree: Path, operating_system: str) -> dict[str, str]:
    """The paths kept against the rule's second half, and why — read off the tree, not off a list.

    **The asymmetry this was written to resolve does not exist, and finding that out is the whole
    of the task.** ``RbConfig::CONFIG['ENABLE_SHARED']`` is `yes` on the two Windows cells and `no`
    on the four compiled ones, which reads as *two cells can be embedded in a program and four
    cannot* — the same question P4a answered for CPython, apparently coming out the other way. What
    the six trees actually contain says otherwise. ``--disable-shared`` does not mean no libruby; it
    means libruby is a **static archive**, installed as ``lib/libruby-static.a`` on Linux and
    ``lib/libruby.3.4-static.a`` on macOS — 41.4 MB and 28.3 MB on 3.4.10, the largest file in
    either tree and larger than the ``bin/ruby`` that already contains it.

    And each half names its own copy from a record that survives the move. ``rbconfig.rb`` opens
    with ``TOPDIR = File.dirname(__FILE__).chomp!("/lib/ruby/3.4.0/x86_64-linux")`` — that is
    ``--enable-load-relative``, the flag this whole row turns on — so ``libdir`` follows the tree
    wherever it goes, and ``CONFIG['LIBRUBYARG']`` is ``-Wl,-rpath,$(libdir) -lruby-static
    $(MAINLIBS)`` on the compiled cells and ``-lx64-ucrt-ruby340`` on the borrowed ones, which is
    what ``lib/libx64-ucrt-ruby340.dll.a`` resolves. ``lib/pkgconfig/ruby-3.4.pc`` restates it with
    ``prefix=${pcfiledir}/../..``. Delete either file and the artifact goes on handing an embedder a
    link line naming something it does not carry — the exact failure P4a kept CPython's unloaded
    ``libpython`` to avoid, and here it applies to all six cells instead of four.

    So both stay, the difference is linkage and not capability, and ``--disable-shared`` is left
    alone. The one thing that is genuinely uneven is smaller than the question: Windows'
    ``LIBRUBYARG_STATIC`` names a ``lib…-static.a`` RubyInstaller does not ship, so the *other* of
    the two spellings is the empty one there. Nothing follows it unless an embedder asks for it by
    name, and there is nothing to ship that would answer.

    ``include`` is the second entry and the easier one, for CPython's reason exactly: a gem with a C
    extension is compiled on the machine installing it, against these headers. It stays on the two
    cells that have just declared in `LACKS` that they cannot compile one, because *cannot today*
    and *cannot ever* are different artifacts — ``ridk install`` is the supported way a user adds
    the toolchain, and an archive with the headers deleted stays broken afterwards. That is the
    argument P4 used to keep ``libs/python3XX.lib`` on a Windows shipping no compiler either.
    """
    reasons = {
        "include": (
            "the Ruby C API headers. A gem with a C extension is compiled by `gem install` on the "
            "machine doing the installing, against these, and there is nowhere else to fetch a set "
            "matching this interpreter. Kept on the Windows cells too, where `lacks` says no "
            "compiler is present: `ridk install` adds one, and headers deleted here cannot be."
        ),
    }
    found = sorted((tree / "lib").glob(LINKER[operating_system]))
    if not found:
        raise SystemExit(
            f"this tree has no lib/{LINKER[operating_system]} — the file `rbconfig.rb` names in "
            f"LIBRUBYARG, which is what a program embedding Ruby links against. Either the build "
            f"stopped installing it or upstream renamed it; both change what this cell offers, and "
            f"shipping an artifact whose own build record points at a missing file is the outcome "
            f"this check exists to prevent."
        )
    for library in found:
        reasons[library.relative_to(tree).as_posix()] = (
            "libruby, as this toolchain spells it — an import library against the DLL beside "
            "`bin/ruby.exe` on Windows, the interpreter itself as a static archive on the cells "
            "configured `--disable-shared`. `rbconfig.rb` computes its own prefix from its own "
            "location and publishes this file in LIBRUBYARG, so anything embedding Ruby is sent "
            "here on all six cells; ENABLE_SHARED differs between them and the capability does not."
        )
    return reasons
