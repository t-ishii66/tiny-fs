"""Chapter 12: FUSE. Linux only, so it is skipped by default.

Run it inside the Docker image:  pytest -m fuse
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

import pytest

from tiny_fs.device import SectorDevice
from tiny_fs.disk import BlockDevice
from tiny_fs.fs import FileSystem, mkfs
from tiny_fs.fsck import fsck

pytestmark = pytest.mark.fuse


def _unmount(mountpoint: str) -> None:
    for tool in ("fusermount3", "fusermount", "umount"):
        if shutil.which(tool):
            flag = ["-u"] if tool.startswith("fusermount") else []
            subprocess.run([tool, *flag, mountpoint], check=False,
                           capture_output=True)
            return


def test_mount_write_unmount(tmp_path):
    pytest.importorskip("fuse")
    image = str(tmp_path / "disk.img")
    mountpoint = tmp_path / "mnt"
    mountpoint.mkdir()
    mkfs(image)

    proc = subprocess.Popen(
        [sys.executable, "-m", "tiny_fs.fuse_ops", image, str(mountpoint), "-f"]
    )
    try:
        # Wait for the mount to show up.
        for _ in range(100):
            if os.path.ismount(str(mountpoint)):
                break
            time.sleep(0.05)
        else:
            pytest.fail("the filesystem never mounted")

        # Plain tools, which know nothing about tiny-fs.
        (mountpoint / "a.txt").write_bytes(b"hello\n")
        (mountpoint / "docs").mkdir()
        shutil.copyfile("/etc/hostname", mountpoint / "docs" / "hostname")

        assert (mountpoint / "a.txt").read_bytes() == b"hello\n"
        assert sorted(os.listdir(mountpoint)) == ["a.txt", "docs"]
        assert os.statvfs(mountpoint).f_bsize == 4096
    finally:
        _unmount(str(mountpoint))
        proc.wait(timeout=10)

    # The same image, read back through the CLI path.
    with FileSystem(BlockDevice(SectorDevice(image))) as fs:
        assert fs.read("/a.txt", 0, 100) == b"hello\n"
        assert fs.stat("/docs/hostname").size > 0
    with BlockDevice(SectorDevice(image)) as blocks:
        assert fsck(blocks) == []
