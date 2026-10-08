# 11. コマンドラインから触る — CLI

![コマンドを入力してパソコンを操作する Alice と Bob](../images/11-alice-bob.png)

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
それをファイルシステムとして解釈できるのはこのコマンドだけである
（第 13 章の FUSE で、この前提が変わる）。

| サブコマンド | 機能 | 呼ぶ API |
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

どのサブコマンドも、対応する API を 1 回呼ぶだけになる。
そのぶん、同じ名前のシェルコマンドとは挙動が違うところがある。

**`mv` は宛先を特別扱いしない。** シェルの `mv a b` は、`b` が既存のディレクトリなら
`a` をその**中へ**移す（`rename("a", "b/a")` に書き換えてから呼ぶ）。
`tiny-fs mv` は受け取ったパスをそのまま `rename` に渡すので、
同じ状況では第 8 章の表のとおり `EISDIR` になる。中へ入れたいなら宛先まで書く。

```bash
tiny-fs mv disk.img /a.txt /docs        # EISDIR（/docs は既存のディレクトリ）
tiny-fs mv disk.img /a.txt /docs/a.txt  # こう書く
```

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

**8 ブロック、i-node 1 個** は mkfs 直後と同じ数字。
作って消したものが、すべて解放されている。

`fsck` の i-node の数は、予約の bit 0 を含めない（第 5 章）。
同じ状態を `dump` で見ると `used 2 / 129` と出る（第 9 章）ので、1 だけ違う。

---

## 3. ホストとのファイルの受け渡し

`put` と `get` は、**2 つのファイルシステムの境界**になる。
ホスト側は普通のファイル、イメージ側は tiny-fs のパスで、
処理は「一方から読んで、他方へ書く」だけになる。

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

サブコマンドの登録は `argparse` に任せる。引数の並びは §1 の表のとおり。

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

`ValueError` は第 1 章の `device.py` から来る。「512 の倍数でないファイル」を
`ValueError` で拒むので（まだファイルシステムの層ではないため `FSError` ではない）、
捕捉しないとトレースバックで終わる。

```
$ printf 'hello' > odd.txt
$ tiny-fs fsck ./odd.txt
tiny-fs: ./odd.txt: size 5 is not a multiple of 512
```

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
サブプロセスを起動せずにテストできる。

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

`test_full_session_ends_clean` が、この章の到達点をそのまま検査する。

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
作る、入れる、見る、出す、消す、確かめる。すべてコマンドからできる。

残っているのは最後の 1 歩
— これを OS につないで、本物の `ls` や `cat` やエディタから触れるようにすること。
