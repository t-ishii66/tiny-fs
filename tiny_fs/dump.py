"""Show what is on the image, in a form a human can read. Reads only.

Works straight off the BlockDevice, without FileSystem: a broken image has to
be viewable too, so nothing here validates the superblock.
"""

from __future__ import annotations

import stat
from typing import TextIO

from .bitmap import Bitmap
from .constants import (
    BLOCK_BITMAP_BLOCK,
    BLOCK_SIZE,
    DIRENT_SIZE,
    INODE_BITMAP_BLOCK,
    INODE_SIZE,
    INODES_PER_BLOCK,
    SUPERBLOCK_BLOCK,
)
from .directory import DOT, DOTDOT, Dirent
from .disk import BlockDevice
from .fs import Superblock, superblock_problems
from .inode import Inode


def dump(blocks: BlockDevice, out: TextIO) -> None:
    sb = Superblock.decode(blocks.read_block(SUPERBLOCK_BLOCK))
    sections = [_superblock(sb)]
    # A wrong magic is still shown and read on (that is the point of dump), but
    # if the geometry itself is nonsense there is nothing left to follow.
    broken = superblock_problems(sb, blocks.num_blocks, check_magic=False)
    if broken:
        sections.append(["cannot read the rest of the layout:"]
                        + [f"  {problem}" for problem in broken])
    else:
        sections += [
            _bitmap(blocks, BLOCK_BITMAP_BLOCK, sb.total_blocks, "block bitmap",
                    None),
            _bitmap(blocks, INODE_BITMAP_BLOCK, sb.num_inodes + 1,
                    "inode bitmap", "(bit 0 is reserved)"),
            _inodes(blocks, sb),
            *_directories(blocks, sb),
        ]
    # One blank line between sections, none at the end.
    print("\n\n".join("\n".join(lines) for lines in sections), file=out)


def dump_raw(blocks: BlockDevice, block_no: int, length: int,
             out: TextIO) -> None:
    data = blocks.read_block(block_no)[:length]
    print(f"block {block_no}", file=out)
    hexdump(data, out)


def hexdump(data: bytes, out: TextIO, base: int = 0) -> None:
    for offset in range(0, len(data), 16):
        chunk = data[offset:offset + 16]
        hexpart = " ".join(f"{byte:02x}" for byte in chunk)
        text = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in chunk)
        print(f"{base + offset:08x}  {hexpart:<47}  |{text}|", file=out)


def _superblock(sb: Superblock) -> list[str]:
    # Big-endian on purpose: this prints the name of the format ("tfs1"), not
    # the byte order it is stored in (a raw dump shows |1sft|).
    magic_text = "".join(chr(b) if 0x20 <= b < 0x7F else "."
                         for b in sb.magic.to_bytes(4, "big"))
    return [
        f"superblock (block {SUPERBLOCK_BLOCK})",
        _field("magic", f'0x{sb.magic:08x} "{magic_text}"'),
        _field("block_size", sb.block_size),
        _field("total_blocks", sb.total_blocks),
        _field("num_inodes", sb.num_inodes),
        _field("inode_table_start", sb.inode_table_start),
        _field("data_start", sb.data_start),
        _field("root_ino", sb.root_ino),
        _field("free_hint", sb.free_hint),
    ]


def _field(name: str, value: object) -> str:
    return f"  {name:<19}{value}"


def _bitmap(blocks: BlockDevice, block_no: int, capacity: int, title: str,
            note: str | None) -> list[str]:
    bitmap = Bitmap(bytearray(blocks.read_block(block_no)), capacity)
    used = bitmap.used_indexes()
    counts = f"  used {len(used):>4} / {capacity:<6}  free {capacity - len(used)}"
    if note:
        counts = f"{counts:<30}     {note}"
    return [
        f"{title} (block {block_no})",
        counts.rstrip(),
        f"  ranges  {_ranges(used)}",
    ]


