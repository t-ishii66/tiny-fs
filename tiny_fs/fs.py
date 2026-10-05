"""L5: the filesystem. Paths in, i-nodes out.

The only layer that knows the layout. It reads the superblock, works out where
i-nodes and bitmaps live, and drives the layers below it.
"""

from __future__ import annotations

import stat
import struct
import time
from dataclasses import dataclass

from . import directory
from . import inode as inode_mod
from .bitmap import Bitmap
from .constants import (
    BLOCK_BITMAP_BLOCK,
    BLOCK_SIZE,
    DATA_START,
    INODE_BITMAP_BLOCK,
    INODE_SIZE,
    INODE_TABLE_START,
    INODES_PER_BLOCK,
    MAGIC,
    NUM_INODES,
    ROOT_INO,
    SUPERBLOCK_BLOCK,
    SUPERBLOCK_FMT,
    TOTAL_BLOCKS,
    TOTAL_SECTORS,
)
from .device import SectorDevice
from .disk import BlockDevice
from .errors import (
    AlreadyExistsError,
    CorruptionError,
    FSError,
    InvalidArgumentError,
    IsADirectoryError,
    NameTooLongError,
    NotADirectoryError,
    NotEmptyError,
    NotFoundError,
)
from .inode import Inode

SUPERBLOCK_BYTES = struct.calcsize(SUPERBLOCK_FMT)


def now() -> int:
    """Unix epoch seconds. One place, so tests can watch it."""
    return int(time.time())


@dataclass
class Superblock:
    """The superblock. 32 bytes at the start of block 0."""

    magic: int              # u32  0x74667331
    block_size: int         # u32  4096
    total_blocks: int       # u32  blocks in this image
    num_inodes: int         # u32  how many i-nodes exist
    inode_table_start: int  # u32  first block of the i-node table
    data_start: int         # u32  first block of the data area
    root_ino: int           # u32  i-node number of the root directory
    free_hint: int          # u32  where to start looking for a free block

    def encode(self) -> bytes:
        return struct.pack(SUPERBLOCK_FMT, self.magic, self.block_size,
                           self.total_blocks, self.num_inodes,
                           self.inode_table_start, self.data_start,
                           self.root_ino, self.free_hint)

    @classmethod
    def decode(cls, raw: bytes) -> "Superblock":
        # Takes the whole block; only the first 32 bytes mean anything.
        return cls(*struct.unpack(SUPERBLOCK_FMT, raw[:SUPERBLOCK_BYTES]))


def superblock_problems(sb: Superblock, num_blocks: int,
                        check_magic: bool = True) -> list[str]:
    """Can these numbers describe an image at all?

    dump and fsck have to survive a broken image, so they ask this instead of
    letting the arithmetic run off the end of the device.
    """
    problems: list[str] = []
    if check_magic and sb.magic != MAGIC:
        problems.append(f"superblock magic is 0x{sb.magic:08x}, "
                        f"expected 0x{MAGIC:08x}")
    if sb.block_size != BLOCK_SIZE:
        problems.append(f"superblock block_size is {sb.block_size}, "
                        f"expected {BLOCK_SIZE}")
    if not 1 <= sb.total_blocks <= num_blocks:
        problems.append(f"superblock total_blocks is {sb.total_blocks}, "
                        f"but the image holds {num_blocks} blocks")
        return problems                      # nothing below can be judged
    if not INODE_BITMAP_BLOCK < sb.inode_table_start < sb.data_start <= sb.total_blocks:
        problems.append(f"superblock has inode_table_start="
                        f"{sb.inode_table_start}, data_start={sb.data_start}")
        return problems
    capacity = (sb.data_start - sb.inode_table_start) * INODES_PER_BLOCK
    if not 1 <= sb.num_inodes <= capacity:
        problems.append(f"superblock num_inodes is {sb.num_inodes}, but the "
                        f"table holds {capacity}")
    elif not 1 <= sb.root_ino <= sb.num_inodes:
        problems.append(f"superblock root_ino is {sb.root_ino}")
    return problems


