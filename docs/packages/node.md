# Node.js

*Part of [mixengine-packages](../../README.md), which holds the table of what is packaged.*

There is nothing to evaluate here and one recipe for every cell:

| OS / arch | Range | How |
| --- | --- | --- |
| Windows x86_64 | **16 – newest** | **borrowed** — official nodejs.org zip, repacked |
| Windows aarch64 | **20 – newest** | ditto; upstream's first Windows-on-ARM build is 20.0.0 |
| macOS aarch64 | **16 – newest** | **borrowed** — official tarball; 16.0.0 is upstream's first native Apple Silicon build |
| macOS x86_64 | **16 – newest** | ditto |
| Linux x86_64, aarch64 | **16 – newest** | ditto |

The floor is 16 because that is where a *native* build exists for every architecture MixEngine runs
on — before it, the only macOS Node is x86_64, and handing that to an Apple Silicon machine would be
offering emulation under the name of a version. Where a line has no build for a target,
`tools/node.py` says so and exits 75 — an empty cell of the table, which the workflow skips rather
than fails, so that one absent build does not stop the release of the five that exist.

One decision was needed, and it is the only one this row makes: **`include/node` is dropped, on
every cell**. Upstream ships 59 MB of C++ headers to Unix and none at all to Windows — 2,726 files
in 24.19.0, 29.7% of that archive, byte for byte its own `node-v24.19.0-headers.tar.gz` — so one
version meant two things before anything here chose. The reading that keeps them is that `node-gyp`
needs headers, and it does; what it does not do is read *these*. `node-gyp` looks inside the runtime
it is running under only when the build set `use_prefix_to_find_headers`, a flag distributions pass
so their `-dev` package can compile offline, and every official build has it false — read out of the
`process.config` baked into the Linux binaries rather than assumed. Without it, `configure.js`
downloads `process.release.headersUrl` into `~/.node-gyp/<version>` and compiles against that, which
is why native modules have always built on Windows against an archive with no `include/` at all.

Keeping them everywhere was not reachable in any case: `node-gyp --nodedir` on Windows links against
`<nodedir>/$(Configuration)/node.lib`, a path out of a build tree that the headers tarball has never
contained and that upstream publishes separately, per architecture.

What goes with them is whatever else sits at the root and is not the runtime, because `tools/node.py`
keeps a list of what stays rather than a list of what goes: `include/` and `share/` on Unix,
`install_tools.bat` — a Chocolatey install of Python and the VC build tools, onto the whole machine —
and `nodevars.bat` on Windows, `README.md` and `CHANGELOG.md` on both. The keep-list is why Node 16's
`share/systemtap` and `node_etw_provider.man` went too, neither of which anything here knew existed.
What stays, checked rather than assumed, is npm's 2.7 MB of `docs/` and `man/`: `npm help-search`
reads `docs/content`, and `npm help` opens `docs/output/*.html` on Windows and runs `man` against
`man/man[1-7]` on Unix. Once both cells are pruned, the whole remaining difference between the
Windows and Linux trees of 24.19.0 is `node.exe` against `bin/node` and the per-shell launchers
beside it.
