# Python

*Part of [mixengine-packages](../../README.md), which holds the table of what is packaged.*

The evaluation was as short as Node.js's, and the row had been *assumed* borrowable since before
there was a pipeline. It is:

| OS / arch | Range | How |
| --- | --- | --- |
| Windows x86_64 | **3.10 – newest** | **borrowed** — [`python-build-standalone`](https://github.com/astral-sh/python-build-standalone), repacked |
| Windows aarch64 | **3.11 – newest** | ditto; the 3.10 line has no ARM64 build |
| macOS aarch64, x86_64 | **3.10 – newest** | ditto |
| Linux x86_64, aarch64 | **3.10 – newest** | ditto, the `gnu` builds and never `musl` |

One thing had to be added rather than repacked, and only on Windows: upstream ships `Scripts/` empty,
so `pip` is importable and not runnable. `tools/python.py` writes a two-line `Scripts/pip.cmd` that
calls the interpreter beside it, because the alternative — letting `ensurepip` generate `pip.exe` —
bakes an absolute interpreter path into a tree whose whole purpose is to be movable.

One thing is removed, and only on Linux before 3.13: `_crypt` links against a libxcrypt no archive
here carries, which Debian and Ubuntu install as a base package and Fedora does not — so keeping it
would mean publishing an artifact that works on some glibc distributions and not others. CPython
deprecated `crypt` in 3.11 and removed it in 3.13; this makes the three lines before that behave the
same way. Both the addition and the removal are named in each archive's `mixengine-artifact.json`,
under `upstream.added` and `upstream.removed`, so a reader comparing against upstream finds the
difference stated rather than deducing it.

**One decision was needed, and it is tkinter — dropped, on every cell.** Not for its size, though it
is 11.7 MB of a 59.8 MB Windows archive, but because upstream ships a *different* tkinter to each
half of the table: Windows gets Tk 8.6 with Tix 8.4.3 beside it, Unix gets Tk 9.0 with itcl and the
Tcl Thread package, and on 3.14 the Windows DLLs turn into Tcl 9 and carry the whole Tcl script
library inside themselves. So `python 3.13.15` already meant one toolkit on one machine and a
different one on the next. Dropping it rather than levelling it up is the rule's own test applied to
the feature: nothing MixEngine runs draws a window, and the two things in the standard library that
need a display are the graphical toolkit and the editor written in it. The Unix cells lose the whole
of `share/` with it — a manual page whose Windows twin has never existed, and a 2.2 MB terminfo
database the ncurses compiled into these very builds cannot reach, because the paths built into that
binary are `/etc/terminfo`, `/lib/terminfo` and `/usr/share/terminfo` and none of them is inside the
archive — and the Windows cells lose the per-extension import libraries, 31 of them on 3.10, which
link a module *into* a Python being compiled and are opened by nothing that installs a wheel.

**And one thing is kept that this document rules out everywhere else: the C API.** `include/` on all
six cells, plus `libs/python3XX.lib` and `libs/python3.lib` on the two with a linker that needs
them. That is the opposite answer to the one P2 gave PHP, where `dev/php8.lib` was deleted as *"892
KB of import library in a runtime that is not an SDK"*, and the difference is not a lapse — it is
what "no more than is needed" means when the question is asked per runtime. A PHP extension reaches
a developer's machine as a DLL somebody else compiled. A Python extension frequently does not:
`pip install` of any source distribution without a matching wheel compiles C *on the machine doing
the installing*, against `Python.h` and, on Windows, linked to `python3XX.lib`. Take them out and
the runtime still starts, still passes every check in its smoke test, and fails the first such
install. So each archive carries a top-level `keeps` in its manifest naming the path and the reason,
`borrow.declare` refuses to write a `keeps` naming something the tree does not have, and the smoke
test proves the claim from the other side by asking the relocated interpreter where its headers are
and finding `Python.h` there.

**The second kept thing is the one that looks least defensible and is the same decision.** Every
Unix cell carries `lib/libpython3.X.so` — 30.3 MiB on Linux, 16.7 MiB on macOS — which is a second
complete copy of an interpreter `bin/python3.X` already contains statically, and which nothing that
ships in the archive loads: of the twelve dynamically linked ELF files in a Linux tree, none names it
in `DT_NEEDED`, and the macOS binary's `LC_LOAD_DYLIB` list says the same. It is kept because of what
upstream ships beside it. There is a `libpython3.X.so` symlink whose only possible consumer is a
linker, the runtime loader going by `SONAME` instead; `python3-config` derives its prefix from its
own location precisely so a *moved* tree still answers, and prints `-L<tree>/lib -lpython3.X` with
`PY_ENABLE_SHARED=1` written in so it will not look anywhere else. Delete the library and the
artifact is describing a file it does not carry — repairable only by rewriting the publisher's record
of its own build. Windows settles the same question with no choice in the matter, `python3XX.dll`
being the interpreter there and `libs/python3XX.lib` the same file an embedder links, so two cells
can embed a Python whatever anyone decides. **That is the whole of the remaining difference between
the halves**, and the arithmetic says so exactly: pruned and stripped, Linux is 18.6 MiB larger than
Windows on 3.13.15 while this file weighs 19.1 MiB, so without it the Linux artifact would be the
*smaller* one. macOS lands within 1.4 MiB of Windows carrying a 15.3 MiB copy of its own.

**Stripped, because a variant named `install_only_stripped` still shipped 14.5 MiB of tables nothing
in a running process reads**, and it was named honestly: upstream's step removed the debug
information and only that — 207 MB of it from one library — which by definition left the symbol
tables behind, and from 3.12 on left something stranger beside them. `.note.bolt_info` appears in
`bin/python3.X` and in no library; `.rela.text` appears in the library and in no executable. So BOLT
ran on the executable and consumed its relocations, and the library, linked with the same
`--emit-relocs` and then never optimised, carries 6.5 MB of the input to a step that skipped it. The
direction is again Windows': its stripped variant ships **no `.pdb` at all**, so there is nothing to
level up to. The flag is not the same on both Unix halves and that is the point — ELF hides a second,
allocated symbol table a `strip` may not touch, while Mach-O has one table whose exported range
`--strip-all` empties, taking 1,755 symbols and `_Py_Initialize` with it, so macOS gets `-x`. And
because the file most at risk is the one nothing in the archive loads, the proof is not a test run:
`python.mapped` compares every allocated section, every program header and every table a linker reads
across the operation and refuses to publish an artifact where any of it moved, `python.countersigned`
recomputes the arm64 code signature's 4,230 page hashes rather than trusting the tool to have
re-signed, and `upstream.changed` names each file and the command that changed it, because a file
carrying upstream's path and not upstream's bytes is the one difference that reads as corruption.