class _Allocator:
    """One bitmap, wrapped so that every change is written back at once.

    The method names match Bitmap's. From above it is just "a Bitmap that
    writes itself back".
    """

    def __init__(self, buf: bytearray, blocks: BlockDevice, block_no: int,
                 capacity: int, hint: int = 0) -> None:
        self._bitmap = Bitmap(buf, capacity)
        self._blocks = blocks
        self._block_no = block_no
        self.hint = hint if 0 <= hint < capacity else 0

    def allocate(self, hint: int | None = None) -> int:
        index = self._bitmap.allocate(self.hint if hint is None else hint)
        # Next time, start just past what was handed out.
        self.hint = (index + 1) % self._bitmap.capacity
        self._flush()
        return index

    def mark_used(self, index: int) -> None:
        self._bitmap.mark_used(index)
        self._flush()

    def mark_free(self, index: int) -> None:
        self._bitmap.mark_free(index)
        self._flush()

    def is_used(self, index: int) -> bool:
        return self._bitmap.is_used(index)

    def count_free(self) -> int:
        return self._bitmap.count_free()     # read only, nothing to write back

    def count_used(self) -> int:
        return self._bitmap.count_used()

    def _flush(self) -> None:
        self._blocks.write_block(self._block_no, self._bitmap.buf)


def split_path(path: str) -> list[bytes]:
    """Split a path into components, dropping the empty ones."""
    if not path.startswith("/"):
        raise InvalidArgumentError("path must be absolute")
    # This alone normalizes "//docs///a.txt" and "/docs/".
    return [c.encode() for c in path.split("/") if c]


