# 11. コマンドラインから触る — CLI

`fs.py` に操作は揃い、`dump` と `fsck` で中身も確かめられるようになった。
残っているのは、それらを**普通のコマンドとして使えるようにする**こと。

この章が終わると、FUSE を使わずに
「作る・入れる・見る・出す・消す・確かめる」が一通りできる状態になる。

---

## 1. コマンドの形

```
tiny-fs <サブコマンド> <イメージ> [引数...]
```

イメージのパスを毎回渡すのは、**マウントしていないから**。
OS から見れば `disk.img` はただのファイルで、
それをファイルシステムとして解釈できるのはこのコマンドだけになる
（第 12 章の FUSE で、この前提が変わる）。

| サブコマンド | 働き | 呼ぶ API |
|---|---|---|
| `mkfs <img>` | イメージを初期化する | 第 5 章の mkfs |
| `ls [-l] <img> <path>` | ディレクトリの中身を並べる | `readdir` / `stat` |
| `mkdir <img> <path>` | ディレクトリを作る | `mkdir` |
| `rmdir <img> <path>` | 空のディレクトリを消す | `rmdir` |
| `put <img> <host> <path>` | ホストのファイルを入れる | `create` / `truncate` / `write` |
| `get <img> <path> <host>` | 中のファイルを取り出す | `read` |
| `cat <img> <path>` | 中身を標準出力へ | `read` |
| `mv <img> <old> <new>` | 名前を変える / 移す | `rename` |
| `rm <img> <path>` | ファイルを消す | `unlink` |
| `dump <img> [--raw N]` | 中身を人間可読で表示 | 第 9 章 |
| `fsck <img>` | 整合性を検査する | 第 10 章 |

---

## 2. 一通り使ってみる

```
$ tiny-fs mkfs disk.img
created disk.img: 1024 blocks (4 MiB), 128 inodes

$ tiny-fs mkdir disk.img /docs
$ tiny-fs put disk.img ./sample.txt /docs/sample.txt

$ tiny-fs ls disk.img /
.
..
docs

$ tiny-fs ls -l disk.img /docs
d rwxr-xr-x  2  4096  2026-09-27 12:00  .
d rwxr-xr-x  3  4096  2026-09-27 12:00  ..
- rw-r--r--  1  5000  2026-09-27 12:03  sample.txt
```

`/docs` の `.` は自分自身なので `nlink = 2`、
`..` はルートで、子ディレクトリを 1 個持つので `nlink = 3`。
第 4 章で決めた数え方が、そのまま `ls -l` に出ている。

取り出して、元のファイルと一致することを確かめる。

```
$ tiny-fs get disk.img /docs/sample.txt ./copy.txt
$ diff ./sample.txt ./copy.txt && echo same
same
```

最後に全部消して、mkfs 直後の状態に戻ることを見る。

```
$ tiny-fs rm disk.img /docs/sample.txt
$ tiny-fs rmdir disk.img /docs
$ tiny-fs fsck disk.img
fsck: clean (8 blocks used, 1 inodes used)
```

**8 ブロック、i-node 1 個** は mkfs 直後と同じ数字（第 9 章）。
作って消したものが、きれいに返っている。

---

## 3. ホストとの出し入れ

`put` と `get` は、**2 つのファイルシステムの境界**になる。
ホスト側は普通のファイル、中側は tiny-fs のパスで、
やっていることは「一方で読んで、他方に書く」だけ。

```python
def cmd_put(args) -> int:
    data = pathlib.Path(args.host_path).read_bytes()   # ホスト側
    with _open(args.image) as fs:
        try:
            fs.create(args.fs_path)
        except AlreadyExistsError:
            fs.truncate(args.fs_path, 0)               # 既にあれば上書き
        fs.write(args.fs_path, 0, data)
    return 0
```

ホストのファイルを丸ごとメモリに読んでから書いている。
`MAX_FILE_SIZE` が 48 KiB なので、それで足りる。
48 KiB を超えるファイルは `write` が `EFBIG` で弾く。

`cat` は標準出力にバイト列をそのまま流す。

```python
def cmd_cat(args) -> int:
    with _open(args.image) as fs:
        size = fs.stat(args.fs_path).size
        sys.stdout.buffer.write(fs.read(args.fs_path, 0, size))
    return 0
```

`sys.stdout` ではなく `sys.stdout.buffer` に書く。
テキストとして書くと、文字コードの変換が入って**バイナリが壊れる**。

---

