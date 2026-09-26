# 04. 名前をどう付けるか — ディレクトリ

第 3 章で、ファイルの中身は完全に表せるようになった。
5000 バイトのテキストは「17 番の i-node」として、いつでも正しく読み書きできる。

しかし人間は **17 番とは呼ばない**。`memo.txt` と呼ぶ。
そして i-node には、名前を入れる場所がなかった。

---

## 1. 問題 — 番号と名前のあいだに何もない

足りないものは 2 つある。

- **名前 → i-node 番号の対応**。`memo.txt` と言われたら 17 を返せること。
- **グループ分け**。ファイルが 100 個あるとき、全部を 1 列に並べたくない。
  `docs/` の下、`src/` の下、と分けたい。

1 つ目だけなら、対応表を 1 枚どこかに置けば済む。
2 つ目まで含めて考えると、置き場所の選び方が効いてくる。

---

## 2. 対応表を「ファイル」にする

名前と i-node 番号の対応表を作る。`memo.txt → 5`、`docs → 3` という行を並べたものだ。

置き場所は、**普通のファイルの中**にする。
つまり対応表そのものに i-node を 1 個与えて、中身が対応表になっているファイルとして扱う。
これが**ディレクトリ**。

こうすると、第 3 章で作ったものが全部そのまま使える。

- 表が伸びる → ファイルが伸びるのと同じ。`write` がそのまま使える
- 表の置き場所 → `direct[]` が管理する。専用の固定領域を用意しなくていい
- 表の大きさの上限 → 「ファイルの最大サイズ」に吸収される
- **表の中から別の表を指せる** → 入れ子になり、木構造が何の仕掛けもなく出てくる

そして名前が対応表の側にあるので、**i-node は自分の名前を知らない**ままになる。
これは後で効いてくる: 同じ i-node に複数の名前を付けられるし、
`rename` はデータを 1 バイトも動かさずに済む。

---

## 3. dirent — 対応表の 1 行

表の 1 行を **dirent** (directory entry) と呼ぶ。32 バイト固定。

![dirent の 32 バイト](../images/04-dirent.svg)

```python
DIRENT_FMT = "<I27sx"     # ino(4) + name(27) + padding(1) = 32 バイト
MAX_NAME_LEN = 27         # 32 − 4 (ino) − 1 (NUL)
```

`memo.txt` → i-node 5 の dirent と、その次の空きスロットの実バイト列:

```
00000000  05 00 00 00 6d 65 6d 6f 2e 74 78 74 00 00 00 00  |....memo.txt....|
          ^^^^^^^^^^^ ^^^^^^^^^^^^^^^^^^^^^^^ 名前は NUL 埋め（終端ではない）
          ino = 5     "memo.txt"
00000010  00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00  |................|
00000020  00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00  |................|
          ^^^^^^^^^^^ ino = 0 → このスロットは空き
00000030  00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00  |................|
```

決めておくことが 3 つある。

**固定長 32 バイトにする。** `BLOCK_SIZE` の約数なので、
1 ブロックにちょうど 128 エントリが収まり、**dirent がブロックをまたぐことがない**。
さらに「n 番目のエントリは `32n` バイト目」と位置が計算で決まる。
これが後で `readdir` のオフセットとして効く（§6）。

**名前は NUL 終端ではなく NUL 埋め。** 27 バイトちょうどの名前は終端バイトを持たない。
だから比較は「取り出してから `rstrip`」ではなく、
**検索語のほうを 27 バイトに右 NUL 埋めして、そのまま 27 バイト比較する**。
こうすると「名前に NUL は含められない」という前提が 1 か所に閉じ込められる。

**`ino == 0` は空きスロット。** 第 3 章で i-node 番号を 1 始まりにして
0 を番兵に予約したのが、ここで効く。

名前として受け付けないものも決めておく。

| 名前 | 結果 |
|---|---|
| 28 バイト以上 | `ENAMETOOLONG` |
| 空文字列 | `EINVAL` |
| `/` を含む | `EINVAL`（パスの区切りなので、名前には入れられない） |
| NUL を含む | `EINVAL`（NUL 埋めと区別できなくなる） |
| すでに存在する名前 | `EEXIST` |

---

## 4. ディレクトリは普通のファイル

![ディレクトリ = 中身が dirent の配列になっているだけの普通のファイル](../images/04-directory-is-a-file.svg)

