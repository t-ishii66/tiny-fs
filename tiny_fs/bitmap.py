"""L2: bits. One bit per block, or per i-node.

This layer never touches the disk. It gets a bytearray that somebody else read,
flips bits in it, and lets that somebody else write it back.
"""

from __future__ import annotations

from .errors import CorruptionError, NoSpaceError


class Bitmap:
    """An array of bits. Bit i set means "number i is in use".

    buf is one block as read from the disk. capacity says how many of those
    bits stand for something that really exists (1024 blocks / 129 i-nodes).
    """

    def __init__(self, buf: bytearray, capacity: int) -> None:
        if capacity > len(buf) * 8:
            raise ValueError(f"capacity {capacity} does not fit in {len(buf)} bytes")
        self._buf = buf
        self._capacity = capacity

    @property
    def buf(self) -> bytearray:
        return self._buf

    @property
    def capacity(self) -> int:
        return self._capacity

    def is_used(self, index: int) -> bool:
        self._check_range(index)
        byte_index, bit_index = divmod(index, 8)
        return self._buf[byte_index] & (1 << bit_index) != 0

    def mark_used(self, index: int) -> None:
        self._check_range(index)
        byte_index, bit_index = divmod(index, 8)
        self._buf[byte_index] |= 1 << bit_index

    def mark_free(self, index: int) -> None:
        self._check_range(index)
        if not self.is_used(index):
            # Double free. Either a bug one layer up, or a corrupted image.
            raise CorruptionError(f"index {index} is already free")
        byte_index, bit_index = divmod(index, 8)
        # Python's ~ gives a negative number (~1 == -2), so mask it back into
        # one byte. Forget the & 0xFF and assigning to the bytearray raises.
        self._buf[byte_index] &= ~(1 << bit_index) & 0xFF

    def allocate(self, hint: int = 0) -> int:
        """Take the first free bit at or after hint, wrapping around once."""
        if not 0 <= hint < self._capacity:
            hint = 0
        # Stopping at capacity is what keeps the unused tail of buf from ever
        # being handed out (chapter 2).
        order = list(range(hint, self._capacity)) + list(range(0, hint))
        for index in order:
            if not self.is_used(index):
                self.mark_used(index)
                return index
        raise NoSpaceError("no free entry left")

    def count_used(self) -> int:
        # capacity is not always a multiple of 8, so the last, partial byte
        # has to be masked before counting.
        full_bytes, extra_bits = divmod(self._capacity, 8)
        used = sum(byte.bit_count() for byte in self._buf[:full_bytes])
        if extra_bits:
            mask = (1 << extra_bits) - 1
            used += (self._buf[full_bytes] & mask).bit_count()
        return used

    def count_free(self) -> int:
        return self._capacity - self.count_used()

    def used_indexes(self) -> list[int]:
        return [i for i in range(self._capacity) if self.is_used(i)]

    def _check_range(self, index: int) -> None:
        if not 0 <= index < self._capacity:
            raise CorruptionError(f"index {index} out of range (0..{self._capacity - 1})")
