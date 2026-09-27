"""Chapter 7: fs.py. Files, from a path."""

import pytest

from tiny_fs.constants import BLOCK_SIZE, MAX_FILE_SIZE, NUM_INODES
from tiny_fs.device import SectorDevice
from tiny_fs.disk import BlockDevice
from tiny_fs.errors import (
    AlreadyExistsError,
    CorruptionError,
    InvalidArgumentError,
    IsADirectoryError,
    NoSpaceError,
    NotADirectoryError,
    NotFoundError,
)
from tiny_fs.fs import FileSystem, split_path


def test_split_path():
    assert split_path("/") == []
    assert split_path("/docs/a.txt") == [b"docs", b"a.txt"]
    assert split_path("//docs///a.txt") == [b"docs", b"a.txt"]
    assert split_path("/docs/") == [b"docs"]
    with pytest.raises(InvalidArgumentError):
        split_path("docs/a.txt")


def test_lookup_dot_and_dotdot(fs):
    fs.mkdir("/docs")
    assert fs.lookup("/docs/..") == fs.superblock.root_ino
    assert fs.lookup("/..") == fs.superblock.root_ino
    assert fs.lookup("//docs///") == fs.lookup("/docs")


def test_lookup_parent_of_root_raises_einval(fs):
    with pytest.raises(InvalidArgumentError):
        fs.lookup_parent("/")


def test_lookup_through_a_file_raises_enotdir(fs):
    fs.create("/memo.txt")
    with pytest.raises(NotADirectoryError):
        fs.lookup("/memo.txt/x")


def test_create_then_read_back(fs):
    fs.create("/a.txt")
    fs.write("/a.txt", 0, b"hello")
    assert fs.read("/a.txt", 0, 100) == b"hello"
    assert fs.stat("/a.txt").nlink == 1


def test_written_data_survives_remount(image):
    # The only test that can catch a missing write_inode: a fresh FileSystem
    # has to read it back off the disk.
    with FileSystem(BlockDevice(SectorDevice(image))) as fs:
        fs.create("/a.txt")
        fs.write("/a.txt", 0, b"x" * 5000)

    with FileSystem(BlockDevice(SectorDevice(image))) as fs:
        assert fs.stat("/a.txt").size == 5000
        assert fs.read("/a.txt", 0, 5000) == b"x" * 5000


def test_create_existing_name_raises_eexist(fs):
    fs.create("/a.txt")
    with pytest.raises(AlreadyExistsError):
        fs.create("/a.txt")


def test_create_rollback_on_failure(fs):
    # The second create takes an i-node, then fails to attach a name. The
    # i-node has to come back, or every failed create would lose one.
    fs.create("/a.txt")
    free_inodes = fs.count_free_inodes()
    with pytest.raises(AlreadyExistsError):
        fs.create("/a.txt")
    assert fs.count_free_inodes() == free_inodes


def test_write_grows_size_and_allocates_blocks(fs):
    free_blocks = fs.count_free_blocks()
    fs.create("/a.txt")
    fs.write("/a.txt", 0, b"x" * 5000)

    inode = fs.stat("/a.txt")
    assert inode.size == 5000
    assert len([b for b in inode.direct if b]) == 2
    assert fs.count_free_blocks() == free_blocks - 2


def test_write_at_an_offset(fs):
    fs.create("/a.txt")
    fs.write("/a.txt", 0, b"a" * BLOCK_SIZE)
    fs.write("/a.txt", 100, b"b" * 10)
    data = fs.read("/a.txt", 0, BLOCK_SIZE)
    assert data[99] == ord("a") and data[100:110] == b"b" * 10


def test_truncate_shrinks_and_frees(fs):
    fs.create("/a.txt")
    fs.write("/a.txt", 0, b"x" * (BLOCK_SIZE * 3))
    free_blocks = fs.count_free_blocks()

    fs.truncate("/a.txt", 10)

    assert fs.stat("/a.txt").size == 10
    assert fs.count_free_blocks() == free_blocks + 2


def test_unlink_frees_blocks_and_inode(fs):
    free_blocks, free_inodes = fs.count_free_blocks(), fs.count_free_inodes()
    fs.create("/a.txt")
    fs.write("/a.txt", 0, b"x" * 5000)
    fs.unlink("/a.txt")

    assert fs.count_free_blocks() == free_blocks
    assert fs.count_free_inodes() == free_inodes
    with pytest.raises(NotFoundError):
        fs.read("/a.txt", 0, 1)


def test_unlink_missing_raises_enoent(fs):
    with pytest.raises(NotFoundError):
        fs.unlink("/nothing")


def test_read_directory_raises_eisdir(fs):
    with pytest.raises(IsADirectoryError):
        fs.read("/", 0, 10)


def test_write_beyond_max_file_size_raises_efbig(fs):
    from tiny_fs.errors import FileTooBigError

    fs.create("/a.txt")
    fs.write("/a.txt", 0, b"x" * MAX_FILE_SIZE)
    with pytest.raises(FileTooBigError):
        fs.write("/a.txt", MAX_FILE_SIZE, b"x")


def test_disk_full_raises_enospc(fs):
    # 48 KiB at a time, the data area runs out before the i-nodes do.
    data = b"x" * MAX_FILE_SIZE
    with pytest.raises(NoSpaceError):
        for i in range(200):
            fs.create(f"/f{i}")
            fs.write(f"/f{i}", 0, data)
    assert fs.count_free_blocks() == 0


def test_inodes_exhausted_raises_enospc(fs):
    # 127 files fit beside the root, and the 128th does not.
    for i in range(NUM_INODES - 1):
        fs.create(f"/f{i}")
    assert fs.count_free_inodes() == 0
    with pytest.raises(NoSpaceError):
        fs.create("/one_too_many")


def test_bad_inode_number_raises_corruption(fs):
    with pytest.raises(CorruptionError):
        fs.read_inode(0)
    with pytest.raises(CorruptionError):
        fs.read_inode(NUM_INODES + 1)
