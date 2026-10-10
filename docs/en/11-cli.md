# 11. Touching It from the Command Line — the CLI

![Alice and Bob typing commands to operate a computer](../images/11-alice-bob.png)

The operations are all in `fs.py`, and with `dump` and `fsck` the contents can be confirmed too.
What remains is **making them usable as ordinary commands**.

When this chapter is over, "create, put in, look, take out, remove, confirm" can all be done
without using FUSE.

---

## 1. The shape of the command

```
tiny-fs <subcommand> <image> [args...]
```

The reason we pass the image path every time is that **it is not mounted**.
As far as the OS is concerned, `disk.img` is just a file,
and the only thing that can interpret it as a file system is this command
(with the FUSE of chapter 13, this premise changes).

| Subcommand | Function | API called |
|---|---|---|
| `mkfs <img>` | Initialize an image | the mkfs of chapter 5 |
| `ls [-l] <img> <path>` | List the contents of a directory | `readdir` / `stat` |
| `mkdir <img> <path>` | Create a directory | `mkdir` |
| `rmdir <img> <path>` | Remove an empty directory | `rmdir` |
| `put <img> <host> <path>` | Put a host file in | `create` / `truncate` / `write` |
| `get <img> <path> <host>` | Take a file inside out | `read` |
| `cat <img> <path>` | Contents to standard output | `read` |
| `mv <img> <old> <new>` | Change a name / move | `rename` |
| `rm <img> <path>` | Remove a file | `unlink` |
| `dump <img> [--raw N]` | Display the contents human-readably | chapter 9 |
| `fsck <img>` | Check consistency | chapter 10 |

Every subcommand does nothing more than call the corresponding API once.
Because of that, there are places where the behaviour differs from the shell command of the same name.

**`mv` does not treat the destination specially.** The shell's `mv a b`, if `b` is an existing directory,
moves `a` **into** it (it rewrites the call as `rename("a", "b/a")` before calling).
`tiny-fs mv` passes the paths it received straight to `rename`, so
in the same situation it becomes `EISDIR`, as in the table in chapter 8. If you want it inside, write out the destination.

```bash
tiny-fs mv disk.img /a.txt /docs        # EISDIR (/docs is an existing directory)
tiny-fs mv disk.img /a.txt /docs/a.txt  # write it like this
```

---

## 2. Using it all round

```
$ tiny-fs mkfs disk.img
created disk.img: 1024 blocks (4 MiB), 128 inodes

$ tiny-fs mkdir disk.img /docs
$ tiny-fs put disk.img ./sample.txt /docs/sample.txt

$ tiny-fs ls disk.img /
.
..
docs

$ tiny-fs ls -l disk.img /docs
d rwxr-xr-x  2  4096  2026-09-27 12:00  .
d rwxr-xr-x  3  4096  2026-09-27 12:00  ..
- rw-r--r--  1  5000  2026-09-27 12:03  sample.txt
```

`/docs`'s `.` is itself so `nlink = 2`, and
`..` is the root, which has one child directory, so `nlink = 3`.
The way of counting decided in chapter 4 comes out directly in `ls -l`.

Take it out, and confirm that it matches the original file.

```
$ tiny-fs get disk.img /docs/sample.txt ./copy.txt
$ diff ./sample.txt ./copy.txt && echo same
same
```

Finally, remove everything and see that it returns to the state right after mkfs.

```
$ tiny-fs rm disk.img /docs/sample.txt
$ tiny-fs rmdir disk.img /docs
$ tiny-fs fsck disk.img
fsck: clean (8 blocks used, 1 inodes used)
```

**8 blocks, 1 i-node** are the same numbers as right after mkfs.
Everything that was created and removed has been freed.

`fsck`'s i-node count does not include the reserved bit 0 (chapter 5).
Looking at the same state with `dump` shows `used 2 / 129` (chapter 9), so it differs by 1.

---

## 3. Passing files to and from the host

