# CLAUDE.md

教材用の極小ファイルシステム **tiny-fs** を、Python で「読めば構造が分かる」形で実装し、
最終的に Linux 上で FUSE としてマウントして本物の `ls` / `cat` / エディタから触れるようにする
プロジェクト。

このファイルは Claude Code 向けのプロジェクト指示書。ユーザ全体設定
(`~/.claude/CLAUDE.md`) を上書きせず、それに追加する形で適用される。

### 名前の使い分け（揺らさない）

プロジェクト名はハイフン付きの **tiny-fs**。ただし Python は識別子にハイフンを使えず
`import tiny-fs` は書けないため、以下の対応で固定する。

| 用途 | 表記 |
|---|---|
| プロジェクト名 / リポジトリ名 / ディレクトリ名 | `tiny-fs` |
| CLI コマンド名 | `tiny-fs` |
| マウントポイント | `~/mnt/tiny-fs` |
| Python パッケージ（import 名・ディレクトリ） | `tiny_fs` |
| 配布名 (`pyproject.toml` の `[project].name`) | `tiny-fs` |
| ディスク上のマジック | `"tfs1"` (`0x74667331`) |

散文やドキュメント中で FS を指すときは `tiny-fs` と書く。`tinyfs`（区切りなし）は使わない。

---

## 1. プロジェクトのゴール

1. **理解できること**が最優先。性能・堅牢性・機能の豊富さは目的ではない。
   ディスク上のバイト列が「いま何を表しているか」を常に説明できる状態を保つ。
2. 1 個のイメージファイル (`disk.img`) をブロックデバイスに見立て、
   その上にスーパーブロック / ビットマップ / i-node テーブル / データ領域を自前で構築する。
3. 同じコアロジックを FUSE 経由で Linux カーネルに接続し、
   「自分で書いた FS が本当にマウントできる」ところまで到達する。

### 明示的なスコープ外（聞かれない限り実装しない）

- 間接ブロック（単/二重/三重）→ **直接ポインタのみ**。ファイルサイズ上限はそれで決まる
- ジャーナリング、write-ahead log、crash consistency
- シンボリックリンク、ハードリンク（リンクカウントは持つが `link()` は実装しない）、
  デバイスファイル、FIFO、ソケット
- 拡張属性 (xattr)、ACL、クォータ
- キャッシュ層、遅延書き込み、mmap
- ファイルの穴 (sparse file)

これらを「ついでに」追加しない。教材としてのノイズになる。
必要になったら必ずユーザに確認してから着手する。

---

## 2. オンディスク・レイアウト（この定義が仕様の中心）

すべてリトルエンディアン。`struct` のフォーマット文字列は必ず先頭に `<` を付ける。

```
block 0            スーパーブロック
block 1            データブロック・ビットマップ (1 block 固定)
block 2            i-node ビットマップ       (1 block 固定)
block 3 .. 3+I-1   i-node テーブル
block 3+I ..       データブロック領域
```

| 定数 | 値 | 備考 |
|---|---|---|
| `SECTOR_SIZE` | 512 | **装置**が読み書きできる最小単位。これより細かい I/O は存在しない |
| `SECTORS_PER_BLOCK` | 8 | 4096 ÷ 512 |
| `BLOCK_SIZE` | 4096 | **ファイルシステム**が扱う単位。8 セクタをまとめたもの |
| `TOTAL_BLOCKS` | 1024 | = 8192 セクタ = 4 MiB のイメージ。**固定**（mkfs に大きさの指定はない） |
| `INODE_SIZE` | 128 | 1 ブロックに 32 個 |
| `NUM_INODES` | 128 | **固定**。→ i-node テーブルは 4 ブロック |
| `NDIRECT` | 12 | i-node の直接ポインタ数 |
| `MAX_FILE_SIZE` | 12 × 4096 = 49152 | **間接参照がないのでこれが上限** |
| `DIRENT_SIZE` | 32 | 1 ブロックに 128 エントリ |
| `MAX_NAME_LEN` | 27 | 32 − 4(inode番号) − 1(NUL) |
| `MAGIC` | `0x74667331` (`"tfs1"`) | スーパーブロック先頭 |

