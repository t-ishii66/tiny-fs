# 12. FUSE でマウントする

第 11 章で、tiny-fs はファイルシステムとして完成した。
ただし触れるのは `tiny-fs` コマンドからだけで、
OS から見れば `disk.img` はいまだに**ただのファイル**にすぎない。

最後の 1 歩は、これを OS につなぐこと。
`/mnt/tiny-fs` に置いて、本物の `ls` や `cat` やエディタから触れるようにする。

---

## 1. FUSE とは何か

ファイルシステムは普通カーネルの中にある。
**FUSE** (Filesystem in Userspace) は、その処理を**ユーザ空間のプロセスに投げる**仕組み。
カーネルの FUSE ドライバが `/dev/fuse` を窓口にして、
「このパスを読みたい」という依頼をこちらのプロセスに渡し、返事を待つ。

![cat /mnt/tiny-fs/a.txt が tiny-fs に届くまで](../images/12-fuse-flow.svg)

`cat` から見れば、これは普通のファイルを読んだのとまったく区別がつかない。
カーネルモジュールを書かなくても、**Python のプロセス 1 個がファイルシステムになれる**。

ここで初めて「マウント」が本物になる。第 7 章の `FileSystem` を作る操作は
メモリ上の準備にすぎなかったが、今回は OS のディレクトリツリーに組み込まれる。

---

## 2. 実行環境

**macOS では動かせない。** `/dev/fuse` は Linux のものなので、Docker の Linux で動かす。

```dockerfile
# docker/Dockerfile
FROM python:3.11-slim

# fusepy が読み込むのは libfuse.so.2 なので libfuse2 が要る。
# fuse3 はアンマウント用の fusermount3 コマンドのために入れている。
RUN apt-get update && apt-get install -y --no-install-recommends \
        fuse3 libfuse2 \
    && rm -rf /var/lib/apt/lists/*

# fusepy はここだけで使う。pytest は -m fuse を走らせるために入れている
RUN pip install --no-cache-dir fusepy pytest

# リポジトリはここに bind mount される。入れ直さずに import できるようにする
ENV PYTHONPATH=/work
WORKDIR /work
```

`libfuse2` を忘れると、`fusepy` の import は通るのに
マウントの直前で「ライブラリが見つからない」で落ちる。

```bash
docker build -t tiny-fs-dev docker/
docker run --rm -it \
  --cap-add SYS_ADMIN --device /dev/fuse \
  --security-opt apparmor:unconfined \
  -v "$PWD":/work -w /work tiny-fs-dev bash
```

3 つのオプションはすべて必要になる。
`--device /dev/fuse` で窓口を渡し、`--cap-add SYS_ADMIN` でマウントを許し、
`--security-opt apparmor:unconfined` で AppArmor の既定の禁止を外す。

---

## 3. 呼び出しの対応

FUSE から来る操作は、`fs.py` の API とほぼ 1 対 1 に対応する。

| FUSE | `fs.py` | 備考 |
|---|---|---|
| `getattr` | `stat` | `st_*` の辞書に詰め替える |
| `readdir` | `readdir` | `.` と `..` は実体があるのでそのまま返る |
| `read` | `read` | |
| `write` | `write` | |
| `create` | `create` | |
| `truncate` | `truncate` | |
| `unlink` / `mkdir` / `rmdir` / `rename` | 同名 | |
| `statfs` | ビットマップの空き数 | `df` が読む |
| `chmod` / `chown` / `utimens` | 同名 | `cp` やエディタが保存の最後に呼ぶ。実装しないとそこで失敗する |
| `destroy` | `close` | アンマウント時。`free_hint` がここで書き戻る |
| `flush` / `fsync` | **何もしない** | キャッシュを持たないので、既に書き終わっている |
| `open` / `release` | **何もしない** | パスだけで操作できるので、開いた状態を持たない |

第 II 部で作った API がそのまま使えるのは、
`fs.py` が FUSE を知らない形で書かれているから。
この章で足すのは**変換だけ**になる。

---

## 4. 実装 — `fuse_ops.py`

```python
class TinyFSOperations(Operations):
    def __init__(self, image: str) -> None:
        self._fs = FileSystem(BlockDevice(SectorDevice(image)))

    def __call__(self, op: str, *args):
        # 例外を errno に変える場所は、ここ 1 か所だけ
        try:
            return super().__call__(op, *args)
        except FSError as e:
            raise FuseOSError(e.errno)

    def getattr(self, path, fh=None):
        inode = self._fs.stat(path)
        return {
            "st_mode":  inode.mode,
            "st_nlink": inode.nlink,
            "st_size":  inode.size,
            "st_uid":   inode.uid,
            "st_gid":   inode.gid,
            "st_mtime": inode.mtime,
            "st_ctime": inode.ctime,
            "st_atime": inode.mtime,   # atime は持っていないので mtime を返す
        }

    def readdir(self, path, fh):
        return [name.decode() for name, _ino in self._fs.readdir(path)]

    def read(self, path, size, offset, fh):
        return self._fs.read(path, offset, size)

    def write(self, path, data, offset, fh):
        return self._fs.write(path, offset, data)

    def create(self, path, mode, fi=None):
        self._fs.create(path, S_IFREG | (mode & 0o777))
        return 0

    def unlink(self, path):
        self._fs.unlink(path)

    def mkdir(self, path, mode):
        self._fs.mkdir(path, S_IFDIR | (mode & 0o777))

    def destroy(self, path):
        self._fs.close()          # アンマウント。ここで free_hint が書き戻る
```

