"""Chapter 1: 8 sectors make one block."""

import pytest

from tiny_fs.constants import BLOCK_SIZE, SECTOR_SIZE, SECTORS_PER_BLOCK, TOTAL_BLOCKS
from tiny_fs.device import SectorDevice
from tiny_fs.disk import BlockDevice


def _blocks(tmp_path) -> BlockDevice:
    return BlockDevice(SectorDevice.create(str(tmp_path / "raw.img")))


def test_block_roundtrip(tmp_path):
    # The most boring test there is, and the most useful one.
    blocks = _blocks(tmp_path)
    data = bytes((i * 7) % 256 for i in range(BLOCK_SIZE))
    blocks.write_block(5, data)
    assert blocks.read_block(5) == data
    blocks.close()


def test_block_is_eight_sectors(tmp_path):
    # Writing block 1 must change sectors 8-15, and nothing on either side.
    dev = SectorDevice.create(str(tmp_path / "raw.img"))
    blocks = BlockDevice(dev)
    dev.write_sector(7, b"\xaa" * SECTOR_SIZE)
    dev.write_sector(16, b"\xbb" * SECTOR_SIZE)

    blocks.write_block(1, b"\x11" * BLOCK_SIZE)

    for sector_no in range(8, 8 + SECTORS_PER_BLOCK):
        assert dev.read_sector(sector_no) == b"\x11" * SECTOR_SIZE
    assert dev.read_sector(7) == b"\xaa" * SECTOR_SIZE
    assert dev.read_sector(16) == b"\xbb" * SECTOR_SIZE
    blocks.close()


def test_num_blocks(tmp_path):
    blocks = _blocks(tmp_path)
    assert blocks.num_blocks == TOTAL_BLOCKS
    blocks.close()


def test_out_of_range_raises(tmp_path):
    blocks = _blocks(tmp_path)
    with pytest.raises(ValueError):
        blocks.read_block(TOTAL_BLOCKS)
    blocks.close()


def test_write_of_wrong_length_raises(tmp_path):
    blocks = _blocks(tmp_path)
    with pytest.raises(ValueError):
        blocks.write_block(0, b"short")
    blocks.close()
