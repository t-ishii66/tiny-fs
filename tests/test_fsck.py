"""Chapter 10: fsck. Two independent views of the same fact."""

from tiny_fs.constants import (
    BLOCK_BITMAP_BLOCK,
    INODE_BITMAP_BLOCK,
    SUPERBLOCK_BLOCK,
)
from tiny_fs.device import SectorDevice
from tiny_fs.disk import BlockDevice
from tiny_fs.dump import read_inode
from tiny_fs.fs import FileSystem, Superblock
from tiny_fs.fsck import fsck, summary


def _blocks(image: str) -> BlockDevice:
    return BlockDevice(SectorDevice(image))


def _check(image: str) -> list[str]:
    with _blocks(image) as blocks:
        return fsck(blocks)


def _patch_inode(image: str, ino: int, **fields) -> None:
    """Rewrite one i-node in place, without going through FileSystem."""
    with _blocks(image) as blocks:
        sb = Superblock.decode(blocks.read_block(SUPERBLOCK_BLOCK))
        inode = read_inode(blocks, sb, ino)
        for name, value in fields.items():
            setattr(inode, name, value)
        with FileSystem(_blocks(image)) as fs:
            fs.write_inode(ino, inode)


def test_fsck_clean_after_mkfs(image):
    assert _check(image) == []
    with _blocks(image) as blocks:
        assert summary(blocks) == (8, 1)


def test_fsck_clean_after_many_operations(image):
    with FileSystem(_blocks(image)) as fs:
        fs.mkdir("/docs")
        fs.create("/docs/a.txt")
        fs.write("/docs/a.txt", 0, b"x" * 9000)
        fs.mkdir("/docs/sub")
        fs.create("/b.txt")
        fs.rename("/b.txt", "/docs/sub/b.txt")
        fs.unlink("/docs/a.txt")
    assert _check(image) == []


def test_detects_leaked_block(image):
    # One bit in the bitmap is enough to invent a block nobody refers to.
    with _blocks(image) as blocks:
        buf = bytearray(blocks.read_block(BLOCK_BITMAP_BLOCK))
        buf[2] |= 0x01                       # block 16 now looks used
        blocks.write_block(BLOCK_BITMAP_BLOCK, buf)

    problems = _check(image)
    assert any("leak" in p for p in problems)


def test_detects_block_marked_free_while_in_use(image):
    with FileSystem(_blocks(image)) as fs:
        fs.create("/a.txt")
        fs.write("/a.txt", 0, b"x")
        block_no = fs.stat("/a.txt").direct[0]
    with _blocks(image) as blocks:
        buf = bytearray(blocks.read_block(BLOCK_BITMAP_BLOCK))
        buf[block_no // 8] &= ~(1 << (block_no % 8)) & 0xFF
        blocks.write_block(BLOCK_BITMAP_BLOCK, buf)

    assert any("marked free in the block bitmap" in p for p in _check(image))


def test_detects_double_reference(image):
    with FileSystem(_blocks(image)) as fs:
        fs.create("/a.txt")
        fs.write("/a.txt", 0, b"a")
        fs.create("/b.txt")
        fs.write("/b.txt", 0, b"b")
        shared = fs.stat("/a.txt").direct[0]
        b_ino = fs.lookup("/b.txt")
        inode = fs.read_inode(b_ino)
        inode.direct[0] = shared              # both files now claim one block
        fs.write_inode(b_ino, inode)

    assert any("double use" in p for p in _check(image))


def test_detects_orphan_inode(image):
    with _blocks(image) as blocks:
        buf = bytearray(blocks.read_block(INODE_BITMAP_BLOCK))
        buf[1] |= 0x01                        # ino 8 is used, but unreachable
        blocks.write_block(INODE_BITMAP_BLOCK, buf)

    assert any("orphan" in p for p in _check(image))


def test_detects_wrong_nlink(image):
    with FileSystem(_blocks(image)) as fs:
        fs.create("/a.txt")
        ino = fs.lookup("/a.txt")
    _patch_inode(image, ino, nlink=2)

    assert any("nlink=2" in p for p in _check(image))


def test_detects_block_number_out_of_range(image):
    with FileSystem(_blocks(image)) as fs:
        fs.create("/a.txt")
        ino = fs.lookup("/a.txt")
        inode = fs.read_inode(ino)
        inode.direct[0] = 5                   # a metadata block, not data
        inode.size = 10
        fs.write_inode(ino, inode)

    assert any("outside the data area" in p for p in _check(image))


def test_detects_bits_past_the_end_of_the_bitmap(image):
    with _blocks(image) as blocks:
        buf = bytearray(blocks.read_block(BLOCK_BITMAP_BLOCK))
        buf[200] = 0x01                       # block 1600 does not exist
        blocks.write_block(BLOCK_BITMAP_BLOCK, buf)

    assert any("past index" in p for p in _check(image))


def test_survives_a_cycle_in_dotdot(image):
    # Point /docs/.. at /docs itself. fsck has to finish and complain, not hang.
    with FileSystem(_blocks(image)) as fs:
        fs.mkdir("/docs")
        docs_ino = fs.lookup("/docs")
        from tiny_fs import directory

        inode = fs.read_inode(docs_ino)
        directory.set_entry(inode, fs.blocks, fs.blocks_alloc, b"..", docs_ino)
        fs.write_inode(docs_ino, inode)

    problems = _check(image)
    assert problems                            # it noticed something
    assert any("nlink" in p for p in problems)


def test_create_and_delete_returns_to_clean(image):
    # A leak shows up only when repeated. 100 rounds make the drift visible.
    with FileSystem(_blocks(image)) as fs:
        before = (fs.count_free_blocks(), fs.count_free_inodes())
        for i in range(100):
            fs.create("/scratch")
            fs.write("/scratch", 0, b"x" * 5000)
            fs.mkdir("/dir")
            fs.unlink("/scratch")
            fs.rmdir("/dir")
        assert (fs.count_free_blocks(), fs.count_free_inodes()) == before
    assert _check(image) == []


def test_fsck_reports_a_broken_superblock(tmp_path):
    # A zeroed file: the layout cannot be followed, so say that and stop.
    path = tmp_path / "zero.img"
    SectorDevice.create(str(path)).close()

    with BlockDevice(SectorDevice(str(path))) as blocks:
        problems = fsck(blocks)
        assert summary(blocks) == (0, 0)
    assert any("magic" in p for p in problems)
    assert any("block_size" in p for p in problems)


def test_fsck_still_checks_an_image_with_only_a_bad_magic(image):
    # The geometry is fine, so the real checks still run.
    with BlockDevice(SectorDevice(image)) as blocks:
        buf = bytearray(blocks.read_block(SUPERBLOCK_BLOCK))
        buf[0:4] = b"tfs9"
        blocks.write_block(SUPERBLOCK_BLOCK, buf)

    problems = _check(image)
    assert len(problems) == 1 and "magic" in problems[0]