`__call__` を上書きしているのが要点になる。
これは自前の工夫ではなく、**fusepy が用意している入口**で、
基底クラス `Operations` の側はこうなっている。

```python
# fusepy (fuse.py) より
class Operations(object):
    def __call__(self, op, *args):
        if not hasattr(self, op):
            raise FuseOSError(errno.EFAULT)
        return getattr(self, op)(*args)
```

操作名を受け取って、同名のメソッドを探して呼ぶだけの振り分け口。
カーネルからの呼び出しを受ける `FUSE` クラスの中は、全部この形になっている。

```python
return self.operations('mkdir', path.decode(self.encoding), mode)
return self.operations('unlink', path.decode(self.encoding))
```

`self.operations.mkdir(...)` と直接呼ぶ箇所は 1 つもなく、
**すべての操作が `__call__` を通る**。だから全操作に共通の処理を挟みたいときは、
ここを上書きするのが fusepy での定型になる。
fusepy 自身、全操作をログに出す `LoggingMixIn` をこのやり方で書いている。

tiny-fs はそこにログではなく例外変換を置いた。結果として
**独自例外 → `FuseOSError` の変換が 1 か所に集まる**。
各メソッドに `try` を書く必要がない。

メソッドの中身はどれも 1〜2 行。
**ここにファイルシステムの処理を書かない**、という第 1 章からの約束が守られていれば、
この層はこれ以上大きくならない。

マウントは 1 行。

```python
FUSE(TinyFSOperations(image), mountpoint, foreground=True,
     nothreads=True, fsname="tiny-fs")
```

`nothreads=True` を必ず付ける。
`fs.py` はロックを持たないので、同時に 2 つの操作が走ると壊れる。
`fsname` は `df` や `mount` の Filesystem 欄に出る名前で、
省くとクラス名がそのまま表示される。

---

## 5. マウントして触る

```
# python -m tiny_fs.cli mkfs disk.img
created disk.img: 1024 blocks (4 MiB), 128 inodes

# mkdir -p /mnt/tiny-fs
# python -m tiny_fs.fuse_ops disk.img /mnt/tiny-fs -f &

# ls -la /mnt/tiny-fs
total 4
drwxr-xr-x 2 root root 4096 Sep 27 12:00 .
drwxr-xr-x 1 root root 4096 Sep 27 12:00 ..

# echo hello > /mnt/tiny-fs/a.txt
# cat /mnt/tiny-fs/a.txt
hello

# mkdir /mnt/tiny-fs/docs
# cp /etc/hostname /mnt/tiny-fs/docs/
# ls -l /mnt/tiny-fs/docs
total 0
-rw-r--r-- 1 root root 13 Sep 27 12:01 hostname

# df -h /mnt/tiny-fs
Filesystem      Size  Used Avail Use% Mounted on
tiny-fs         4.0M   44K  4.0M   2% /mnt/tiny-fs

# fusermount -u /mnt/tiny-fs
```

**`echo` も `cat` も `cp` も `ls` も、tiny-fs のことを何も知らない。**
これらは普通のシステムコールを呼んでいるだけで、
その先がユーザ空間の Python だとは気づいていない。

アンマウントしてから、同じイメージを CLI で読む。

```
# python -m tiny_fs.cli ls disk.img /
.
..
a.txt
docs

# python -m tiny_fs.cli cat disk.img /a.txt
hello

# python -m tiny_fs.cli fsck disk.img
fsck: clean (11 blocks used, 4 inodes used)
```

FUSE 経由で書いたものが、CLI からそのまま読める。
どちらも同じ `fs.py` を通っているので、当然そうなる。
**FUSE を外しても全機能が使える**、という層の分け方がここで証明される。

---

## 6. つまずく点

| 症状 | 原因 |
|---|---|
| import は通るのにマウントで落ちる | `libfuse2` が入っていない |
| マウントできない（Permission denied） | `--cap-add SYS_ADMIN` か `--device /dev/fuse` がない |
| 大きいファイルの `cp` が途中で失敗する | 48 KiB の上限（`EFBIG`）。仕様どおり |
| `ls -l` が遅い | エントリごとに `getattr` が呼ばれる。1 回ごとにパスを解決し直している |
| 開いたままのファイルを消すと壊れる | `nlink` が 0 になった時点で解放してしまう。オープン数を数えていない |
| 時々おかしくなる | `nothreads=True` を付け忘れている |

`atime` は持っていないので `mtime` を返している。
`ls -lu` は `ls -l` と同じ結果になる。

### テスト

FUSE を必要とするテストは、macOS では動かせない。
第 1 章から守ってきた分離が、ここで効く。

```python
@pytest.mark.fuse
def test_mount_write_unmount(tmp_path):
    # マウントして、シェル経由で書き、アンマウントし、
    # CLI と fsck で内容を確かめる
```

`@pytest.mark.fuse` を付けたテストは**既定でスキップ**する。
Docker の中でだけ `pytest -m fuse` で走らせる。
第 11 章までのテストは、この印が付いていないので macOS でそのまま通る。

---

これで当初の目標に到達した。
512 バイトのセクタしか読み書きできない装置から始めて、
自分で設計したバイト列の上に、本物の `ls` と `cat` が動いている。

次章で、作ったものと、その限界と、次の一歩を整理する。
