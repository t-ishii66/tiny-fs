# 10. 壊れていないことを確かめる — fsck

`dump` は「いまディスクに何が書かれているか」を見せる道具だった。
この章で作るのは、それが**辻褄の合った状態かどうか**を判定する道具になる。

---

## 1. 何を検査するのか

検査ができるのは、**同じ事実がディスク上に 2 通り記録されているから**。
重複している以上、食い違いうる。食い違っていれば壊れている。

| 事実 | 記録① | 記録② |
|---|---|---|
| どのブロックが使用中か | データブロック・ビットマップ | 各 i-node の `direct[]` |
| どの i-node が使用中か | i-node ビットマップ | ディレクトリの dirent |
| ある i-node を指す名前の数 | i-node の `nlink` | 実際にその ino を指す dirent の数 |

`fsck` がやることは 1 つだけ。**①をそのまま読み、②から組み立て直して、突き合わせる。**

![同じ事実を 2 通りの方法で求めて、突き合わせる](../images/10-fsck-two-views.svg)

---

## 2. 期待値を作る

②の側は、**ルートから木をたどって数え上げる**。

```python
def _collect(blocks, sb):
    """ルートから到達できるものを全部たどって、期待値を作る。"""
    block_refs: dict[int, list[int]] = {}   # ブロック番号 → 参照している ino の一覧
    seen_inodes: set[int] = set()
    nlink_count: Counter[int] = Counter()
    problems: list[str] = []               # 先頭 2 エントリの検査（項目 7）はここで

    # メタデータのブロックは、誰の i-node にも属さないが使用中
    for block_no in range(sb.data_start):
        block_refs.setdefault(block_no, []).append(METADATA)

    stack = [sb.root_ino]
    while stack:
        ino = stack.pop()
        if ino in seen_inodes:
            continue                        # ループしていても止まる
        seen_inodes.add(ino)

        inode = read_inode(blocks, sb, ino)
        for block_no in inode.direct:
            if block_no != 0:
                block_refs.setdefault(block_no, []).append(ino)

        if stat.S_ISDIR(inode.mode):
            entries = list(iter_entries(inode, blocks))
            if [e.name for _slot, e in entries][:2] != [b".", b".."]:
                problems.append(f'directory inode {ino} does not start with '
                                f'"." and ".."')
            for _slot, entry in entries:
                nlink_count[entry.ino] += 1     # "." と ".." も 1 つの名前として数える
                if entry.name not in (b".", b".."):
                    stack.append(entry.ino)
    return block_refs, seen_inodes, nlink_count, problems
```

`.` と `..` も dirent なので、`nlink` の数え上げに**含める**。
第 4 章で決めた「`nlink` = 2 + 子ディレクトリの数」は、この数え方の結果として出る。

`seen_inodes` でループを止めているのが大事なところ。
`..` が壊れて輪ができているイメージでも、`fsck` は止まらずに最後まで走る。

---

## 3. 突き合わせる

検査項目は 7 つ。それぞれが見つける壊れ方を並べる。

| # | 検査 | 見つかる壊れ方 |
|---|---|---|
| 1 | ビットマップが使用中 ⇔ 誰かが参照している | **解放漏れ**（参照なしなのに使用中）。空き容量がじわじわ減る |
| 2 | 同上 | **未割り当てブロックの使用**（参照ありなのに空き）。いずれ二重に割り当てられる |
| 3 | 1 ブロックを参照する i-node は 1 つ以下 | **二重参照**。片方を消すともう片方が壊れる |
| 4 | i-node ビットマップ ⇔ ルートから到達できる i-node | **孤立 i-node**。作りかけの残骸（第 7 章の `create` が後始末を忘れた形） |
| 5 | `inode.nlink` == 数え上げた名前の数 | `nlink` の食い違い。0 にならず解放されない、または早すぎる解放 |
| 6 | ブロック番号が `data_start` 以上 `total_blocks` 未満 | 範囲外のブロック番号を持つ i-node |
| 7 | ディレクトリの先頭 2 エントリが `.` と `..` | 木の構造が壊れている |

加えて 2 つ。ビットマップの**範囲外のビットが 1 になっていないこと**
（第 5 章で「余りビットは 0 のまま」と決めたため）と、
**スーパーブロックの数値が辻褄の合う範囲にあること**。
後者が壊れていると i-node の位置計算が成立しないので、
そこだけを報告して打ち切る。

