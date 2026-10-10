---
title: "tiny-fs — build a file system from scratch in Python"
description: "Build a file system from scratch in Python: i-nodes, bitmaps, directories and a superblock on a 4 MiB disk image, then mount it on Linux with FUSE."
permalink: /
---

# tiny-fs

[English](README.md) · [日本語](README-jp.md)

![Alice and Bob building a town of files out of small blocks](docs/images/readme-alice-bob.png)

A teaching text that starts from a "device" that can do nothing but read and write 512-byte sectors, builds
a file system on top of it, and follows along until it is finally mounted on Linux with FUSE. The implementation is in Python.

<!-- SEO intro added by setup-github-pages; review and adjust -->

If you have ever wanted to **build your own file system from scratch** — to see how
**i-nodes**, **bitmaps** and **directory entries** fit together, to understand what a
**superblock** actually holds, or to learn **OS internals** by working through a small
but real example — this is for you. The topics covered include **block devices** and
sectors, free-space management, path resolution, `fsck` and hex **dump** tooling, a
**FUSE** mount on Linux, and what the kernel's **page cache** and **journaling** do
underneath it all.

<!-- /SEO intro -->

We treat a single 4 MiB file, `disk.img`, as one disk, and lay out the superblock, the two bitmaps,
the i-node table, and the data region ourselves. The priority is on keeping a state in which we can always explain
what the byte sequence on the disk represents right now.

The image is fixed at 4 MiB, there are 128 i-nodes, and one file is at most 48 KiB.
It has neither indirect blocks, nor journaling, nor a cache.

---

## Prerequisites

- Ubuntu
- Python 3.11 or later
- The core works with the standard library alone. The only things brought in from outside are `pytest` for the tests, and
  `fusepy`, used in chapter 13
- `sudo apt install fuse libfuse2t64` only when trying the FUSE mount in chapter 13
  (sudo is required only for this install; the mount itself can be done as an ordinary user)

## Setting up

```bash
git clone https://github.com/t-ishii66/tiny-fs.git
cd tiny-fs

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

With this the `tiny-fs` command becomes usable. In substance it is a single script inside the venv.

```
$ which tiny-fs
/home/dev/tiny-fs/.venv/bin/tiny-fs
```

Nothing goes into the system side. `deactivate` makes it disappear from PATH, and deleting `.venv/` leaves no trace.
Because it is `-e` (editable), the sources are not copied, and what you fix takes effect on the next run as it is.

## Confirming that it works

```bash
pytest -q

tiny-fs mkfs disk.img
tiny-fs dump disk.img
```

One 4 MiB file is created, and if its contents are displayed in a form a human can read, the preparation is complete.

## Using it all round

```bash
tiny-fs mkdir disk.img /docs
tiny-fs put   disk.img ./README-jp.md /docs/readme.md
tiny-fs ls -l disk.img /docs
tiny-fs cat   disk.img /docs/readme.md
tiny-fs fsck  disk.img
```

The list of subcommands is in [chapter 11](docs/en/11-cli.md).
`disk.img` may be recreated as many times as you like.

---

## Reading

At the end of each chapter there is a **Try it**, where you can confirm with commands what that chapter built.

### Part I  From Problems to Concepts

1. [The disk as a device](docs/en/01-device.md) — a device that can only read and write 512-byte sectors. Bundling 8 sectors into 1 block
2. [Which parts are free — the bitmap](docs/en/02-bitmap.md) — managing free space with 1 bit = 1 block
3. [The bitmap alone is not enough — the i-node](docs/en/03-inode.md) — gathering the list of block numbers and the metadata into a fixed-length 128-byte structure
4. [How to attach names — the directory](docs/en/04-directory.md) — a table mapping names to i-node numbers. Tying the tree together with `.` and `..`
5. [Drawing the map — fixing the layout](docs/en/05-layout.md) — the superblock and `mkfs`. The placement is decided here

### Part II  Making It Work as a File System

6. [Path resolution](docs/en/06-path.md) — the procedure for obtaining an i-node number from `/docs/a.txt`
7. [Implementing `fs.py`](docs/en/07-fs.md) — opening the image, creating, reading and writing, removing
8. [Directory operations](docs/en/08-dir-ops.md) — `mkdir` / `rmdir` / `readdir` / `rename`

### Part III  Observation and Verification

9. [Peeking inside the image — dump](docs/en/09-dump.md) — translating a byte sequence into a form a human can read
10. [Making sure nothing is broken — fsck](docs/en/10-fsck.md) — obtaining the same information in two ways and comparing them
11. [Touching it from the command line — the CLI](docs/en/11-cli.md) — here it becomes usable in the ordinary way

### Part IV  Connecting to the Kernel

12. [Mounting](docs/en/12-mount.md) — grafting a tree onto a tree. Where does a path move to another FS?
13. [Mounting with FUSE](docs/en/13-fuse.md) — touching it from the real `ls` and `cat`. **This is the goal**

### Summary

14. [Summary, and the next step](docs/en/14-summary.md)

### Appendices

- [A. List of constants](docs/en/A-constants.md)
- [B. Error code table](docs/en/B-errno.md)
- [C. What is happening below tiny-fs (reference)](docs/en/C-under-the-hood.md)
- [D. Journaling (reference)](docs/en/D-journaling.md)

---

## The contents

```
tiny_fs/
  constants.py   the layout constants and the struct formats
  errors.py      the FSError-derived exceptions. All of them have an errno
  device.py      L0  reading and writing 512-byte sectors
  disk.py        L1  8 sectors = 1 block
  bitmap.py      L2  free-space management
  inode.py       L3  file contents and metadata
  directory.py   L4  names and the tree structure
  fs.py          L5  grasping the layout, path resolution, the FS API
  dump.py        display the image human-readably
  fsck.py        check consistency
  cli.py         the tiny-fs command
  fuse_ops.py    L6  the connection with the OS (only the errno conversion)
tests/
docs/ja/         the Japanese documentation
docs/en/         the English documentation
docs/images/     figures (Japanese) and illustrations; English figures in docs/images/en/
```

**A lower layer does not know the layers above it.** Even with `fuse_ops.py` removed, the functionality up to chapter 11 works just as it is.


## Credits

- Planning: t-ishii66
- Documentation: Claude Code Opus 5
- Code: Claude Code Opus 5
- Documentation review: t-ishii66
- Code review: t-ishii66
- Date of publication: 2026/10/10
- Version: 1.0.0
