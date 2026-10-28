"""ECC200 Data Matrix 编码。符号表、纠错与放置遵循 ISO/IEC 16022 / ZXing。"""
from __future__ import annotations

from dataclasses import dataclass

_MODULO = 0x12D
_PAD = 129

_FACTOR_SETS = (5, 7, 10, 11, 12, 14, 18, 20, 24, 28, 36, 42, 48, 56, 62, 68)
_FACTORS = (
    (228, 48, 15, 111, 62),
    (23, 68, 144, 134, 240, 92, 254),
    (28, 24, 185, 166, 223, 248, 116, 255, 110, 61),
    (175, 138, 205, 12, 194, 168, 39, 245, 60, 97, 120),
    (41, 153, 158, 91, 61, 42, 142, 213, 97, 178, 100, 242),
    (156, 97, 192, 252, 95, 9, 157, 119, 138, 45, 18, 186, 83, 185),
    (83, 195, 100, 39, 188, 75, 66, 61, 241, 213, 109, 129, 94, 254, 225, 48, 90, 188),
    (15, 195, 244, 9, 233, 71, 168, 2, 188, 160, 153, 145, 253, 79, 108, 82, 27, 174, 186, 172),
    (52, 190, 88, 205, 109, 39, 176, 21, 155, 197, 251, 223, 155, 21, 5, 172, 254, 124, 12, 181, 184, 96, 50, 193),
    (211, 231, 43, 97, 71, 96, 103, 174, 37, 151, 170, 53, 75, 34, 249, 121, 17, 138, 110, 213, 141, 136, 120, 151, 233, 168, 93, 255),
    (245, 127, 242, 218, 130, 250, 162, 181, 102, 120, 84, 179, 220, 251, 80, 182, 229, 18, 2, 4, 68, 33, 101, 137, 95, 119, 115, 44, 175, 184, 59, 25, 225, 98, 81, 112),
    (77, 193, 137, 31, 19, 38, 22, 153, 247, 105, 122, 2, 245, 133, 242, 8, 175, 95, 100, 9, 167, 105, 214, 111, 57, 121, 21, 1, 253, 57, 54, 101, 248, 202, 69, 50, 150, 177, 226, 5, 9, 5),
    (245, 132, 172, 223, 96, 32, 117, 22, 238, 133, 238, 231, 205, 188, 237, 87, 191, 106, 16, 147, 118, 23, 37, 90, 170, 205, 131, 88, 120, 100, 66, 138, 186, 240, 82, 44, 176, 87, 187, 147, 160, 175, 69, 213, 92, 253, 225, 19),
    (175, 9, 223, 238, 12, 17, 220, 208, 100, 29, 175, 170, 230, 192, 215, 235, 150, 159, 36, 223, 38, 200, 132, 54, 228, 146, 218, 234, 117, 203, 29, 232, 144, 238, 22, 150, 201, 117, 62, 207, 164, 13, 137, 245, 127, 67, 247, 28, 155, 43, 203, 107, 233, 53, 143, 46),
    (242, 93, 169, 50, 144, 210, 39, 118, 202, 188, 201, 189, 143, 108, 196, 37, 185, 112, 134, 230, 245, 63, 197, 190, 250, 106, 185, 221, 175, 64, 114, 71, 161, 44, 147, 6, 27, 218, 51, 63, 87, 10, 40, 130, 188, 17, 163, 31, 176, 170, 4, 107, 232, 7, 94, 166, 224, 124, 86, 47, 11, 204),
    (220, 228, 173, 89, 251, 149, 159, 56, 89, 33, 147, 244, 154, 36, 73, 127, 213, 136, 248, 180, 234, 197, 158, 177, 68, 122, 93, 213, 15, 160, 227, 236, 66, 139, 153, 185, 202, 167, 179, 25, 220, 232, 96, 210, 231, 136, 223, 239, 181, 241, 59, 52, 172, 25, 49, 232, 211, 189, 64, 54, 108, 153, 132, 63, 96, 103, 82, 186),
)

_LOG = [0] * 256
_ALOG = [0] * 255
_p = 1
for _i in range(255):
    _ALOG[_i] = _p
    _LOG[_p] = _i
    _p *= 2
    if _p >= 256:
        _p ^= _MODULO


