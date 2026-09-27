"""L4: dirents. A directory is a file whose contents are an array of dirents.

This layer knows nothing about paths and nothing about where the directory
lives on disk. It reads and writes the directory file through inode.py.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Iterator

from . import inode as inode_mod
from .constants import (
    BLOCK_SIZE,
    DIRENT_FMT,
    DIRENT_SIZE,
    MAX_DIRENTS,
    MAX_NAME_LEN,
)
from .disk import BlockDevice
from .errors import (
    AlreadyExistsError,
    InvalidArgumentError,
    NameTooLongError,
    NoSpaceError,
    NotFoundError,
)
from .inode import Allocator, Inode

DOT = b"."
DOTDOT = b".."


@dataclass
class Dirent:
    """One dirent. Occupies 32 bytes on disk."""

    ino: int      # u32   the i-node this name points at. 0 means "free slot"
    name: bytes   # 27 B  the file name, NUL-padded on the right when written

    def encode(self) -> bytes:
        return struct.pack(DIRENT_FMT, self.ino, self.name)

    @classmethod
    def decode(cls, raw: bytes) -> "Dirent":
        ino, name = struct.unpack(DIRENT_FMT, raw)
        # Drop the padding to get the name back.
        return cls(ino=ino, name=name.rstrip(b"\x00"))


def check_name(name: bytes) -> None:
    """Reject anything that cannot be stored, or that would be ambiguous."""
    if not name:
        raise InvalidArgumentError("empty name")
    if len(name) > MAX_NAME_LEN:
        raise NameTooLongError(name)
    if b"/" in name:
        raise InvalidArgumentError(name)      # the path separator
    if b"\x00" in name:
        raise InvalidArgumentError(name)      # would collide with the padding


def iter_entries(dir_inode: Inode, blocks: BlockDevice) -> Iterator[tuple[int, Dirent]]:
    """Yield (slot number, Dirent) for the entries that are in use."""
    raw = _read_all(dir_inode, blocks)
    for slot in range(len(raw) // DIRENT_SIZE):
        entry = Dirent.decode(raw[slot * DIRENT_SIZE:(slot + 1) * DIRENT_SIZE])
        if entry.ino != 0:          # 0 is a free slot
            yield slot, entry


def lookup(dir_inode: Inode, blocks: BlockDevice, name: bytes) -> int:
    """Name to i-node number. ENOENT if there is no such name."""
    found = find(dir_inode, blocks, name)
    if found is None:
        raise NotFoundError(name)
    return found[1].ino


def find(dir_inode: Inode, blocks: BlockDevice,
         name: bytes) -> tuple[int, Dirent] | None:
    for slot, entry in iter_entries(dir_inode, blocks):
        if entry.name == name:
            return slot, entry
    return None


def exists(dir_inode: Inode, blocks: BlockDevice, name: bytes) -> bool:
    return find(dir_inode, blocks, name) is not None


def add_entry(dir_inode: Inode, blocks: BlockDevice, alloc: Allocator,
              name: bytes, ino: int) -> None:
    check_name(name)
    if exists(dir_inode, blocks, name):
        raise AlreadyExistsError(name)

    slot = _find_free_slot(dir_inode, blocks)
    if slot is None:
        if dir_inode.size // DIRENT_SIZE >= MAX_DIRENTS:
            raise NoSpaceError(name)
        # Out of free slots. Grow the file by one block: growing zero-fills, so
        # the new 4096 bytes are 128 free slots with ino = 0.
        slot = dir_inode.size // DIRENT_SIZE
        inode_mod.truncate(dir_inode, blocks, alloc, dir_inode.size + BLOCK_SIZE)

    raw = Dirent(ino=ino, name=name).encode()
    inode_mod.write(dir_inode, blocks, alloc, slot * DIRENT_SIZE, raw)


def remove_entry(dir_inode: Inode, blocks: BlockDevice, alloc: Allocator,
                 name: bytes) -> int:
    found = find(dir_inode, blocks, name)
    if found is None:
        raise NotFoundError(name)
    slot, entry = found
    if entry.name in (DOT, DOTDOT):
        raise InvalidArgumentError("cannot remove . or ..")
    empty = Dirent(ino=0, name=b"").encode()
    inode_mod.write(dir_inode, blocks, alloc, slot * DIRENT_SIZE, empty)
    return entry.ino       # the caller decrements nlink (chapter 7)


def set_entry(dir_inode: Inode, blocks: BlockDevice, alloc: Allocator,
              name: bytes, ino: int) -> None:
    """Point an existing name at another i-node (used for ".." on rename)."""
    found = find(dir_inode, blocks, name)
    if found is None:
        raise NotFoundError(name)
    slot, _entry = found
    raw = Dirent(ino=ino, name=name).encode()
    inode_mod.write(dir_inode, blocks, alloc, slot * DIRENT_SIZE, raw)


def init_directory(dir_inode: Inode, blocks: BlockDevice, alloc: Allocator,
                   self_ino: int, parent_ino: int) -> None:
    # The first two entries are always these, in this order. For the root,
    # parent_ino == self_ino.
    add_entry(dir_inode, blocks, alloc, DOT, self_ino)
    add_entry(dir_inode, blocks, alloc, DOTDOT, parent_ino)


def is_empty(dir_inode: Inode, blocks: BlockDevice) -> bool:
    for _slot, entry in iter_entries(dir_inode, blocks):
        if entry.name not in (DOT, DOTDOT):
            return False       # one live entry is enough to say "not empty"
    return True


def _read_all(dir_inode: Inode, blocks: BlockDevice) -> bytes:
    # A directory is a file, so reading it needs no special function.
    return inode_mod.read(dir_inode, blocks, 0, dir_inode.size)


def _find_free_slot(dir_inode: Inode, blocks: BlockDevice) -> int | None:
    raw = _read_all(dir_inode, blocks)
    for slot in range(len(raw) // DIRENT_SIZE):
        entry = Dirent.decode(raw[slot * DIRENT_SIZE:(slot + 1) * DIRENT_SIZE])
        if entry.ino == 0:
            return slot
    return None
