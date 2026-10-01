# 09. イメージを覗く — dump

第 II 部で、ファイルシステムとしての操作は一通りできるようになった。
ただし結果を確かめる手段が、まだ同じ API しかない。
`write` したものが `read` で返ってくる、という確認は、
**両方が同じように間違っていると通ってしまう**。

そこで、イメージを直接読んで人間に見せる道具を作る。
`dump.py` はディスクを**読むだけ**で、1 バイトも書き換えない。

---

## 1. 出力の形

出力は、第 5 章で決めたレイアウトの順に並べる。

```
$ tiny-fs dump disk.img

superblock (block 0)
  ...

block bitmap (block 1)
  ...

inode bitmap (block 2)
  ...

inode table (block 3-6)
  ...

directory /  (ino 1)
  ...
```

前半 4 つはレイアウトそのもの、最後がディレクトリの中身になる。
ビットマップは 4096 バイトをすべて並べても人間には読み取れないので、
**使用中のインデックスを範囲にまとめて**出力する。
i-node テーブルも同じで、128 個すべてではなく**使用中のものだけ**を出力する。

---

## 2. mkfs 直後を見る

```
$ tiny-fs mkfs disk.img
$ tiny-fs dump disk.img

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
```

第 5 章で図と 16 進ダンプで説明した状態が、そのまま出力されている。

- 使用中の 8 ブロックは、スーパーブロック・ビットマップ 2 枚・i-node テーブル 4 枚・
  ルートのデータブロック 1 枚
- i-node ビットマップの `used 2` は、予約の bit 0 とルートの ino 1
- ルートの `size` が 4096 なのは、ブロックを 1 個ぶん確保しているから（第 4 章）。
  中身は 2 エントリしかない

---

## 3. ファイルを 1 個作って差分を見る

`dump` の出力をファイルに保存して `diff` を取ると、
1 つの操作がディスクのどこを変えたかがそのまま読める。

```
$ tiny-fs dump disk.img > before.txt
$ tiny-fs put disk.img ./memo.txt /a.txt      # 5000 バイト
$ tiny-fs dump disk.img > after.txt
$ diff before.txt after.txt
```

変わるのは 5 か所だけになる。

| 変わったもの | before | after |
|---|---|---|
| `free_hint` | 8 | 10 |
| ブロックの使用数 | used 8、`ranges 0-7` | used 10、`ranges 0-9` |
| i-node の使用数 | used 2、`ranges 0-1` | used 3、`ranges 0-2` |
| i-node テーブル | ino 1 のみ | ino 2 が増える |
| ルートの中身 | `.` と `..` | `a.txt` が slot 2 に増える |

after 側の該当部分はこうなる。

```
inode table (block 3-6)
  ino  mode         nlink  size  blocks
    1  d rwxr-xr-x      2  4096  [7]
    2  - rw-r--r--      1  5000  [8, 9]

directory /  (ino 1)
  slot  ino  name
     0    1  "."
     1    1  ".."
     2    2  "a.txt"
```

**変わらなかったもの**のほうが、この差分では重要になる。

- ルートの `size` は 4096 のまま。エントリが 1 個増えても、
  空きスロットを使うので容量は増えない（第 4 章）
- ルートの `direct[0]` は 7 のまま。データブロックは増えていない
- 5000 バイトのファイルがブロックを 2 個使っている。
  2 個目は 904 バイトしか使わないが、確保は 1 個単位（第 3 章）

---

## 4. 生バイトを見る

解釈せずに 16 進で見たいときのために、ブロック番号を指定できるようにする。

```
$ tiny-fs dump disk.img --raw 7 --length 64
block 7
00000000  01 00 00 00 2e 00 00 00 00 00 00 00 00 00 00 00  |................|
00000010  00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00  |................|
00000020  01 00 00 00 2e 2e 00 00 00 00 00 00 00 00 00 00  |................|
00000030  00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00  |................|
```

ここまでの章で示してきた 16 進ダンプは、すべてこの形で出力できる。
**解釈した表示と生バイトを往復できる**ことが、この道具の役割になる。

---

## 5. 実装 — `dump.py`

`FileSystem` を使わず、`BlockDevice` から直接読む。
壊れたイメージも覗けるようにしておきたいので、
スーパーブロックの検証では異常終了させない（`magic` が違っても、その値を表示して続ける）。

