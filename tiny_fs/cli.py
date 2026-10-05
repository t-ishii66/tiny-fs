"""The tiny-fs command. Opens an image, calls one or two API functions, closes.

No filesystem logic lives here. This file only turns command lines into calls
and errors into an exit status.
"""

from __future__ import annotations

import argparse
import errno as errno_mod
import pathlib
import sys
import time

from .constants import BLOCK_SIZE, NUM_INODES, TOTAL_BLOCKS
from .device import SectorDevice
from .disk import BlockDevice
from .dump import dump, dump_raw, mode_str
from .errors import AlreadyExistsError, FSError
from .fs import FileSystem, mkfs
from .fsck import fsck, summary


def _open(image: str) -> FileSystem:
    # The layers of chapter 1 onward: device -> blocks -> filesystem
    return FileSystem(BlockDevice(SectorDevice(image)))


def _open_blocks(image: str) -> BlockDevice:
    # dump and fsck look at the bytes, not at the filesystem
    return BlockDevice(SectorDevice(image))


def cmd_mkfs(args: argparse.Namespace) -> int:
    mkfs(args.image)
    mib = TOTAL_BLOCKS * BLOCK_SIZE // (1024 * 1024)
    print(f"created {args.image}: {TOTAL_BLOCKS} blocks ({mib} MiB), "
          f"{NUM_INODES} inodes")
    return 0


def cmd_ls(args: argparse.Namespace) -> int:
    with _open(args.image) as fs:
        entries = fs.readdir(args.fs_path)
        if not args.long:
            for name, _ino in entries:
                print(name.decode("utf-8", "replace"))
            return 0
        base = args.fs_path.rstrip("/")
        for name, _ino in entries:
            text = name.decode("utf-8", "replace")
            inode = fs.stat(f"{base}/{text}")
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(inode.mtime))
            print(f"{mode_str(inode.mode)}  {inode.nlink}  {inode.size}  "
                  f"{when}  {text}")
    return 0


def cmd_mkdir(args: argparse.Namespace) -> int:
    with _open(args.image) as fs:
        fs.mkdir(args.fs_path)
    return 0


def cmd_rmdir(args: argparse.Namespace) -> int:
    with _open(args.image) as fs:
        fs.rmdir(args.fs_path)
    return 0


def cmd_put(args: argparse.Namespace) -> int:
    data = pathlib.Path(args.host_path).read_bytes()   # the host side
    with _open(args.image) as fs:
        created = False
        try:
            fs.create(args.fs_path)
            created = True
        except AlreadyExistsError:
            fs.truncate(args.fs_path, 0)               # overwrite what is there
        try:
            fs.write(args.fs_path, 0, data)
        except FSError:
            if created:
                fs.unlink(args.fs_path)    # no empty leftovers (chapter 7)
            raise
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    with _open(args.image) as fs:
        size = fs.stat(args.fs_path).size
        data = fs.read(args.fs_path, 0, size)
    pathlib.Path(args.host_path).write_bytes(data)
    return 0


def cmd_cat(args: argparse.Namespace) -> int:
    with _open(args.image) as fs:
        size = fs.stat(args.fs_path).size
        # .buffer, not sys.stdout: text mode would re-encode and ruin binaries
        sys.stdout.buffer.write(fs.read(args.fs_path, 0, size))
        sys.stdout.buffer.flush()
    return 0


def cmd_mv(args: argparse.Namespace) -> int:
    with _open(args.image) as fs:
        fs.rename(args.old_path, args.new_path)
    return 0


def cmd_rm(args: argparse.Namespace) -> int:
    with _open(args.image) as fs:
        fs.unlink(args.fs_path)
    return 0


def cmd_dump(args: argparse.Namespace) -> int:
    with _open_blocks(args.image) as blocks:
        if args.raw is None:
            dump(blocks, sys.stdout)
        else:
            dump_raw(blocks, args.raw, args.length, sys.stdout)
    return 0


def cmd_fsck(args: argparse.Namespace) -> int:
    with _open_blocks(args.image) as blocks:
        problems = fsck(blocks)
        blocks_used, inodes_used = summary(blocks)
    if not problems:
        print(f"fsck: clean ({blocks_used} blocks used, {inodes_used} inodes used)")
        return 0
    print(f"fsck: {len(problems)} problems found")
    print()
    for problem in problems:
        print(f"  {problem}")
    return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tiny-fs")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("mkfs", help="initialise an image")
    p.add_argument("image")
    p.set_defaults(func=cmd_mkfs)

    p = sub.add_parser("ls", help="list a directory")
    p.add_argument("-l", "--long", action="store_true", help="long format")
    p.add_argument("image")
    p.add_argument("fs_path")
    p.set_defaults(func=cmd_ls)

    p = sub.add_parser("mkdir", help="make a directory")
    p.add_argument("image")
    p.add_argument("fs_path")
    p.set_defaults(func=cmd_mkdir)

    p = sub.add_parser("rmdir", help="remove an empty directory")
    p.add_argument("image")
    p.add_argument("fs_path")
    p.set_defaults(func=cmd_rmdir)

    p = sub.add_parser("put", help="copy a host file into the image")
    p.add_argument("image")
    p.add_argument("host_path")
    p.add_argument("fs_path")
    p.set_defaults(func=cmd_put)

    p = sub.add_parser("get", help="copy a file out of the image")
    p.add_argument("image")
    p.add_argument("fs_path")
    p.add_argument("host_path")
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("cat", help="write a file to standard output")
    p.add_argument("image")
    p.add_argument("fs_path")
    p.set_defaults(func=cmd_cat)

    p = sub.add_parser("mv", help="rename or move")
    p.add_argument("image")
    p.add_argument("old_path")
    p.add_argument("new_path")
    p.set_defaults(func=cmd_mv)

    p = sub.add_parser("rm", help="remove a file")
    p.add_argument("image")
    p.add_argument("fs_path")
    p.set_defaults(func=cmd_rm)

    p = sub.add_parser("dump", help="show the image in a readable form")
    p.add_argument("image")
    p.add_argument("--raw", type=int, metavar="BLOCK",
                   help="hex dump of one block instead")
    p.add_argument("--length", type=int, default=BLOCK_SIZE,
                   help="bytes to show with --raw")
    p.set_defaults(func=cmd_dump)

    p = sub.add_parser("fsck", help="check the image")
    p.add_argument("image")
    p.set_defaults(func=cmd_fsck)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.func(args)
    except FSError as e:
        # Print the errno name: it says which rule was broken, in one word.
        name = errno_mod.errorcode.get(e.errno, str(e.errno))
        print(f"tiny-fs: {e.path}: {e.strerror} ({name})", file=sys.stderr)
        return 1
    except OSError as e:
        # A host-side file, not the image
        print(f"tiny-fs: {e.filename}: {e.strerror}", file=sys.stderr)
        return 1
    except ValueError as e:
        # The lower layers raise this when the file cannot be a tiny-fs image
        print(f"tiny-fs: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
