# B. エラーコード対応表

tiny-fs の例外はすべて `FSError` 派生で、`errno` を持つ。
この 1 つの規約で、CLI は終了ステータスに変換でき（第 11 章）、
FUSE は `FuseOSError` に変換できる（第 13 章）。

---

## 1. 例外の定義

```python
# tiny_fs/errors.py
class FSError(Exception):
    """tiny-fs のすべてのエラーの基底。errno を必ず持つ。"""

    errno = EIO
    strerror = "I/O error"

    def __init__(self, path: str = "") -> None:
        self.path = path
        super().__init__(f"{path}: {self.strerror}")
```

派生クラスは 2 行ずつ。

```python
class NotFoundError(FSError):
    errno, strerror = ENOENT, "No such file or directory"

class AlreadyExistsError(FSError):
    errno, strerror = EEXIST, "File exists"
```

---

## 2. 一覧

| 例外 | `errno` | 意味 | いつ出るか |
|---|---|---|---|
| `NotFoundError` | `ENOENT` | そのパスがない | `lookup` で成分が見つからない。`unlink` / `rm` の対象がない |
| `AlreadyExistsError` | `EEXIST` | 既にある | `create` / `mkdir` が作ろうとしたパスが既にある（`rename` は置き換えるので出ない） |
| `NotADirectoryError` | `ENOTDIR` | ディレクトリではない | パスの途中の成分がファイル（`/memo.txt/x`）。`readdir` / `rmdir` の対象がファイル。`rename` でディレクトリをファイルに上書きしようとした |
| `IsADirectoryError` | `EISDIR` | ディレクトリである | `read` / `write` / `unlink` の対象がディレクトリ。`rename` でファイルをディレクトリに上書きしようとした |
| `NotEmptyError` | `ENOTEMPTY` | 空ではない | `rmdir` の対象に有効なエントリが残っている。`rename` の宛先が空でないディレクトリ |
| `NoSpaceError` | `ENOSPC` | 空きがない | データブロックの枯渇。i-node の枯渇。ディレクトリが 1536 エントリに達した |
| `FileTooBigError` | `EFBIG` | 大きすぎる | 書き込みが `MAX_FILE_SIZE`（48 KiB）を超える |
| `NameTooLongError` | `ENAMETOOLONG` | 名前が長すぎる | 名前が 28 バイト以上 |
| `InvalidArgumentError` | `EINVAL` | 引数がおかしい | 相対パス。名前が空、`/` や NUL を含む。`/` の親を求めた。ディレクトリを自分の子孫へ移そうとした |
| `CorruptionError` | `EIO` | イメージが壊れている | i-node 番号が範囲外。二重解放。スーパーブロックが定数と食い違う |

---

## 3. 層ごとの役割

その層で**はじめて出てくる**例外を挙げる（上の層が同じ型を投げ直すこともある）。

| 層 | 例外について |
|---|---|
| `device.py` / `disk.py` | 範囲外のセクタ / ブロック番号は `ValueError`。まだファイルシステムの層ではない |
| `bitmap.py` | `NoSpaceError`、二重解放の `CorruptionError` |
| `inode.py` | `FileTooBigError`、`InvalidArgumentError`、`CorruptionError` |
| `directory.py` | `NotFoundError`、`AlreadyExistsError`、`NameTooLongError`、`InvalidArgumentError` |
| `fs.py` | `NotADirectoryError`、`IsADirectoryError`、`NotEmptyError`、`CorruptionError`、`InvalidArgumentError`（相対パス、`/` の親、子孫への `rename`） |
| `cli.py` | **変換するだけ** — メッセージ 1 行 + 終了ステータス 1 |
| `fuse_ops.py` | **変換するだけ** — `FuseOSError(e.errno)` |

下の層は「何が起きたか」だけを投げ、
**どう見せるかは境界の 2 つのモジュールが決める**。
`fs.py` までは `errno` 以外のことを知らない。

---

## 4. 実際の見え方

同じ `ENOENT` が、3 つの経路でこう見える。

```
# ライブラリとして
>>> fs.read("/nothing", 0, 10)
NotFoundError: /nothing: No such file or directory

# CLI から（第 11 章）
$ tiny-fs cat disk.img /nothing
tiny-fs: /nothing: No such file or directory (ENOENT)
$ echo $?
1

# FUSE 経由（第 13 章）
$ cat ~/mnt/tiny-fs/nothing
cat: /home/dev/mnt/tiny-fs/nothing: No such file or directory
```

FUSE 経由の例は `cat` が出しているメッセージで、tiny-fs は関与していない。
`errno` を正しく返せば、**あとは OS とコマンドが説明を引き受ける**。
