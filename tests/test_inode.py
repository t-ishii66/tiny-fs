"""Chapter 3: the i-node."""

import struct

import pytest

from tiny_fs import inode as inode_mod
from tiny_fs.bitmap import Bitmap
from tiny_fs.constants import (
    BLOCK_SIZE,
    INODE_FMT,
    INODE_SIZE,
    MAX_FILE_SIZE,
    NDIRECT,
    NUM_INODES,
)
from tiny_fs.errors import FileTooBigError
from tiny_fs.inode import Inode


def _inode(mode: int = 0o100644) -> Inode:
    return Inode(mode=mode, nlink=1, uid=0, gid=0, size=0, mtime=1, ctime=2,
                 direct=[0] * NDIRECT)


def _inode_alloc() -> Bitmap:
    bitmap = Bitmap(bytearray(BLOCK_SIZE), NUM_INODES + 1)
    bitmap.mark_used(0)          # what mkfs does: ino 0 is not allocatable
    return bitmap


def test_encode_decode_roundtrip():
    inode = Inode(mode=0o100644, nlink=1, uid=501, gid=20, size=5000,
                  mtime=1790000000, ctime=1790000001,
                  direct=[8, 9] + [0] * 10)
    raw = inode.encode()
    assert len(raw) == INODE_SIZE
    assert Inode.decode(raw) == inode


def test_encoded_size_is_exactly_128():
    # 84 bytes of fields, then 44 bytes of zeros. The spare room is part of
    # the format, so it is checked directly.
    raw = _inode().encode()
    assert struct.calcsize(INODE_FMT) == 84
    assert len(raw) == 128
    assert raw[84:] == bytes(44)


def test_decode_rejects_a_wrong_length():
    with pytest.raises(ValueError):
        Inode.decode(b"\x00" * 64)


def test_write_across_block_boundary(raw_device, block_alloc):
    # 12 bytes from offset 4090 land in two blocks of the file.
    inode = _inode()
    inode_mod.write(inode, raw_device, block_alloc, 4090, b"ABCDEFGHIJKL")

    assert inode.size == 4102
    assert inode.direct[0] != 0 and inode.direct[1] != 0
    # Seen from the device, both blocks really changed.
    assert raw_device.read_block(inode.direct[0])[4090:] == b"ABCDEF"
    assert raw_device.read_block(inode.direct[1])[:6] == b"GHIJKL"
    assert inode_mod.read(inode, raw_device, 4090, 12) == b"ABCDEFGHIJKL"


def test_partial_write_preserves_neighbours(raw_device, block_alloc):
    inode = _inode()
    inode_mod.write(inode, raw_device, block_alloc, 0, b"\xaa" * BLOCK_SIZE)
    inode_mod.write(inode, raw_device, block_alloc, 100, b"\x11" * 10)

    block = raw_device.read_block(inode.direct[0])
    assert block[99] == 0xAA          # read-modify-write, not a fresh block
    assert block[100:110] == b"\x11" * 10
    assert block[110] == 0xAA


def test_read_stops_at_size(raw_device, block_alloc):
    inode = _inode()
    inode_mod.write(inode, raw_device, block_alloc, 0, b"z" * 5000)
    assert len(inode_mod.read(inode, raw_device, 0, 8192)) == 5000
    assert inode_mod.read(inode, raw_device, 5000, 10) == b""


def test_write_beyond_max_file_size_raises_efbig(raw_device, block_alloc):
    inode = _inode()
    inode_mod.write(inode, raw_device, block_alloc, 0, b"x" * MAX_FILE_SIZE)
    assert inode.size == MAX_FILE_SIZE
    with pytest.raises(FileTooBigError):
        inode_mod.write(inode, raw_device, block_alloc, MAX_FILE_SIZE, b"x")


def test_truncate_shrink_frees_blocks(raw_device, block_alloc):
    inode = _inode()
    inode_mod.write(inode, raw_device, block_alloc, 0, b"x" * (BLOCK_SIZE * 3))
    free_before = block_alloc.count_free()

    inode_mod.truncate(inode, raw_device, block_alloc, BLOCK_SIZE)

    assert block_alloc.count_free() == free_before + 2
    assert inode.direct[1] == 0 and inode.direct[2] == 0
    assert inode.size == BLOCK_SIZE


def test_truncate_grow_fills_zero(raw_device, block_alloc):
    inode = _inode()
    inode_mod.write(inode, raw_device, block_alloc, 0, b"abc")
    inode_mod.truncate(inode, raw_device, block_alloc, 5000)

    data = inode_mod.read(inode, raw_device, 0, 5000)
    assert data == b"abc" + bytes(4997)      # no rubbish shows through


def test_write_past_the_end_fills_the_gap(raw_device, block_alloc):
    # No sparse files: the skipped blocks are really allocated and zeroed.
    inode = _inode()
    inode_mod.write(inode, raw_device, block_alloc, 0, b"a")
    inode_mod.write(inode, raw_device, block_alloc, 9000, b"b")

    assert all(inode.direct[i] != 0 for i in range(3))
    assert inode_mod.read(inode, raw_device, 0, 9001) == b"a" + bytes(8999) + b"b"


def test_allocate_inode_never_returns_zero():
    inode_bitmap = _inode_alloc()
    ino, inode = inode_mod.allocate_inode(inode_bitmap, 0o100644, now=123)
    assert ino >= 1
    assert inode.size == 0
    assert inode.direct == [0] * NDIRECT
    assert inode.nlink == 0          # a name has not been attached yet


def test_free_inode_returns_blocks_and_number(raw_device, block_alloc):
    inode_bitmap = _inode_alloc()
    ino, inode = inode_mod.allocate_inode(inode_bitmap, 0o100644, now=123)
    inode_mod.write(inode, raw_device, block_alloc, 0, b"x" * (BLOCK_SIZE * 3))

    blocks_free = block_alloc.count_free()
    inodes_free = inode_bitmap.count_free()
    inode_mod.free_inode(inode, raw_device, block_alloc, inode_bitmap, ino)

    assert block_alloc.count_free() == blocks_free + 3
    assert inode_bitmap.count_free() == inodes_free + 1
    assert inode.direct == [0] * NDIRECT


def test_empty_inode_is_all_zeros():
    assert Inode.empty().encode() == bytes(INODE_SIZE)