@dataclass(frozen=True)
class SymbolInfo:
    data_capacity: int
    error_codewords: int
    matrix_width: int
    matrix_height: int
    data_regions: int
    rs_block_data: int
    rs_block_error: int

    @property
    def horiz_regions(self) -> int:
        return {1: 1, 2: 2, 4: 2, 16: 4, 36: 6}[self.data_regions]

    @property
    def vert_regions(self) -> int:
        return {1: 1, 2: 1, 4: 2, 16: 4, 36: 6}[self.data_regions]

    @property
    def symbol_data_width(self) -> int:
        return self.horiz_regions * self.matrix_width

    @property
    def symbol_data_height(self) -> int:
        return self.vert_regions * self.matrix_height

    @property
    def symbol_width(self) -> int:
        return self.symbol_data_width + self.horiz_regions * 2

    @property
    def symbol_height(self) -> int:
        return self.symbol_data_height + self.vert_regions * 2

    @property
    def block_count(self) -> int:
        return self.data_capacity // self.rs_block_data


def _sym(data: int, error: int, mw: int, mh: int, regions: int, rs_data: int | None = None, rs_error: int | None = None) -> SymbolInfo:
    return SymbolInfo(data, error, mw, mh, regions, rs_data if rs_data is not None else data, rs_error if rs_error is not None else error)


# 正方形符号，与 ZXing SymbolInfo.PROD_SYMBOLS 对齐
_SQUARE_SYMBOLS = (
    _sym(3, 5, 8, 8, 1),
    _sym(5, 7, 10, 10, 1),
    _sym(8, 10, 12, 12, 1),
    _sym(12, 12, 14, 14, 1),
    _sym(18, 14, 16, 16, 1),
    _sym(22, 18, 18, 18, 1),
    _sym(30, 20, 20, 20, 1),
    _sym(36, 24, 22, 22, 1),
    _sym(44, 28, 24, 24, 1),
    _sym(62, 36, 14, 14, 4),
    _sym(86, 42, 16, 16, 4),
    _sym(114, 48, 18, 18, 4),
    _sym(144, 56, 20, 20, 4),
    _sym(174, 68, 22, 22, 4),
    _sym(204, 84, 24, 24, 4, 102, 42),
    _sym(280, 112, 14, 14, 16, 140, 56),
    _sym(368, 144, 16, 16, 16, 92, 36),
    _sym(456, 192, 18, 18, 16, 114, 48),
    _sym(576, 224, 20, 20, 16, 144, 56),
    _sym(696, 272, 22, 22, 16, 174, 68),
    _sym(816, 336, 24, 24, 16, 136, 56),
    _sym(1050, 408, 18, 18, 36, 175, 68),
    _sym(1304, 496, 20, 20, 36, 163, 62),
)


def encode_ascii(text: str) -> list[int]:
    out: list[int] = []
    index = 0
    while index < len(text):
        ch = text[index]
        if ch.isdigit() and index + 1 < len(text) and text[index + 1].isdigit():
            out.append(130 + int(text[index : index + 2]))
            index += 2
            continue
        code = ord(ch)
        if code > 127:
            out.append(235)
            out.append(code - 127)
        else:
            out.append(code + 1)
        index += 1
    return out


def _pad(data: list[int], capacity: int) -> list[int]:
    out = list(data)
    if len(out) < capacity:
        out.append(_PAD)
    while len(out) < capacity:
        pos = len(out) + 1
        value = _PAD + ((149 * pos) % 253) + 1
        if value > 254:
            value -= 254
        out.append(value)
    return out


def _lookup_symbol(data_len: int, min_symbol: int = 0) -> SymbolInfo:
    for info in _SQUARE_SYMBOLS:
        if info.symbol_width < min_symbol:
            continue
        if data_len <= info.data_capacity:
            return info
    raise ValueError(f"数据过长，无法放入 Data Matrix：{data_len} 码字")


