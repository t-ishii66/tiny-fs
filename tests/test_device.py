"""Chapter 1: the device. 512-byte sectors, nothing else."""

import pytest

from tiny_fs.constants import SECTOR_SIZE, TOTAL_SECTORS
from tiny_fs.device import SectorDevice


def _dev(tmp_path) -> SectorDevice:
    return SectorDevice.create(str(tmp_path / "raw.img"))


def test_created_image_has_the_expected_size(tmp_path):
    dev = _dev(tmp_path)
    assert dev.num_sectors == TOTAL_SECTORS
    assert (tmp_path / "raw.img").stat().st_size == TOTAL_SECTORS * SECTOR_SIZE
    dev.close()


def test_sector_roundtrip(tmp_path):
    dev = _dev(tmp_path)
    data = bytes(range(256)) * 2
    dev.write_sector(3, data)
    assert dev.read_sector(3) == data
    dev.close()


def test_a_fresh_image_reads_as_zeros(tmp_path):
    dev = _dev(tmp_path)
    assert dev.read_sector(0) == bytes(SECTOR_SIZE)
    dev.close()


def test_write_of_wrong_length_raises(tmp_path):
    dev = _dev(tmp_path)
    with pytest.raises(ValueError):
        dev.write_sector(0, b"short")
    with pytest.raises(ValueError):
        dev.write_sector(0, bytes(SECTOR_SIZE + 1))
    dev.close()


def test_out_of_range_raises(tmp_path):
    dev = _dev(tmp_path)
    with pytest.raises(ValueError):
        dev.read_sector(TOTAL_SECTORS)
    with pytest.raises(ValueError):
        dev.read_sector(-1)
    with pytest.raises(ValueError):
        dev.write_sector(TOTAL_SECTORS, bytes(SECTOR_SIZE))
    dev.close()


def test_data_survives_reopening(tmp_path):
    dev = _dev(tmp_path)
    dev.write_sector(7, b"x" * SECTOR_SIZE)
    dev.close()

    again = SectorDevice(str(tmp_path / "raw.img"))
    assert again.read_sector(7) == b"x" * SECTOR_SIZE
    again.close()