class FileSystem:
    """One open filesystem."""

    def __init__(self, blocks: BlockDevice) -> None:
        self._blocks = blocks
        self._closed = False
        self._sb = Superblock.decode(blocks.read_block(SUPERBLOCK_BLOCK))
        self._check_superblock()

        # Both bitmaps are held in memory, one block each.
        self.blocks_alloc = _Allocator(
            bytearray(blocks.read_block(BLOCK_BITMAP_BLOCK)),
            blocks, BLOCK_BITMAP_BLOCK, capacity=self._sb.total_blocks,
            hint=self._sb.free_hint)
        self.inodes_alloc = _Allocator(
            bytearray(blocks.read_block(INODE_BITMAP_BLOCK)),
            blocks, INODE_BITMAP_BLOCK, capacity=self._sb.num_inodes + 1)

    # --- opening and closing ----------------------------------------------

    @property
    def superblock(self) -> Superblock:
        return self._sb

    @property
    def blocks(self) -> BlockDevice:
        return self._blocks

    def _check_superblock(self) -> None:
        sb = self._sb
        if sb.magic != MAGIC:
            raise CorruptionError(
                f"not a tiny-fs image (magic 0x{sb.magic:08x}, want 0x{MAGIC:08x})"
            )
        expected = {
            "block_size": BLOCK_SIZE,
            "total_blocks": TOTAL_BLOCKS,
            "num_inodes": NUM_INODES,
            "inode_table_start": INODE_TABLE_START,
            "data_start": DATA_START,
            "root_ino": ROOT_INO,
        }
        for field, want in expected.items():
            got = getattr(sb, field)
            if got != want:
                raise CorruptionError(f"superblock {field} is {got}, want {want}")
        if self._blocks.num_blocks != sb.total_blocks:
            raise CorruptionError(
                f"image holds {self._blocks.num_blocks} blocks, "
                f"superblock says {sb.total_blocks}"
            )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            # free_hint is kept in memory while running and written back here.
            self._sb.free_hint = self.blocks_alloc.hint
            buf = bytearray(self._blocks.read_block(SUPERBLOCK_BLOCK))
            buf[:SUPERBLOCK_BYTES] = self._sb.encode()
            self._blocks.write_block(SUPERBLOCK_BLOCK, buf)
        finally:
            self._blocks.close()

    def __enter__(self) -> "FileSystem":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- the i-node table --------------------------------------------------

    def _inode_pos(self, ino: int) -> tuple[int, int]:
        # ino starts at 1. Zero and out of range mean the image is broken.
        if not 1 <= ino <= self._sb.num_inodes:
            raise CorruptionError(f"bad inode number: {ino}")
        index = ino - 1
        block_no = self._sb.inode_table_start + index // INODES_PER_BLOCK
        offset = (index % INODES_PER_BLOCK) * INODE_SIZE
        return block_no, offset

    def read_inode(self, ino: int) -> Inode:
        block_no, offset = self._inode_pos(ino)
        raw = self._blocks.read_block(block_no)
        return Inode.decode(raw[offset:offset + INODE_SIZE])

    def write_inode(self, ino: int, inode: Inode) -> None:
        # 32 of them share a block, so this is a read-modify-write.
        block_no, offset = self._inode_pos(ino)
        buf = bytearray(self._blocks.read_block(block_no))
        buf[offset:offset + INODE_SIZE] = inode.encode()
        self._blocks.write_block(block_no, buf)

    # --- path resolution ---------------------------------------------------

    def _walk(self, components: list[bytes], path: str) -> int:
        ino = self._sb.root_ino                  # always start at the root
        for name in components:
            inode = self.read_inode(ino)
            if not stat.S_ISDIR(inode.mode):
                raise NotADirectoryError(path)   # ENOTDIR
            ino = self._lookup_in(inode, name, path)     # ENOENT if absent
        return ino

    def lookup(self, path: str) -> int:
        return self._walk(split_path(path), path)

    def lookup_parent(self, path: str) -> tuple[int, bytes]:
        components = split_path(path)
        if not components:
            raise InvalidArgumentError("root has no parent")
        return self._walk(components[:-1], path), components[-1]

    def _lookup_in(self, dir_inode: Inode, name: bytes, path: str) -> int:
        """directory.lookup, but the error names the path the user asked for
        instead of the one component that failed."""
        try:
            return directory.lookup(dir_inode, self._blocks, name)
        except NotFoundError:
            raise NotFoundError(path) from None

    def _add_entry(self, dir_inode: Inode, name: bytes, ino: int,
                   path: str) -> None:
        """directory.add_entry, with the same relabelling."""
        try:
            directory.add_entry(dir_inode, self._blocks, self.blocks_alloc,
                                name, ino)
        except (AlreadyExistsError, NameTooLongError, InvalidArgumentError) as e:
            raise type(e)(path) from None

    # --- files -------------------------------------------------------------

    def create(self, path: str, mode: int = stat.S_IFREG | 0o644) -> int:
        parent_ino, name = self.lookup_parent(path)
        parent = self.read_inode(parent_ino)

        # (1) take an i-node, (2) write it into the table
        ino, inode = inode_mod.allocate_inode(self.inodes_alloc, mode, now())
        inode.nlink = 1                      # one name is about to point here
        self.write_inode(ino, inode)

        try:
            # (3) add the name to the parent. add_entry rejects duplicates.
            self._add_entry(parent, name, ino, path)
        except FSError:
            # No name could be attached, so give the i-node back.
            self.write_inode(ino, Inode.empty())
            self.inodes_alloc.mark_free(ino)
            raise

        # (4) touch the parent. Adding an entry may have grown its size.
        parent.mtime = parent.ctime = now()
        self.write_inode(parent_ino, parent)
        return ino

    def read(self, path: str, offset: int, length: int) -> bytes:
        inode = self.read_inode(self.lookup(path))
        if stat.S_ISDIR(inode.mode):
            raise IsADirectoryError(path)    # EISDIR
        return inode_mod.read(inode, self._blocks, offset, length)

    def write(self, path: str, offset: int, data: bytes) -> int:
        ino = self.lookup(path)
        inode = self.read_inode(ino)
        if stat.S_ISDIR(inode.mode):
            raise IsADirectoryError(path)
        # Short of blocks, blocks_alloc supplies them; direct[] and size change.
        try:
            inode_mod.write(inode, self._blocks, self.blocks_alloc, offset, data)
        except FSError as e:
            # A write that ran out of space halfway has already taken blocks.
            # Record them before reporting the failure, or nothing would point
            # at them and fsck would be right to call them leaks.
            self.write_inode(ino, inode)
            # inode.py names no path, so its error would read "60000 > 49152".
            # Putting the caller's path back on is this layer's job, as in
            # _lookup_in.
            raise type(e)(path) from None
        inode.mtime = inode.ctime = now()
        self.write_inode(ino, inode)         # forget this and the write is lost
        return len(data)

    def truncate(self, path: str, new_size: int) -> None:
        ino = self.lookup(path)
        inode = self.read_inode(ino)
        if stat.S_ISDIR(inode.mode):
            raise IsADirectoryError(path)
        try:
            inode_mod.truncate(inode, self._blocks, self.blocks_alloc, new_size)
        except FSError as e:
            self.write_inode(ino, inode)     # same reason as in write()
            raise type(e)(path) from None
        inode.mtime = inode.ctime = now()
        self.write_inode(ino, inode)

    # --- metadata ----------------------------------------------------------

    def chmod(self, path: str, mode: int) -> None:
        ino = self.lookup(path)
        inode = self.read_inode(ino)
        # Only the permission bits move. The type bits are what the file is.
        inode.mode = (inode.mode & ~0o7777) | (mode & 0o7777)
        inode.ctime = now()
        self.write_inode(ino, inode)

    def chown(self, path: str, uid: int, gid: int) -> None:
        ino = self.lookup(path)
        inode = self.read_inode(ino)
        # -1 means "leave this one alone" (it arrives as 0xffffffff from FUSE)
        if uid not in (-1, 0xFFFFFFFF):
            inode.uid = uid
        if gid not in (-1, 0xFFFFFFFF):
            inode.gid = gid
        inode.ctime = now()
        self.write_inode(ino, inode)

    def utimens(self, path: str, mtime: int) -> None:
        ino = self.lookup(path)
        inode = self.read_inode(ino)
        inode.mtime = mtime
        inode.ctime = now()
        self.write_inode(ino, inode)

    def unlink(self, path: str) -> None:
        parent_ino, name = self.lookup_parent(path)
        parent = self.read_inode(parent_ino)
        ino = self._lookup_in(parent, name, path)            # ENOENT if absent

        inode = self.read_inode(ino)
        if stat.S_ISDIR(inode.mode):
            raise IsADirectoryError(path)    # directories go through rmdir

        directory.remove_entry(parent, self._blocks, self.blocks_alloc, name)
        inode.nlink -= 1

        if inode.nlink == 0:
            # Hand back the data blocks and the i-node number (chapter 3)
            inode_mod.free_inode(inode, self._blocks, self.blocks_alloc,
                                 self.inodes_alloc, ino)
            # Zero the 128 bytes so "unused" is visible in a dump.
            self.write_inode(ino, Inode.empty())
        else:
            self.write_inode(ino, inode)

        parent.mtime = parent.ctime = now()
        self.write_inode(parent_ino, parent)

    # --- directories -------------------------------------------------------

    def mkdir(self, path: str, mode: int = stat.S_IFDIR | 0o755) -> int:
        parent_ino, name = self.lookup_parent(path)
        parent = self.read_inode(parent_ino)

        ino, inode = inode_mod.allocate_inode(self.inodes_alloc, mode, now())
        inode.nlink = 2                 # its name in the parent, plus its own "."
        try:
            # This allocates one data block, so size becomes 4096.
            directory.init_directory(inode, self._blocks, self.blocks_alloc,
                                     ino, parent_ino)
            self._add_entry(parent, name, ino, path)
        except FSError:
            # Throw the half-built directory away. Blocks and number come back.
            inode_mod.free_inode(inode, self._blocks, self.blocks_alloc,
                                 self.inodes_alloc, ino)
            raise

        self.write_inode(ino, inode)
        parent.nlink += 1               # the child's ".." now points at it
        parent.mtime = parent.ctime = now()
        self.write_inode(parent_ino, parent)
        return ino

    def rmdir(self, path: str) -> None:
        parent_ino, name = self.lookup_parent(path)   # "/" is EINVAL: no rmdir
        parent = self.read_inode(parent_ino)
        ino = self._lookup_in(parent, name, path)

        inode = self.read_inode(ino)
        if not stat.S_ISDIR(inode.mode):
            raise NotADirectoryError(path)     # ENOTDIR. files go through unlink
        if not directory.is_empty(inode, self._blocks):
            raise NotEmptyError(path)          # ENOTEMPTY

        directory.remove_entry(parent, self._blocks, self.blocks_alloc, name)
        # The only name left is its own ".". Free it, data blocks and all.
        inode_mod.free_inode(inode, self._blocks, self.blocks_alloc,
                             self.inodes_alloc, ino)
        self.write_inode(ino, Inode.empty())

        parent.nlink -= 1                      # the child's ".." is gone
        parent.mtime = parent.ctime = now()
        self.write_inode(parent_ino, parent)

    def readdir(self, path: str) -> list[tuple[bytes, int]]:
        inode = self.read_inode(self.lookup(path))
        if not stat.S_ISDIR(inode.mode):
            raise NotADirectoryError(path)
        return [(e.name, e.ino)
                for _slot, e in directory.iter_entries(inode, self._blocks)]

    def stat(self, path: str) -> Inode:
        return self.read_inode(self.lookup(path))

    def rename(self, old_path: str, new_path: str) -> None:
        old_parent_ino, old_name = self.lookup_parent(old_path)
        new_parent_ino, new_name = self.lookup_parent(new_path)
        if old_name in (directory.DOT, directory.DOTDOT) or \
           new_name in (directory.DOT, directory.DOTDOT):
            raise InvalidArgumentError("cannot rename . or ..")

        old_parent = self.read_inode(old_parent_ino)
        ino = self._lookup_in(old_parent, old_name, old_path)
        inode = self.read_inode(ino)

        same_parent = old_parent_ino == new_parent_ino
        if same_parent and old_name == new_name:
            return                                 # nothing to do
        # One directory, one in-memory copy: two would overwrite each other.
        new_parent = old_parent if same_parent else self.read_inode(new_parent_ino)

        # rename replaces whatever sits at the destination (POSIX), so find out
        # what that is before touching anything.
        found = directory.find(new_parent, self._blocks, new_name)
        victim_ino = None if found is None else found[1].ino
        if victim_ino is not None:
            self._check_can_replace(inode, victim_ino, new_path)
        if stat.S_ISDIR(inode.mode):
            self._check_not_descendant(ino, new_parent_ino)

        # Everything that can fail has failed by now.
        if victim_ino is None:
            # Add the new name first: if this fails, the old name is still there.
            self._add_entry(new_parent, new_name, ino, new_path)
        else:
            # Point the name that is already there at the source instead. One
            # 32-byte write, which cannot fail for want of space.
            directory.set_entry(new_parent, self._blocks, self.blocks_alloc,
                                new_name, ino)
        directory.remove_entry(old_parent, self._blocks, self.blocks_alloc,
                               old_name)

        stamp = now()
        if victim_ino is not None:
            self._drop_replaced(victim_ino, new_parent)
        if not same_parent and stat.S_ISDIR(inode.mode):
            # The parent changed, so ".." and both link counts have to follow.
            directory.set_entry(inode, self._blocks, self.blocks_alloc,
                                directory.DOTDOT, new_parent_ino)
            inode.ctime = stamp
            self.write_inode(ino, inode)
            new_parent.nlink += 1
            old_parent.nlink -= 1

        old_parent.mtime = old_parent.ctime = stamp
        self.write_inode(old_parent_ino, old_parent)
        if not same_parent:
            new_parent.mtime = new_parent.ctime = stamp
            self.write_inode(new_parent_ino, new_parent)

    def _check_can_replace(self, inode: Inode, victim_ino: int,
                           new_path: str) -> None:
        """A file may replace a file, and a directory an empty directory."""
        victim = self.read_inode(victim_ino)
        if stat.S_ISDIR(inode.mode):
            if not stat.S_ISDIR(victim.mode):
                raise NotADirectoryError(new_path)    # ENOTDIR
            if not directory.is_empty(victim, self._blocks):
                raise NotEmptyError(new_path)         # ENOTEMPTY
        elif stat.S_ISDIR(victim.mode):
            raise IsADirectoryError(new_path)         # EISDIR

    def _drop_replaced(self, victim_ino: int, new_parent: Inode) -> None:
        """The name that used to point at the replaced i-node is gone."""
        victim = self.read_inode(victim_ino)
        if stat.S_ISDIR(victim.mode):
            # An empty directory, so the only name left is its own ".".
            # Free it, and the parent loses the ".." that pointed back at it.
            new_parent.nlink -= 1
            inode_mod.free_inode(victim, self._blocks, self.blocks_alloc,
                                 self.inodes_alloc, victim_ino)
            self.write_inode(victim_ino, Inode.empty())
            return

        victim.nlink -= 1
        if victim.nlink == 0:
            inode_mod.free_inode(victim, self._blocks, self.blocks_alloc,
                                 self.inodes_alloc, victim_ino)
            self.write_inode(victim_ino, Inode.empty())
        else:
            self.write_inode(victim_ino, victim)

    def _check_not_descendant(self, ino: int, new_parent_ino: int) -> None:
        # Walk ".." up from the new parent. Meeting ino on the way means the
        # move would cut a loop off the tree.
        cur = new_parent_ino
        for _step in range(self._sb.num_inodes + 1):
            if cur == ino:
                raise InvalidArgumentError("cannot move a directory into itself")
            if cur == self._sb.root_ino:
                return
            cur = directory.lookup(self.read_inode(cur), self._blocks,
                                   directory.DOTDOT)
        raise CorruptionError("'..' chain does not reach the root")

    # --- numbers for statfs / fsck / dump ---------------------------------

    def count_free_blocks(self) -> int:
        return self.blocks_alloc.count_free()

    def count_free_inodes(self) -> int:
        # bit 0 is reserved and never free, so it is not counted here.
        return self.inodes_alloc.count_free()


