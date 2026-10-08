"""L3: the i-node. One fixed-size record per file.

This layer does not know where i-nodes live on the disk, and it does not know
what a bitmap is. Whoever calls it passes in a block device and an allocator.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Protocol

from .constants import BLOCK_SIZE, INODE_FMT, INODE_SIZE, MAX_FILE_SIZE, NDIRECT
from .disk import BlockDevice
from .errors import CorruptionError, FileTooBigError, InvalidArgumentError


class Allocator(Protocol):
    """Whatever hands out numbers. Bitmap has this shape, and so does the
    write-through wrapper in fs.py."""

    def allocate(self, hint: int = 0) -> int: ...

    def mark_free(self, index: int) -> None: ...


@dataclass
class Inode:
    """One i-node. Occupies 128 bytes on disk."""

    mode: int           # u16       file type + permissions
    nlink: int          # u16       how many names point at this i-node
    uid: int            # u32       owner
    gid: int            # u32
    size: int           # u64       file length in bytes
    mtime: int          # u64       contents last changed
    ctime: int          # u64       metadata last changed
    direct: list[int]   # u32 x 12  blocks holding the data. 0 = unallocated

    @classmethod
    def empty(cls) -> "Inode":
        """An all-zero i-node. What an unused table slot looks like."""
        return cls(mode=0, nlink=0, uid=0, gid=0, size=0, mtime=0, ctime=0,
                   direct=[0] * NDIRECT)

    def encode(self) -> bytes:
        packed = struct.pack(INODE_FMT, self.mode, self.nlink, self.uid, self.gid,
                             self.size, self.mtime, self.ctime, *self.direct)
        # Stretch 84 bytes to 128. The spare room stays zero, always.
        return packed.ljust(INODE_SIZE, b"\x00")

    @classmethod
    def decode(cls, raw: bytes) -> "Inode":
        if len(raw) != INODE_SIZE:
            raise ValueError(f"an i-node is {INODE_SIZE} bytes, got {len(raw)}")
        values = struct.unpack(INODE_FMT, raw[:struct.calcsize(INODE_FMT)])
        return cls(*values[:7], direct=list(values[7:]))


def allocate_inode(inode_bitmap: Allocator, mode: int, now: int) -> tuple[int, Inode]:
    """Take one free i-node number and return a brand new Inode."""
    # bit 0 is filled in by mkfs, so ino = 0 can never come back
    ino = inode_bitmap.allocate()
    inode = Inode(mode=mode, nlink=0, uid=0, gid=0, size=0,
                  mtime=now, ctime=now, direct=[0] * NDIRECT)
    # nlink is still 0. It becomes 1 the moment a name is attached (chapter 7).
    return ino, inode


def free_inode(inode: Inode, blocks: BlockDevice, alloc: Allocator,
               inode_bitmap: Allocator, ino: int) -> None:
    """Give back every data block the i-node holds, then the number itself."""
    # Data blocks first. The other order leaves blocks marked used with nobody
    # referring to them if something fails in between.
    truncate(inode, blocks, alloc, 0)
    inode_bitmap.mark_free(ino)


def read(inode: Inode, blocks: BlockDevice, offset: int, length: int) -> bytes:
    if offset < 0 or length < 0:
        raise InvalidArgumentError("negative offset or length")
    # Past size is "outside the file". Do not read it even if a block is there.
    length = max(0, min(length, inode.size - offset))
    out = bytearray()
    while length > 0:
        index, inner = divmod(offset, BLOCK_SIZE)
        n = min(length, BLOCK_SIZE - inner)
        block_no = inode.direct[index]
        if block_no == 0:
            # There are no holes in tiny-fs, so this cannot happen by design.
            raise CorruptionError(f"block {index} of the file is unallocated")
        out += blocks.read_block(block_no)[inner:inner + n]
        offset += n
        length -= n
    return bytes(out)


def write(inode: Inode, blocks: BlockDevice, alloc: Allocator,
          offset: int, data: bytes) -> None:
    if offset < 0:
        raise InvalidArgumentError("negative offset")
    if offset + len(data) > MAX_FILE_SIZE:
        raise FileTooBigError(f"{offset + len(data)} > {MAX_FILE_SIZE}")
    if offset > inode.size:
        # No sparse files: fill the gap with real, zeroed blocks first.
        truncate(inode, blocks, alloc, offset)

    pos = 0
    while pos < len(data):
        index, inner = divmod(offset + pos, BLOCK_SIZE)
        n = min(len(data) - pos, BLOCK_SIZE - inner)

        if inode.direct[index] == 0:
            # No block here yet. It was just allocated, so skip the read
            # and treat it as zeros.
            inode.direct[index] = alloc.allocate()
            buf = bytearray(BLOCK_SIZE)
        else:
            # Only part of the block changes, so read it before replacing bytes.
            buf = bytearray(blocks.read_block(inode.direct[index]))

        buf[inner:inner + n] = data[pos:pos + n]
        blocks.write_block(inode.direct[index], buf)
        pos += n

    inode.size = max(inode.size, offset + len(data))


def truncate(inode: Inode, blocks: BlockDevice, alloc: Allocator,
             new_size: int) -> None:
    if new_size < 0:
        raise InvalidArgumentError("negative size")
    if new_size > MAX_FILE_SIZE:
        raise FileTooBigError(f"{new_size} > {MAX_FILE_SIZE}")

    if new_size > inode.size:
        # Growing is the same thing as writing zeros. No holes.
        write(inode, blocks, alloc, inode.size, b"\x00" * (new_size - inode.size))
    else:
        # Shrinking frees the blocks that are no longer covered, right away.
        # Forget this and the bitmap just fills up (fsck will say so).
        first_unused = math.ceil(new_size / BLOCK_SIZE)
        for index in range(first_unused, NDIRECT):
            if inode.direct[index] != 0:
                alloc.mark_free(inode.direct[index])
                inode.direct[index] = 0
        inode.size = new_size