## 4. 実装 — `cli.py`

各サブコマンドは「イメージを開く → API を 1〜2 個呼ぶ → 閉じる」だけ。
**ファイルシステムの処理はここに一切書かない。**

層を積み上げるのはこの 1 か所になる。

```python
def _open(image: str) -> FileSystem:
    # 第 1 章から順に積む: 装置 → ブロック → ファイルシステム
    return FileSystem(BlockDevice(SectorDevice(image)))
```

サブコマンドの登録は `argparse` に任せる。

```python
def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tiny-fs")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("put", help="ホストのファイルを入れる")
    p.add_argument("image")
    p.add_argument("host_path")
    p.add_argument("fs_path")
    p.set_defaults(func=cmd_put)
    ...
    return parser
```

---

## 5. エラーの扱い

`fs.py` から上がってくるのは、すべて `FSError` 派生で `errno` を持っている（付録 C）。
CLI はそれを**1 行のメッセージと終了ステータス**に変える。

```python
def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.func(args)
    except FSError as e:
        # errno の名前を出す。どの層で何が起きたかが追える
        name = errno.errorcode.get(e.errno, str(e.errno))
        print(f"tiny-fs: {e.path}: {e.strerror} ({name})", file=sys.stderr)
        return 1
    except OSError as e:
        # イメージの中ではなく、ホスト側のファイルで失敗した場合
        print(f"tiny-fs: {e.filename}: {e.strerror}", file=sys.stderr)
        return 1
    except ValueError as e:
        # そもそも tiny-fs のイメージになりえないファイルを渡された場合
        print(f"tiny-fs: {e}", file=sys.stderr)
        return 1
```

3 つ目が要る。第 1 章の `device.py` は「512 の倍数でないファイル」を
`ValueError` で拒むので（まだ FS の話ではないため `FSError` ではない）、
それを受けないと `tiny-fs fsck /etc/hosts` がトレースバックで落ちる。

```
$ tiny-fs cat disk.img /nothing
tiny-fs: /nothing: No such file or directory (ENOENT)
$ echo $?
1

$ tiny-fs put disk.img ./big.bin /big.bin
tiny-fs: /big.bin: File too large (EFBIG)
$ echo $?
1

$ tiny-fs rm disk.img /docs
tiny-fs: /docs: Is a directory (EISDIR)
```

終了ステータスは **0 か 1** だけにする。
`fsck` だけは意味を持たせて、問題が見つかったら 1 を返す（第 10 章）。
これで `tiny-fs fsck disk.img && echo ok` のような書き方ができる。

---

## 6. テスト

`main(argv)` を関数として呼べるようにしておくと、
サブプロセスを起こさずにテストできる。

```python
def test_put_get_roundtrip(tmp_path, capsys):
    # put したファイルを get すると、バイト列が一致する

def test_cat_writes_raw_bytes(tmp_path, capsys):
    # バイナリを cat しても壊れないこと

def test_ls_lists_dot_and_dotdot(tmp_path, capsys):

def test_missing_path_returns_1_and_prints_enoent(tmp_path, capsys):
    # 終了ステータスが 1、stderr に ENOENT

def test_rm_on_directory_returns_eisdir(tmp_path, capsys):

def test_fsck_exit_status(tmp_path):
    # 健全なら 0、壊すと 1

def test_full_session_ends_clean(tmp_path):
    # mkfs → mkdir → put → mv → get → rm → rmdir → fsck が clean で終わる
    # 最後の空きブロック数・空き i-node 数が mkfs 直後と一致する
```

最後の 1 本が、この章の到達点をそのまま検査する形になる。

---

## 試す

```bash
python -c "open('sample.txt','w').write('x' * 5000)"

tiny-fs mkfs disk.img
tiny-fs mkdir disk.img /docs
tiny-fs put disk.img ./sample.txt /docs/sample.txt
tiny-fs ls -l disk.img /docs
tiny-fs get disk.img /docs/sample.txt ./copy.txt
diff ./sample.txt ./copy.txt && echo same

tiny-fs rm disk.img /docs/sample.txt
tiny-fs rmdir disk.img /docs
tiny-fs fsck disk.img           # mkfs 直後と同じ数字に戻る
```

---

ここまでで、tiny-fs は**ファイルシステムとして完成**している。
作る、置く、読む、消す、確かめる。すべてコマンドからできる。

残っているのは最後の 1 歩
— これを OS につないで、本物の `ls` や `cat` やエディタから触れるようにすること。
