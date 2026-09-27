"""L0: the device. 512-byte sectors, and nothing else."""

from __future__ import annotations

import os

from .constants import SECTOR_SIZE, TOTAL_SECTORS


class SectorDevice:
    """One image file, seen as an array of 512-byte sectors."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._file = open(path, "r+b", buffering=0)
        size = os.fstat(self._file.fileno()).st_size
        if size % SECTOR_SIZE != 0:
            self._file.close()
            raise ValueError(
                f"{path}: size {size} is not a multiple of {SECTOR_SIZE}"
            )
        self.num_sectors = size // SECTOR_SIZE

    @classmethod
    def create(cls, path: str, num_sectors: int = TOTAL_SECTORS) -> "SectorDevice":
        """Make a new image file of the given size, filled with zeros."""
        with open(path, "wb") as f:
            f.truncate(num_sectors * SECTOR_SIZE)
        return cls(path)

    @property
    def path(self) -> str:
        return self._path

    def read_sector(self, sector_no: int) -> bytes:
        # Out of range must raise, never quietly return zeros: a read that
        # "succeeds" where it should not is very hard to track down later.
        self._check_range(sector_no)
        self._file.seek(sector_no * SECTOR_SIZE)
        data = self._file.read(SECTOR_SIZE)
        if len(data) != SECTOR_SIZE:
            raise ValueError(f"short read at sector {sector_no}")
        return data

    def write_sector(self, sector_no: int, data: bytes) -> None:
        # Exactly one sector, or nothing. This is the device's own rule.
        if len(data) != SECTOR_SIZE:
            raise ValueError(
                f"write_sector wants exactly {SECTOR_SIZE} bytes, got {len(data)}"
            )
        self._check_range(sector_no)
        self._file.seek(sector_no * SECTOR_SIZE)
        written = self._file.write(data)
        if written != SECTOR_SIZE:
            raise ValueError(f"short write at sector {sector_no}")

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()

    def __enter__(self) -> "SectorDevice":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _check_range(self, sector_no: int) -> None:
        # Explicit check, not assert: -O would remove an assert, and the job of
        # not scribbling outside the image must not disappear with a flag.
        if not 0 <= sector_no < self.num_sectors:
            raise ValueError(
                f"sector {sector_no} out of range (0..{self.num_sectors - 1})"
            )