### 構造体

```
superblock  <IIIIIIII>  magic, block_size, total_blocks,
                        num_inodes, inode_table_start,
                        data_start, root_ino, free_hint
inode       <HHIIQQQ12I> mode, nlink, uid, gid,
                        size, mtime, ctime, direct[12]   → 128 バイトに 0 パディング
dirent      <I27sx>     ino, name(NUL埋め), padding      → 32 バイト
```

- ビットマップは 1 ビット = 1 ブロック / 1 i-node。ビット 0 が「空き」。
  ビット順は **LSB first**（バイト `b` のビット `i` がブロック `8*b + i`）。
- i-node 番号は 1 始まり。**0 は「無効／未使用」を表す番兵**として予約する。
  ルートディレクトリは常に ino = 1。
- 空きエントリのディレクトリスロットは `ino == 0` で表す（削除は詰めずに 0 を書く）。
- 各ディレクトリは作成時に `.` と `..` を先頭 2 エントリとして持つ。

イメージの大きさと i-node の個数は**固定**にする。mkfs にサイズ指定の引数を持たせない。
ただしスーパーブロックには `total_blocks` / `num_inodes` / `inode_table_start` /
`data_start` を書き、**上の層はディスクから読んだ値を使う**（イメージが自分の構造を
説明している状態を保つ）。マウント時に定数と一致するかを検証し、合わなければ落とす。

> **重要**: 上記のレイアウトを変更したら、必ず同じコミットで
> `docs/ja/` の該当箇所と `MAGIC`/バージョンを更新する。
> 既存の `disk.img` との互換は維持しなくてよいが、「静かに壊れる」のは最悪。
> mkfs 時にマジックとバージョンを検証し、合わなければ明確なエラーで落とす。

---

## 3. コードの構成（層を混ぜない）

```
tiny_fs/
  constants.py   レイアウト定数と struct フォーマット。ここ以外に数値を書かない
  errors.py      FSError 派生の例外。すべて errno を持つ（層ではない）
  device.py      L0: 装置のモデル。512 B セクタの read_sector/write_sector だけ
  disk.py        L1: 8 セクタ = 1 ブロックにまとめる read_block/write_block だけ
  bitmap.py      L2: ビットの割り当て / 解放 / 走査
  inode.py       L3: i-node の encode/decode、ブロック割り当て、read/write
  directory.py   L4: dirent の追加 / 削除 / 検索 / 列挙
  fs.py          L5: パス解決と FS API（mkfs, create, mkdir, open, read, write,
                     truncate, unlink, rmdir, rename, stat, readdir）
  fsck.py        整合性チェック（ビットマップ vs 実際の参照、nlink、孤立 i-node）
  dump.py        イメージを人間が読める形で可視化（教材の主役の一つ）
  cli.py         `tiny-fs` コマンド
  fuse_ops.py    L6: fusepy の Operations → fs.py への薄い変換だけ
tests/
docs/ja/         日本語の解説（正）
docs/en/         英訳（doc-translator-ja-en が生成）
```

ルール:

- **下の層は上の層を知らない。** `inode.py` が `fs.py` を import したらその設計は間違い。
- セクタを知っているのは `device.py` だけ。`disk.py` より上は **ブロック番号しか見ない**。
- `bitmap.py` / `inode.py` / `directory.py` は「自分がディスク上のどこに置かれているか」を
  知らない。配置を決めるのは `fs.py`（= スーパーブロック）の仕事。
  この分離があるので、解説は「概念 → 実装」の順に進め、レイアウト確定を後ろに回せる。
- `fuse_ops.py` にファイルシステムのロジックを書かない。errno への変換と
  `fs.py` 呼び出しだけに保つ。FUSE を外しても全機能が使えること。
- 例外は `tiny-fs` 独自の例外型 (`FSError` 派生、`errno` 属性を持つ) を使い、
  `fuse_ops.py` の境界で `FuseOSError` に変換する。
- グローバル可変状態を作らない。`FileSystem` インスタンスに閉じる。

---

## 4. コーディング規約

