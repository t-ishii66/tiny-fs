"""Chapter 4: dirents and directories."""

import pytest

from tiny_fs import directory
from tiny_fs.constants import BLOCK_SIZE, DIRENT_SIZE, MAX_DIRENTS, NDIRECT
from tiny_fs.directory import Dirent
from tiny_fs.errors import (
    AlreadyExistsError,
    InvalidArgumentError,
    NameTooLongError,
    NoSpaceError,
    NotFoundError,
)
from tiny_fs.inode import Inode


@pytest.fixture
def directory_inode(raw_device, block_alloc) -> Inode:
    """A directory holding just "." and "..", the way mkdir leaves it."""
    inode = Inode(mode=0o040755, nlink=2, uid=0, gid=0, size=0, mtime=1,
                  ctime=1, direct=[0] * NDIRECT)
    directory.init_directory(inode, raw_device, block_alloc, 7, 1)
    return inode


def test_dirent_roundtrip():
    raw = Dirent(ino=5, name=b"memo.txt").encode()
    assert len(raw) == DIRENT_SIZE
    assert raw[:4] == b"\x05\x00\x00\x00"
    assert raw[4:12] == b"memo.txt"
    assert raw[12:] == bytes(DIRENT_SIZE - 12)   # padded with NUL, not ended
    assert Dirent.decode(raw) == Dirent(ino=5, name=b"memo.txt")


def test_add_then_lookup(directory_inode, raw_device, block_alloc):
    directory.add_entry(directory_inode, raw_device, block_alloc, b"memo.txt", 5)
    assert directory.lookup(directory_inode, raw_device, b"memo.txt") == 5


def test_lookup_missing_raises_enoent(directory_inode, raw_device):
    with pytest.raises(NotFoundError):
        directory.lookup(directory_inode, raw_device, b"nothing")


def test_name_of_exactly_27_bytes_is_ok(directory_inode, raw_device, block_alloc):
    name = b"a" * 27
    directory.add_entry(directory_inode, raw_device, block_alloc, name, 9)
    # A 27-byte name has no terminator, so the roundtrip is what proves it.
    assert directory.lookup(directory_inode, raw_device, name) == 9
    with pytest.raises(NameTooLongError):
        directory.add_entry(directory_inode, raw_device, block_alloc,
                            b"a" * 28, 10)


def test_bad_names_are_rejected(directory_inode, raw_device, block_alloc):
    for name in (b"", b"a/b", b"a\x00b"):
        with pytest.raises(InvalidArgumentError):
            directory.add_entry(directory_inode, raw_device, block_alloc, name, 9)


def test_duplicate_name_raises_eexist(directory_inode, raw_device, block_alloc):
    directory.add_entry(directory_inode, raw_device, block_alloc, b"a", 5)
    with pytest.raises(AlreadyExistsError):
        directory.add_entry(directory_inode, raw_device, block_alloc, b"a", 6)


def test_remove_keeps_following_entries_in_place(directory_inode, raw_device,
                                                 block_alloc):
    # Removing never compacts: the slot numbers behind the hole do not move.
    for i, name in enumerate((b"a", b"b", b"c")):
        directory.add_entry(directory_inode, raw_device, block_alloc, name, 10 + i)
    before = dict(
        (entry.name, slot)
        for slot, entry in directory.iter_entries(directory_inode, raw_device)
    )

    directory.remove_entry(directory_inode, raw_device, block_alloc, b"b")

    after = dict(
        (entry.name, slot)
        for slot, entry in directory.iter_entries(directory_inode, raw_device)
    )
    assert b"b" not in after
    assert after[b"c"] == before[b"c"]
    assert after[b"a"] == before[b"a"]


def test_removed_slot_is_reused(directory_inode, raw_device, block_alloc):
    for name in (b"a", b"b", b"c"):
        directory.add_entry(directory_inode, raw_device, block_alloc, name, 5)
    size_before = directory_inode.size

    directory.remove_entry(directory_inode, raw_device, block_alloc, b"b")
    directory.add_entry(directory_inode, raw_device, block_alloc, b"d", 6)

    slots = {entry.name: slot for slot, entry
             in directory.iter_entries(directory_inode, raw_device)}
    assert slots[b"d"] == 3          # the hole left by "b"
    assert directory_inode.size == size_before


def test_new_directory_is_one_block(directory_inode, raw_device):
    assert directory_inode.size == BLOCK_SIZE
    entries = list(directory.iter_entries(directory_inode, raw_device))
    assert [entry.name for _slot, entry in entries] == [b".", b".."]
    assert [entry.ino for _slot, entry in entries] == [7, 1]


def test_add_beyond_one_block_grows_the_directory(directory_inode, raw_device,
                                                  block_alloc):
    # 128 entries fit in one block ("." and ".." included).
    for i in range(126):
        directory.add_entry(directory_inode, raw_device, block_alloc,
                            f"f{i}".encode(), 10 + i)
    assert directory_inode.size == BLOCK_SIZE

    directory.add_entry(directory_inode, raw_device, block_alloc, b"one_more", 200)
    assert directory_inode.size == BLOCK_SIZE * 2
    # Everything that was there is untouched.
    assert directory.lookup(directory_inode, raw_device, b"f0") == 10
    assert directory.lookup(directory_inode, raw_device, b"f125") == 135
    assert directory.lookup(directory_inode, raw_device, b"one_more") == 200


def test_dot_and_dotdot_cannot_be_removed(directory_inode, raw_device, block_alloc):
    for name in (b".", b".."):
        with pytest.raises(InvalidArgumentError):
            directory.remove_entry(directory_inode, raw_device, block_alloc, name)


def test_is_empty_ignores_removed_slots(directory_inode, raw_device, block_alloc):
    assert directory.is_empty(directory_inode, raw_device)
    directory.add_entry(directory_inode, raw_device, block_alloc, b"a", 5)
    assert not directory.is_empty(directory_inode, raw_device)
    directory.remove_entry(directory_inode, raw_device, block_alloc, b"a")
    assert directory.is_empty(directory_inode, raw_device)


def test_set_entry_repoints_a_name(directory_inode, raw_device, block_alloc):
    directory.set_entry(directory_inode, raw_device, block_alloc, b"..", 42)
    assert directory.lookup(directory_inode, raw_device, b"..") == 42


def test_directory_full_raises_enospc(directory_inode, raw_device, block_alloc):
    # 1536 entries is where MAX_FILE_SIZE stops it.
    for i in range(MAX_DIRENTS - 2):
        directory.add_entry(directory_inode, raw_device, block_alloc,
                            f"f{i}".encode(), 10)
    assert directory_inode.size == MAX_DIRENTS * DIRENT_SIZE
    with pytest.raises(NoSpaceError):
        directory.add_entry(directory_inode, raw_device, block_alloc, b"last", 10)
