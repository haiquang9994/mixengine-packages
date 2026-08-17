#!/usr/bin/env python3
"""Take the symbols out of a binary, and prove that nothing else went with them.

Written for P4b, where CPython's Unix cells were levelled down to the Windows one that already ships
no `.pdb`, and lifted out of `python.py` for P5b, where the Ruby row needs the same operation on
files of a shape P4b never had to describe. The argument for hoisting it is the one this repository
makes about every pair of producers that answer one question: two recipes stripping their own
binaries by their own rules would disagree about the same file, and nothing outside either recipe
could notice.

**The proof is structural, and it has to be.** The usual way to justify writing over a compiled file
is to run it afterwards, and that proof does not reach the files most at risk. CPython's
`lib/libpython3.X.so.1.0` and Ruby's `lib/libruby-static.a` are both things *nothing in their own
archive loads* — kept, and declared in `keeps`, because the tree's own build record hands an
embedder a link line naming them. A smoke test can start either interpreter a thousand times without
touching either file. So the claim is made by comparison instead: if everything a loader maps and
everything a linker reads is identical across the operation, the two files cannot behave
differently, and running them would say no more than that.

Which comparison that is depends on what the file is for, and the two are not the same claim.

:func:`mapped` is the loader's view, for an executable or a shared library. On ELF the split is
drawn by the hardware — `SHF_ALLOC` marks a section as part of the process image and a strip removes
only sections without it — and on Mach-O, which has no such bit, the line is drawn at `__LINKEDIT`,
the one segment that legitimately shrinks.

:func:`resolvable` is the linker's, for a static library, and it is P5b's addition. An `ar` archive
is not an image: a member is compiled and never mapped, `SHF_ALLOC` says nothing about what matters,
and what has to survive is every symbol an embedder resolves and every relocation that will be
applied when it does. Both are *renumbered* by a successful strip — removing `.debug_*` renumbers
the section headers, and removing a debug symbol renumbers the table every relocation indexes into —
so comparing those tables as bytes reports a difference on every run that worked. They are compared
**by name** instead, which is the question actually being asked: can this still be linked against.

Nothing here downloads or unpacks anything, and nothing here decides *which* files to strip; a
recipe knows what it built. Python 3 stdlib only, like everything else in this directory.
"""

from __future__ import annotations

import collections
import hashlib
import shutil
import struct
import subprocess
from collections.abc import Sequence
from pathlib import Path

# What the platform's own `strip` is asked to do, and **not one of the four instructions is
# interchangeable with another**, which is the finding these tables exist to carry rather than a
# portability wrinkle.
#
# The rows differ by object format. ELF keeps two symbol tables and a linker only ever reads the
# allocated one: `.dynsym` has `SHF_ALLOC`, `strip` may not touch it, so `--strip-all` takes
# `.symtab` and everything anyone links against survives untouched. Mach-O keeps **one** —
# `LC_SYMTAB` holds the local, the exported and the undefined symbols in three ranges that
# `LC_DYSYMTAB` indexes — and the same instruction there empties the exported range: measured on
# `libpython3.13.dylib`, 1,755 exported symbols become 0 and `_Py_Initialize` stops existing in the
# file, to save 66 KB more than `-x` saves. So `-x`, which discards the local range and is what the
# exported one is defined against.
#
# The columns differ by what the file is *for*, and that is P5b's half. An image is loaded and its
# symbol table is dead weight. A static library is linked against, and its symbol table is the
# entire point — `--strip-all` over `libruby-static.a` takes it from 41.4 MB to 7.9 MB and leaves a
# file that resolves nothing, which is a broken artifact no smoke test in this repository would
# catch, because nothing inside either tree links against that file either. An archive therefore
# gets `--strip-debug`, which removes the `.debug_*` sections and the symbols that only describe
# them.
#
# Windows is absent from both tables, and the absence is the argument rather than an omission:
# python-build-standalone's stripped variant ships no `.pdb` at all — zero files, of 3,303 — and
# RubyInstaller links with `-s`, visible in the `DLDFLAGS` its own `.pc` publishes. Both Windows
# cells have already had this done to them by their publisher, and levelling the other four down to
# them is the same move, in the same direction, that `python.prune` made with tkinter.
IMAGES = {"linux": ["--strip-all"], "macos": ["-x"]}
ARCHIVES = {"linux": ["--strip-debug"], "macos": ["-S"]}

# ELF's "the loader maps this" bit, the section type that occupies address space without occupying
# the file, and the four kinds of section header a static library is read through.
SHF_ALLOC = 0x2
SHT_NOBITS = 0x8
PT_LOAD = 0x1
SHT_SYMTAB, SHT_RELA, SHT_REL = 0x2, 0x4, 0x9
STB_LOCAL, STT_SECTION = 0, 3

# The load commands that point into `__LINKEDIT`, minus the two a strip is *meant* to rewrite. The
# symbol table is what is being removed and the code signature has to be remade over the smaller
# file, so those two are compared by other means below; everything else here — the export trie the
# static linker reads, the chained fixups dyld applies, the function starts a profiler walks — is
# load-bearing and has to come through byte for byte.
LINKEDIT = {
    0x80000022: 5,           # LC_DYLD_INFO_ONLY: rebase, bind, weak bind, lazy bind, export
    0x80000033: 1,           # LC_DYLD_EXPORTS_TRIE
    0x80000034: 1,           # LC_DYLD_CHAINED_FIXUPS
    0x00000026: 1,           # LC_FUNCTION_STARTS
    0x00000029: 1,           # LC_DATA_IN_CODE
}

# The fields of the tuples :func:`mapped` builds, so that a refusal can say *what* moved instead of
# only that something did. P4c cost a download of the right distribution and a local reproduction to
# learn that a strip had changed one program header's `p_offset` and one segment's `p_filesz`; a log
# that says so costs nothing, and is the difference between diagnosing the next one from a CI run
# and diagnosing it from a machine somebody has to go and find.
PROGRAM_HEADER = ("p_type", "p_flags", "p_vaddr", "p_paddr", "p_filesz", "p_memsz", "p_align")
SECTION_HEADER = ("sh_type", "sh_flags", "sh_addr", "sh_size", "contents")
MACHO_SECTION = ("addr", "length", "flags", "contents")
# What :func:`_macho_object` and :func:`_elf_object` put in a `symbols` entry, told apart by length.
# The Mach-O row carries no address: see that function for why a `value` there is the assembler's
# layout rather than the object's content.
MACHO_SYMBOL = ("name", "type", "section", "contents", "references")
ELF_SYMBOL = ("name", "bind", "type", "section", "value", "size")

# The two structured sections a Mach-O strip rewrites wholesale, and the section types whose whole
# licence is that identical entries may be merged. Each is read by its own rule below, because a
# comparison that reads them as flat bytes is comparing where the assembler put things.
UNWIND = "__LD,__compact_unwind"
UNWIND_RECORD = 32
FRAME = "__TEXT,__eh_frame"
LITERAL_SECTIONS = {0x2: 0, 0x3: 4, 0x4: 8, 0x5: 8, 0xE: 16}     # cstring, 4/8/pointer/16-byte
ZEROFILL_SECTIONS = {0x1, 0xC, 0x12}
CPU_ARM64, CPU_X86_64 = 0x0100000C, 0x01000007

ELF_MAGIC = b"\x7fELF"
MACHO_MAGIC = b"\xcf\xfa\xed\xfe"
AR_MAGIC = b"!<arch>\n"


# ------------------------------------------------------------------------- what a loader sees ---