ディレクトリも i-node を持つ。`direct[12]` にデータブロックを持ち、`size` をバイト単位で持つ。
**普通のファイルとの違いは `mode` の種別ビットだけ**（`S_IFDIR` か `S_IFREG` か）。

```python
# ディレクトリの中身を読むのに、専用の関数は要らない
raw = inode_read(dir_inode, blocks, offset=0, length=dir_inode.size)
# あとは 32 バイトずつ区切って解釈するだけ
```

この帰結として、上限が自動的に決まる。

| | |
|---|---|
| 1 ディレクトリの最大サイズ | `MAX_FILE_SIZE` = 48 KiB |
| 1 ディレクトリの最大エントリ数 | **1536**（12 ブロック × 128 エントリ、`.` と `..` 込み） |
| ディレクトリの `size` | エントリ数 × 32 バイト |

「ディレクトリのサイズ」が `ls -l` で 4096 などと表示されて不思議に思ったことがあれば、
これがその正体になる。ディレクトリのサイズは**中のファイルの合計ではなく、
対応表そのものの大きさ**。

---

## 5. `.` と `..` — 木をつなぐ

![. と .. — 木の上下をたどるのも、ただの dirent](../images/04-tree-dotdot.svg)

ディレクトリを作るとき、**必ず先頭 2 エントリとして `.` と `..` を書く**。

- `.` は自分自身の i-node 番号
- `..` は親ディレクトリの i-node 番号。**ルートの `..` は自分自身**（ino = 1）

「親をたどる」ために特別な仕組みを用意しないのがポイントで、
親へ戻るのも、ただの dirent を 1 個引くだけになる。
第 6 章のパス解決は、この規約のおかげで `..` を特別扱いせずに書ける。

### `nlink` の数え方

第 3 章で i-node に置いた `nlink` は、**その i-node を指している dirent の数**。
ディレクトリの場合はこうなる。

```
ディレクトリの nlink = 2 + 子ディレクトリの数
                      │   └ 子それぞれの ".." が自分を指す
                      └ 親の中の自分の名前 + 自分の "."
```

普通のファイルは `nlink = 1`（名前が 1 つ）。
0 になった時点で i-node とデータブロックを解放する、というのが第 7 章の `unlink`。

### 禁止すること

- `.` と `..` の削除・改名（木が壊れる）
- 空でないディレクトリの `rmdir` → `ENOTEMPTY`。
  「空」とは **`.` と `..` 以外に有効なエントリがない**こと
- ディレクトリを自分の子孫の下へ `rename` すること（第 8 章。木から切り離された輪ができる）

---

## 6. 削除は詰めない

![削除はエントリを詰めず、ino = 0 を書くだけ](../images/04-slot-reuse.svg)

エントリを消すときは、**`ino` に 0 を書くだけ**。
後ろのエントリを前に詰めることはせず、`size` も縮めない。
次に作るときは、まず空きスロットを探して再利用する。

なぜ詰めないか。**エントリの位置を動かさないため**。

`readdir` は「オフセット 3 から続きをくれ」という形で何度も呼ばれる
（第 8 章、そして FUSE 越しの `ls` がまさにこれをやる）。
途中でエントリが前に詰められると、
**まだ読んでいないエントリが読んだ位置より前に移動して、取りこぼす**。
位置が固定なら、この問題が最初から存在しない。

代償は、**一度太ったディレクトリは縮まない**こと。
1000 個作って 999 個消しても、`size` は 32000 バイトのままで、
検索は毎回そこまで走査する。これはディレクトリ内の断片化になる。

---

## 7. 実装 — `directory.py` (L4)

この層は「dirent の並んだファイル」を扱うだけ。
ディレクトリがディスクのどこにあるかも、パスがどう解決されるかも知らない。
第 3 章の `inode.read` / `inode.write` を呼ぶだけで済む。

```python
@dataclass
class Dirent:
    ino: int
    name: bytes          # NUL 埋めではなく、実際の名前（比較のときに埋める）

    def encode(self) -> bytes:
        # struct の 27s は自動で右 NUL 埋めしてくれる
        return struct.pack(DIRENT_FMT, self.ino, self.name)

    @classmethod
    def decode(cls, raw: bytes) -> "Dirent":
        ino, name = struct.unpack(DIRENT_FMT, raw)
        # 埋めた NUL を落として、本来の名前に戻す
        return cls(ino=ino, name=name.rstrip(b"\x00"))
```