def _create_ecc_block(codewords: list[int], num_ec: int) -> list[int]:
    try:
        table = _FACTOR_SETS.index(num_ec)
    except ValueError as exc:
        raise ValueError(f"不支持的纠错码字数：{num_ec}") from exc
    poly = _FACTORS[table]
    ecc = [0] * num_ec
    for word in codewords:
        m = ecc[-1] ^ word
        for k in range(num_ec - 1, 0, -1):
            if m and poly[k]:
                ecc[k] = ecc[k - 1] ^ _ALOG[(_LOG[m] + _LOG[poly[k]]) % 255]
            else:
                ecc[k] = ecc[k - 1]
        if m and poly[0]:
            ecc[0] = _ALOG[(_LOG[m] + _LOG[poly[0]]) % 255]
        else:
            ecc[0] = 0
    return list(reversed(ecc))


def _encode_ecc200(data: list[int], info: SymbolInfo) -> list[int]:
    if len(data) != info.data_capacity:
        raise ValueError("码字数与符号容量不一致")
    if info.block_count == 1:
        return data + _create_ecc_block(data, info.error_codewords)
    out = list(data) + [0] * info.error_codewords
    for block in range(info.block_count):
        temp = [data[d] for d in range(block, info.data_capacity, info.block_count)]
        ecc = _create_ecc_block(temp, info.rs_block_error)
        pos = 0
        for err_i in range(block, info.rs_block_error * info.block_count, info.block_count):
            out[info.data_capacity + err_i] = ecc[pos]
            pos += 1
    return out


class _Placement:
    def __init__(self, codewords: list[int], numcols: int, numrows: int):
        self.codewords = codewords
        self.numcols = numcols
        self.numrows = numrows
        self.bits = [-1] * (numcols * numrows)

    def get_bit(self, col: int, row: int) -> bool:
        return self.bits[row * self.numcols + col] == 1

    def _set(self, col: int, row: int, bit: bool) -> None:
        self.bits[row * self.numcols + col] = 1 if bit else 0

    def _empty(self, col: int, row: int) -> bool:
        return self.bits[row * self.numcols + col] < 0

    def _module(self, row: int, col: int, pos: int, bit: int) -> None:
        if row < 0:
            row += self.numrows
            col += 4 - ((self.numrows + 4) % 8)
        if col < 0:
            col += self.numcols
            row += 4 - ((self.numcols + 4) % 8)
        value = self.codewords[pos]
        self._set(col, row, bool(value & (1 << (8 - bit))))

    def _utah(self, row: int, col: int, pos: int) -> None:
        self._module(row - 2, col - 2, pos, 1)
        self._module(row - 2, col - 1, pos, 2)
        self._module(row - 1, col - 2, pos, 3)
        self._module(row - 1, col - 1, pos, 4)
        self._module(row - 1, col, pos, 5)
        self._module(row, col - 2, pos, 6)
        self._module(row, col - 1, pos, 7)
        self._module(row, col, pos, 8)

    def _corner1(self, pos: int) -> None:
        self._module(self.numrows - 1, 0, pos, 1)
        self._module(self.numrows - 1, 1, pos, 2)
        self._module(self.numrows - 1, 2, pos, 3)
        self._module(0, self.numcols - 2, pos, 4)
        self._module(0, self.numcols - 1, pos, 5)
        self._module(1, self.numcols - 1, pos, 6)
        self._module(2, self.numcols - 1, pos, 7)
        self._module(3, self.numcols - 1, pos, 8)

    def _corner2(self, pos: int) -> None:
        self._module(self.numrows - 3, 0, pos, 1)
        self._module(self.numrows - 2, 0, pos, 2)
        self._module(self.numrows - 1, 0, pos, 3)
        self._module(0, self.numcols - 4, pos, 4)
        self._module(0, self.numcols - 3, pos, 5)
        self._module(0, self.numcols - 2, pos, 6)
        self._module(0, self.numcols - 1, pos, 7)
        self._module(1, self.numcols - 1, pos, 8)

    def _corner3(self, pos: int) -> None:
        self._module(self.numrows - 3, 0, pos, 1)
        self._module(self.numrows - 2, 0, pos, 2)
        self._module(self.numrows - 1, 0, pos, 3)
        self._module(0, self.numcols - 2, pos, 4)
        self._module(0, self.numcols - 1, pos, 5)
        self._module(1, self.numcols - 1, pos, 6)
        self._module(2, self.numcols - 1, pos, 7)
        self._module(3, self.numcols - 1, pos, 8)

    def _corner4(self, pos: int) -> None:
        self._module(self.numrows - 1, 0, pos, 1)
        self._module(self.numrows - 1, self.numcols - 1, pos, 2)
        self._module(0, self.numcols - 3, pos, 3)
        self._module(0, self.numcols - 2, pos, 4)
        self._module(0, self.numcols - 1, pos, 5)
        self._module(1, self.numcols - 3, pos, 6)
        self._module(1, self.numcols - 2, pos, 7)
        self._module(1, self.numcols - 1, pos, 8)

    def place(self) -> None:
        pos = 0
        row = 4
        col = 0
        while True:
            if row == self.numrows and col == 0:
                self._corner1(pos)
                pos += 1
            if row == self.numrows - 2 and col == 0 and (self.numcols % 4):
                self._corner2(pos)
                pos += 1
            if row == self.numrows - 2 and col == 0 and self.numcols % 8 == 4:
                self._corner3(pos)
                pos += 1
            if row == self.numrows + 4 and col == 2 and self.numcols % 8 == 0:
                self._corner4(pos)
                pos += 1
            while True:
                if row < self.numrows and col >= 0 and self._empty(col, row):
                    self._utah(row, col, pos)
                    pos += 1
                row -= 2
                col += 2
                if row < 0 or col >= self.numcols:
                    break
            row += 1
            col += 3
            while True:
                if row >= 0 and col < self.numcols and self._empty(col, row):
                    self._utah(row, col, pos)
                    pos += 1
                row += 2
                col -= 2
                if row >= self.numrows or col < 0:
                    break
            row += 3
            col += 1
            if row >= self.numrows and col >= self.numcols:
                break
        if self._empty(self.numcols - 1, self.numrows - 1):
            self._set(self.numcols - 1, self.numrows - 1, True)
            self._set(self.numcols - 2, self.numrows - 2, True)


