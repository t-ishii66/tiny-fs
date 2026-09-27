"""Chapter 9: dump. Reads the image, changes nothing."""

import io

from tiny_fs.constants import SUPERBLOCK_BLOCK
from tiny_fs.device import SectorDevice
from tiny_fs.disk import BlockDevice
from tiny_fs.dump import _ranges, dump, dump_raw, mode_str
from tiny_fs.fs import FileSystem

EXPECTED_AFTER_MKFS = '''\
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
'''


def _dump(image: str) -> str:
    out = io.StringIO()
    with BlockDevice(SectorDevice(image)) as blocks:
        dump(blocks, out)
    return out.getvalue()


def test_dump_after_mkfs_is_exact(image):
    assert _dump(image) == EXPECTED_AFTER_MKFS


def test_dump_lists_only_used_inodes(image):
    text = _dump(image)
    inode_lines = [line for line in text.splitlines()
                   if line.startswith("    ") and "rwx" in line]
    assert len(inode_lines) == 1          # not 128 rows, just the one in use


def test_dump_ranges_are_compressed():
    assert _ranges([0, 1, 2, 3, 7, 8]) == "0-3, 7-8"
    assert _ranges([5]) == "5"
    assert _ranges([]) == ""


def test_mode_str_matches_ls():
    assert mode_str(0o040755) == "d rwxr-xr-x"
    assert mode_str(0o100644) == "- rw-r--r--"


def test_dump_does_not_modify_the_image(image):
    before = open(image, "rb").read()
    _dump(image)
    out = io.StringIO()
    with BlockDevice(SectorDevice(image)) as blocks:
        dump_raw(blocks, 7, 64, out)
    assert open(image, "rb").read() == before


def test_dump_shows_the_diff_of_one_create(image):
    import difflib

    before = _dump(image).splitlines()
    with FileSystem(BlockDevice(SectorDevice(image))) as fs:
        fs.create("/a.txt")
        fs.write("/a.txt", 0, b"x" * 5000)
    after = _dump(image).splitlines()

    changed = [line for line in difflib.ndiff(before, after)
               if line.startswith(("+ ", "- "))]
    removed = [line for line in changed if line.startswith("- ")]
    added = [line for line in changed if line.startswith("+ ")]
    # five lines replaced (free_hint, both bitmap counts, both bitmap ranges)
    # plus two brand new rows (the i-node and the dirent)
    assert len(removed) == 5
    assert len(added) == 7
    assert "  free_hint          10" in after
    assert "    2  - rw-r--r--      1  5000  [8, 9]" in after
    assert '     2    2  "a.txt"' in after
    # what did NOT change matters too: the root still has one block
    assert "    1  d rwxr-xr-x      2  4096  [7]" in after


def test_dump_raw_is_a_hex_dump(image):
    out = io.StringIO()
    with BlockDevice(SectorDevice(image)) as blocks:
        dump_raw(blocks, 7, 64, out)
    lines = out.getvalue().splitlines()
    assert lines[0] == "block 7"
    assert lines[1] == ("00000000  01 00 00 00 2e 00 00 00 00 00 00 00 "
                        "00 00 00 00  |................|")
    assert lines[3].startswith("00000020  01 00 00 00 2e 2e 00 00")
    assert len(lines) == 5          # 64 bytes is four rows


def test_dump_survives_a_broken_magic(image):
    with BlockDevice(SectorDevice(image)) as blocks:
        buf = bytearray(blocks.read_block(SUPERBLOCK_BLOCK))
        buf[0:4] = b"\x00\x00\x00\x00"
        blocks.write_block(SUPERBLOCK_BLOCK, buf)

    text = _dump(image)          # no exception: a broken image must be viewable
    assert "magic              0x00000000" in text


def test_dump_shows_a_nested_directory(image):
    with FileSystem(BlockDevice(SectorDevice(image))) as fs:
        fs.mkdir("/docs")
        fs.create("/docs/a.txt")
    text = _dump(image)
    assert "directory /docs  (ino 2)" in text


def test_dump_of_a_zeroed_image_says_so(tmp_path):
    # Not a tiny-fs image at all. dump must report that, not fall over.
    path = tmp_path / "zero.img"
    SectorDevice.create(str(path)).close()

    out = io.StringIO()
    with BlockDevice(SectorDevice(str(path))) as blocks:
        dump(blocks, out)
    text = out.getvalue()
    assert "cannot read the rest of the layout" in text
    assert "block_size is 0" in text