走査はスロット番号で回す。**空きスロット (`ino == 0`) は読み飛ばす**。

```python
def iter_entries(dir_inode, blocks):
    """有効なエントリを (スロット番号, Dirent) で返す。"""
    for slot in range(dir_inode.size // DIRENT_SIZE):
        raw = inode.read(dir_inode, blocks, slot * DIRENT_SIZE, DIRENT_SIZE)
        entry = Dirent.decode(raw)
        if entry.ino != 0:          # 0 は空きスロット
            yield slot, entry


def lookup(dir_inode, blocks, name: bytes) -> int:
    """名前から i-node 番号を引く。なければ ENOENT。"""
    for _slot, entry in iter_entries(dir_inode, blocks):
        if entry.name == name:
            return entry.ino
    raise NotFoundError(name)
```

追加は「空きスロットを探す → なければ末尾に足す」。
末尾に足す側は、ディレクトリという**ファイルを伸ばす**ことになる。
つまり第 3 章の `write` がそのまま呼ばれ、必要ならブロックが 1 個割り当てられる。

```python
def add_entry(dir_inode, blocks, alloc, name: bytes, ino: int) -> None:
    check_name(name)                       # 長さ / '/' / NUL の検査
    if _find(dir_inode, blocks, name) is not None:
        raise AlreadyExistsError(name)     # EEXIST

    raw = Dirent(ino=ino, name=name).encode()

    free_slot = _find_free_slot(dir_inode, blocks)
    if free_slot is not None:
        # 消えたエントリの跡地を再利用する（size は増えない）
        inode.write(dir_inode, blocks, alloc, free_slot * DIRENT_SIZE, raw)
    else:
        # 末尾に足す = ディレクトリというファイルが 32 バイト伸びる
        inode.write(dir_inode, blocks, alloc, dir_inode.size, raw)
```

削除は `ino = 0` を書くだけ。**`size` も後続エントリも触らない。**

```python
def remove_entry(dir_inode, blocks, alloc, name: bytes) -> int:
    slot, entry = _find(dir_inode, blocks, name)
    if entry.name in (b".", b".."):
        raise InvalidArgumentError("cannot remove . or ..")
    empty = Dirent(ino=0, name=b"").encode()
    inode.write(dir_inode, blocks, alloc, slot * DIRENT_SIZE, empty)
    return entry.ino       # 呼び出し側が nlink を減らす（第 7 章）
```

新しいディレクトリの初期化は、`.` と `..` を書くだけ。

```python
def init_directory(dir_inode, blocks, alloc, self_ino: int, parent_ino: int) -> None:
    # 先頭 2 エントリは必ずこの順。ルートでは parent_ino == self_ino
    add_entry(dir_inode, blocks, alloc, b".", self_ino)
    add_entry(dir_inode, blocks, alloc, b"..", parent_ino)
```

---

## 8. テスト

```python
def test_add_then_lookup():
    # 足した名前が引けること

def test_lookup_missing_raises_enoent():

def test_name_of_exactly_27_bytes_is_ok():
    # 27 バイトちょうどは通り、28 バイトは ENAMETOOLONG。
    # NUL 終端を持たない名前が正しく往復すること

def test_duplicate_name_raises_eexist():

def test_remove_keeps_following_entries_in_place():
    # 真ん中を消しても、後ろのエントリのスロット番号が変わらないこと
    # （詰めない、という規約そのものの検査）

def test_removed_slot_is_reused():
    # 消した跡地に次のエントリが入り、size が増えないこと

def test_add_beyond_one_block_grows_the_directory():
    # 129 個目のエントリでブロックが 1 個割り当てられ、
    # 128 個目までのエントリが無傷であること

def test_dot_and_dotdot_cannot_be_removed():

def test_directory_full_raises_enospc():
    # 1536 個で打ち止め（= MAX_FILE_SIZE の壁）
```

5 つ目が第 2 章・第 3 章と同じ役割の「規約そのものを見るテスト」。
往復テストだけでは、実装が親切に詰めてしまっても気づけない。

---

これで部品が揃った。ブロックの空き管理、ファイルの表現、名前と階層。
残っているのは、**それぞれをディスクのどこに置くか**を決めて、
次に開いたときに読み直せるようにすること。次章で地図を描く。
