"""L1: bundle 8 sectors into one block. Nothing clever happens here."""

from __future__ import annotations

from .constants import BLOCK_SIZE, SECTOR_SIZE, SECTORS_PER_BLOCK
from .device import SectorDevice


class BlockDevice:
    """The device seen in units of 4096 bytes."""

    def __init__(self, dev: SectorDevice) -> None:
        self._dev = dev

    @property
    def num_blocks(self) -> int:
        return self._dev.num_sectors // SECTORS_PER_BLOCK

    def read_block(self, block_no: int) -> bytes:
        first = block_no * SECTORS_PER_BLOCK
        return b"".join(
            self._dev.read_sector(first + i) for i in range(SECTORS_PER_BLOCK)
        )

    def write_block(self, block_no: int, data: bytes) -> None:
        if len(data) != BLOCK_SIZE:
            raise ValueError(
                f"write_block wants exactly {BLOCK_SIZE} bytes, got {len(data)}"
            )
        # No cache, no delayed write: 8 write_sector calls, straight to disk.
        first = block_no * SECTORS_PER_BLOCK
        for i in range(SECTORS_PER_BLOCK):
            start = i * SECTOR_SIZE
            self._dev.write_sector(first + i, data[start:start + SECTOR_SIZE])

    def close(self) -> None:
        self._dev.close()

    def __enter__(self) -> "BlockDevice":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