- Python 3.11+。型ヒントを付ける。
- **コアは標準ライブラリのみ**（`struct`, `os`, `time`, `errno`）。
  `fusepy` は `fuse_ops.py` でのみ import する。テストは `pytest`。
- コメントは英語（ユーザ全体設定のルール）。ただし `docs/ja/` 内のコード断片は日本語コメント。
- 教材なので **賢いコードより素直なコード**。ビット演算のワンライナーより、
  意図が読める 3 行を選ぶ。ただし「なぜこうなるか」は必ずコメントか docs に書く。
- マジックナンバー禁止。すべて `constants.py` へ。
- ディスクに書く前に `assert` ではなく明示的な検証＋例外を使う（`-O` で消えるため）。

---

## 5. 作業の進め方（フェーズ）

順番を飛ばさない。各フェーズは「動いて、テストが通って、日本語ドキュメントがある」状態で完了。

**章の一覧は `README-jp.md` にある。準備・動かし方もそこに集約する（`docs/ja/` に置かない）。**

1. **概念の導出と実装（第 I 部）**: `device` → `disk` → `bitmap` → `inode` → `directory`
   → レイアウト確定 (`constants` + `fs.mkfs`)。
   **ドキュメントは「問題 → 必要に迫られて概念を導入 → 実装」の順で書く。**
   完成したレイアウトを先に提示しない
2. **FS API（第 II 部）**: パス解決（第 6 章、手順のみ）→ `fs.py` の実装とファイル操作
   （第 7 章）→ ディレクトリ操作（第 8 章）。各段階で pytest を追加。
   ここが終わった時点で FUSE なしに全操作ができる
3. **可視化 / 検証**: `dump.py` と `fsck.py`。
   「mkfs 直後のイメージ」「ファイルを 1 個作った後」の差分を見せられるようにする
4. **CLI**: `tiny-fs mkfs / ls / mkdir / put / get / cat / rm / dump / fsck`
5. **FUSE**: `fuse_ops.py` + ホスト上でのマウント検証
6. **英訳**: `doc-translator-ja-en` で `docs/en/` を生成

---

## 6. コマンド

```bash
python -m pytest -q
tiny-fs mkfs disk.img
tiny-fs dump disk.img            # レイアウトを人間可読で表示
tiny-fs fsck disk.img
tiny-fs mkdir disk.img /docs
tiny-fs put  disk.img ./README-jp.md /docs/readme.md
tiny-fs ls   disk.img /docs
tiny-fs cat  disk.img /docs/readme.md
tiny-fs get  disk.img /docs/readme.md ./copy.md
tiny-fs mv   disk.img /docs/readme.md /readme.md
tiny-fs rm   disk.img /readme.md
tiny-fs rmdir disk.img /docs

# FUSE（第 12 章。sudo apt install fuse libfuse2t64 && pip install fusepy）
mkdir -p ~/mnt/tiny-fs
python -m tiny_fs.fuse_ops disk.img ~/mnt/tiny-fs -f   # -f = フォアグラウンド
fusermount -u ~/mnt/tiny-fs
```

---

## 7. 実行環境の前提

- 前提は **Ubuntu**（24.04 で確認）。FUSE もホストでそのまま動く。コンテナも特権も要らない。
- FUSE に要るのは `fuse`（setuid の `fusermount`）、`libfuse2t64`（`libfuse.so.2`）、
  そして `fusepy`。**マウント自体は一般ユーザでできる**（sudo が要るのは apt だけ）。
  Ubuntu 22.04 では `libfuse2t64` が `libfuse2`。
- FUSE を必要とするテストは `@pytest.mark.fuse` で選べるようにするが、**既定でも走らせる**。
  `fusepy` か `/dev/fuse` が無ければ、そのテストが自分でスキップする。
- バインディングは **fusepy**（同期・`Operations` サブクラス方式）。
  `pyfuse3` に乗り換えない。教材としての可読性を優先している。
- アンマウントは `fusermount -u ~/mnt/tiny-fs`。フォアグラウンド実行中は Ctrl-C。

---

## 8. テスト方針

