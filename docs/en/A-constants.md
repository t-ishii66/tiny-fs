# A. List of Constants

The whole of `tiny_fs/constants.py`. **No numbers are written anywhere else.**

```python
"""The constants that decide the on-disk layout."""

# --- The device (chapter 1) -----------------------------------------------
SECTOR_SIZE = 512                                    # the smallest unit the device handles
SECTORS_PER_BLOCK = 8
BLOCK_SIZE = SECTOR_SIZE * SECTORS_PER_BLOCK         # 4096

# --- The whole image (chapter 5) ------------------------------------------
TOTAL_BLOCKS = 1024                                  # 4 MiB. Fixed
TOTAL_SECTORS = TOTAL_BLOCKS * SECTORS_PER_BLOCK     # 8192

# --- Block allocation (chapter 5) -----------------------------------------
SUPERBLOCK_BLOCK = 0
BLOCK_BITMAP_BLOCK = 1
INODE_BITMAP_BLOCK = 2
INODE_TABLE_START = 3

# --- i-node (chapter 3) ---------------------------------------------------
INODE_SIZE = 128
INODES_PER_BLOCK = BLOCK_SIZE // INODE_SIZE          # 32
NUM_INODES = 128                                     # fixed
INODE_TABLE_BLOCKS = NUM_INODES // INODES_PER_BLOCK  # 4
DATA_START = INODE_TABLE_START + INODE_TABLE_BLOCKS  # 7
NDIRECT = 12
MAX_FILE_SIZE = NDIRECT * BLOCK_SIZE                 # 49152 = 48 KiB
ROOT_INO = 1                                         # 0 is the sentinel for invalid

# --- Directory (chapter 4) ------------------------------------------------
DIRENT_SIZE = 32
DIRENTS_PER_BLOCK = BLOCK_SIZE // DIRENT_SIZE        # 128
MAX_NAME_LEN = 27                                    # 32 - 4 (ino) - 1 (padding)
MAX_DIRENTS = NDIRECT * DIRENTS_PER_BLOCK            # 1536

# --- The on-disk structures -----------------------------------------------
SUPERBLOCK_FMT = "<IIIIIIII"      # 32 bytes (chapter 5)
INODE_FMT = "<HHIIQQQ12I"         # 84 bytes -> zero-padded to 128 bytes (chapter 3)
DIRENT_FMT = "<I27sx"             # 32 bytes (chapter 4)

# --- Identification -------------------------------------------------------
MAGIC = 0x74667331                # "tfs1". Raise it to tfs2 if the layout changes
```

---

## The grounds for the derivations

| Constant | Value | Why that value | First appearance |
|---|---|---|---|
| `SECTOR_SIZE` | 512 | The device decides it. Not something we can choose | chapter 1 |
| `BLOCK_SIZE` | 4096 | The management information becomes one eighth. The same as the OS's page size | chapter 1 |
| `INODE_SIZE` | 128 | What is used is 84 bytes. The smallest power of 2 that divides `BLOCK_SIZE` evenly | chapter 3 |
| `INODES_PER_BLOCK` | 32 | 4096 / 128. It divides evenly so an i-node does not straddle a boundary | chapter 3 |
| `NDIRECT` | 12 | The number of entries chosen while leaving room in the 128-byte frame. The limit for one file is decided here | chapter 3 |
| `MAX_FILE_SIZE` | 48 KiB | 12 × 4096. With no indirect block, this is the limit | chapter 3 |
| `DIRENT_SIZE` | 32 | ino 4 + name 27 + padding 1. A divisor of `BLOCK_SIZE` | chapter 4 |
| `MAX_NAME_LEN` | 27 | 32 - 4 - 1 | chapter 4 |
| `TOTAL_BLOCKS` | 1024 | 4 MiB. It fits within the range of up to 32768 that one bitmap can express | chapter 5 |
| `NUM_INODES` | 128 | The table is exactly 4 blocks | chapter 5 |
| `ROOT_INO` | 1 | So that 0 can be used as the sentinel for "invalid" | chapter 3 |
| `MAGIC` | `0x74667331` | `"tfs1"` turned into an integer | chapter 5 |

---

## Capacity

| Item | Value | Derivation |
|---|---|---|
| Image size | 4 MiB | 4096 × 1024 |
| Metadata | 28 KiB | 7 blocks (superblock + 2 bitmaps + 4 i-node table) |
| Data region | 1017 blocks ≈ 3.97 MiB | 1024 - 7 |
| Maximum for one file | 48 KiB | `NDIRECT` × `BLOCK_SIZE` |
| Maximum number of files / directories | 127 | `NUM_INODES` - 1 (the root) |
| Maximum entries in one directory | 1536 | 12 × 128 (including `.` `..`) |
| Limit of one bitmap | 32768 blocks = 128 MiB | 4096 × 8 bits |