def mapped(path: Path) -> dict[str, object]:
    """Reduce a binary to everything a loader or a linker can see in it, and to nothing else.

    On ELF, `.dynsym`, `.dynstr`, `.gnu.hash`, `.dynamic` and the `.rela.dyn`/`.rela.plt` a loader
    applies are all allocated; `.symtab`, `.strtab` and the `.rela.text` a post-link optimiser
    wanted are not. Program headers are compared separately from the sections, because they are what
    the kernel actually reads and a section header could in principle be tidy while a segment moved
    — every field of one except `p_offset`, which is compared as the bytes it points at instead of
    as the number it is. See the comment on that line for the artifact that taught the difference.

    Mach-O has no such bit, so the line is drawn at `__LINKEDIT` — the one segment that legitimately
    shrinks, since the symbol table is inside it. Everything else in there that a strip must not
    disturb is named in :data:`LINKEDIT` and hashed by hand, and the exported symbols are compared
    as *names* rather than as table offsets, which is the only comparison that survives the table
    being rebuilt underneath them.
    """
    blob = path.read_bytes()
    seen: dict[str, object] = {}

    if blob[:4] == ELF_MAGIC:
        if blob[4] != 2:
            raise SystemExit(f"{path.name} is a 32-bit ELF, and every cell in this table is 64-bit")
        end = "<" if blob[5] == 1 else ">"
        kind, machine = struct.unpack_from(end + "HH", blob, 0x10)
        e_phoff, e_shoff = struct.unpack_from(end + "QQ", blob, 0x20)
        e_phentsize, e_phnum = struct.unpack_from(end + "HH", blob, 0x36)
        e_shentsize, e_shnum, e_shstrndx = struct.unpack_from(end + "HHH", blob, 0x3A)
        seen["elf"] = (kind, machine, e_phnum)
        for index in range(e_phnum):
            p_type, p_flags, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, p_align = \
                struct.unpack_from(end + "IIQQQQQQ", blob, e_phoff + index * e_phentsize)
            # **`p_offset` is left out, and it is the only field that is**, which is P5c. It says
            # where a segment's contents sit *in the file*; a loader maps `p_filesz` bytes at
            # `p_vaddr` and does not care which part of the file they were read from. A strip that
            # removes a non-allocated section lying before a segment compacts everything after it,
            # so the number moves and nothing about the process image does — measured on a freshly
            # built OpenSSL 3.5.7, where three segments of `lib/libcrypto.so.3` each moved down by
            # exactly 835,584 bytes and this refused to publish an artifact that was correct.
            #
            # It was tried as a hash of the bytes at that offset first, which is the obvious way to
            # keep asking *what* is there, and it does not work: the program header table is itself
            # inside the first `PT_LOAD`, so any offset that moves changes the contents of `PT_PHDR`
            # and of the segment containing it. That version failed an ordinary `strip --strip-all`
            # of an ordinary shared library — the check calling its own subject a failure again.
            #
            # What ties the segments back to the file is :func:`unmapped`, asked a second time after
            # the operation: every allocated section still has to lie inside some `PT_LOAD`. The
            # sections are already compared here by address, size and contents, so a segment table
            # that still covers all of them, at the same addresses and sizes, is mapping the same
            # image. The CPython case P4c found is caught here regardless, by `p_filesz` and
            # `p_memsz` — a segment mapping *less* than it did, which no offset arithmetic explains.
            seen[f"segment {index}"] = (p_type, p_flags, p_vaddr, p_paddr, p_filesz, p_memsz,
                                        p_align)

        def header(index: int) -> tuple:
            return struct.unpack_from(end + "IIQQQQ", blob, e_shoff + index * e_shentsize)

        names_at = header(e_shstrndx)[4]
        for index in range(e_shnum):
            name, kind, flags, addr, offset, size = header(index)
            if not flags & SHF_ALLOC:
                continue
            stop = blob.index(b"\0", names_at + name)
            # `.bss` occupies address space and no file, so hashing at its offset would hash
            # whatever section happens to be lying there and call a stripped file different for no
            # reason.
            body = b"" if kind == SHT_NOBITS else blob[offset:offset + size]
            label = blob[names_at + name:stop].decode()
            seen[f"section {label}"] = (kind, flags, addr, size, hashlib.sha256(body).hexdigest())
        return seen

    if blob[:4] != MACHO_MAGIC:
        raise SystemExit(f"{path.name} is neither a 64-bit ELF nor a thin 64-bit Mach-O")

    cpu, ncmds = struct.unpack_from("<I", blob, 4)[0], struct.unpack_from("<I", blob, 16)[0]
    seen["macho"] = (cpu, struct.unpack_from("<I", blob, 12)[0])
    offset, symtab, dysymtab, dylibs, rpaths = 32, None, None, [], []
    for _ in range(ncmds):
        command, size = struct.unpack_from("<II", blob, offset)
        if command == 0x19:                                          # LC_SEGMENT_64
            label = blob[offset + 8:offset + 24].split(b"\0")[0].decode()
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from("<QQQQ", blob, offset + 24)
            count, = struct.unpack_from("<I", blob, offset + 64)
            if label == "__LINKEDIT":
                offset += size
                continue
            seen[f"segment {label}"] = (vmaddr, vmsize, filesize, count)
            # Sections rather than the segment's file range, and this is the correction the check
            # made on its own first run rather than a nicety. `__TEXT` begins at file offset 0 on a
            # Mach-O, so it contains the header and every load command — including the `LC_SYMTAB`
            # and `LC_CODE_SIGNATURE` whose offsets a strip is *supposed* to move. Hashing the
            # segment whole therefore reports `__TEXT` as different on every successful strip, which
            # is the check calling its own subject a failure. Sections start after the load commands
            # and are the same granularity the ELF branch above compares.
            for index in range(count):
                at = offset + 72 + index * 80
                name = blob[at:at + 16].split(b"\0")[0].decode()
                addr, length = struct.unpack_from("<QQ", blob, at + 32)
                where, flags = struct.unpack_from("<I", blob, at + 48)[0], \
                    struct.unpack_from("<I", blob, at + 64)[0]
                # The zero-fill types occupy address space and no file, exactly as ELF's NOBITS
                # does, and hashing at their offset would hash whatever is lying there.
                body = b"" if flags & 0xFF in (0x1, 0xC, 0x12) else blob[where:where + length]
                seen[f"section {label},{name}"] = (addr, length, flags,
                                                   hashlib.sha256(body).hexdigest())
        elif command == 0x02:                                        # LC_SYMTAB
            symtab = struct.unpack_from("<IIIIII", blob, offset)
        elif command == 0x0B:                                        # LC_DYSYMTAB
            dysymtab = struct.unpack_from("<20I", blob, offset)
        elif command == 0x0D:                                        # LC_ID_DYLIB
            seen["install name"] = blob[offset + 24:offset + size].split(b"\0")[0].decode()
        elif command in (0x0C, 0x8000001F, 0x18):                    # LC_LOAD*_DYLIB, LC_REEXPORT
            dylibs.append(blob[offset + 24:offset + size].split(b"\0")[0].decode())
        elif command == 0x8000001C:                                  # LC_RPATH
            rpaths.append(blob[offset + 12:offset + size].split(b"\0")[0].decode())
        elif command in LINKEDIT:
            for pair in range(LINKEDIT[command]):
                at, length = struct.unpack_from("<II", blob, offset + 8 + pair * 8)
                seen[f"linkedit {command:#x}/{pair}"] = hashlib.sha256(
                    blob[at:at + length]).hexdigest()
        offset += size
    seen["dylibs"], seen["rpaths"] = tuple(dylibs), tuple(rpaths)

    # The exported symbols, by name. A strip rebuilds the table and renumbers everything in it, so
    # comparing `LC_DYSYMTAB`'s indices would report a difference on every successful run; comparing
    # the names it points at is the question actually being asked — can this still be linked?
    if symtab and dysymtab:
        _, _, symoff, _nsyms, stroff, _strsize = symtab
        first, count = dysymtab[4], dysymtab[5]
        exported = []
        for index in range(first, first + count):
            strx, = struct.unpack_from("<I", blob, symoff + index * 16)
            exported.append(blob[stroff + strx:blob.index(b"\0", stroff + strx)].decode())
        seen["exports"] = tuple(sorted(exported))
    return seen


