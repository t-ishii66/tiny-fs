"""All tiny-fs errors. Every one of them carries an errno.

That single rule is what lets cli.py turn an error into one line plus an exit
status (chapter 11), and fuse_ops.py turn it into a FuseOSError (chapter 12).
"""

from errno import (
    EEXIST,
    EFBIG,
    EINVAL,
    EIO,
    EISDIR,
    ENAMETOOLONG,
    ENOENT,
    ENOSPC,
    ENOTDIR,
    ENOTEMPTY,
)


class FSError(Exception):
    """Base of every tiny-fs error. Always has an errno."""

    errno = EIO
    strerror = "I/O error"

    def __init__(self, path: str | bytes = "") -> None:
        if isinstance(path, bytes):
            path = path.decode("utf-8", "replace")
        self.path = path
        super().__init__(f"{path}: {self.strerror}")


class NotFoundError(FSError):
    errno, strerror = ENOENT, "No such file or directory"


class AlreadyExistsError(FSError):
    errno, strerror = EEXIST, "File exists"


class NotADirectoryError(FSError):
    errno, strerror = ENOTDIR, "Not a directory"


class IsADirectoryError(FSError):
    errno, strerror = EISDIR, "Is a directory"


class NotEmptyError(FSError):
    errno, strerror = ENOTEMPTY, "Directory not empty"


class NoSpaceError(FSError):
    errno, strerror = ENOSPC, "No space left on device"


class FileTooBigError(FSError):
    errno, strerror = EFBIG, "File too large"


class NameTooLongError(FSError):
    errno, strerror = ENAMETOOLONG, "File name too long"


class InvalidArgumentError(FSError):
    errno, strerror = EINVAL, "Invalid argument"


class CorruptionError(FSError):
    errno, strerror = EIO, "Image is corrupted"