ただし、`total_blocks` や `inode_table_start` が壊れている場合は
i-node の位置計算そのものが成立しない（イメージの外を読もうとする）。
その場合はスーパーブロックだけを表示し、理由を 1 行添えて処理を終了する。

```python
def dump(blocks: BlockDevice, out) -> None:
    sb = Superblock.decode(blocks.read_block(SUPERBLOCK_BLOCK))
    sections = [
        _superblock(sb),
        _bitmap(blocks, BLOCK_BITMAP_BLOCK, sb.total_blocks, "block bitmap", None),
        _bitmap(blocks, INODE_BITMAP_BLOCK, sb.num_inodes + 1, "inode bitmap",
                "(bit 0 is reserved)"),
        _inodes(blocks, sb),
        *_directories(blocks, sb),        # 到達できるディレクトリの数だけ
    ]
    # 各関数は行のリストを返すだけ。区切りの空行は 1 か所で入れる
    print("\n\n".join("\n".join(lines) for lines in sections), file=out)
```

使用中のインデックスをまとめる部分だけ、少し実装の手間がかかる。

```python
def _ranges(indexes: list[int]) -> str:
    """[0,1,2,3,7,8] を "0-3, 7-8" の形にまとめる。"""
    parts, start, prev = [], None, None
    for i in indexes:
        if start is None:
            start = prev = i
        elif i == prev + 1:
            prev = i
        else:
            parts.append(f"{start}-{prev}" if start != prev else f"{start}")
            start = prev = i
    if start is not None:
        parts.append(f"{start}-{prev}" if start != prev else f"{start}")
    return ", ".join(parts)
```

`mode` は `ls -l` と同じ表示形式にする。種別 1 文字 + パーミッション 9 文字。
これは第 11 章の `ls -l` からも呼ぶので、`dump.py` の公開関数にしておく。

```python
def mode_str(mode: int) -> str:
    kind = "d" if stat.S_ISDIR(mode) else "-"
    bits = ""
    for who in ("USR", "GRP", "OTH"):
        for what, ch in (("R", "r"), ("W", "w"), ("X", "x")):
            bits += ch if mode & getattr(stat, f"S_I{what}{who}") else "-"
    return f"{kind} {bits}"
```

---

## 6. テスト

`dump` は文字列を作る関数なので、**出力そのもの**を突き合わせられる。

```python
def test_dump_after_mkfs_is_exact(tmp_path):
    # mkfs 直後の出力が、期待する文字列と完全に一致すること

def test_dump_lists_only_used_inodes(tmp_path):
    # 128 行ではなく、使用中の行だけが出ること

def test_dump_ranges_are_compressed():
    # _ranges([0,1,2,3,7,8]) == "0-3, 7-8"

def test_dump_does_not_modify_the_image(tmp_path):
    # dump の前後でイメージのバイト列が 1 バイトも変わらないこと

def test_dump_shows_the_diff_of_one_create(tmp_path):
    # create の前後で、変わる行が想定の 5 か所だけであること

def test_dump_survives_a_broken_magic(tmp_path):
    # magic を壊しても例外にならず、その値を表示して続けること
```

`test_dump_does_not_modify_the_image` を必ず入れる。**覗くだけの道具が書き込んでしまう**のは、
この種の道具で最も起きやすく、最も気づきにくい壊れ方になる。

`test_dump_shows_the_diff_of_one_create` は、第 III 部のこれ以降と、第 10 章の `fsck` の土台にもなる。

---

## 試す

```bash
tiny-fs mkfs disk.img
tiny-fs dump disk.img > before.txt

python -c "open('sample.txt','w').write('x' * 5000)"
tiny-fs put disk.img ./sample.txt /a.txt
tiny-fs dump disk.img > after.txt
diff before.txt after.txt                     # 「3. ファイルを 1 個作って差分を見る」の表と同じ 5 か所が出る

# 解釈した表示と生バイトを往復する
tiny-fs dump disk.img --raw 3 --length 128    # ルートの i-node
```

---

次章では、この「見る」道具を「検査する」道具へ発展させる。
ビットマップ上で使用中とされているブロックと、実際に参照されているブロックが一致しているか。
