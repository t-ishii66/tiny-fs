# 00. 準備 — 手元で動かす

各章には、その章で作ったものを試すためのコマンドが付いている。
先にこの状態を作っておけば、読みながらそのまま実行できる。

第 1 章から第 11 章までは、macOS でも Linux でもそのまま動く。
**Docker が要るのは第 12 章（FUSE）だけ。**

---

## 1. 用意する

```bash
git clone https://github.com/t-ishii66/tiny-fs.git
cd tiny-fs

python3 -m venv .venv
source .venv/bin/activate          # Windows は .venv\Scripts\activate

pip install -e ".[dev]"            # 本体 + pytest
```

Python は 3.11 以上。本体は標準ライブラリしか使っていないので、
入るのはテスト用の `pytest` だけになる。

これで `tiny-fs` コマンドが使えるようになる。
インストールせずに使うなら、以降の `tiny-fs` をすべて
`python -m tiny_fs.cli` に読み替える。

---

## 2. テストを走らせる

```bash
pytest -q
```

FUSE を必要とするテストは既定でスキップされるので、macOS でも全部通る。

```bash
pytest -q tests/test_bitmap.py     # 1 ファイルだけ
pytest -q -x --ff                  # 失敗したところで止める
```

---

## 3. 動くことを確かめる

```bash
tiny-fs mkfs disk.img
tiny-fs dump disk.img
```

4 MiB のファイルが 1 個でき、その中身が人間に読める形で表示される。
ここまで出れば準備は完了になる。

```bash
rm disk.img        # 各章で作り直すので、消してかまわない
```

---

## 4. 各章の「試す」

章の終わりに **試す** という節がある。
その章で説明したことを、3〜5 行のコマンドで確認できるようにしてある。

| 章 | 試せること |
|---|---|
| 01–04 | 各層の単体テスト |
| 05 | mkfs 直後のイメージを読む |
| 06 | `.` と `..` をたどる |
| 07–08 | 操作がディスクのどこを変えるか |
| 09–10 | 差分の取り方、壊して検出する |
| 11 | 一通りの操作を通しで |
| 12 | FUSE でマウントする（Docker） |

`disk.img` は何度作り直してもいい。`.gitignore` に入れてあるので、
消し忘れてもコミットされることはない。
