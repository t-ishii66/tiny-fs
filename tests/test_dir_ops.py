"""Chapter 8: mkdir, rmdir, readdir, rename."""

import pytest

from tiny_fs.constants import BLOCK_SIZE
from tiny_fs.errors import (
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


def test_rename_replaces_an_existing_file(fs):
    # rename(2) replaces the destination: mv a b works even if b is there.
    fs.create("/a.txt")
    fs.write("/a.txt", 0, b"source")
    fs.create("/b.txt")
    fs.write("/b.txt", 0, b"x" * 5000)      # 2 blocks, about to be freed
    ino = fs.lookup("/a.txt")
    free_blocks, free_inodes = fs.count_free_blocks(), fs.count_free_inodes()

    fs.rename("/a.txt", "/b.txt")

    assert fs.lookup("/b.txt") == ino
    assert fs.read("/b.txt", 0, 100) == b"source"
    assert [name for name, _ino in fs.readdir("/")] == [b".", b"..", b"b.txt"]
    # the replaced file gave back its i-node and both of its blocks
    assert fs.count_free_blocks() == free_blocks + 2
    assert fs.count_free_inodes() == free_inodes + 1


def test_rename_replaces_an_existing_file_across_directories(fs):
    fs.mkdir("/docs")
    fs.create("/a.txt")
    fs.write("/a.txt", 0, b"source")
    fs.create("/docs/a.txt")
    free_inodes = fs.count_free_inodes()

    fs.rename("/a.txt", "/docs/a.txt")

    assert fs.read("/docs/a.txt", 0, 100) == b"source"
    assert fs.readdir("/") == [(b".", 1), (b"..", 1), (b"docs", fs.lookup("/docs"))]
    assert fs.count_free_inodes() == free_inodes + 1


def test_rename_replaces_an_empty_directory(fs):
    fs.mkdir("/a")
    fs.mkdir("/b")
    fs.create("/a/inside.txt")
    ino = fs.lookup("/a")
    root_nlink = fs.stat("/").nlink          # 2 + 2 children = 4

    fs.rename("/a", "/b")

    assert fs.lookup("/b") == ino
    assert fs.read("/b/inside.txt", 0, 1) == b""
    assert fs.stat("/").nlink == root_nlink - 1     # one child directory left
    assert fs.lookup("/b/..") == fs.superblock.root_ino


def test_rename_over_a_non_empty_directory_raises_enotempty(fs):
    fs.mkdir("/a")
    fs.mkdir("/b")
    fs.create("/b/kid.txt")
    with pytest.raises(NotEmptyError):
        fs.rename("/a", "/b")


def test_rename_a_file_over_a_directory_raises_eisdir(fs):
    fs.create("/a.txt")
    fs.mkdir("/b")
    with pytest.raises(IsADirectoryError):
        fs.rename("/a.txt", "/b")


def test_rename_a_directory_over_a_file_raises_enotdir(fs):
    fs.mkdir("/a")
    fs.create("/b.txt")
    with pytest.raises(NotADirectoryError):
        fs.rename("/a", "/b.txt")


def test_a_refused_rename_changes_nothing(fs):
    fs.mkdir("/a")
    fs.mkdir("/b")
    fs.create("/b/kid.txt")
    before = (fs.count_free_blocks(), fs.count_free_inodes())
    with pytest.raises(NotEmptyError):
        fs.rename("/a", "/b")
    assert (fs.count_free_blocks(), fs.count_free_inodes()) == before
    assert fs.lookup("/a") and fs.lookup("/b/kid.txt")


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
