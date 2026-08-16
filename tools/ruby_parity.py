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

This is the same argument ``php_parity.py`` makes for its own row and ``borrow.py`` makes about
mechanics: a decision two producers take separately will drift, and the drift is invisible because
they agree on the name of the thing. The rule served is [*One version means one thing, and no more
than is needed*][rule].

Nothing here downloads, unpacks or runs anything.

[rule]: ../README.md#one-version-means-one-thing-and-no-more-than-is-needed
"""

from __future__ import annotations

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