`put` and `get` are **the boundary between two file systems**.
The host side is an ordinary file and the image side is a tiny-fs path, and
the processing is nothing more than "read from one, write to the other".

```python
def cmd_put(args) -> int:
    data = pathlib.Path(args.host_path).read_bytes()   # the host side
    with _open(args.image) as fs:
        created = False
        try:
            fs.create(args.fs_path)
            created = True
        except AlreadyExistsError:
            fs.truncate(args.fs_path, 0)               # if it already exists, overwrite
        try:
            fs.write(args.fs_path, 0, data)
        except FSError:
            if created:
                fs.unlink(args.fs_path)    # do not leave a 0-byte file behind (chapter 7)
            raise
    return 0
```

It reads the whole host file into memory and then writes it.
`MAX_FILE_SIZE` is 48 KiB, so that is enough.

`cat` streams the byte sequence straight to standard output.

```python
def cmd_cat(args) -> int:
    with _open(args.image) as fs:
        size = fs.stat(args.fs_path).size
        sys.stdout.buffer.write(fs.read(args.fs_path, 0, size))
    return 0
```

We write to `sys.stdout.buffer`, not `sys.stdout`.
Write it as text and a character-encoding conversion creeps in and **breaks binary data**.

---

## 4. Implementation — `cli.py`

Each subcommand only "opens the image -> calls 1 or 2 APIs -> closes it".
**Not a single piece of file system processing is written here.**

Stacking the layers happens in this one place.

```python
def _open(image: str) -> FileSystem:
    # Stack them in order from chapter 1: device -> blocks -> file system
    return FileSystem(BlockDevice(SectorDevice(image)))
```

Parsing the command line is left to the standard library's **`argparse`**.
Create one `ArgumentParser`, add the mouth that accepts subcommands with `add_subparsers()`, and
`sub.add_parser("ls")` returns **a parser dedicated to `ls`**.
We declare `ls`'s arguments on it, and register the function that handles it with `set_defaults(func=...)`.

```python
def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tiny-fs")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ls", help="list a directory")       # <- p is the parser for ls
    p.add_argument("-l", "--long", action="store_true")
    p.add_argument("image")
    p.add_argument("fs_path")
    p.set_defaults(func=cmd_ls)                             # the function that handles ls

    ...                                                     # the remaining subcommands take the same shape
    return parser
```

The name you declared becomes the outlet for taking out the value, as it is.
The `tiny-fs ls -l disk.img /docs` typed in §2 is connected in 3 stages.

**(1) `argparse` parses it and builds `args`.**
Since we declared `add_argument("image")`, `"disk.img"` goes into `args.image`.
The `add_argument("-l", "--long", action="store_true")` one is a flag that takes no value, and
**if `-l` (or `--long`) is on the command line `args.long` is `True`,
and if not it is `False`**. The name of the attribute is determined by the longer spelling `--long`.

```python
Namespace(command='ls', long=True, image='disk.img', fs_path='/docs',
          func=<function cmd_ls at 0x...>)
```

**(2) `main` calls `args.func(args)`** (§5). What is registered is `cmd_ls`, so
`cmd_ls(args)` runs.

**(3) `cmd_ls` reads the fields of `args` and calls `fs.py`.**

```python
def cmd_ls(args) -> int:
    with _open(args.image) as fs:              # open "disk.img"
        entries = fs.readdir(args.fs_path)     # the contents of "/docs"
        if not args.long:                      # without -l, names only
            for name, _ino in entries:
                print(name.decode("utf-8", "replace"))
            return 0
        ...                                    # with -l, attach mode_str and the timestamp
```

However many subcommands are added, what is added is only 4 lines of `_parser()` and one `cmd_*`.

---

## 5. Handling errors

Everything that comes up from `fs.py` is `FSError`-derived and has an `errno` (appendix B).
The CLI turns that into **a one-line message and an exit status**.