def countersigned(path: Path) -> str | None:
    """Recompute a Mach-O's ad-hoc signature, or say there is none. ``None`` means valid.

    **The failure this exists for cannot be caught any other way.** The arm64 cells carry an ad-hoc
    signature whose CodeDirectory holds a SHA-256 of each 4 KB page of the file — 4,230 of them in
    `libpython3.13.dylib` — and the kernel refuses to map one whose pages no longer hash to what it
    says: not with an error a caller can print, with `SIGKILL`. The `x86_64-apple-darwin` cell of
    the same release carries no `LC_CODE_SIGNATURE` at all, which is why this answers ``None`` for a
    file without one rather than treating its absence as a fault. A strip that resized the file and
    left the signature behind would therefore produce a dylib that is structurally perfect, passes
    every comparison in :func:`mapped`, and kills any process that loads it. Nothing else here would
    notice, least of all the smoke test: `libpython3.X.dylib` is the file that archive never opens.

    So it is checked the way everything else in this repository is checked — by recomputing it. The
    blobs inside a signature are big-endian inside a little-endian file, which is the one thing
    about the format worth knowing before reading the `struct` calls below.
    """
    blob = path.read_bytes()
    ncmds, offset, at = struct.unpack_from("<I", blob, 16)[0], 32, None
    for _ in range(ncmds):
        command, size = struct.unpack_from("<II", blob, offset)
        if command == 0x1D:                                          # LC_CODE_SIGNATURE
            at = struct.unpack_from("<I", blob, offset + 8)[0]
            break
        offset += size
    if at is None:
        return None

    magic, _length, count = struct.unpack_from(">III", blob, at)
    if magic != 0xFADE0CC0:
        return f"{path.name} has a code signature that is not an embedded superblob ({magic:#x})"
    for index in range(count):
        _kind, relative = struct.unpack_from(">II", blob, at + 12 + index * 8)
        directory = at + relative
        if struct.unpack_from(">I", blob, directory)[0] != 0xFADE0C02:
            continue
        hashes, _ident, _special, slots, limit = struct.unpack_from(">IIIII", blob, directory + 16)
        width, algorithm, _platform, page = struct.unpack_from(">BBBB", blob, directory + 36)
        if algorithm != 2:
            return f"{path.name} is signed with hash type {algorithm}, and this only knows SHA-256"
        for slot in range(slots):
            start = slot * (1 << page)
            want = blob[directory + hashes + slot * width:directory + hashes + (slot + 1) * width]
            if hashlib.sha256(blob[start:min(start + (1 << page), limit)]).digest()[:width] != want:
                return (f"{path.name} carries a code signature that no longer matches its own "
                        f"bytes at page {slot} — on arm64 the kernel answers that with SIGKILL")
        return None
    return f"{path.name} has a code signature with no CodeDirectory in it"


def moved(key: str, old: object, new: object, depth: int = 2) -> str:
    """*key*, followed by which of its fields differ, as far down as this module can label them.

    A key whose value is a shape this cannot name comes back as itself, which is what every message
    said before :data:`PROGRAM_HEADER` existed. Two levels by default, because :func:`resolvable`
    keys a whole object by member and the useful sentence is inside it — *which member* is 469
    repetitions of nothing, and *which member and what about it* is a diagnosis.
    """
    if isinstance(old, dict) and isinstance(new, dict) and depth:
        inside = [name for name in sorted(set(old) | set(new)) if old.get(name) != new.get(name)]
        # Identity before layout here too, and for the reason the caller sorts: a member with a
        # dozen relocations and one changed symbol would otherwise report the relocations and hide
        # the symbol behind "and 9 more", which is the one line a reader needs.
        inside.sort(key=lambda name: 0 if name in ("symbols", "index", "members") else 1)
        spelled = ", ".join(moved(name, old.get(name), new.get(name), depth - 1)
                            for name in inside[:3])
        more = f" and {len(inside) - 3} more" if len(inside) > 3 else ""
        return f"{key} {{{spelled}{more}}}"

    if not (isinstance(old, tuple) and isinstance(new, tuple)):
        return key

    # **A list of symbols, compared by name before anything else.** That order is the whole of what
    # `resolvable` is asking — a name that is gone cannot be linked against, and a name still there
    # describing the same bytes has not gone anywhere, wherever those bytes now sit. Which of the
    # remaining fields moved is worth saying after that and not before it: `contents` is the atom a
    # name stands for and `references` is what that atom reaches out to, and the two read very
    # differently in a refusal.
    if old and new and isinstance(old[0], tuple) and old[0] and isinstance(old[0][0], str):
        was, now = {item[0] for item in old}, {item[0] for item in new}
        gone, fresh = sorted(was - now), sorted(now - was)
        if gone or fresh:
            return (f"{key} [{len(gone)} name(s) gone: {', '.join(gone[:3]) or '—'}"
                    f"{f'; {len(fresh)} new' if fresh else ''}]")
        # Every name survived, so say which *field* of them moved rather than leaving a reader to
        # assume the worst about a difference that may be a layout number.
        names = MACHO_SYMBOL if len(old[0]) == len(MACHO_SYMBOL) else ELF_SYMBOL
        seen_before = {item[0]: item for item in old}
        fields = {names[index] for item in new for index in range(1, len(item))
                  if item[0] in seen_before and seen_before[item[0]][index] != item[index]}
        return (f"{key} [all {len(was)} name(s) still there; what moved: "
                f"{', '.join(sorted(fields)) or 'nothing but the order'}]")

    # Any other list — an archive's index, the exported names of a dylib.
    if old and new and isinstance(old[0], str) and len(old) != len(new):
        gone = [item for item in old if item not in set(new)]
        added = len(new) - len(old) + len(gone)
        summary = f"{len(gone)} gone" + (f", {added} new" if added else "")
        return f"{key} [{summary}: {', '.join(gone[:2]) or '—'}]"

    names = None
    if len(old) == len(new):
        if key.startswith("segment ") and len(old) == len(PROGRAM_HEADER):
            names = PROGRAM_HEADER
        elif key.startswith("section ") and len(old) == len(SECTION_HEADER):
            names = SECTION_HEADER
        elif key.startswith("section ") and len(old) == len(MACHO_SECTION):
            names = MACHO_SECTION
    if names is None:
        return key
    fields = [f"{name} {was} -> {now}" for name, was, now in zip(names, old, new) if was != now]
    return f"{key} [{', '.join(fields)}]" if fields else key


def tally(before: dict, after: dict, differences: Sequence[str]) -> str:
    """What kind of thing differed, counted over *every* difference rather than the first few.

    **A list of four out of 469 is not a diagnosis**, and this is the sentence that turns one into
    one. `libruby.3.2-static.a` has 470 members; naming four of them says only that a lot changed,
    while counting what changed *inside* all of them says whether any member stopped publishing a
    symbol — which is the entire question :func:`resolvable` exists to ask. A tally that does not
    mention `symbols` is the archive still resolving everything it used to.

    Bucketed by kind, because the names are per-member and per-section: three hundred distinct
    section names counted once each is the same non-answer as the list of four.
    """
    counted: dict[str, int] = {}
    for key in differences:
        was, now = before.get(key), after.get(key)
        if not (isinstance(was, dict) and isinstance(now, dict)):
            continue
        for name in sorted(set(was) | set(now)):
            if was.get(name) == now.get(name):
                continue
            kind = ("relocations" if name.startswith("relocations against ")
                    else "sections" if name.startswith("section ")
                    else "atoms" if name.startswith("atoms in ")
                    else "literals" if name.startswith("literals in ") else name)
            counted[kind] = counted.get(kind, 0) + 1
    if not counted:
        return ""
    spelled = ", ".join(f"{kind} ({count})" for kind, count in
                        sorted(counted.items(), key=lambda row: -row[1]))
    return f" — over all {len(differences)}, what differs is: {spelled}"


def resign(path: Path) -> str | None:
    """Put an ad-hoc signature back on a Mach-O a strip has just resized. ``None`` means it worked.

    **Only reached when :func:`countersigned` says the old one no longer matches**, and only for a
    file whose loadable content this run has already proved identical — `mapped` runs first and the
    build is refused before this if anything a loader sees moved. So what is being restored is a
    hash of bytes that are known to be the right bytes, which is the difference between re-signing
    here and re-signing to make a complaint go away.

    Ad-hoc, `codesign -s -`, because that is the kind of signature these files arrive with: on arm64
    the linker signs every binary it produces, there is no identity involved, and the kernel's only
    question is whether the pages hash to what the CodeDirectory says. P5b needed this and did not
    have it — `bin/ruby` is rewritten by `install_name_tool` and then stripped, and the first CI run
    of that step reported the signature no longer matching *at page 0*, which is the header and the
    load commands. CPython's macOS cells never asked: the x86_64 one carries no signature at all and
    the arm64 one comes through Apple's `strip` still valid.
    """
    tool = shutil.which("codesign")
    if tool is None:
        return (f"{path.name} lost its code signature to `strip` and there is no `codesign` on "
                f"PATH to put one back — on arm64 the kernel answers that with SIGKILL")
    done = subprocess.run([tool, "--force", "--sign", "-", str(path)],
                          capture_output=True, text=True)
    if done.returncode != 0:
        return f"codesign --force --sign - {path.name} failed: {done.stderr.strip()}"
    return None


