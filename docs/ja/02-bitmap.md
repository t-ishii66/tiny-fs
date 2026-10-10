# 02. どこが空いているか — ビットマップ

![雨の日にジグソーパズルで遊ぶ Alice と Bob](../images/02-alice-bob.png)

セクタを読み書きできるだけでは、ファイルは作れない。
この章から、その問題に取り組む。

最初に要るのは**空き管理**。これがないと、ファイルを 1 個置く場所すら決められない。

---

## 1. 問題 — 使用中か未使用かを区別したい

4 MiB を 4096 バイトずつに区切ると、**ブロックは全部で 1024 個**ある。
このどれが使用中かは、装置に聞いても中身を読んでも分からない（第 1 章）。
別に記録しておくしかない。

---

## 2. ビットマップという方法

こういうときの定番が**ビットマップ**。

記録したいのは 1 ブロックにつき「使用中か / 空きか」の 2 値だけなので、
**1 ビットあれば足りる**。そこで、ブロック番号 `n` の状態を `n` 番目のビットに入れる。
**位置そのものがブロック番号を表す**ので、「どのブロックの話か」を書かずに済む。

1024 ブロックなら 1024 ビット = **128 バイト**。ビットマップ全体が 1 ブロックに楽に収まる。
ビットの意味は **0 が空き、1 が使用中**とする。

![ビット順は LSB first](../images/02-bit-order.svg)

ビット順は **LSB first**。バイト `b` のビット `i` がブロック `8 * b + i` に対応する。

```python
# ブロック番号 → (何バイト目の, 何ビット目か)
byte_index, bit_index = divmod(block_no, 8)
mask = 1 << bit_index

is_used = buf[byte_index] & mask != 0
```

> **Python tips** — `divmod(a, b)` は商と余りをまとめて返す組み込み関数で、
> `(a // b, a % b)` と同じ。`divmod(11, 8)` は `(1, 3)` なので、
> 「ブロック 11 は 1 バイト目の 3 ビット目」が 1 行で求まる。
> 第 3 章の「オフセット → (何番目のブロックの, 何バイト目か)」も同じ形になる。

**図の並びとコードの並びは一致している。** 逆に見えるのは、
その 1 バイトを 16 進や 2 進の「数」として書いたときだけ。

```python
# 図の並び（左からブロック 0）
#   ブロック : 0  1  2  3  4  5  6  7
#   ビット   : 1  1  1  1  0  1  0  0
#
# これを 1 バイトの数として書くと
#   buf[0] == 0x2f == 0b0010_1111
#                        ~~~~ ~~~~ ← 数は上位桁が左なので、図と左右が逆に見える
#
# 図から読んでも数から読んでも、ブロック n を調べる式は同じ
buf[0] >> 3 & 1 == 1     # ブロック 3 は使用中
buf[0] >> 4 & 1 == 0     # ブロック 4 は空き
```

### 1 ブロックでどこまで管理できるか

ビットマップ自身も 1 ブロック (4096 バイト) を使う。
1024 個のブロックのうち、1 個がこのビットマップで、
残りがファイルの中身を置く**データブロック**になる。

1024 ブロックの管理に要るのは先頭の 128 バイトだけで、残りは 0 のまま置いておく。
その 0 は「空きブロック」ではないので、**走査を `capacity` で打ち切る**。
余りを 1 で埋めないのは、ビットの意味を「0 = 空き / 1 = 使用中」の 1 つに保つため。

1 ブロックで表せる上限は 4096 × 8 = 32768 ブロック = 128 MiB。
tiny-fs は 1024 ブロック固定なので、ここには届かない。

---

## 3. 割り当てと解放

![割り当てと解放 — 変わるのは 1 ビットだけ](../images/02-alloc-free.svg)

**割り当て**は「先頭から探して、最初に見つかった 0 のビットを 1 にして、その番号を返す」。
これを **first-fit** という。

**解放**は「そのビットを 0 にする」。それだけで、
**データブロックの中身には触らない**（次に割り当てられたときに上書きされる）。
削除したファイルの中身がディスクに残り続けるのは、このため。

境界の扱いを 2 つ決めておく。

- **空きがない**とき、`allocate()` は `-1` や `None` を返さず、`ENOSPC` の例外で落とす
  （`ENOSPC` は POSIX のエラー番号で「装置に空きがない」。付録 B に一覧がある）。
- **すでに 0 のビットを解放**しようとしたら例外にする。二重解放を黙って成功させると、
  あとで同じブロックが 2 つのファイルに割り当てられる。

### `free_hint` — 毎回先頭から探さないために

毎回先頭から探すと、ディスクの先頭ばかりを何度も走査することになる。
そこで**次に探し始める位置をヒントとして受け取る**。
この値はスーパーブロックに持たせて永続化する（第 5 章）。

