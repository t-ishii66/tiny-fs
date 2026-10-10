# A. 定数一覧

`tiny_fs/constants.py` の全体。**数値はここ以外に書かない。**

```python
"""ディスク上のレイアウトを決める定数。"""

# --- 装置（第 1 章） ------------------------------------------------------
SECTOR_SIZE = 512                                    # 装置が扱える最小単位
SECTORS_PER_BLOCK = 8
BLOCK_SIZE = SECTOR_SIZE * SECTORS_PER_BLOCK         # 4096

# --- イメージ全体（第 5 章） ----------------------------------------------
TOTAL_BLOCKS = 1024                                  # 4 MiB。固定
TOTAL_SECTORS = TOTAL_BLOCKS * SECTORS_PER_BLOCK     # 8192

# --- ブロックの割り当て（第 5 章） ----------------------------------------
SUPERBLOCK_BLOCK = 0
BLOCK_BITMAP_BLOCK = 1
INODE_BITMAP_BLOCK = 2
INODE_TABLE_START = 3

# --- i-node（第 3 章） ----------------------------------------------------
INODE_SIZE = 128
INODES_PER_BLOCK = BLOCK_SIZE // INODE_SIZE          # 32
NUM_INODES = 128                                     # 固定
INODE_TABLE_BLOCKS = NUM_INODES // INODES_PER_BLOCK  # 4
DATA_START = INODE_TABLE_START + INODE_TABLE_BLOCKS  # 7
NDIRECT = 12
MAX_FILE_SIZE = NDIRECT * BLOCK_SIZE                 # 49152 = 48 KiB
ROOT_INO = 1                                         # 0 は無効を表す番兵

# --- ディレクトリ（第 4 章） ----------------------------------------------
DIRENT_SIZE = 32
DIRENTS_PER_BLOCK = BLOCK_SIZE // DIRENT_SIZE        # 128
MAX_NAME_LEN = 27                                    # 32 − 4 (ino) − 1 (詰め物)
MAX_DIRENTS = NDIRECT * DIRENTS_PER_BLOCK            # 1536

# --- ディスク上の構造体 ---------------------------------------------------
SUPERBLOCK_FMT = "<IIIIIIII"      # 32 バイト（第 5 章）
INODE_FMT = "<HHIIQQQ12I"         # 84 バイト → 128 バイトにゼロ埋め（第 3 章）
DIRENT_FMT = "<I27sx"             # 32 バイト（第 4 章）

# --- 識別 -----------------------------------------------------------------
MAGIC = 0x74667331                # "tfs1"。レイアウトを変えたら tfs2 に上げる
```

---

## 導出の根拠

| 定数 | 値 | なぜその値か | 初出 |
|---|---|---|---|
| `SECTOR_SIZE` | 512 | 装置が決めている。選べない | 第 1 章 |
| `BLOCK_SIZE` | 4096 | 管理情報が 8 分の 1 になる。OS のページサイズと同じ | 第 1 章 |
| `INODE_SIZE` | 128 | 使うのは 84 バイト。`BLOCK_SIZE` を割り切れる最小の 2 の冪 | 第 3 章 |
| `INODES_PER_BLOCK` | 32 | 4096 ÷ 128。割り切れるので i-node が境界をまたがない | 第 3 章 |
| `NDIRECT` | 12 | 128 バイトの枠に余裕を残して選んだ本数。1 ファイルの上限がここで決まる | 第 3 章 |
| `MAX_FILE_SIZE` | 48 KiB | 12 × 4096。間接ブロックがないのでこれが上限 | 第 3 章 |
| `DIRENT_SIZE` | 32 | ino 4 + 名前 27 + 詰め物 1。`BLOCK_SIZE` の約数 | 第 4 章 |
| `MAX_NAME_LEN` | 27 | 32 − 4 − 1 | 第 4 章 |
| `TOTAL_BLOCKS` | 1024 | 4 MiB。ビットマップ 1 枚で 32768 まで表せる範囲に収まる | 第 5 章 |
| `NUM_INODES` | 128 | テーブルがちょうど 4 ブロック | 第 5 章 |
| `ROOT_INO` | 1 | 0 を「無効」の番兵に使うため | 第 3 章 |
| `MAGIC` | `0x74667331` | `"tfs1"` を整数にしたもの | 第 5 章 |

---

## 容量

| 項目 | 値 | 導出 |
|---|---|---|
| イメージサイズ | 4 MiB | 4096 × 1024 |
| メタデータ | 28 KiB | 7 ブロック（スーパーブロック + ビットマップ 2 + i-node テーブル 4） |
| データ領域 | 1017 ブロック ≈ 3.97 MiB | 1024 − 7 |
| 1 ファイルの最大 | 48 KiB | `NDIRECT` × `BLOCK_SIZE` |
| ファイル / ディレクトリの最大数 | 127 | `NUM_INODES` − 1（ルート） |
| 1 ディレクトリの最大エントリ | 1536 | 12 × 128（`.` `..` 込み） |
| ビットマップ 1 枚の上限 | 32768 ブロック = 128 MiB | 4096 × 8 ビット |