def _make_root(fs: FileSystem) -> None:
    """Create the root directory. Only mkfs does this."""
    ino, inode = inode_mod.allocate_inode(fs.inodes_alloc,
                                          stat.S_IFDIR | 0o755, now())
    if ino != fs.superblock.root_ino:
        raise CorruptionError(f"root must be ino {fs.superblock.root_ino}, got {ino}")
    inode.nlink = 2                 # its own "." and its own ".."
    directory.init_directory(inode, fs.blocks, fs.blocks_alloc, ino, ino)
    fs.write_inode(ino, inode)


def mkfs(path: str) -> None:
    """Write a brand new tiny-fs image, root directory included."""
    dev = SectorDevice.create(path, TOTAL_SECTORS)
    with BlockDevice(dev) as blocks:
        sb = Superblock(magic=MAGIC, block_size=BLOCK_SIZE,
                        total_blocks=TOTAL_BLOCKS, num_inodes=NUM_INODES,
                        inode_table_start=INODE_TABLE_START,
                        data_start=DATA_START, root_ino=ROOT_INO,
                        free_hint=DATA_START)
        blocks.write_block(SUPERBLOCK_BLOCK, sb.encode().ljust(BLOCK_SIZE, b"\x00"))

        # The metadata blocks are in use from the start (chapter 5).
        block_bitmap = Bitmap(bytearray(BLOCK_SIZE), TOTAL_BLOCKS)
        for block_no in range(DATA_START):
            block_bitmap.mark_used(block_no)
        blocks.write_block(BLOCK_BITMAP_BLOCK, block_bitmap.buf)

        # Room for bit 0 .. bit NUM_INODES. Bit 0 is the invalid number: fill it
        # in now so it is never picked as free. Give capacity NUM_INODES and
        # ino 128 could never be allocated.
        inode_bitmap = Bitmap(bytearray(BLOCK_SIZE), NUM_INODES + 1)
        inode_bitmap.mark_used(0)
        blocks.write_block(INODE_BITMAP_BLOCK, inode_bitmap.buf)

        # The i-node table stays as it was created: all zeros.

    # The root is then built with the same code every other directory uses.
    with FileSystem(BlockDevice(SectorDevice(path))) as fs:
        _make_root(fs)


def open_fs(path: str) -> FileSystem:
    """Stack the layers of chapter 1 onward: device -> blocks -> filesystem."""
    return FileSystem(BlockDevice(SectorDevice(path)))
