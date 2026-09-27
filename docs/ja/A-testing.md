# 付録 A. テストの書き方

各章に出てきたテストの方針をまとめる。

---

## 1. 基本の形

**毎回 `tmp_path` に mkfs して、最後に `fsck` を通す。**
既存の `disk.img` には依存しない。

```python
# tests/conftest.py
@pytest.fixture
def image(tmp_path) -> str:
    """まっさらなイメージのパス。"""
    path = tmp_path / "disk.img"
    mkfs(str(path))
    return str(path)


@pytest.fixture
def fs(image):
    """開いた FileSystem。テストが終わったら閉じて fsck にかける。"""
    filesystem = FileSystem(BlockDevice(SectorDevice(image)))
    yield filesystem
    filesystem.close()
    # 終わった時点で整合性が取れていること
    with BlockDevice(SectorDevice(image)) as blocks:
        assert fsck(blocks) == []
```

イメージのパスだけが欲しいテスト（開き直しの確認、`dump` の出力比較、
わざと壊す `fsck` のテスト）は `image` を、
操作したいテストは `fs` を受け取る。

`fsck` を後片付けに置くと、**すべてのテストが整合性検査を兼ねる**。
解放漏れや `nlink` の食い違いは、それを狙ったテストではなく
まったく別のテストの後片付けで見つかることが多い。

---

## 2. 規約そのものを見るテスト

往復テスト（書いて読む）は、**両方が同じように間違っていると通ってしまう**。
自分で決めた規約は、規約そのものを見るテストで守る。

| 章 | 規約 | テスト |
|---|---|---|
| 1 | 1 ブロック = 8 セクタ | ブロック 1 に書いたら、セクタ 8–15 が変わり、7 と 16 は変わらない |
| 2 | ビット順は LSB first | index 8 を使用中にしたら `buf[1] == 0x01` |
| 3 | i-node は 128 バイト | `encode()` の長さと、余白 44 バイトがゼロであること |
| 4 | 削除で詰めない | 真ん中を消しても、後ろのエントリのスロット番号が変わらない |
| 5 | レイアウト | mkfs 直後の `dump` 出力が、期待する文字列と完全に一致する |
| 7 | 書き戻し | 書いたあと開き直しても読める |
| 9 | 読むだけ | `dump` の前後でイメージが 1 バイトも変わらない |

---

## 3. 押さえる境界ケース

| 対象 | ちょうど | 1 つ超え |
|---|---|---|
| ファイルサイズ | 49152 バイト（48 KiB） | 49153 → `EFBIG` |
| 名前の長さ | 27 バイト | 28 → `ENAMETOOLONG` |
| i-node 数 | 127 個 | 128 個目 → `ENOSPC` |
| ディレクトリのエントリ | 1536 個 | 1537 個目 → `ENOSPC` |
| ブロック | 1017 個使い切る | 次の書き込み → `ENOSPC` |

加えて次を必ず入れる。

- ブロック境界をまたぐ read / write（4090 バイト目から 12 バイト）
- ブロックの途中だけ書き換えて、前後が壊れていないこと
- `truncate` の伸長がゼロで埋まること、縮小でブロックが返ること
- 空でないディレクトリの `rmdir` → `ENOTEMPTY`
- `.` と `..` の削除・改名の拒否
- 書いて消して `fsck`、空きブロック数と空き i-node 数が元に戻ること

---

## 4. 壊れたイメージを作る

`fsck` のテストには、**わざと壊したイメージ**が要る。
`BlockDevice` を直接使えば、どのバイトでも書き換えられる。

```python
def test_detects_leaked_block(image):
    with BlockDevice(SectorDevice(image)) as blocks:
        # ビットマップのビットを 1 つ立てるだけで、参照のないブロックができる
        buf = bytearray(blocks.read_block(BLOCK_BITMAP_BLOCK))
        buf[2] |= 0x01                       # ブロック 16 を使用中に
        blocks.write_block(BLOCK_BITMAP_BLOCK, buf)

    with BlockDevice(SectorDevice(image)) as blocks:
        assert any("leak" in p for p in fsck(blocks))
```

---

## 5. FUSE のテストを分ける

FUSE は Linux でしか動かないので、印を付けて**既定でスキップ**する。

```toml
# pyproject.toml
[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["fuse: FUSE が要るテスト（Docker の Linux でのみ実行）"]
addopts = "-m 'not fuse'"
```

```python
@pytest.mark.fuse
def test_mount_write_unmount(tmp_path):
    ...
```

macOS では `pytest` がそのまま通り、Docker の中では `pytest -m fuse` で
FUSE のテストだけを走らせる。
第 1 章から層を分けてきたのは、この分離が成立する状態を保つためでもある。
