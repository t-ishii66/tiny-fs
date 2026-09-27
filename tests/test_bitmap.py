"""Chapter 2: the bitmap."""

import pytest

from tiny_fs.bitmap import Bitmap
from tiny_fs.constants import BLOCK_SIZE
from tiny_fs.errors import CorruptionError, NoSpaceError


def _bitmap(capacity: int = 1024) -> Bitmap:
    return Bitmap(bytearray(BLOCK_SIZE), capacity)


def test_alloc_marks_used():
    bitmap = _bitmap()
    first = bitmap.allocate()
    assert bitmap.is_used(first)
    assert bitmap.allocate() != first


def test_alloc_until_full_raises_nospace():
    bitmap = _bitmap(capacity=64)
    taken = {bitmap.allocate() for _ in range(64)}
    assert taken == set(range(64))
    with pytest.raises(NoSpaceError):
        bitmap.allocate()


def test_free_then_alloc_reuses_the_same_index():
    bitmap = _bitmap(capacity=16)
    for _ in range(16):
        bitmap.allocate()
    bitmap.mark_free(5)
    assert bitmap.count_free() == 1
    assert bitmap.allocate() == 5


def test_double_free_raises():
    bitmap = _bitmap()
    bitmap.mark_used(3)
    bitmap.mark_free(3)
    with pytest.raises(CorruptionError):
        bitmap.mark_free(3)


def test_bit_position_matches_the_spec():
    # Marking index 8 must set bit 0 of byte 1. A roundtrip test would not
    # notice an MSB-first implementation; this does.
    bitmap = _bitmap()
    bitmap.mark_used(8)
    assert bitmap.buf[0] == 0x00
    assert bitmap.buf[1] == 0x01

    bitmap.mark_used(3)
    assert bitmap.buf[0] == 0x08


def test_lsb_first_byte_reads_as_documented():
    # blocks 0-3 and 5 used  ->  0b0010_1111 == 0x2f  (chapter 2)
    bitmap = _bitmap()
    for index in (0, 1, 2, 3, 5):
        bitmap.mark_used(index)
    assert bitmap.buf[0] == 0x2F
    assert bitmap.buf[0] >> 3 & 1 == 1      # block 3 is used
    assert bitmap.buf[0] >> 4 & 1 == 0      # block 4 is free


def test_capacity_limits_allocation():
    # The buffer is 4096 bytes, but a capacity of 1024 means 1024 entries and
    # the bytes past 128 stay zero.
    bitmap = _bitmap(capacity=1024)
    for _ in range(1024):
        bitmap.allocate()
    with pytest.raises(NoSpaceError):
        bitmap.allocate()
    assert bitmap.buf[:128] == b"\xff" * 128
    assert bitmap.buf[128:] == bytes(BLOCK_SIZE - 128)


def test_out_of_range_raises():
    bitmap = _bitmap(capacity=1024)
    with pytest.raises(CorruptionError):
        bitmap.is_used(1024)


def test_count_free_counts_the_last_partial_byte():
    # The i-node bitmap holds 129 bits, so bit 128 lives in a byte of its own.
    bitmap = _bitmap(capacity=129)
    bitmap.mark_used(0)
    assert bitmap.count_free() == 128
    assert bitmap.allocate(hint=128) == 128       # ino 128 is reachable
    assert bitmap.count_free() == 127
    assert bitmap.count_used() == 2


def test_hint_is_only_a_hint():
    bitmap = _bitmap(capacity=32)
    for index in range(10):
        bitmap.mark_used(index)
    assert bitmap.allocate(hint=20) == 20
    # A hint pointing at used entries just means the search wraps around.
    assert bitmap.allocate(hint=0) == 10
