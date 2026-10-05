"""Chapter 11: the command line. main(argv) is called directly."""

import pytest

from tiny_fs.cli import main
from tiny_fs.fs import mkfs


@pytest.fixture
def img(tmp_path) -> str:
    # Made with the library, not the command, so nothing is on stdout yet.
    path = str(tmp_path / "disk.img")
    mkfs(path)
    return path


def test_mkfs_reports_what_it_made(tmp_path, capsys):
    path = str(tmp_path / "disk.img")
    assert main(["mkfs", path]) == 0
    out = capsys.readouterr().out
    assert out == f"created {path}: 1024 blocks (4 MiB), 128 inodes\n"


def test_put_get_roundtrip(img, tmp_path, capsys):
    host = tmp_path / "host.bin"
    host.write_bytes(bytes(range(256)) * 20)
    copy = tmp_path / "copy.bin"

    assert main(["put", img, str(host), "/a.bin"]) == 0
    assert main(["get", img, "/a.bin", str(copy)]) == 0
    assert copy.read_bytes() == host.read_bytes()


def test_put_overwrites_an_existing_file(img, tmp_path):
    host = tmp_path / "host.txt"
    host.write_bytes(b"first version, longer")
    main(["put", img, str(host), "/a.txt"])
    host.write_bytes(b"second")
    assert main(["put", img, str(host), "/a.txt"]) == 0

    out = tmp_path / "out.txt"
    main(["get", img, "/a.txt", str(out)])
    assert out.read_bytes() == b"second"


def test_cat_writes_raw_bytes(img, tmp_path, capsysbinary):
    # Binary in, the same binary out: writing to sys.stdout instead of
    # sys.stdout.buffer would mangle these bytes.
    host = tmp_path / "host.bin"
    host.write_bytes(b"\x00\x01\xfe\xff\nline\n")
    main(["put", img, str(host), "/a.bin"])
    capsysbinary.readouterr()

    assert main(["cat", img, "/a.bin"]) == 0
    assert capsysbinary.readouterr().out == b"\x00\x01\xfe\xff\nline\n"


def test_ls_lists_dot_and_dotdot(img, capsys):
    main(["mkdir", img, "/docs"])
    capsys.readouterr()
    assert main(["ls", img, "/"]) == 0
    assert capsys.readouterr().out == ".\n..\ndocs\n"


def test_ls_long_shows_mode_nlink_size(img, capsys):
    main(["mkdir", img, "/docs"])
    capsys.readouterr()
    assert main(["ls", "-l", img, "/docs"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("d rwxr-xr-x  2  4096  ")
    assert lines[0].endswith("  .")
    assert lines[1].startswith("d rwxr-xr-x  3  4096  ")     # the root, 2 + 1 child


def test_missing_path_returns_1_and_prints_enoent(img, capsys):
    assert main(["cat", img, "/nothing"]) == 1
    err = capsys.readouterr().err
    assert err == "tiny-fs: /nothing: No such file or directory (ENOENT)\n"


def test_rm_on_directory_returns_eisdir(img, capsys):
    main(["mkdir", img, "/docs"])
    capsys.readouterr()
    assert main(["rm", img, "/docs"]) == 1
    assert "(EISDIR)" in capsys.readouterr().err


def test_missing_host_file_returns_1(img, tmp_path, capsys):
    assert main(["put", img, str(tmp_path / "nope"), "/a.txt"]) == 1
    assert "No such file" in capsys.readouterr().err


def test_fsck_exit_status(img, tmp_path, capsys):
    assert main(["fsck", img]) == 0
    assert capsys.readouterr().out == "fsck: clean (8 blocks used, 1 inodes used)\n"

    from tiny_fs.device import SectorDevice
    from tiny_fs.disk import BlockDevice

    with BlockDevice(SectorDevice(img)) as blocks:
        buf = bytearray(blocks.read_block(1))
        buf[2] |= 0x01
        blocks.write_block(1, buf)

    assert main(["fsck", img]) == 1
    out = capsys.readouterr().out
    assert out.startswith("fsck: 1 problems found")
    assert "leak" in out


def test_dump_raw_prints_one_block(img, capsys):
    assert main(["dump", img, "--raw", "7", "--length", "64"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "block 7"
    assert len(lines) == 5


def test_full_session_ends_clean(img, tmp_path, capsys):
    host = tmp_path / "README.md"
    host.write_bytes(b"# readme\n" * 100)
    copy = tmp_path / "copy.md"

    assert main(["mkdir", img, "/docs"]) == 0
    assert main(["put", img, str(host), "/docs/readme.md"]) == 0
    assert main(["mv", img, "/docs/readme.md", "/readme.md"]) == 0
    assert main(["get", img, "/readme.md", str(copy)]) == 0
    assert copy.read_bytes() == host.read_bytes()
    assert main(["rm", img, "/readme.md"]) == 0
    assert main(["rmdir", img, "/docs"]) == 0
    capsys.readouterr()

    assert main(["fsck", img]) == 0
    # back to the numbers mkfs left behind
    assert capsys.readouterr().out == "fsck: clean (8 blocks used, 1 inodes used)\n"


def test_a_file_that_cannot_be_an_image_returns_1(tmp_path, capsys):
    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"hello")
    assert main(["fsck", str(junk)]) == 1
    assert "not a multiple of 512" in capsys.readouterr().err


def test_a_missing_image_returns_1(tmp_path, capsys):
    assert main(["dump", str(tmp_path / "nope.img")]) == 1
    assert "No such file" in capsys.readouterr().err


def test_put_too_big_names_the_path_and_leaves_nothing(img, tmp_path, capsys):
    # The error comes from inode.py, which knows no path: fs.py must put it back.
    host = tmp_path / "big.bin"
    host.write_bytes(b"x" * 60000)

    assert main(["put", img, str(host), "/big.bin"]) == 1
    assert capsys.readouterr().err == "tiny-fs: /big.bin: File too large (EFBIG)\n"

    # The file create() made is gone again, so the image is as it was.
    assert main(["ls", img, "/"]) == 0
    assert capsys.readouterr().out == ".\n..\n"
    assert main(["fsck", img]) == 0
    assert capsys.readouterr().out == "fsck: clean (8 blocks used, 1 inodes used)\n"