---

## 4. 報告する

**`fsck` は直さない。検出して報告するだけ。**

```
$ tiny-fs fsck disk.img
fsck: 3 problems found

  block 9 is referenced by inode 2 but marked free in the block bitmap
  block 12 is marked used but referenced by nothing (leak)
  inode 5 has nlink=2 but 1 name points to it

$ echo $?
1
```

問題がなければ 1 行だけ出して 0 で終わる。

```
$ tiny-fs fsck disk.img
fsck: clean (10 blocks used, 2 inodes used)
$ echo $?
0
```

終了ステータスを分けておくと、テストから使いやすくなる。
**すべてのテストの最後に `fsck` を通す**、という運用ができる（第 7 章以降のテストがそれ）。

---

## 5. 実装 — `fsck.py`

`dump` と同じく `BlockDevice` から直接読み、`FileSystem` は使わない。
検査する相手を、検査される側の API で読むわけにいかないため。

```python
def fsck(blocks: BlockDevice) -> list[str]:
    """問題の一覧を返す。空リストなら健全。"""
    sb = Superblock.decode(blocks.read_block(SUPERBLOCK_BLOCK))
    problems: list[str] = []

    block_refs, seen_inodes, nlink_count, dir_problems = _collect(blocks, sb)
    block_bitmap = Bitmap(bytearray(blocks.read_block(BLOCK_BITMAP_BLOCK)),
                          sb.total_blocks)

    for block_no in range(sb.total_blocks):
        refs = block_refs.get(block_no, [])
        used = block_bitmap.is_used(block_no)
        if len(refs) > 1:
            problems.append(f"block {block_no} is referenced by "
                            f"{_refs_str(refs)} (double use)")
        elif refs and not used:
            problems.append(f"block {block_no} is referenced by inode {refs[0]} "
                            f"but marked free in the block bitmap")
        elif not refs and used:
            problems.append(f"block {block_no} is marked used but referenced "
                            f"by nothing (leak)")
    ...
    return problems
```

すべての検査が「集合を 2 つ作って比べる」形になっている。
第 2 章でビットマップを選んだときの「`n` 番は使用中か」に O(1) で答えられる性質が、
ここで効いている。

---

## 6. テスト

`fsck` のテストは、**わざと壊したイメージを用意できるか**にかかる。
`BlockDevice` を直接使えば、どのバイトでも書き換えられる。

```python
def test_fsck_clean_after_mkfs(tmp_path):
def test_fsck_clean_after_many_operations(tmp_path):
    # 作る・書く・消すを一通りやったあとで健全

def test_detects_leaked_block(tmp_path):
    # ビットマップのビットを 1 にするだけで、参照のないブロックを作る

def test_detects_double_reference(tmp_path):
    # 2 つの i-node の direct[0] を同じブロック番号にする

def test_detects_orphan_inode(tmp_path):
    # i-node ビットマップのビットを立てるだけで、到達できない i-node を作る

def test_detects_wrong_nlink(tmp_path):
    # i-node の nlink を +1 する

def test_survives_a_cycle_in_dotdot(tmp_path):
    # ".." を子自身に向けても、fsck が止まらずに報告すること

def test_create_and_delete_returns_to_clean(tmp_path):
    # 100 回作って消しても、空きブロック数と空き i-node 数が元に戻る
```

最後のテストが、第 III 部で一番効く。
**解放漏れは 1 回では気づけない**。繰り返して初めて減り方が見える。

---

## 試す

```bash
tiny-fs mkfs disk.img
tiny-fs fsck disk.img && echo clean

# ビットマップを 1 バイト壊す（ブロック 16 を使用中に見せかける）
python - <<'PY'
from tiny_fs.device import SectorDevice
from tiny_fs.disk import BlockDevice
blocks = BlockDevice(SectorDevice("disk.img"))
buf = bytearray(blocks.read_block(1))
buf[2] |= 0x01
blocks.write_block(1, buf)
PY

tiny-fs fsck disk.img; echo "exit=$?"     # leak として検出され、1 で終わる
```

---

第 III 部の残りは、ここまでの機能をコマンドとして束ねる作業になる。
次章で `tiny-fs` コマンドを作れば、ファイルシステムとして普通に使えるようになる。