- すべてのテストは `tmp_path` 上に自前で mkfs したイメージを使う。既存の `disk.img` に依存しない。
- 各テストの最後に **`fsck` を通す**のを基本形にする（ビットマップと実参照の不一致を早期に検出）。
- 必ず押さえる境界ケース:
  - ファイルサイズが `MAX_FILE_SIZE` ちょうど / +1（後者は `EFBIG`）
  - 名前長が `MAX_NAME_LEN` ちょうど / +1（後者は `ENAMETOOLONG`）
  - ディスクフル、i-node 枯渇 (`ENOSPC`)
  - 空でないディレクトリの `rmdir` (`ENOTEMPTY`)、`.` / `..` の削除拒否
  - ブロック境界をまたぐ read/write、オフセット付き部分書き込み、`truncate` の伸長（0 埋め）
  - 未使用ブロックの解放漏れ（書いて消して `fsck` で空き数が戻ること）

---

## 9. ドキュメント

- `docs/ja/` が正。**日本語側だけを編集する**（ユーザ全体設定の bilingual ルール）。
  `docs/en/` は `doc-translator-ja-en` で後から生成する。
  リポジトリの入口は `README-jp.md`（これも日本語が正、`README.md` は英訳）。
- 解説は「バイト列 → 意味」の対応が見えるように書く。
  `dump.py` の実出力を貼るのが最も効く。
- **図の向きは常に「左から 0」。** セクタ番号もブロック番号も i-node 番号もビット番号も、
  左端が 0 で右に向かって増える。16 進ダンプ上の並び（バイト内では下位ビットが右）と
  逆になる箇所は、図をダンプに合わせるのではなく、本文で一言ことわる。
- **図は SVG**。`docs/images/NN-name.svg` に置き、`docs/ja/` からは `../images/...` で参照する
  （`docs/en/` からも同じ相対パスで届くので、図は 1 枚だけ持つ）。
  ASCII 図や mermaid は使わない。ただし **16 進ダンプは図ではない**ので、
  コードブロックのテキストのまま貼る（コピーして grep できることに意味がある）。
  SVG は背景に明示的な白い矩形を敷く（GitHub のダークテーマで文字が消えないため）。
- **短いほうがよい。** 図を見れば分かることを本文で言い直さない。
  同じことを表と文章の両方で書かない。歴史的経緯や豆知識は書かない。
  1 文削って意味が変わらないなら、削る。
- **実装に忠実であろうとして、本筋を曇らせない。** コードは理解を進めるためにあり、
  ドキュメントはその仕様書ではない。通常の操作で成り立つことを言い切り、
  例外的な経路まで本文に書き足さない（「常に」「必ず」と書いた一文を守るために
  端のケースの但し書きが要るなら、但し書きではなく「常に」を外す）。
- **読み手が当たり前に受け入れる結論を、理詰めで導かない。**
  「A が要る。それは B なので C でなければならない」と積み上げると、
  結論が自明なぶんだけ回りくどくなる。言い切って次へ進む
  （例:「管理情報もディスクに置く」に理由付けは要らない）。
  導出を書くのは、結論が読み手の予想と違うときだけ。
- **採る方法を 1 つだけ、単刀直入に提示する。** 「① こういう案もあるが壊れる、
  ② これもあるが遅い、だから ③ を採る」という比較の形で書かない。
  読み手が知りたいのは「この FS はこう動く」であって、選択肢の一覧ではない。
  仕組みを 1 つ示し、それが機能することが分かれば足りる。
- 各章は **この FS がどう動くか** だけを書く。
  「なぜこの設計にしたか」という設計の弁明や、ext2 など他のファイルシステムとの比較を
  章の中に混ぜない。どちらも**全体を理解したあとに湧いてくる疑問**で、
  途中に置くと読み進める邪魔になる。最終章に集めるのも避ける
  （集めても量は変わらず、読了感を損なうだけだった）。書かない。
  ただし、その場で必要な判断の根拠（「ここは `capacity` で打ち切る。ビットの意味を
  1 つに保つため」程度）は 1〜2 文で本文に書いてよい。節にしない。

---

## 10. Git

- `disk.img` や `*.img`、`mnt/` は `.gitignore` に入れる（成果物であり巨大になりうる）。
- `PROBLEMS.md` はコミットしない（ユーザ全体設定のルール）。
