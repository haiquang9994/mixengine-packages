# Ruby

*Part of [mixengine-packages](../../README.md), which holds the table of what is packaged.*

One of the three columns turned out to be borrowable and two did not:

| OS / arch | Range | How |
| --- | --- | --- |
| Windows x86_64 | **3.2 – newest** | **borrowed** — [RubyInstaller](https://github.com/oneclick/rubyinstaller2) `.7z`, repacked |
| Windows aarch64 | **3.4 – newest** | ditto; upstream's first ARM64 archive is in the 3.4 line |
| macOS aarch64, x86_64 | **3.2 – newest** | **built** — from ruby-lang.org's source, dependencies bundled |
| Linux x86_64, aarch64 | **3.2 – newest** | **built** — the same, inside AlmaLinux 8 for the glibc floor |

RubyInstaller configures Ruby with `--enable-load-relative`, so the standard library, the gem home
and the CA bundle are all computed from the executable's own location — `tools/ruby.py` checks all
four from a directory the archive has been moved to. The macOS and Linux cells have no such
publisher: Homebrew's `portable-ruby` is relocatable and publishes exactly one version,
`ruby/ruby-builder`'s artifacts "embed the install path when built and cannot be moved around" in
its own README's words, and RVM's binaries are prefix-bound and years stale. So `tools/ruby_unix.py`
passes the same flag to a build of its own — and the two recipes share `tools/ruby_smoke.py`, which
is *what they claim* rather than how they got there, because a daemon installing one of these cannot
tell which produced it.

**What they claim was shared and what they *decide* was not, and that had cost 225 MB.** The four
compiled cells pass `--disable-install-doc` and delete `share/man`, `share/doc` and `share/ri` on an
explicit argument — that a development environment has no business shipping four copies of Ruby's
manual for every version of every line. The two borrowed cells had never been told, because
RubyInstaller ships a general-purpose Ruby and nothing had measured what that meant: 60.3 MB of a
108 MB tree on 3.4.10, and **224.9 MB of a 276 MB tree on 4.0.6**, four fifths of an artifact of a
programming language being RDoc's HTML rendering of that language's own manual. The list is
`tools/ruby_parity.py` now and both recipes read it, which halves the Windows archive — 34.0 MB to
17.2 MB on 3.4.10, 53.5 MB to 18.8 MB on 4.0.6.

**And one asymmetry here cannot be closed at any price, so the artifact states it.** Asked what
RubyInstaller does about the three flags the Unix build passes, a Windows Ruby answers that it has
no YJIT — `RubyVM::YJIT` is undefined and `ruby --yjit` warns, because CRuby does not build YJIT for
`x64-mingw-ucrt` — and that `gem install` of a gem with a C extension exits 1 with `MSYS2 could not
be found`, the compiler being a separate ~1 GB toolchain published as its own installer. The Unix
recipe *fails a build* over both. Neither can be moved, so both are written into a top-level `lacks`
field: a daemon can then refuse to enable a feature the cell does not have instead of passing a flag
that warns, and a blueprint asking for a native gem can fail where it is written rather than on
somebody's machine. It is the only field here that is an admission, and an absence nothing states is
an absence a reader has to discover.

**A second asymmetry was written down and then measured, and it was not there.**
`RbConfig::CONFIG['ENABLE_SHARED']` is `yes` on the two borrowed cells and `no` on the four compiled
ones, which reads as *two cells can be embedded in a program and four cannot* — the question P4a
answered for CPython, apparently coming out the other way. `--disable-shared` does not mean no
libruby. It means libruby is a **static archive**: `lib/libruby-static.a` on Linux and
`lib/libruby.3.4-static.a` on macOS, 41.4 MB and 28.3 MB on 3.4.10 and the largest file in either
tree. And each half names its own copy from a record that survives the move — `rbconfig.rb` begins
by deriving its prefix from its own location, which is `--enable-load-relative` again, so
`CONFIG['LIBRUBYARG']` reads `-Wl,-rpath,$(libdir) -lruby-static $(MAINLIBS)` on the compiled cells
and `-lx64-ucrt-ruby340` on the borrowed ones, which is what the 2.4 MB
`lib/libx64-ucrt-ruby340.dll.a` resolves. So all six hand an embedder a link line naming a file
inside the artifact, the difference is linkage and not capability, and the import library is not
surplus after all. Both stay, with `include/` beside them, declared in `keeps` — on the Windows
cells too, where `lacks` has just said no compiler is present: `ridk install` adds one, and headers
deleted here cannot be.

What the same measurement did find is that **the Unix cells shipped their debug information**, which
is P4b's finding on a different row and is levelled in the same direction. Of 3.4.10's Linux tree,
`bin/ruby` was 20.5 MB with 11.7 MB of DWARF in it and the static library 41.4 MB of which 26.1 MB
was DWARF and its relocations; Windows has none, RubyInstaller having linked with `-s`. Every
compiled file is now stripped and every one of them is proven across the operation, which takes
104.4 MB of Linux tree to 63.1 MB and 78.4 MB of macOS tree to 55.3 MB.

**It takes two instructions, because the tree holds two kinds of file and either instruction would
destroy the other kind.** `bin/ruby` and the extension modules are loaded, and their symbol tables
are dead weight — those get `--strip-all` on Linux and `-x` on macOS, and are checked by comparing
every byte a loader maps and every table a linker reads before and after. The static library is
*linked against*, which is what `keeps` has just finished arguing, and its symbol table is the entire
point: `--strip-all` over `libruby-static.a` takes it from 41.4 MB to 7.9 MB and leaves a file that
resolves nothing, a broken artifact that no test here would catch because nothing inside the tree
links against it either. So it gets `--strip-debug`, and it is checked by a different comparison —
the archive's own symbol index, then every member's globals, its relocations resolved *by name*, and
the bytes of every section that will end up in somebody else's binary. By name because a successful
strip renumbers both tables underneath them; comparing the tables as bytes would report every
working run as a failure.

The CA store is the part that is not obvious. A Ruby linked against a distribution's OpenSSL
inherits that distribution's `OPENSSLDIR` — `/etc/pki/tls` on the Red Hat family, `/etc/ssl` on the
Debian one — so an artifact built on one verifies certificates perfectly on the build machine and
fails every handshake on a user running the other, with an error that names nothing. OpenSSL is
therefore compiled here with its four default-path functions taught to answer relative to the
loaded `libcrypto`'s own location, which is `--enable-load-relative` applied one library down, and
the bundle itself ships inside the tree. `OpenSSL::X509::DEFAULT_CERT_FILE` names a file inside the
artifact on all six targets, and the smoke test verifies a real chain over the network rather than
trusting the path.
