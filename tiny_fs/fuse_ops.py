"""L6: FUSE. Turns fusepy calls into fs.py calls, and nothing else.

No filesystem logic here. Take the FUSE layer away and every feature is still
reachable from cli.py.
"""

from __future__ import annotations

import stat
import sys
import time

from fuse import FUSE, FuseOSError, Operations

from .constants import BLOCK_SIZE, MAX_NAME_LEN
from .device import SectorDevice
from .disk import BlockDevice
from .errors import FSError
from .fs import FileSystem


class TinyFSOperations(Operations):
    def __init__(self, image: str) -> None:
        self._fs = FileSystem(BlockDevice(SectorDevice(image)))

    def __call__(self, op: str, *args):
        # The one and only place where an error becomes an errno.
        try:
            return super().__call__(op, *args)
        except FSError as e:
            raise FuseOSError(e.errno)

    def destroy(self, path: str) -> None:
        self._fs.close()

    # --- reading -----------------------------------------------------------

    def getattr(self, path, fh=None):
        inode = self._fs.stat(path)
        return {
            "st_mode":  inode.mode,
            "st_nlink": inode.nlink,
            "st_size":  inode.size,
            "st_uid":   inode.uid,
            "st_gid":   inode.gid,
            "st_mtime": inode.mtime,
            "st_ctime": inode.ctime,
            "st_atime": inode.mtime,   # no atime is kept, so mtime stands in
        }

    def readdir(self, path, fh):
        return [name.decode("utf-8", "replace")
                for name, _ino in self._fs.readdir(path)]

    def read(self, path, size, offset, fh):
        return self._fs.read(path, offset, size)

    def statfs(self, path):
        free_blocks = self._fs.count_free_blocks()
        sb = self._fs.superblock
        return {
            "f_bsize":   BLOCK_SIZE,
            "f_frsize":  BLOCK_SIZE,
            "f_blocks":  sb.total_blocks,
            "f_bfree":   free_blocks,
            "f_bavail":  free_blocks,
            "f_files":   sb.num_inodes,
            "f_ffree":   self._fs.count_free_inodes(),
            "f_namemax": MAX_NAME_LEN,
        }

    # --- writing -----------------------------------------------------------

    def create(self, path, mode, fi=None):
        self._fs.create(path, stat.S_IFREG | (mode & 0o777))
        return 0

    def write(self, path, data, offset, fh):
        return self._fs.write(path, offset, data)

    def truncate(self, path, length, fh=None):
        self._fs.truncate(path, length)

    def unlink(self, path):
        self._fs.unlink(path)

    def mkdir(self, path, mode):
        self._fs.mkdir(path, stat.S_IFDIR | (mode & 0o777))

    def rmdir(self, path):
        self._fs.rmdir(path)

    def rename(self, old, new):
        self._fs.rename(old, new)

    # --- metadata that ordinary tools expect to be able to set -------------

    def chmod(self, path, mode):
        self._fs.chmod(path, mode)

    def chown(self, path, uid, gid):
        self._fs.chown(path, uid, gid)

    def utimens(self, path, times=None):
        mtime = int(times[1]) if times else int(time.time())
        self._fs.utimens(path, mtime)

    # --- nothing to do -----------------------------------------------------

    def open(self, path, flags):
        return 0           # no open file state: every call carries the path

    def release(self, path, fh):
        return 0

    def flush(self, path, fh):
        return 0           # no cache, so it is already on disk

    def fsync(self, path, datasync, fh):
        return 0


def mount(image: str, mountpoint: str, foreground: bool = True) -> None:
    # nothreads is not optional: fs.py has no locks, so two operations at once
    # would corrupt the image.
    # fsname is what df and mount show in the "Filesystem" column.
    FUSE(TinyFSOperations(image), mountpoint, foreground=foreground,
         nothreads=True, fsname="tiny-fs")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    foreground = True
    for flag in ("-f", "--foreground"):
        if flag in argv:
            argv.remove(flag)
    if len(argv) != 2:
        print("usage: python -m tiny_fs.fuse_ops <image> <mountpoint> [-f]",
              file=sys.stderr)
        return 2
    mount(argv[0], argv[1], foreground=foreground)
    return 0


if __name__ == "__main__":
    sys.exit(main())