def _inodes(blocks: BlockDevice, sb: Superblock) -> list[str]:
    table_blocks = sb.num_inodes // INODES_PER_BLOCK
    last = sb.inode_table_start + table_blocks - 1
    lines = [f"inode table (block {sb.inode_table_start}-{last})",
             "  ino  mode         nlink  size  blocks"]
    bitmap = Bitmap(bytearray(blocks.read_block(INODE_BITMAP_BLOCK)),
                    sb.num_inodes + 1)
    for ino in range(1, sb.num_inodes + 1):
        if not bitmap.is_used(ino):
            continue
        inode = read_inode(blocks, sb, ino)
        used_blocks = [b for b in inode.direct if b != 0]
        lines.append(f"  {ino:>3}  {mode_str(inode.mode):<11}  {inode.nlink:>5}  "
                     f"{inode.size:>4}  {used_blocks}")
    return lines


def _directories(blocks: BlockDevice, sb: Superblock) -> list[list[str]]:
    sections = []
    for path, ino in _walk_tree(blocks, sb):
        inode = read_inode(blocks, sb, ino)
        lines = [f"directory {path}  (ino {ino})", "  slot  ino  name"]
        raw = _read_file(blocks, inode)
        for slot in range(len(raw) // DIRENT_SIZE):
            entry = Dirent.decode(raw[slot * DIRENT_SIZE:(slot + 1) * DIRENT_SIZE])
            if entry.ino == 0:
                continue
            name = entry.name.decode("utf-8", "replace")
            lines.append(f'  {slot:>4}  {entry.ino:>3}  "{name}"')
        sections.append(lines)
    return sections


def _walk_tree(blocks: BlockDevice, sb: Superblock) -> list[tuple[str, int]]:
    """Every directory reachable from the root, as (path, ino)."""
    found: list[tuple[str, int]] = []
    seen: set[int] = set()
    queue = [("/", sb.root_ino)]
    while queue:
        path, ino = queue.pop(0)
        if ino in seen:
            continue                      # a broken ".." must not loop forever
        seen.add(ino)
        inode = read_inode(blocks, sb, ino)
        if not stat.S_ISDIR(inode.mode):
            continue
        found.append((path, ino))
        raw = _read_file(blocks, inode)
        for slot in range(len(raw) // DIRENT_SIZE):
            entry = Dirent.decode(raw[slot * DIRENT_SIZE:(slot + 1) * DIRENT_SIZE])
            if entry.ino == 0 or entry.name in (DOT, DOTDOT):
                continue
            if not 1 <= entry.ino <= sb.num_inodes:
                continue                  # a broken dirent, not a directory
            child = read_inode(blocks, sb, entry.ino)
            if stat.S_ISDIR(child.mode):
                prefix = "" if path == "/" else path
                queue.append((f"{prefix}/{entry.name.decode('utf-8', 'replace')}",
                              entry.ino))
    return found


def read_inode(blocks: BlockDevice, sb: Superblock, ino: int) -> Inode:
    """Same arithmetic as FileSystem._inode_pos, without FileSystem."""
    index = ino - 1
    block_no = sb.inode_table_start + index // INODES_PER_BLOCK
    offset = (index % INODES_PER_BLOCK) * INODE_SIZE
    raw = blocks.read_block(block_no)
    return Inode.decode(raw[offset:offset + INODE_SIZE])


def _read_file(blocks: BlockDevice, inode: Inode) -> bytes:
    out = bytearray()
    remaining = inode.size
    for block_no in inode.direct:
        if remaining <= 0 or block_no == 0:
            break
        take = min(remaining, BLOCK_SIZE)
        out += blocks.read_block(block_no)[:take]
        remaining -= take
    return bytes(out)


def _ranges(indexes: list[int]) -> str:
    """Turn [0,1,2,3,7,8] into "0-3, 7-8"."""
    parts: list[str] = []
    start = prev = None
    for i in indexes:
        if start is None:
            start = prev = i
        elif i == prev + 1:
            prev = i
        else:
            parts.append(f"{start}-{prev}" if start != prev else f"{start}")
            start = prev = i
    if start is not None:
        parts.append(f"{start}-{prev}" if start != prev else f"{start}")
    return ", ".join(parts)


def mode_str(mode: int) -> str:
    """Type plus permissions, the way ls -l shows them."""
    kind = "d" if stat.S_ISDIR(mode) else "-"
    bits = ""
    for who in ("USR", "GRP", "OTH"):
        for what, ch in (("R", "r"), ("W", "w"), ("X", "x")):
            bits += ch if mode & getattr(stat, f"S_I{what}{who}") else "-"
    return f"{kind} {bits}"