def unmapped(path: Path) -> list[str]:
    """Allocated sections whose bytes lie outside every ``PT_LOAD``, which is the two tables
    disagreeing.

    **A precondition, and the only one here that stops the operation instead of judging it.** An ELF
    says what it contains twice — the section headers, which a *linker* reads, and the program
    headers, which the *loader* reads — and nothing enforces that the two describe the same file.
    GNU `strip` works from the section table and writes a new program header table out of it, so
    where the two disagree it does not preserve the disagreement: it resolves it, in the section
    table's favour, and whatever the loader was reaching through the old segments stops being
    mapped.

    That is not hypothetical and it is not rare enough to leave to chance. python-build-standalone
    runs BOLT over the `x86_64-unknown-linux-gnu` interpreter, and BOLT moves `.dynstr`'s 45 KB of
    bytes to the end of the file while leaving its `sh_addr` at ``0x3ff5a0``, inside the first
    ``PT_LOAD`` — where the bytes are not. `strip --strip-all` says so itself, in a warning nobody
    reads (``allocated section `.dynstr' not in segment``), shrinks that segment from ``0x1000`` to
    ``0x5a0``, and produces an interpreter whose ``DT_STRTAB`` points at unmapped memory: every
    dynamic symbol name reads back as the empty string and the process dies before `main` with
    ``undefined symbol: , version``. Measured on binutils 2.42, the version ubuntu-24.04 runs, by
    running the binary before and after and restoring it in between. `lib/libpython3.14.so.1.0` in
    the same archive has a consistent layout, strips cleanly, and is worth four times the saving.
    :func:`mapped` catches the damage after the fact — it is what caught this — but by then the only
    copy of the file has been overwritten, so the answer has to be asked first.

    Read from the file, not from `strip`'s warning, because a warning is a string on stderr that a
    future binutils may reword and because this has to be true of a file rather than of a tool.
    Mach-O gets an empty list: it has no second table to disagree with, and the two macOS cells of
    this same release strip without complaint.
    """
    blob = path.read_bytes()
    if blob[:4] != ELF_MAGIC:
        return []
    end = "<" if blob[5] == 1 else ">"
    e_phoff, e_shoff = struct.unpack_from(end + "QQ", blob, 0x20)
    e_phentsize, e_phnum = struct.unpack_from(end + "HH", blob, 0x36)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from(end + "HHH", blob, 0x3A)
    if not e_shoff or not e_shnum or e_shstrndx >= e_shnum:
        return []

    loads = []
    for index in range(e_phnum):
        head = struct.unpack_from(end + "IIQQQQQQ", blob, e_phoff + index * e_phentsize)
        if head[0] == PT_LOAD:
            loads.append((head[2], head[5]))                          # p_offset, p_filesz

    def header(index: int) -> tuple:
        return struct.unpack_from(end + "IIQQQQ", blob, e_shoff + index * e_shentsize)

    names_at = header(e_shstrndx)[4]
    astray = []
    for index in range(e_shnum):
        name, kind, flags, _addr, offset, size = header(index)
        # `.bss` and its kind occupy address space and no file, so there is nothing for a segment to
        # cover and nothing for a strip to strand.
        if not flags & SHF_ALLOC or kind == SHT_NOBITS or not size:
            continue
        if any(at <= offset and offset + size <= at + length for at, length in loads):
            continue
        stop = blob.index(b"\0", names_at + name)
        astray.append(blob[names_at + name:stop].decode())
    return astray


# ------------------------------------------------------------------------- what a linker sees ---


