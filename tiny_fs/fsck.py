"""Check that the image adds up. Detects, never repairs.

Every check has the same shape: the same fact is recorded twice on disk, so
read one copy, rebuild the other, and compare.
"""

from __future__ import annotations

import stat
from collections import Counter

from .bitmap import Bitmap
from .constants import (
    BLOCK_BITMAP_BLOCK,
    DIRENT_SIZE,
    INODE_BITMAP_BLOCK,
    SUPERBLOCK_BLOCK,
)
from .directory import DOT, DOTDOT, Dirent
from .disk import BlockDevice
from .dump import read_inode
from .fs import Superblock, superblock_problems

METADATA = 0     # "referenced by the layout itself", not by any i-node


def fsck(blocks: BlockDevice) -> list[str]:
    """Return the list of problems. An empty list means the image is healthy."""
    sb = Superblock.decode(blocks.read_block(SUPERBLOCK_BLOCK))
    problems = superblock_problems(sb, blocks.num_blocks)
    if superblock_problems(sb, blocks.num_blocks, check_magic=False):
        return problems          # the layout cannot be followed at all

    block_refs, seen_inodes, nlink_count, dir_problems = _collect(blocks, sb)
    block_bitmap = Bitmap(bytearray(blocks.read_block(BLOCK_BITMAP_BLOCK)),
                          sb.total_blocks)
    inode_bitmap = Bitmap(bytearray(blocks.read_block(INODE_BITMAP_BLOCK)),
                          sb.num_inodes + 1)

    # 1-3: the block bitmap against the blocks the i-nodes actually point at
    for block_no in range(sb.total_blocks):
        refs = block_refs.get(block_no, [])
        used = block_bitmap.is_used(block_no)
        if len(refs) > 1:
            problems.append(f"block {block_no} is referenced by "
                            f"{_refs_str(refs)} (double use)")
        elif refs and not used:
            problems.append(f"block {block_no} is referenced by inode {refs[0]} "
                            f"but marked free in the block bitmap")
        elif not refs and used:
            problems.append(f"block {block_no} is marked used but referenced "
                            f"by nothing (leak)")

    # 4: the i-node bitmap against what the tree reaches
    for ino in range(1, sb.num_inodes + 1):
        used = inode_bitmap.is_used(ino)
        if used and ino not in seen_inodes:
            problems.append(f"inode {ino} is marked used but cannot be reached "
                            f"from the root (orphan)")
        elif not used and ino in seen_inodes:
            problems.append(f"inode {ino} is reachable but marked free in the "
                            f"inode bitmap")

    # 5: nlink against the number of names that point at the i-node
    for ino in sorted(seen_inodes):
        inode = read_inode(blocks, sb, ino)
        counted = nlink_count[ino]
        if inode.nlink != counted:
            problems.append(f"inode {ino} has nlink={inode.nlink} but "
                            f"{counted} {_names(counted)} to it")

    # 6: block numbers have to be inside the data area
    for ino in sorted(seen_inodes):
        inode = read_inode(blocks, sb, ino)
        for block_no in inode.direct:
            if block_no == 0:
                continue
            if not sb.data_start <= block_no < sb.total_blocks:
                problems.append(f"inode {ino} points at block {block_no}, "
                                f"outside the data area "
                                f"({sb.data_start}..{sb.total_blocks - 1})")

    # 7: the first two entries of a directory
    problems.extend(dir_problems)

    # the spare bits of either bitmap must stay zero (chapter 5)
    problems.extend(_check_spare_bits(blocks, BLOCK_BITMAP_BLOCK,
                                      sb.total_blocks, "block bitmap"))
    problems.extend(_check_spare_bits(blocks, INODE_BITMAP_BLOCK,
                                      sb.num_inodes + 1, "inode bitmap"))
    return problems


def summary(blocks: BlockDevice) -> tuple[int, int]:
    """(blocks in use, i-nodes in use). The reserved i-node bit is not counted."""
    sb = Superblock.decode(blocks.read_block(SUPERBLOCK_BLOCK))
    if superblock_problems(sb, blocks.num_blocks, check_magic=False):
        return 0, 0
    block_bitmap = Bitmap(bytearray(blocks.read_block(BLOCK_BITMAP_BLOCK)),
                          sb.total_blocks)
    inode_bitmap = Bitmap(bytearray(blocks.read_block(INODE_BITMAP_BLOCK)),
                          sb.num_inodes + 1)
    return block_bitmap.count_used(), inode_bitmap.count_used() - 1


def _collect(blocks: BlockDevice, sb: Superblock):
    """Walk the tree from the root and build the expected values."""
    block_refs: dict[int, list[int]] = {}   # block number -> i-nodes using it
    seen_inodes: set[int] = set()
    nlink_count: Counter[int] = Counter()
    problems: list[str] = []

    # The metadata blocks belong to no i-node, but they are in use.
    for block_no in range(sb.data_start):
        block_refs.setdefault(block_no, []).append(METADATA)

    stack = [sb.root_ino]
    while stack:
        ino = stack.pop()
        if ino in seen_inodes:
            continue                        # keeps a loop from spinning forever
        if not 1 <= ino <= sb.num_inodes:
            problems.append(f"a dirent points at inode {ino}, out of range")
            continue
        seen_inodes.add(ino)

        inode = read_inode(blocks, sb, ino)
        for block_no in inode.direct:
            if block_no != 0:
                block_refs.setdefault(block_no, []).append(ino)

        if not stat.S_ISDIR(inode.mode):
            continue

        entries = _entries(blocks, sb, inode)
        names = [e.name for _slot, e in entries]
        if names[:2] != [DOT, DOTDOT]:
            problems.append(f'directory inode {ino} does not start with '
                            f'"." and ".."')
        for _slot, entry in entries:
            nlink_count[entry.ino] += 1     # "." and ".." count as names too
            if entry.name not in (DOT, DOTDOT):
                stack.append(entry.ino)
    return block_refs, seen_inodes, nlink_count, problems


def _entries(blocks: BlockDevice, sb: Superblock, inode) -> list[tuple[int, Dirent]]:
    raw = bytearray()
    remaining = inode.size
    for block_no in inode.direct:
        if remaining <= 0 or not sb.data_start <= block_no < sb.total_blocks:
            break
        take = min(remaining, len(blocks.read_block(block_no)))
        raw += blocks.read_block(block_no)[:take]
        remaining -= take
    out = []
    for slot in range(len(raw) // DIRENT_SIZE):
        entry = Dirent.decode(bytes(raw[slot * DIRENT_SIZE:(slot + 1) * DIRENT_SIZE]))
        if entry.ino != 0:
            out.append((slot, entry))
    return out


def _check_spare_bits(blocks: BlockDevice, block_no: int, capacity: int,
                      title: str) -> list[str]:
    buf = blocks.read_block(block_no)
    full_bytes, extra_bits = divmod(capacity, 8)
    if extra_bits and buf[full_bytes] >> extra_bits:
        return [f"{title} has bits set past index {capacity - 1}"]
    first_spare = full_bytes + (1 if extra_bits else 0)
    if any(buf[first_spare:]):
        return [f"{title} has bits set past index {capacity - 1}"]
    return []


def _refs_str(refs: list[int]) -> str:
    return ", ".join("the layout" if r == METADATA else f"inode {r}" for r in refs)


def _names(count: int) -> str:
    return "name points" if count == 1 else "names point"
