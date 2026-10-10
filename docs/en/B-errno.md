# B. Error Code Table

Every tiny-fs exception is `FSError`-derived and has an `errno`.
With this single convention, the CLI can convert it into an exit status (chapter 11), and
FUSE can convert it into a `FuseOSError` (chapter 13).

---

## 1. The definition of the exceptions

```python
# tiny_fs/errors.py
class FSError(Exception):
    """The base of all tiny-fs errors. Always has an errno."""

    errno = EIO
    strerror = "I/O error"

    def __init__(self, path: str = "") -> None:
        self.path = path
        super().__init__(f"{path}: {self.strerror}")
```

The derived classes are 2 lines each.

```python
class NotFoundError(FSError):
    errno, strerror = ENOENT, "No such file or directory"

class AlreadyExistsError(FSError):
    errno, strerror = EEXIST, "File exists"
```

---

## 2. The list

| Exception | `errno` | Meaning | When it comes out |
|---|---|---|---|
| `NotFoundError` | `ENOENT` | There is no such path | A component is not found in `lookup`. The target of `unlink` / `rm` does not exist |
| `AlreadyExistsError` | `EEXIST` | It already exists | The path `create` / `mkdir` tried to create already exists (`rename` replaces, so it does not come out there) |
| `NotADirectoryError` | `ENOTDIR` | It is not a directory | A component partway along the path is a file (`/memo.txt/x`). The target of `readdir` / `rmdir` is a file. `rename` tried to overwrite a file with a directory |
| `IsADirectoryError` | `EISDIR` | It is a directory | The target of `read` / `write` / `unlink` is a directory. `rename` tried to overwrite a directory with a file |
| `NotEmptyError` | `ENOTEMPTY` | It is not empty | Valid entries remain in the target of `rmdir`. The destination of `rename` is a non-empty directory |
| `NoSpaceError` | `ENOSPC` | There is no free space | Exhaustion of data blocks. Exhaustion of i-nodes. A directory reached 1536 entries |
| `FileTooBigError` | `EFBIG` | It is too large | A write exceeds `MAX_FILE_SIZE` (48 KiB) |
| `NameTooLongError` | `ENAMETOOLONG` | The name is too long | The name is 28 bytes or more |
| `InvalidArgumentError` | `EINVAL` | The argument is wrong | A relative path. The name is empty, or contains `/` or NUL. The parent of `/` was asked for. An attempt to move a directory into its own descendant |
| `CorruptionError` | `EIO` | The image is corrupt | An i-node number out of range. A double free. The superblock disagrees with the constants |

---

## 3. The role of each layer

We list the exceptions that **appear for the first time** in that layer (a layer above may rethrow the same type).

| Layer | About exceptions |
|---|---|
| `device.py` / `disk.py` | An out-of-range sector / block number is a `ValueError`. Not yet the file system layer |
| `bitmap.py` | `NoSpaceError`, and `CorruptionError` for a double free |
| `inode.py` | `FileTooBigError`, `InvalidArgumentError`, `CorruptionError` |
| `directory.py` | `NotFoundError`, `AlreadyExistsError`, `NameTooLongError`, `InvalidArgumentError` |
| `fs.py` | `NotADirectoryError`, `IsADirectoryError`, `NotEmptyError`, `CorruptionError`, `InvalidArgumentError` (a relative path, the parent of `/`, a `rename` into a descendant) |
| `cli.py` | **Converts only** — one line of message + exit status 1 |
| `fuse_ops.py` | **Converts only** — `FuseOSError(e.errno)` |

The layers below throw only "what happened", and
**how to show it is decided by the two modules at the boundary**.
Up to `fs.py`, nothing is known beyond the `errno`.

---

## 4. How it actually looks

The same `ENOENT` looks like this through 3 routes.

```
# As a library
>>> fs.read("/nothing", 0, 10)
NotFoundError: /nothing: No such file or directory

# From the CLI (chapter 11)
$ tiny-fs cat disk.img /nothing
tiny-fs: /nothing: No such file or directory (ENOENT)
$ echo $?
1

# Via FUSE (chapter 13)
$ cat ~/mnt/tiny-fs/nothing
cat: /home/dev/mnt/tiny-fs/nothing: No such file or directory
```

The example via FUSE is the message `cat` is printing, and tiny-fs is not involved.
Return the `errno` correctly and **the OS and the command take care of the explanation from there**.