def archives(tree: Path) -> list[Path]:
    """Every `ar` archive in the tree, by its own first eight bytes, symlinks skipped.

    By magic rather than by ``*.a``, for the reason `relocate.kind` reads a header rather than a
    suffix: the question is what the file is, and a list of names has to be right about a build
    system nobody here controls. It is also why this looks at the whole tree — Ruby installs one
    static library beside `bin/`, and a bundled gem that compiled an extension could leave another
    anywhere under `lib/ruby/gems`.
    """
    found = []
    for path in sorted(tree.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            with path.open("rb") as handle:
                if handle.read(8) == AR_MAGIC:
                    found.append(path)
        except OSError:
            continue
    return found


def members(blob: bytes) -> list[tuple[str, bytes]]:
    """``(name, body)`` for each member of an `ar` archive, in both dialects this table produces.

    The two halves of the Ruby row do not write the same archive and neither is wrong. GNU `ar`, on
    Linux, puts the symbol index in a member called `/`, the names longer than fifteen characters in
    a member called `//`, and spells a member's own name `array.o/`. BSD `ar`, on macOS, calls the
    index `__.SYMDEF SORTED`, has no long-name table, and writes any awkward name as `#1/<length>`
    with the name occupying the first *length* bytes of the body — which is why the body is trimmed
    here rather than at the caller.

    The index is returned like any other member. :func:`resolvable` reads it, because whether a
    linker can find a symbol at all is decided there before any member is opened.
    """
    if blob[:8] != AR_MAGIC:
        raise SystemExit("not an ar archive, and the caller said it was")
    at, longnames, found = 8, b"", []
    while at + 60 <= len(blob):
        header = blob[at:at + 60]
        raw = header[:16].decode("ascii", "replace").strip()
        size = int(header[48:58].decode("ascii", "replace").strip() or 0)
        body = blob[at + 60:at + 60 + size]
        at += 60 + size + (size & 1)
        if raw == "//":
            longnames = body
            continue
        if raw.startswith("#1/"):                                    # BSD: the name is in the body
            width = int(raw[3:])
            raw, body = body[:width].split(b"\0")[0].decode("ascii", "replace"), body[width:]
        elif raw.startswith("/") and raw[1:].isdigit():              # GNU: an offset into `//`
            start = int(raw[1:])
            raw = longnames[start:longnames.index(b"/\n", start)].decode("ascii", "replace")
        # GNU terminates a member's own name with `/`, which is not part of it. The index is named
        # `/` outright and keeps its name, since that is how :func:`_index` recognises it.
        found.append((raw if raw.startswith(("/", "__.SYMDEF")) else raw.rstrip("/"), body))
    return found


def _index(name: str, body: bytes) -> tuple[str, ...] | None:
    """The symbol names an archive's own index publishes, or ``None`` if this member is not one.

    A linker reads this before it reads any member: a symbol absent here is a symbol the archive
    does not offer, whatever is compiled inside it. It is compared as a sorted list of names because
    the file offsets beside them are rewritten by every strip that changes a member's size, which is
    every strip that did anything.
    """
    if name in ("/", "/SYM64/"):                                     # GNU, 32- and 64-bit offsets
        width = 8 if name == "/SYM64/" else 4
        mark = ">Q" if width == 8 else ">I"
        count = struct.unpack_from(mark, body, 0)[0]
        at, names = width + count * width, []
        for _ in range(count):
            stop = body.index(b"\0", at)
            names.append(body[at:stop].decode("ascii", "replace"))
            at = stop + 1
        return tuple(sorted(names))
    if name.startswith("__.SYMDEF"):                                 # BSD
        wide = name.startswith("__.SYMDEF_64")
        mark, width = (">Q", 8) if wide else ("<I", 4)
        size = struct.unpack_from(mark, body, 0)[0]
        strings = width + size + width
        names = []
        for at in range(width, width + size, width * 2):
            offset = struct.unpack_from(mark, body, at)[0]
            stop = body.index(b"\0", strings + offset)
            names.append(body[strings + offset:stop].decode("ascii", "replace"))
        return tuple(sorted(names))
    return None


def _elf_object(blob: bytes) -> dict[str, object]:
    """A relocatable ELF reduced to what a linker resolves through it.

    Three things, and the first is the only one `SHF_ALLOC` decides: the bytes that will end up in
    somebody else's image. The other two are the ones a byte comparison gets wrong. Removing a
    `.debug_*` section renumbers every section header after it, so a symbol's `st_shndx` is compared
    as the section's *name*; removing a debug symbol renumbers the table, so a relocation's symbol
    is compared as the symbol's *name* and the whole section of them is hashed after that
    substitution. Relocations against a section that is not allocated are skipped with the section
    they apply to, which is where `.rela.debug_info` goes — 9.6 MB of the 41.4 MB this operation is
    removing.
    """
    end = "<" if blob[5] == 1 else ">"
    e_shoff, = struct.unpack_from(end + "Q", blob, 0x28)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from(end + "HHH", blob, 0x3A)
    if not e_shoff or not e_shnum:
        return {"elf": hashlib.sha256(blob).hexdigest()}

    headers = [struct.unpack_from(end + "IIQQQQIIQQ", blob, e_shoff + index * e_shentsize)
               for index in range(e_shnum)]
    names_at = headers[e_shstrndx][4]

    def text(where: int) -> str:
        return blob[where:blob.index(b"\0", where)].decode("ascii", "replace")

    labels = [text(names_at + head[0]) for head in headers]

    # Every symbol table, read once, with each symbol's own name already resolved. A section symbol
    # has no name of its own and is what most relocations against `.text` point at, so it borrows
    # the name of the section it marks — otherwise every such relocation would compare as "symbol
    # 3".
    tables: dict[int, tuple[list[tuple], list[str]]] = {}
    for index, head in enumerate(headers):
        if head[1] != SHT_SYMTAB:
            continue
        strings = headers[head[6]][4]
        entries, resolved = [], []
        for at in range(head[4], head[4] + head[5], 24):
            name, info, _other, shndx = struct.unpack_from(end + "IBBH", blob, at)
            value, size = struct.unpack_from(end + "QQ", blob, at + 8)
            spelling = text(strings + name)
            if not spelling and info & 0xF == STT_SECTION and shndx < e_shnum:
                spelling = labels[shndx]
            entries.append((spelling, info >> 4, info & 0xF, shndx, value, size))
            resolved.append(spelling)
        tables[index] = (entries, resolved)

    sections: dict[str, list] = {}
    relocations: dict[str, list] = {}
    for index, head in enumerate(headers):
        _name, kind, flags, addr, offset, size, link, info, _align, _entsize = head
        if flags & SHF_ALLOC:
            body = b"" if kind == SHT_NOBITS else blob[offset:offset + size]
            sections.setdefault(labels[index], []).append(
                (kind, flags, addr, size, hashlib.sha256(body).hexdigest()))
            continue
        if kind not in (SHT_RELA, SHT_REL) or info >= e_shnum:
            continue
        if not headers[info][2] & SHF_ALLOC:
            continue
        _entries, resolved = tables.get(link, ([], []))
        width = 24 if kind == SHT_RELA else 16
        digest = hashlib.sha256()
        for at in range(offset, offset + size, width):
            where, packed = struct.unpack_from(end + "QQ", blob, at)
            addend = struct.unpack_from(end + "q", blob, at + 16)[0] if kind == SHT_RELA else 0
            symbol = packed >> 32
            spelling = resolved[symbol] if symbol < len(resolved) else f"#{symbol}"
            digest.update(f"{where} {packed & 0xFFFFFFFF} {addend} {spelling}\n".encode())
        relocations.setdefault(labels[info], []).append(digest.hexdigest())

    seen: dict[str, object] = {}
    for label, entries in sections.items():
        seen[f"section {label}"] = tuple(sorted(entries))
    for label, digests in relocations.items():
        seen[f"relocations against {label}"] = tuple(sorted(digests))
    # Only what is global or weak, defined or undefined. A local symbol is invisible to whoever
    # links this, and the ones a relocation needs are already covered above by name — which is what
    # lets a strip remove the `STT_FILE` and debug locals it is being asked to remove.
    published = []
    for entries, _resolved in tables.values():
        for spelling, bind, kind, shndx, value, size in entries:
            if bind == STB_LOCAL:
                continue
            where = labels[shndx] if 0 < shndx < e_shnum else f"#{shndx}"
            published.append((spelling, bind, kind, where, value, size))
    seen["symbols"] = tuple(sorted(published))
    return seen


def _cfi_entries(body: bytes, addr: int) -> list[int]:
    """The start address of every CIE and FDE in an `__eh_frame`, off its own length prefixes."""
    here, at = [], 0
    while at + 4 <= len(body):
        length, = struct.unpack_from("<I", body, at)
        here.append(addr + at)
        if length == 0:                                          # the terminator, and the end
            break
        if length == 0xFFFFFFFF or at + 4 + length > len(body):  # 64-bit form, or a walk gone wrong
            return []
        at += 4 + length
    return here


def _frame_opaque(entry: bytes) -> int:
    """How far into a `__eh_frame` entry the layout reaches, past which its bytes are its own.

    An FDE says where the function is, how far it runs, and — in a block that is there to be skipped
    without understanding it — where its exception table is. Every one of those is an address or a
    span, spelled as a constant before this operation and as a relocation after it, so all of them
    are read once, by name, rather than compared as bytes. The block's length is a LEB128 the format
    puts there precisely so a reader that does not know the CIE can step over it.
    """
    if len(entry) < 8 or not struct.unpack_from("<I", entry, 0)[0]:
        return 4                                                 # a terminator has nothing after it
    if not struct.unpack_from("<I", entry, 4)[0]:
        return 8                                                 # a CIE: only the id above is ours
    at, shift, size = 24, 0, 0
    while at < len(entry):
        byte = entry[at]
        size |= (byte & 0x7F) << shift
        at, shift = at + 1, shift + 7
        if not byte & 0x80:
            return min(len(entry), at + size)
    return min(len(entry), 24)


def _literal_starts(body: bytes, addr: int, kind: int) -> list[int]:
    """The start address of every literal in a literal section, by that section's own rule."""
    width = LITERAL_SECTIONS[kind]
    if width:
        return list(range(addr, addr + len(body), width))
    here, at = [], 0                                             # C strings, NUL-terminated
    while at < len(body):
        here.append(addr + at)
        stop = body.find(b"\0", at)
        at = len(body) if stop < 0 else stop + 1
    return here


def _macho_object(blob: bytes) -> dict[str, object]:
    """A relocatable Mach-O reduced to what a linker resolves through it, and to nothing a strip is
    entitled to rebuild.

    **`strip -S` on macOS does not remove debug information from an object; it reassembles the
    object without it.** That is the thing P5c cost three CI round trips to learn, and it is not a
    detail — measured on the published `ruby-3.2.11-macos-aarch64` archive, the operation reorders
    the sections, reorders the *functions inside* `__text` (`_rb_warn` moves from `0x850` to
    `0xa540`), renames every local label (`l_.str` becomes `LC1`), coalesces duplicate literals,
    turns section-relative relocations into references to the symbol sitting there, and rewrites
    `__eh_frame`'s internal distances as explicit relocations. Stripping the same object outside the
    archive reproduces all of it byte for byte, so it is `strip`'s behaviour and not the archive's.

    Nothing a linker resolves is lost in any of that: across all 496 Mach-O members of that archive,
    all 6,159 defined external symbols survive, every atom is identical byte for byte once its
    relocated fields are read as the names they point at, and no member's set of undefined externals
    changes. So the comparison is made of what survives, and made in a way that cannot see the rest:

    * an atom, not a section, is the unit — bounded by the symbols in it, or for the three kinds of
      section that carry their own structure, by that structure;
    * a reference is what it *names*, never where its target sat: an external symbol by name, one of
      this object's own places by the contents of the atom it lands in, since a local label's name
      is not preserved either;
    * a `SUBTRACTOR` pair encodes a distance from an anchor the two assemblers put in different
      places, so it is read as the thing it names and the distance is dropped;
    * addresses, section order, atom order and the assembler's section flags are absent.

    The line for debug information is where the compiler drew it: DWARF lives in sections whose
    *segment* is `__DWARF`, and debug symbols are the `N_STAB` entries — the same distinction the
    symbol table makes itself, and exactly what `strip -S` exists to remove.

    What this gives up, stated rather than discovered later, and all of it in the unwind tables:
    which CIE an FDE belongs to, how far an FDE or a `__compact_unwind` record says its function
    runs, and where either says the exception table is. Each of those is a distance or an address,
    each is a bare constant before this operation and a relocation after it — and a span there
    covers the function *and* the padding behind it, which an alignment moves. The function every
    one of them is about is compared, by name, and so is everything else in them.

    Everything a linker can reach is still compared, which seven deliberate corruptions of a real
    member confirm on both macOS cells: a flipped instruction byte, a renamed global, an altered
    unwind encoding, a relocation aimed at the next symbol, a changed C string, a changed CFI
    instruction and sixteen zeroed bytes of `__const` are each refused. The one edit that is *not*
    refused is a byte inside a relocated field, which is the point of blanking them: the linker
    overwrites those bytes on its way in, so what they hold here is not what anything runs.
    """
    cpu, ncmds = struct.unpack_from("<I", blob, 4)[0], struct.unpack_from("<I", blob, 16)[0]
    # `ARM64_RELOC_ADDEND` puts a *literal* in the field every other relocation type fills with a
    # symbol or a section number, so it has to be told apart before anything is looked up. This is
    # not a hypothetical: with the two read alike, an addend of 12 becomes "the twelfth section",
    # and on 50 of the 116 members of `libruby.3.4-static.a` the twelfth section is one of the
    # `__DWARF` ones this operation removes — so the check reported the strip it had just performed
    # correctly as having damaged the archive.
    addend = 10 if cpu == CPU_ARM64 else None
    subtractor = 1 if cpu == CPU_ARM64 else 5 if cpu == CPU_X86_64 else None
    offset, symtab, found = 32, None, []
    for _ in range(ncmds):
        command, size = struct.unpack_from("<II", blob, offset)
        if command == 0x19:                                          # LC_SEGMENT_64
            count, = struct.unpack_from("<I", blob, offset + 64)
            for index in range(count):
                at = offset + 72 + index * 80
                name = blob[at:at + 16].split(b"\0")[0].decode("ascii", "replace")
                segment = blob[at + 16:at + 32].split(b"\0")[0].decode("ascii", "replace")
                addr, length = struct.unpack_from("<QQ", blob, at + 32)
                where, = struct.unpack_from("<I", blob, at + 48)
                reloff, nreloc = struct.unpack_from("<II", blob, at + 56)
                flags, = struct.unpack_from("<I", blob, at + 64)
                found.append((segment, name, addr, length, where, reloff, nreloc, flags))
        elif command == 0x02:                                        # LC_SYMTAB
            symtab = struct.unpack_from("<IIIIII", blob, offset)
        offset += size

    names: list[str] = []
    table: list[tuple[str, int, int, int]] = []
    if symtab:
        _, _, symoff, nsyms, stroff, _strsize = symtab
        for index in range(nsyms):
            at = symoff + index * 16
            strx, kind, sect, _desc = struct.unpack_from("<IBBH", blob, at)
            value, = struct.unpack_from("<Q", blob, at + 8)
            stop = blob.index(b"\0", stroff + strx)
            spelling = blob[stroff + strx:stop].decode("ascii", "replace")
            names.append(spelling)
            if not kind & 0xE0:                                      # not N_STAB
                table.append((spelling, kind, sect, value))

    label = {si: f"{s[0]},{s[1]}" for si, s in enumerate(found, start=1)}
    live = {si for si, s in enumerate(found, start=1) if s[0] != "__DWARF"}

    # Where each atom begins. Three kinds of section say so themselves and the rest are told by
    # their symbols — and for those three the symbols are precisely what does not survive: a strip
    # reorders `__compact_unwind`'s records, relabels `__eh_frame`'s entries and merges literals.
    bounds: dict[int, list[int]] = {}
    content: dict[int, bytes] = {}
    for si in live:
        _seg, _nm, addr, length, where, _ro, _nr, flags = found[si - 1]
        content[si] = b"" if flags & 0xFF in ZEROFILL_SECTIONS else blob[where:where + length]
        if label[si] == UNWIND:
            here = list(range(addr, addr + length, UNWIND_RECORD))
        elif label[si] == FRAME:
            here = _cfi_entries(content[si], addr)
        elif flags & 0xFF in LITERAL_SECTIONS:
            here = _literal_starts(content[si], addr, flags & 0xFF)
        else:
            here = sorted({value for _n, _k, sect, value in table if sect == si})
        bounds[si] = here + [addr + length]

    relocations: dict[int, list[tuple]] = {}
    for si in live:
        _seg, _nm, _addr, _len, _off, reloff, nreloc, _flags = found[si - 1]
        rows = []
        for index in range(nreloc):
            address, packed = struct.unpack_from("<II", blob, reloff + index * 8)
            rows.append((address, packed & 0xFFFFFF, (packed >> 27) & 1, (packed >> 28) & 0xF,
                         (packed >> 24) & 1, 1 << ((packed >> 25) & 3)))
        relocations[si] = rows

    def piece(si: int, start: int, stop: int) -> bytearray:
        base = found[si - 1][2]
        return bytearray(content[si][start - base:stop - base]) if content[si] else bytearray()

    def touching(si: int, start: int, stop: int) -> list[tuple]:
        base = found[si - 1][2]
        return [row for row in relocations.get(si, []) if start <= base + row[0] < stop]

    # **What an atom is made of, with every relocated field blanked.** Those fields hold addresses
    # in a layout this operation rebuilds; what they point at is recovered by name below, so nothing
    # is given up by refusing to read them as numbers. An `__eh_frame` entry's second word is
    # blanked with them: it says which CIE the entry belongs to, as a distance before the strip and
    # as a relocation after it.
    plain: dict[tuple[int, int], str] = {}
    for si in bounds:
        base, flags = found[si - 1][2], found[si - 1][7]
        for index, start in enumerate(bounds[si][:-1]):
            stop = bounds[si][index + 1]
            if not content[si]:
                plain[(si, start)] = f"zerofill {stop - start}"
                continue
            here = piece(si, start, stop)
            # Read before anything is blanked: an entry's own length and kind are the first two
            # words, and a relocation lands on the second of them.
            stops = _frame_opaque(bytes(here)) if label[si] == FRAME else 0
            for address, _n, _e, _k, _p, width in touching(si, start, stop):
                at = base + address - start
                here[at:at + width] = bytes(min(width, max(0, len(here) - at)))
            if label[si] == FRAME:
                # Before a strip, on x86_64, those fields are *constants* — a difference between two
                # addresses the assembler already knows needs no relocation — and the re-layout is
                # what makes them need one. Both spellings are blanked here, and what they name is
                # recovered below.
                here[4:stops] = bytes(max(0, min(stops, len(here)) - 4))
            # An atom runs to wherever the next one starts, so it carries whatever the assembler put
            # between them: the last function of `compar.o` is 81 bytes before this operation and 96
            # after, the fifteen being `nop`. Padding is not content, and trimming it from both
            # sides is what lets the content compare.
            if flags & 0x80000400:                               # some or all of it is instructions
                here = here.rstrip(b"\x90\x00")
            plain[(si, start)] = hashlib.sha256(bytes(here)).hexdigest()

    def atom_at(si: int, address: int) -> int | None:
        pick = None
        for start in bounds.get(si, []):
            if start > address:
                break
            pick = start
        return pick

    exported = {name for name, kind, _sect, _v in table if kind & 0x01}
    seated = {name: (sect, atom_at(sect, value), value)
              for name, _k, sect, value in table if sect in bounds}
    standing = {(sect, value): name for name, kind, sect, value in table if kind & 0x01}

    def by_name(name: str) -> str:
        """A symbol a linker can see is its own identity; one it cannot see has none.

        A local falls through to the place it marks, and to the *same* spelling
        :func:`by_place` gives that place — a strip turns section-relative references into
        references to the symbol standing there, so one object says by name what the other says by
        address and neither is a change.
        """
        if name in exported:
            return name
        seat = seated.get(name)
        if seat is None or seat[1] is None:
            return f"local {name}"
        return by_place(seat[0], seat[2])

    def by_place(si: int, address: int) -> str:
        """A place in this object, said in a way a re-layout cannot change. A strip rewrites a
        section-relative reference as a reference to the symbol sitting there, so both spellings
        have to come out the same or every one of them reads as a change."""
        if (si, address) in standing:
            return standing[(si, address)]
        start = atom_at(si, address)
        if start is None:
            return f"{label[si]}+{address - found[si - 1][2]}"
        if (si, start) in standing:
            return f"{standing[(si, start)]}+{address - start}"
        return f"{label[si]} {plain.get((si, start), '?')}+{address - start}"

    def by_address(address: int) -> str:
        for si in bounds:
            base, length = found[si - 1][2], found[si - 1][3]
            if base <= address < base + length:
                return by_place(si, address)
        return f"nowhere in this object +{address}"

    def _described(si: int, start: int, here: bytearray, rows: list[tuple]) -> str:
        """The function an `__eh_frame` entry is about, however this object happens to say it.

        A relocation names it after a strip. Before one, on x86_64, it is a bare number: the
        assembler knew both addresses and a difference of two knowns needs no relocation. The two
        are the same fact and have to read as the same fact.
        """
        for address, number, extern, kind, _pcrel, _width in rows:
            if found[si - 1][2] + address - start != 8 or kind == subtractor:
                continue
            if extern and number < len(names):
                seat = seated.get(names[number])
                return by_place(seat[0], seat[2]) if seat else names[number]
            if not extern and number in bounds:
                return label[number]
        if len(here) >= 16:
            reach = int.from_bytes(here[8:16], "little", signed=True)
            return by_address((start + 8 + reach) % (1 << 64))
        return "unsaid"

    seen: dict[str, object] = {}
    atoms: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    literals: dict[str, set] = collections.defaultdict(set)
    unwind: collections.Counter = collections.Counter()
    frame: collections.Counter = collections.Counter()
    published: dict[str, tuple] = {}
    owner: dict[tuple[int, int], list[tuple[str, int]]] = collections.defaultdict(list)
    for name, kind, sect, value in table:
        if sect in bounds:
            owner[(sect, value)].append((name, kind))

    for si in bounds:
        base, flags = found[si - 1][2], found[si - 1][7]
        for index, start in enumerate(bounds[si][:-1]):
            stop = bounds[si][index + 1]
            here = piece(si, start, stop)
            rows = touching(si, start, stop)
            anchored = {base + row[0] for row in rows if row[3] == subtractor}
            refs = []
            for address, number, extern, kind, pcrel, width in rows:
                at = base + address
                if label[si] == FRAME and 4 <= at - start < _frame_opaque(bytes(here)):
                    # Everything the layout reaches into, blanked above and said once, below, in the
                    # one spelling both layouts share.
                    continue
                if at in anchored and extern and number < len(names):
                    # One half of a difference. The end it is measured *from* is an anchor the two
                    # assemblers put in different places, and that it is the anchor is all it says.
                    seat = seated.get(names[number])
                    refs.append((at - start, kind, width, pcrel,
                                 "here" if seat and seat[0] == si and names[number] not in exported
                                 else by_name(names[number])))
                    continue
                # What the field carries, and the two architectures do not agree. arm64 keeps a
                # relocation's addend in a *separate* `ARM64_RELOC_ADDEND` and fills a pc-relative
                # field with instruction bits, so only the eight-byte absolute ones can be read as a
                # number at all. x86_64 has no such pseudo-relocation and puts the addend in the
                # field — signed, and layout-free wherever the relocation names a symbol.
                # `None` is not a field reading zero: address zero is a section's first atom.
                carried = None
                if here and at - start + width <= len(here):
                    if cpu == CPU_ARM64:
                        if width == 8 and not pcrel:
                            carried = struct.unpack_from("<Q", bytes(here), at - start)[0]
                    else:
                        carried = int.from_bytes(here[at - start:at - start + width],
                                                 "little", signed=True)
                if not extern and kind == addend:
                    target = f"addend {number}"                   # a literal, not a place
                elif extern and number < len(names):
                    # `SIGNED_1/2/4` say how many bytes of the instruction follow the field, and a
                    # field holding an addend is short by exactly that. Read without the bias, a
                    # reference to a literal lands one byte inside the literal before it.
                    reach = (carried or 0) + ({6: 1, 7: 2, 8: 4}.get(kind, 0) if pcrel else 0)
                    seat = seated.get(names[number])
                    target = by_place(seat[0], (seat[2] + reach) % (1 << 64)) if seat \
                        else names[number] + (f"+{reach}" if reach else "")
                elif not extern and number in bounds and carried is not None:
                    # Where the field is pc-relative it holds the distance from the end of the
                    # instruction, and `SIGNED_1/2/4` say how many bytes of that instruction follow
                    # the field itself — so the place it names is recovered rather than the distance
                    # compared.
                    ahead = at + width + ({6: 1, 7: 2, 8: 4}.get(kind, 0) if pcrel else 0)
                    target = by_place(number, (carried + (ahead if pcrel else 0)) % (1 << 64))
                elif not extern and 0 < number <= len(found):
                    target = label[number]
                else:
                    target = f"symbol #{number}" if extern else f"section #{number}"
                refs.append((at - start, kind, width, pcrel, target))

            # A CIE says how to unwind and an FDE says what to unwind, and only the second names a
            # function. Told apart the way the format tells them apart, by whether the second word
            # is zero — and the answer is kept, so one turning into the other is a difference.
            role = ""
            if label[si] == FRAME and len(here) >= 8:
                span, second = struct.unpack_from("<II", bytes(here), 0)
                role = "terminator" if not span else "CIE" if not second else "FDE"
                if role == "FDE" and len(here) >= 16:
                    refs.append((8, 0, 8, 0, _described(si, start, here, rows)))

            made = (plain[(si, start)], tuple(sorted(refs)))
            if label[si] == UNWIND:
                # The function this record describes, and how it is unwound. Not how far it runs:
                # that field covers the function *and the padding after it*, so it moves with an
                # alignment rather than with anything the record says. Not its personality or LSDA
                # either — those a strip copies in from the FDE that carried them, and that FDE is
                # compared below.
                encoding, = struct.unpack_from("<I", bytes(here), 12) if len(here) >= 16 else (0,)
                unwind[(tuple(sorted(row for row in refs if not row[0])), encoding)] += 1
            elif label[si] == FRAME:
                frame[(role,) + made] += 1
            elif flags & 0xFF in LITERAL_SECTIONS:
                literals[label[si]].add(made)                     # a set: merging them is licensed
            elif any(kind & 0x01 for _n, kind in owner.get((si, start), [])):
                for name, kind in owner[(si, start)]:
                    if kind & 0x01:
                        published[name] = (kind & 0x0E, label[si]) + made
            else:
                atoms[label[si]][made] += 1

    seen["symbols"] = tuple(sorted((name,) + rest for name, rest in published.items()))
    seen["undefined"] = tuple(sorted(name for name, kind, sect, _v in table
                                     if not sect and kind & 0x01))
    for name, counted in atoms.items():
        seen[f"atoms in {name}"] = tuple(sorted(counted.items()))
    for name, kept in literals.items():
        seen[f"literals in {name}"] = tuple(sorted(kept))
    if unwind:
        seen["unwind records"] = tuple(sorted(unwind.items()))
    if frame:
        seen["frame entries"] = tuple(sorted(frame.items()))
    return seen


def resolvable(path: Path) -> dict[str, object]:
    """Reduce a static library to everything a linker can resolve through it, and to nothing else.

    This is the check P5b needed and P4b never wrote, because `lib/libruby-static.a` is the first
    file this repository strips that is not an image. :func:`mapped` cannot be pointed at it and
    would not be answering the right question if it could: an archive member is never mapped, has no
    program headers, and on macOS carries its DWARF in ordinary sections that a successful strip
    removes — which :func:`mapped` would report as the file having been damaged.

    A member that is neither a 64-bit ELF nor a thin 64-bit Mach-O is hashed whole. That is the
    stricter answer, not the lazier one: this does not know what such a member is for, and a strip
    that changed one has done something nobody here can vouch for.
    """
    blob = path.read_bytes()
    seen: dict[str, object] = {}
    catalogue: list[str] = []
    index: tuple[str, ...] | None = None
    for order, (name, body) in enumerate(members(blob)):
        published = _index(name, body)
        if published is not None:
            index = published
            continue
        catalogue.append(name)
        # Keyed by position as well as by name, because an archive may hold two members of one name
        # — Ruby's does not, but `ar` allows it and a collision here would silently compare one
        # member against the other. Members are not reordered by a strip; if they ever were, this
        # reports it rather than hiding it.
        if body[:4] == ELF_MAGIC and len(body) > 64 and body[4] == 2:
            seen[f"member {order} {name}"] = _elf_object(body)
        elif body[:4] == MACHO_MAGIC:
            seen[f"member {order} {name}"] = _macho_object(body)
        else:
            seen[f"member {order} {name}"] = hashlib.sha256(body).hexdigest()
    if index is None:
        raise SystemExit(
            f"{path.name} carries no symbol index, so a linker asked for a symbol in it would have "
            f"to open every member to find out — which `ld` does not do. Run `ranlib` on it."
        )
    seen["members"], seen["index"] = tuple(catalogue), index
    return seen


# ------------------------------------------------------------------------------ the operation ---


def whole(path: Path) -> str:
    """The digest of the file exactly as it sits on disk.

    Not a proof of anything — :func:`mapped` and :func:`resolvable` are that — but the only way to
    tell a strip that removed something from one that found nothing to remove. The difference
    matters to what gets written down rather than to what gets published: `upstream.changed` says
    *this file is not the bytes upstream published*, and a file `strip` left alone is exactly those
    bytes. `mariadb_deb.py` is why it exists and expects to find nothing, because Debian strips its
    own binaries before it packages them.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def symbols(tree: Path, paths: Sequence[Path], flags: Sequence[str],
            operating_system: str) -> dict[str, str]:
    """Run the platform's `strip` over *paths*, proving each one across the operation.

    *paths* is what a recipe decided to strip and *flags* is what it decided to ask for; both belong
    to the recipe, because only it knows whether the file it built is loaded or linked. What is
    shared is everything after: the same proof, the same refusal, and the same sentence written into
    the manifest, so that two artifacts stripped by two recipes carry one claim.

    The tool is the platform's own rather than a bundled one, and it is required rather than
    optional. `mariadb.strip_debug` returns quietly when there is no `strip` on PATH, because there
    the saving is the whole point and an unstripped bintar is merely large. Here the tree ships
    either way and the difference is what the artifact claims about itself, so a missing tool has to
    stop the pack rather than silently publish the other archive.

    What comes back is `upstream.changed` — every path whose bytes this actually altered, mapped to
    the command that altered them. Every path, and not one more: a file `strip` found nothing to
    remove is not in it, which is the difference between a manifest a reader can check against
    upstream's archive and one that sends them looking for a change nobody made.
    """
    if not paths:
        return {}
    tool = shutil.which("strip")
    if tool is None:
        raise SystemExit(
            "no `strip` on PATH, and this recipe modifies the binaries it ships — packing without "
            "it would publish a tree that does not match the one every other run of this produces"
        )

    changed: dict[str, str] = {}
    for path in paths:
        relative = path.relative_to(tree).as_posix()
        with path.open("rb") as handle:
            archive = handle.read(8) == AR_MAGIC

        # Asked before the file is opened for writing, because the proof below is made by comparing
        # against a copy this does not keep. See :func:`unmapped`: where the section table and the
        # segment table disagree, `strip` resolves the disagreement rather than preserving it, and
        # the file it hands back is the one it broke. Skipped rather than refused — the archive
        # ships either way and upstream's binary is the one that works, which is the same trade
        # `IMAGES` makes by having no `windows` row — and named rather than passed over, because a
        # file quietly left out of `upstream.changed` is a saving nobody can account for.
        astray = [] if archive else unmapped(path)
        if astray:
            print(f"leaving {relative} unstripped: {', '.join(astray)} is allocated but sits "
                  f"outside every PT_LOAD, so `strip` would rebuild the program headers without "
                  f"it and publish a binary that cannot resolve a symbol")
            continue

        proof = resolvable if archive else mapped
        noun = "debug information" if archive else "symbol table"
        seeing = "a linker can resolve" if archive else "a loader or a linker can see"

        before, was, original = proof(path), path.stat().st_size, whole(path)
        done = subprocess.run([tool, *flags, str(path)], capture_output=True, text=True)
        if done.returncode != 0:
            raise SystemExit(f"strip {' '.join(flags)} {relative} failed: {done.stderr.strip()}")

        after = proof(path)
        differences = [key for key in sorted(set(before) | set(after))
                       if before.get(key) != after.get(key)]

        # One key is compared for loss rather than for equality, and only one. An archive's index is
        # a *cache* of which member defines which symbol, rebuilt from the members by whatever tool
        # last wrote the archive — and two tools disagree about one kind of entry. A common symbol,
        # which is what a C tentative definition such as `VALUE rb_cArray;` compiles to, is in every
        # member of `libruby.3.4-static.a` and in none of the `__.SYMDEF SORTED` Apple's archiver
        # wrote; llvm's archiver puts all 165 of them back, with the members byte-identical either
        # way. Nothing that resolved stopped resolving, which is the whole of what is being asked
        # here, so the direction is enforced and the growth is reported rather than hidden.
        grew = 0
        if archive and "index" in differences:
            lost = sorted(set(before["index"]) - set(after["index"]))
            if lost:
                raise SystemExit(
                    f"strip {' '.join(flags)} {relative} left {len(lost)} symbol(s) out of the "
                    f"archive's index — {', '.join(lost[:4])} — so a program that could link "
                    f"against this file cannot any more, whatever is still compiled inside it"
                )
            grew = len(after["index"]) - len(before["index"])
            differences.remove("index")

        # The postcondition to the precondition above, and what took `p_offset`'s place. `mapped`
        # compares every allocated section by address, size and contents, and every segment by
        # everything except where it reads from; this is the sentence that joins the two tables back
        # together — a segment table that no longer covers a section is one that stopped describing
        # the file, whatever its own fields say.
        astray = [] if archive else unmapped(path)
        if astray:
            raise SystemExit(
                f"strip {' '.join(flags)} {relative} left {', '.join(astray)} allocated and "
                f"outside every PT_LOAD, so the segment table no longer covers what the section "
                f"table says the file holds, and the artifact is not being published"
            )

        if differences:
            # **Worst first, not alphabetically first.** Only four of them are printed, and the
            # four that sort earliest are not the four worth reading: `libruby.3.2-static.a` came
            # back with 469 differing members of which 50 differed in `symbols`, and the four shown
            # were `dln.o`, `complex.o` and two of libgcc's, none of which did — so the message
            # named the layout and hid the identity. A difference in what an object *publishes*
            # outranks a difference in where it happens to sit.
            def gravity(key: str) -> int:
                was, now = before.get(key), after.get(key)
                if not (isinstance(was, dict) and isinstance(now, dict)):
                    return 1
                inner = {name for name in set(was) | set(now) if was.get(name) != now.get(name)}
                return 0 if inner & {"symbols", "undefined", "index", "members"} else 1

            spelled = ", ".join(moved(key, before.get(key), after.get(key))
                                for key in sorted(differences, key=gravity)[:4])
            raise SystemExit(
                f"strip {' '.join(flags)} {relative} changed {len(differences)} thing(s) "
                f"{seeing} — {spelled}{tally(before, after, differences)} — so this is not the "
                f"{noun} coming out, and the artifact is not being published"
            )
        wrong = countersigned(path) if operating_system == "macos" and not archive else None
        if wrong:
            # Put one back and ask again, rather than refuse. See `resign`: the comparison above has
            # already proved that everything a loader maps is identical, so a signature that no
            # longer matches is a hash of the right bytes going stale — and the second call is what
            # keeps this from being a way of not answering the question.
            wrong = resign(path) or countersigned(path)
        if wrong:
            raise SystemExit(wrong)

        # A file `strip` found nothing to remove is upstream's file, and claiming it in
        # `upstream.changed` would send a reader holding both archives looking for a difference that
        # is not there. Read from the bytes rather than from the size, because the field's whole
        # subject is bytes.
        if whole(path) == original:
            continue

        now = path.stat().st_size
        # The command rather than a sentence, because this field's reader is holding upstream's
        # archive and ours and wants to know what was done to the file, not why it was a good idea.
        changed[relative] = f"strip {' '.join(flags)}"
        note = f", {grew} more symbol(s) in the index" if grew else ""
        print(f"stripped {relative} ({was:,} -> {now:,}, {was - now:,} of {noun}{note})")
    return changed