def _with_finders(placement: _Placement, info: SymbolInfo) -> list[list[int]]:
    data_w = info.symbol_data_width
    data_h = info.symbol_data_height
    out_w = info.symbol_width
    out_h = info.symbol_height
    matrix = [[0] * out_w for _ in range(out_h)]
    matrix_y = 0
    for y in range(data_h):
        if y % info.matrix_height == 0:
            for x in range(out_w):
                matrix[matrix_y][x] = 1 if x % 2 == 0 else 0
            matrix_y += 1
        matrix_x = 0
        for x in range(data_w):
            if x % info.matrix_width == 0:
                matrix[matrix_y][matrix_x] = 1
                matrix_x += 1
            matrix[matrix_y][matrix_x] = 1 if placement.get_bit(x, y) else 0
            matrix_x += 1
            if x % info.matrix_width == info.matrix_width - 1:
                matrix[matrix_y][matrix_x] = 1 if y % 2 == 0 else 0
                matrix_x += 1
        matrix_y += 1
        if y % info.matrix_height == info.matrix_height - 1:
            for x in range(out_w):
                matrix[matrix_y][x] = 1
            matrix_y += 1
    return matrix


class _ForceMin32(str):
    """追加 Data Matrix PAD，迫使符号升到 32×32，扫码文本不变。"""

    # 26×26 最多 44 个数据码字；45 个会升到 32×32。
    _MIN_DATA = 45

    def encode(self, encoding="utf-8", errors="strict"):  # type: ignore[override]
        if isinstance(encoding, str) and encoding.startswith("datamatrix."):
            data = bytearray(str.encode(self, encoding))
            if len(data) < self._MIN_DATA:
                data.append(129)
                while len(data) < self._MIN_DATA:
                    pos = len(data) + 1
                    data.append((((149 * pos) % 253) + 130) % 254)
            return bytes(data)
        return str.encode(self, encoding, errors)


def encode_datamatrix(text: str, min_symbol: int = 0) -> list[list[int]]:
    """把文本编成 ECC200 点阵（1=黑，0=白），正方形符号。

    使用 ppf.datamatrix（一线可扫的标准 ECC200），不再用自研放置算法。
    min_symbol>=32 时用 PAD 升到 32×32，解码结果仍是原文。
    """
    from ppf.datamatrix import DataMatrix

    payload = text if text else " "
    message: str = _ForceMin32(payload) if min_symbol >= 32 else payload
    matrix = DataMatrix(message, codecs=["ascii"]).matrix
    return [list(row) for row in matrix]