ヒントが古くても間違っていても、正しさには影響しない（探索が一周するだけ）。

---

## 4. 実装 — `bitmap.py` (L2)

この層はディスクを触らない。**メモリ上の `bytearray` を受け取って、ビットを操作するだけ。**
ブロックを読んで渡し、変更後に書き戻すのは上の層 (`fs.py`) の仕事。

> **Python tips** — `bytearray` は `bytes` の書き換えられる版の組み込み型で、
> どちらも中身は 0〜255 の整数の並び。`buf[i] |= ...` のような代入は `bytes` ではできないので、
> ディスクから読んだ `bytes` は `bytearray(...)` でコピーしてからこの層に渡す（第 7 章）。
> 整数を渡す `bytearray(4096)` は「4096 バイトのゼロ」になる。
> 第 3 章では、割り当てたばかりのデータブロックを
> ディスクから読まずに「全部ゼロ」として扱うのに使う。
> 要素に代入できるのは 0〜255 だけで、外れると `ValueError`。

```python
class Bitmap:
    """ビットの配列。ビット i が 1 なら「i 番は使用中」。

    buf はディスクから読んだ 1 ブロック分そのもの。capacity は
    「そのうち何個までが実在するか」（例: 1024 ブロック / 129 i-node）。
    """

    def __init__(self, buf: bytearray, capacity: int) -> None:
        self._buf = buf
        self._capacity = capacity

    def is_used(self, index: int) -> bool:
        self._check_range(index)
        byte_index, bit_index = divmod(index, 8)
        return self._buf[byte_index] & (1 << bit_index) != 0

    def mark_used(self, index: int) -> None:
        self._check_range(index)
        byte_index, bit_index = divmod(index, 8)
        self._buf[byte_index] |= 1 << bit_index

    def mark_free(self, index: int) -> None:
        self._check_range(index)
        if not self.is_used(index):
            # 二重解放。上の層のバグか、イメージが壊れている
            raise CorruptionError(f"index {index} is already free")
        byte_index, bit_index = divmod(index, 8)
        # Python の ~ は負の数を返す（~1 == -2）ので、必ず & 0xFF で 1 バイトに戻す。
        # ここを忘れると bytearray への代入で ValueError になる。
        self._buf[byte_index] &= ~(1 << bit_index) & 0xFF
```

割り当ては、ヒントから一周するループにする。

```python
    def allocate(self, hint: int = 0) -> int:
        # capacity で打ち切るので、buf の余っている部分は決して割り当てられない
        order = list(range(hint, self._capacity)) + list(range(0, hint))
        for index in order:
            if not self.is_used(index):
                self.mark_used(index)
                return index
        raise NoSpaceError("no free entry left")

    def count_used(self) -> int:
        # capacity は 8 の倍数とは限らない。
        # 最後の 1 バイトは範囲内のビットだけをマスクしてから数える。
        full_bytes, extra_bits = divmod(self._capacity, 8)
        used = sum(byte.bit_count() for byte in self._buf[:full_bytes])
        if extra_bits:
            mask = (1 << extra_bits) - 1
            used += (self._buf[full_bytes] & mask).bit_count()
        return used

    def count_free(self) -> int:
        return self._capacity - self.count_used()
```

> **Python tips** — `bit_count()` は Python 3.10 以降の `int` のメソッド。
> 自前で数えるより速く、何より「立っているビットを数える」という意図がそのまま出る。

---

## 5. テスト

```python
def test_alloc_marks_used():
    # 割り当てたら is_used が True、同じ番号は二度と返らない

def test_alloc_until_full_raises_nospace():
    # capacity 個取れて、次で ENOSPC

def test_free_then_alloc_reuses_the_same_index():
    # 解放したらその番号がまた返ってくる（= 空きが本当に戻っている）

def test_double_free_raises():
    # 二重解放は黙って通さない

def test_bit_position_matches_the_spec():
    # index 8 を使用中にしたら、buf[1] == 0x01 になること。
    # 「ビット順の規約」を直接確かめる。往復テストだけでは
    # MSB first で実装してしまっても気づけない。

def test_capacity_limits_allocation():
    # buf は 4096 バイトでも capacity が 1024 なら 1024 個しか取れず、
    # buf の 128 バイト目以降は 0 のまま
```

**自分で決めた規約は、往復テストではなく規約そのものを見るテストで守る。**
5 つ目が第 1 章の「ブロックとセクタの対応」テストと同じ役割を持つ。

---

## 試す

```bash
pytest -q tests/test_bitmap.py
```

---

次章では、ブロックを確保できるようになった状態から一歩進める。
**確保したブロックが「どのファイルの何バイト目なのか」を、どこに書いておくか。**
