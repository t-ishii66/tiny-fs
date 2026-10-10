# Part III  Observation and Verification

---

# 09. Peeking Inside the Image — dump

![Alice and Bob peeking into a treasure chest](../images/09-alice-bob.png)

In Part II, the operations of a file system became possible all round.
However, the only means of confirming the result is still that same API.
The confirmation that what was `write`-ten comes back with `read`
**will pass if both are wrong in the same way**.

So we build a tool that reads the image directly and shows it to a human.
`dump.py` **only reads** the disk, and does not rewrite a single byte.

---

## 1. Looking at it right after mkfs

The output is lined up in the order of the layout decided in chapter 5 — the superblock, the two bitmaps,
the i-node table, and then the contents of the directories that can be reached.
Lining up all 4096 bytes of a bitmap cannot be read by a human, so
we output **the indices in use, gathered into ranges**.
The same for the i-node table: we output **only the ones in use**, not all 128.

```
$ tiny-fs mkfs disk.img
$ tiny-fs dump disk.img

superblock (block 0)
  magic              0x74667331 "tfs1"
  block_size         4096
  total_blocks       1024
  num_inodes         128
  inode_table_start  3
  data_start         7
  root_ino           1
  free_hint          8

block bitmap (block 1)
  used    8 / 1024    free 1016
  ranges  0-7

inode bitmap (block 2)
  used    2 / 129     free 127     (bit 0 is reserved)
  ranges  0-1

inode table (block 3-6)
  ino  mode         nlink  size  blocks
    1  d rwxr-xr-x      2  4096  [7]

directory /  (ino 1)
  slot  ino  name
     0    1  "."
     1    1  ".."
```

The state explained with figures and hex dumps in chapter 5 is output just as it is.

- The 8 blocks in use are the superblock, the 2 bitmaps, the 4 i-node table blocks, and
  the 1 data block of the root
- The i-node bitmap's `used 2` is the reserved bit 0 and the root's ino 1
- The root's `size` being 4096 is because one block's worth is allocated (chapter 4).
  The contents are only 2 entries

---

## 2. Creating one file and looking at the difference

Save `dump`'s output to a file and take a `diff`, and
where on the disk one operation changed things can be read off directly.

```
$ tiny-fs dump disk.img > before.txt
$ tiny-fs put disk.img ./memo.txt /a.txt      # 5000 bytes
$ tiny-fs dump disk.img > after.txt
$ diff before.txt after.txt
```

Only 5 places change.

| What changed | before | after |
|---|---|---|
| `free_hint` | 8 | 10 |
| The number of blocks in use | used 8, `ranges 0-7` | used 10, `ranges 0-9` |
| The number of i-nodes in use | used 2, `ranges 0-1` | used 3, `ranges 0-2` |
| The i-node table | ino 1 only | ino 2 is added |
| The contents of the root | `.` and `..` | `a.txt` is added at slot 2 |

The relevant part on the after side looks like this.

```
inode table (block 3-6)
  ino  mode         nlink  size  blocks
    1  d rwxr-xr-x      2  4096  [7]
    2  - rw-r--r--      1  5000  [8, 9]

directory /  (ino 1)
  slot  ino  name
     0    1  "."
     1    1  ".."
     2    2  "a.txt"
```

**What did not change** is the more important thing in this diff.

- The root's `size` stays 4096. Even with one more entry,
  a free slot is used so the capacity does not grow (chapter 4)
- The root's `direct[0]` stays 7. No data block was added
- The 5000-byte file uses 2 blocks.
  The second one uses only 904 bytes, but allocation is in units of one (chapter 3)

---

## 3. Looking at the raw bytes

For when you want to look at it in hexadecimal without interpretation, we make it possible to specify a block number.

```
$ tiny-fs dump disk.img --raw 7 --length 64
block 7
00000000  01 00 00 00 2e 00 00 00 00 00 00 00 00 00 00 00  |................|
00000010  00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00  |................|
00000020  01 00 00 00 2e 2e 00 00 00 00 00 00 00 00 00 00  |................|
00000030  00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00  |................|
```

All the hex dumps shown in the chapters up to here can be output in this form.
**Being able to go back and forth between the interpreted display and the raw bytes** is the role of this tool.

---

## 4. Implementation — `dump.py`

It does not use `FileSystem`; it reads directly from `BlockDevice`.
Since we want to be able to peek into a broken image too,
the validation of the superblock does not abort (even if the `magic` differs, it displays that value and continues).

However, if `total_blocks` or `inode_table_start` is broken,
the position calculation for i-nodes itself does not hold up (it would try to read outside the image).
In that case it displays only the superblock, adds one line with the reason, and ends the processing.

```python
def dump(blocks: BlockDevice, out) -> None:
    sb = Superblock.decode(blocks.read_block(SUPERBLOCK_BLOCK))
    broken = superblock_problems(sb, blocks.num_blocks, check_magic=False)
    if broken:
        ...        # display only the superblock and the reason, and return
    sections = [
        _superblock(sb),
        _bitmap(blocks, BLOCK_BITMAP_BLOCK, sb.total_blocks, "block bitmap", None),
        _bitmap(blocks, INODE_BITMAP_BLOCK, sb.num_inodes + 1, "inode bitmap",
                "(bit 0 is reserved)"),
        _inodes(blocks, sb),
        *_directories(blocks, sb),        # as many as there are reachable directories
    ]
    # Each function only returns a list of lines. The separating blank line is inserted in one place
    print("\n\n".join("\n".join(lines) for lines in sections), file=out)
```

The part that arranges the display is closed inside `dump.py`. For the indices in use, `_ranges`
gathers them into the form `[0,1,2,3,7,8]` -> `"0-3, 7-8"`, and for `mode`, `mode_str` makes it
the same "1 character of type + 9 characters of permissions" as `ls -l`.
`mode_str` is also called from the `ls -l` of chapter 11, so we make it a public function of `dump.py`.

---

## 5. Tests

`dump` is a function that produces a string, so **the output itself** can be compared.

```python
def test_dump_after_mkfs_is_exact(tmp_path):
    # The output right after mkfs matches the expected string exactly

def test_dump_lists_only_used_inodes(tmp_path):
    # Not 128 lines, but only the lines in use come out

def test_dump_ranges_are_compressed():
    # _ranges([0,1,2,3,7,8]) == "0-3, 7-8"

def test_dump_does_not_modify_the_image(tmp_path):
    # Not a single byte of the image's byte sequence changes before and after dump

def test_dump_shows_the_diff_of_one_create(tmp_path):
    # Before and after a create, the lines that change are only the expected 5 places

def test_dump_survives_a_broken_magic(tmp_path):
    # Break the magic and it does not raise, but displays that value and continues
```

Always include `test_dump_does_not_modify_the_image`. **A tool that is only supposed to peek writing something**
is the way of breaking that is most likely to happen, and hardest to notice, with this kind of tool.

---

## Try it

```bash
tiny-fs mkfs disk.img
tiny-fs dump disk.img > before.txt

python -c "open('sample.txt','w').write('x' * 5000)"
tiny-fs put disk.img ./sample.txt /a.txt
tiny-fs dump disk.img > after.txt
diff before.txt after.txt                     # the same 5 places as the table in "2. Creating one file and looking at the difference" come out

# Go back and forth between the interpreted display and the raw bytes
tiny-fs dump disk.img --raw 3 --length 128    # the root's i-node
```

---

In the next chapter we develop this "looking" tool into a "checking" tool.
Do the blocks said to be in use on the bitmap, and the blocks actually referenced, agree?
