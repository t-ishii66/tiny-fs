---
title: "tiny-fs — ファイルシステムを Python で自作する"
description: "512 バイトのセクタしか読み書きできない装置から、i-node・ビットマップ・ディレクトリ・スーパーブロックを自分で並べ、FUSE で Linux にマウントできるファイルシステムを Python で作る教材。"
lang: ja
---

# tiny-fs

[English](README.md) · [日本語](README-jp.md)

![小さなブロックからファイルの街を組み上げる Alice と Bob](docs/images/readme-alice-bob.png)

512 バイトのセクタを読み書きできるだけの「装置」から出発して、その上にファイルシステムを
構築し、最後は FUSE で Linux にマウントするまでを追う教材。実装は Python。

<!-- SEO intro added by setup-github-pages; review and adjust -->

「**ファイルシステムを自作**してみたい」「**i-node** と**ビットマップ**と**ディレクトリ**が
どう繋がっているのか知りたい」「**スーパーブロック**に何が入っているのか確かめたい」
「**OS の中身**を小さくて本物の例で学びたい」 ── そんな人のための教材です。
扱うテーマは、**ブロックデバイス**とセクタ、空き管理、パス解決、`fsck` と 16 進ダンプ、
**FUSE** による Linux へのマウント、そしてその下で動く**ページキャッシュ**と
**ジャーナリング**まで。

<!-- /SEO intro -->

4 MiB のファイル `disk.img` を 1 個のディスクに見立て、スーパーブロック、2 枚のビットマップ、
i-node テーブル、データ領域を自分で並べる。ディスク上のバイト列がいま何を表しているかを、
常に説明できる状態を保つことを優先している。

イメージは 4 MiB 固定、i-node は 128 個、1 ファイルは最大 48 KiB。
間接ブロックもジャーナリングもキャッシュも持たない。

---

## 前提

- Ubuntu
- Python 3.11 以上
- コアは標準ライブラリだけで動く。外から入れるのはテスト用の `pytest` と、
  第 13 章で使う `fusepy` だけ
- 第 13 章で FUSE マウントを試すときだけ `sudo apt install fuse libfuse2t64`
  （sudo が要るのはこのインストールだけで、マウント自体は一般ユーザでできる）

## 用意する

```bash
git clone https://github.com/t-ishii66/tiny-fs.git
cd tiny-fs

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

これで `tiny-fs` コマンドが使えるようになる。実体は venv の中の 1 本のスクリプト。

```
$ which tiny-fs
/home/dev/tiny-fs/.venv/bin/tiny-fs
```

システム側には何も入らない。`deactivate` で PATH から消え、`.venv/` を消せば跡形もなくなる。
`-e`（editable）なのでソースはコピーされず、直したものがそのまま次の実行に効く。

## 動くことを確かめる

```bash
pytest -q

tiny-fs mkfs disk.img
tiny-fs dump disk.img
```

4 MiB のファイルが 1 個でき、その中身が人間に読める形で表示されれば準備は完了。

## 一通り使ってみる

```bash
tiny-fs mkdir disk.img /docs
tiny-fs put   disk.img ./README-jp.md /docs/readme.md
tiny-fs ls -l disk.img /docs
tiny-fs cat   disk.img /docs/readme.md
tiny-fs fsck  disk.img
```

サブコマンドの一覧は[第 11 章](docs/ja/11-cli.md)にある。
`disk.img` は何度作り直してもよい。

---

## 読む

各章の終わりに **試す** があり、その章で作ったものをコマンドで確認できる。

### 第 I 部 問題から概念へ

1. [ディスクという装置](docs/ja/01-device.md) — 512 バイトのセクタしか読み書きできない装置。8 セクタを 1 ブロックに束ねる
2. [どこが空いているか — ビットマップ](docs/ja/02-bitmap.md) — 1 ビット = 1 ブロックで空きを管理する
3. [ビットマップだけでは足りない — i-node](docs/ja/03-inode.md) — 128 バイトの固定長構造体に、ブロック番号の一覧とメタデータをまとめる
4. [名前をどう付けるか — ディレクトリ](docs/ja/04-directory.md) — 名前と i-node 番号の対応表。`.` と `..` で木をつなぐ
5. [地図を描く — レイアウトの確定](docs/ja/05-layout.md) — スーパーブロックと `mkfs`。ここで配置が決まる

### 第 II 部 ファイルシステムとして動かす

6. [パス解決](docs/ja/06-path.md) — `/docs/a.txt` から i-node 番号を得る手順
7. [`fs.py` を実装する](docs/ja/07-fs.md) — イメージを開く、作る、読み書きする、消す
8. [ディレクトリ操作](docs/ja/08-dir-ops.md) — `mkdir` / `rmdir` / `readdir` / `rename`

### 第 III 部 観察と検証

9. [イメージを覗く — dump](docs/ja/09-dump.md) — バイト列を人間に読める形に翻訳する
10. [壊れていないことを確かめる — fsck](docs/ja/10-fsck.md) — 同じ情報を 2 通りに求めて突き合わせる
11. [コマンドラインから触る — CLI](docs/ja/11-cli.md) — ここで普通に使える状態になる

### 第 IV 部 カーネルにつなぐ

12. [マウント](docs/ja/12-mount.md) — 木に木を貼る。パスはどこで別の FS に移るのか
13. [FUSE でマウントする](docs/ja/13-fuse.md) — 本物の `ls` と `cat` から触る。**ここがゴール**

### まとめ

14. [まとめと、次の一歩](docs/ja/14-summary.md)

### 付録

- [A. 定数一覧](docs/ja/A-constants.md)
- [B. エラーコード対応表](docs/ja/B-errno.md)
- [C. tiny-fs の下で起きていること（参考）](docs/ja/C-under-the-hood.md)
- [D. ジャーナリング（参考）](docs/ja/D-journaling.md)

---

## 中身

```
tiny_fs/
  constants.py   レイアウト定数と struct フォーマット
  errors.py      FSError 派生の例外。すべて errno を持つ
  device.py      L0  512 バイトのセクタの読み書き
  disk.py        L1  8 セクタ = 1 ブロック
  bitmap.py      L2  空きの管理
  inode.py       L3  ファイルの中身とメタデータ
  directory.py   L4  名前と木構造
  fs.py          L5  レイアウトの把握、パス解決、FS API
  dump.py        イメージを人間可読で表示する
  fsck.py        整合性を検査する
  cli.py         tiny-fs コマンド
  fuse_ops.py    L6  OS との接続（errno の変換だけ）
tests/
docs/ja/         日本語の解説
docs/en/         英語ドキュメント
docs/images/     図（日本語版）と挿絵。英語版の図は docs/images/en/
```

**下の層は上の層を知らない。** `fuse_ops.py` を外しても、第 11 章までの機能はそのまま動く。


## クレジット

- 企画: t-ishii66
- ドキュメント: Claude Code Opus 5
- コード: Claude Code Opus 5
- ドキュメントレビュー: t-ishii66
- コードレビュー: t-ishii66
- 発行日: 2026/10/10
- バージョン: 1.0.0
