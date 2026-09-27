"""Chapter 8: mkdir, rmdir, readdir, rename."""

import pytest

from tiny_fs.constants import BLOCK_SIZE
from tiny_fs.errors import (
    AlreadyExistsError,
    InvalidArgumentError,
    IsADirectoryError,
    NotADirectoryError,
    NotEmptyError,
)


def test_mkdir_has_dot_and_dotdot(fs):
    fs.mkdir("/docs")
    assert fs.stat("/docs").size == BLOCK_SIZE
    assert fs.readdir("/docs") == [(b".", fs.lookup("/docs")),
                                   (b"..", fs.superblock.root_ino)]


def test_mkdir_updates_nlink(fs):
    root_nlink = fs.stat("/").nlink
    fs.mkdir("/docs")
    assert fs.stat("/docs").nlink == 2          # its own "." and its name
    assert fs.stat("/").nlink == root_nlink + 1  # the child's ".."


def test_mkdir_in_a_missing_directory_raises_enoent(fs):
    from tiny_fs.errors import NotFoundError

    with pytest.raises(NotFoundError):
        fs.mkdir("/nope/deep")


def test_rmdir_restores_nlink_and_frees(fs):
    free_blocks, free_inodes = fs.count_free_blocks(), fs.count_free_inodes()
    root_nlink = fs.stat("/").nlink

    fs.mkdir("/docs")
    fs.rmdir("/docs")

    assert fs.stat("/").nlink == root_nlink
    assert fs.count_free_blocks() == free_blocks
    assert fs.count_free_inodes() == free_inodes


def test_rmdir_non_empty_raises_enotempty(fs):
    fs.mkdir("/docs")
    fs.create("/docs/a.txt")
    with pytest.raises(NotEmptyError):
        fs.rmdir("/docs")


def test_rmdir_after_emptying_succeeds(fs):
    # The size never shrinks, so "empty" cannot be decided from size: what
    # counts is whether any live entry other than "." and ".." is left.
    fs.mkdir("/docs")
    fs.create("/docs/a.txt")
    fs.unlink("/docs/a.txt")
    assert fs.stat("/docs").size == BLOCK_SIZE
    fs.rmdir("/docs")


def test_rmdir_on_file_raises_enotdir(fs):
    fs.create("/a.txt")
    with pytest.raises(NotADirectoryError):
        fs.rmdir("/a.txt")


def test_unlink_on_directory_raises_eisdir(fs):
    fs.mkdir("/docs")
    with pytest.raises(IsADirectoryError):
        fs.unlink("/docs")


def test_root_cannot_be_removed(fs):
    with pytest.raises(InvalidArgumentError):
        fs.rmdir("/")


def test_readdir_on_a_file_raises_enotdir(fs):
    fs.create("/a.txt")
    with pytest.raises(NotADirectoryError):
        fs.readdir("/a.txt")


def test_rename_within_same_directory(fs):
    fs.create("/a.txt")
    fs.write("/a.txt", 0, b"hello")
    ino = fs.lookup("/a.txt")

    fs.rename("/a.txt", "/b.txt")

    assert fs.lookup("/b.txt") == ino          # the i-node did not move
    assert fs.read("/b.txt", 0, 10) == b"hello"
    assert [name for name, _ino in fs.readdir("/")] == [b".", b"..", b"b.txt"]


def test_rename_across_directories_fixes_dotdot(fs):
    fs.mkdir("/docs")
    fs.mkdir("/archive")
    docs_ino = fs.lookup("/docs")
    root_nlink = fs.stat("/").nlink
    archive_nlink = fs.stat("/archive").nlink

    fs.rename("/docs", "/archive/docs")

    assert fs.lookup("/archive/docs") == docs_ino
    # ".." now points at the new parent, so this walks back to /archive
    assert fs.lookup("/archive/docs/..") == fs.lookup("/archive")
    assert fs.stat("/").nlink == root_nlink - 1
    assert fs.stat("/archive").nlink == archive_nlink + 1


def test_rename_a_file_across_directories(fs):
    fs.mkdir("/docs")
    fs.create("/a.txt")
    fs.write("/a.txt", 0, b"body")
    root_nlink = fs.stat("/").nlink

    fs.rename("/a.txt", "/docs/a.txt")

    assert fs.read("/docs/a.txt", 0, 10) == b"body"
    assert fs.stat("/").nlink == root_nlink     # only directories move nlink


def test_rename_into_descendant_raises_einval(fs):
    fs.mkdir("/a")
    fs.mkdir("/a/b")
    with pytest.raises(InvalidArgumentError):
        fs.rename("/a", "/a/b/c")
    with pytest.raises(InvalidArgumentError):
        fs.rename("/a", "/a/c")


def test_rename_to_existing_name_raises_eexist(fs):
    fs.create("/a.txt")
    fs.create("/b.txt")
    with pytest.raises(AlreadyExistsError):
        fs.rename("/a.txt", "/b.txt")


def test_rename_of_dot_is_rejected(fs):
    fs.mkdir("/docs")
    with pytest.raises(InvalidArgumentError):
        fs.rename("/docs/.", "/docs/x")


def test_rename_to_the_same_name_is_a_no_op(fs):
    fs.create("/a.txt")
    fs.rename("/a.txt", "/a.txt")
    assert [name for name, _ino in fs.readdir("/")] == [b".", b"..", b"a.txt"]


def test_stat_reports_what_ls_needs(fs):
    fs.create("/a.txt")
    fs.write("/a.txt", 0, b"12345")
    inode = fs.stat("/a.txt")
    assert inode.size == 5 and inode.nlink == 1 and inode.mode & 0o777 == 0o644
