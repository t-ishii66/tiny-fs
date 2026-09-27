"""Shared fixtures. Every test gets its own freshly made image."""

from __future__ import annotations

import pytest

from tiny_fs.bitmap import Bitmap
from tiny_fs.constants import BLOCK_SIZE, DATA_START, TOTAL_BLOCKS
from tiny_fs.device import SectorDevice
from tiny_fs.disk import BlockDevice
from tiny_fs.fs import FileSystem, mkfs
from tiny_fs.fsck import fsck


@pytest.fixture
def image(tmp_path) -> str:
    """Path to a brand new tiny-fs image."""
    path = tmp_path / "disk.img"
    mkfs(str(path))
    return str(path)


@pytest.fixture
def fs(image):
    """An open FileSystem. Closed and then fsck'd when the test ends."""
    filesystem = FileSystem(BlockDevice(SectorDevice(image)))
    yield filesystem
    filesystem.close()
    # With fsck in the teardown, every test also checks consistency.
    with BlockDevice(SectorDevice(image)) as blocks:
        assert fsck(blocks) == []


@pytest.fixture
def raw_device(tmp_path) -> BlockDevice:
    """A zeroed image with no filesystem on it, for the lower layers."""
    path = tmp_path / "raw.img"
    blocks = BlockDevice(SectorDevice.create(str(path)))
    yield blocks
    blocks.close()


@pytest.fixture
def block_alloc() -> Bitmap:
    """A block allocator that never hands out block 0 (the sentinel)."""
    bitmap = Bitmap(bytearray(BLOCK_SIZE), TOTAL_BLOCKS)
    for block_no in range(DATA_START):
        bitmap.mark_used(block_no)
    return bitmap