```python
def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.func(args)
    except FSError as e:
        # Print the name of the errno. What happened in which layer can be traced
        name = errno.errorcode.get(e.errno, str(e.errno))
        print(f"tiny-fs: {e.path}: {e.strerror} ({name})", file=sys.stderr)
        return 1
    except OSError as e:
        # When the failure was on the host-side file, not inside the image
        print(f"tiny-fs: {e.filename}: {e.strerror}", file=sys.stderr)
        return 1
    except ValueError as e:
        # When we were handed a file that could not possibly be a tiny-fs image in the first place
        print(f"tiny-fs: {e}", file=sys.stderr)
        return 1
```

> **Python tips** — `args.func(args)` looks like doing the same work twice because
> `args.func` is **not a method, but just a function**.
> The "what is to the left of the dot goes into the first argument" we saw in chapter 3 is about functions defined in a class,
> and it does not happen for a function put into an **attribute** of `args` (an instance of `argparse.Namespace`).
>
> ```python
> args.func          # <function cmd_ls at 0x...> — cmd_ls itself
> args.func(args)    # the same as cmd_ls(args). You pass the argument yourself
> ```

The `ValueError` comes from the `device.py` of chapter 1. It rejects "a file that is not a multiple of 512"
with a `ValueError` (not an `FSError`, because it is not yet the file system layer), so
without catching it, things end in a traceback.

```
$ printf 'hello' > odd.txt
$ tiny-fs fsck ./odd.txt
tiny-fs: ./odd.txt: size 5 is not a multiple of 512
```

```
$ tiny-fs cat disk.img /nothing
tiny-fs: /nothing: No such file or directory (ENOENT)
$ echo $?
1

$ tiny-fs put disk.img ./big.bin /big.bin
tiny-fs: /big.bin: File too large (EFBIG)
$ echo $?
1

$ tiny-fs rm disk.img /docs
tiny-fs: /docs: Is a directory (EISDIR)
```

The exit status is kept to **only 0 or 1**.
`fsck` alone is given a meaning: if a problem is found it returns 1 (chapter 10).
This makes a form like `tiny-fs fsck disk.img && echo ok` possible.

---

## 6. Tests

Making `main(argv)` callable as a function
lets us test without launching a subprocess.

```python
def test_put_get_roundtrip(tmp_path, capsys):
    # get a file that was put, and the byte sequences match

def test_cat_writes_raw_bytes(tmp_path, capsys):
    # cat-ing a binary does not break it

def test_ls_lists_dot_and_dotdot(tmp_path, capsys):

def test_missing_path_returns_1_and_prints_enoent(tmp_path, capsys):
    # The exit status is 1, and ENOENT on stderr

def test_rm_on_directory_returns_eisdir(tmp_path, capsys):

def test_fsck_exit_status(tmp_path):
    # 0 if healthy, 1 if broken

def test_full_session_ends_clean(tmp_path):
    # mkfs -> mkdir -> put -> mv -> get -> rm -> rmdir -> fsck ends clean
    # The final free block count and free i-node count match those right after mkfs
```

`test_full_session_ends_clean` checks what this chapter reached, just as it is.

---

## Try it

```bash
python -c "open('sample.txt','w').write('x' * 5000)"

tiny-fs mkfs disk.img
tiny-fs mkdir disk.img /docs
tiny-fs put disk.img ./sample.txt /docs/sample.txt
tiny-fs ls -l disk.img /docs
tiny-fs get disk.img /docs/sample.txt ./copy.txt
diff ./sample.txt ./copy.txt && echo same

tiny-fs rm disk.img /docs/sample.txt
tiny-fs rmdir disk.img /docs
tiny-fs fsck disk.img           # back to the same numbers as right after mkfs
```

---

Up to here, tiny-fs is **complete as a file system**.
Create, put in, look, take out, remove, confirm. All of it can be done from commands.

What remains is the last single step
— connecting this to the OS, so that it can be touched from the real `ls`, `cat`, and editors.
