"""Layout constants and struct formats. No numbers live outside this file."""

# --- the device (chapter 1) ------------------------------------------------
SECTOR_SIZE = 512                                    # smallest unit the device can do
SECTORS_PER_BLOCK = 8
BLOCK_SIZE = SECTOR_SIZE * SECTORS_PER_BLOCK         # 4096

# --- the whole image (chapter 5) ------------------------------------------
TOTAL_BLOCKS = 1024                                  # 4 MiB. fixed
TOTAL_SECTORS = TOTAL_BLOCKS * SECTORS_PER_BLOCK     # 8192

# --- block assignment (chapter 5) -----------------------------------------
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
ROOT_INO = 1                                         # 0 is the "invalid" sentinel

# --- directories (chapter 4) ----------------------------------------------
DIRENT_SIZE = 32
DIRENTS_PER_BLOCK = BLOCK_SIZE // DIRENT_SIZE        # 128
MAX_NAME_LEN = 27                                    # 32 - 4 (ino) - 1 (padding)
MAX_DIRENTS = NDIRECT * DIRENTS_PER_BLOCK            # 1536

# --- on-disk structures ---------------------------------------------------
SUPERBLOCK_FMT = "<IIIIIIII"      # 32 bytes (chapter 5)
INODE_FMT = "<HHIIQQQ12I"         # 84 bytes -> zero-padded to 128 (chapter 3)
DIRENT_FMT = "<I27sx"             # 32 bytes (chapter 4)

# --- identification -------------------------------------------------------
MAGIC = 0x74667331                # "tfs1". bump to tfs2 if the layout changes
