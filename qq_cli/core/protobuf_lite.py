"""极简 protobuf wire-format 解析器 — 用于解读 QQNT 消息内容列(40800)。

只解析、不构造、不依赖 .proto 文件：逐字段读取 (field_no, wire_type, value)，
并提供"递归抽取可读文本"的启发式方法（聊天内容总结/展示足够用）。
"""
import struct

WT_VARINT, WT_64BIT, WT_LEN, WT_32BIT = 0, 1, 2, 5


def parse(data):
    """解析 wire format，返回 [(field_no, wire_type, value)]。
    WT_LEN 的 value 为 bytes；其余为 int。解析失败抛 ValueError。"""
    fields = []
    i, n = 0, len(data)
    while i < n:
        tag, i = _varint(data, i)
        field_no, wt = tag >> 3, tag & 7
        if field_no == 0:
            raise ValueError("field_no=0")
        if wt == WT_VARINT:
            val, i = _varint(data, i)
        elif wt == WT_64BIT:
            if i + 8 > n:
                raise ValueError("truncated 64bit")
            val = struct.unpack("<Q", data[i:i + 8])[0]
            i += 8
        elif wt == WT_LEN:
            ln, i = _varint(data, i)
            if i + ln > n or ln < 0:
                raise ValueError("truncated bytes")
            val = data[i:i + ln]
            i += ln
        elif wt == WT_32BIT:
            if i + 4 > n:
                raise ValueError("truncated 32bit")
            val = struct.unpack("<I", data[i:i + 4])[0]
            i += 4
        else:
            raise ValueError(f"wire_type={wt}")
        fields.append((field_no, wt, val))
    return fields


def _varint(data, i):
    result, shift = 0, 0
    while True:
        if i >= len(data) or shift > 63:
            raise ValueError("bad varint")
        b = data[i]
        result |= (b & 0x7F) << shift
        i += 1
        if not b & 0x80:
            return result, i
        shift += 7


def _text_ok(b: bytes) -> bool:
    if not b:
        return False
    try:
        s = b.decode("utf-8")
    except UnicodeDecodeError:
        return False
    if "\x00" in s:
        return False
    printable = sum(1 for c in s if c.isprintable() or c in "\n\r\t")
    return printable / len(s) > 0.9


def extract_texts(data, max_depth=6, _depth=0):
    """递归抽取消息体中所有可读文本片段（保持出现顺序）。"""
    texts = []
    if _depth > max_depth:
        return texts
    try:
        fields = parse(data)
    except (ValueError, IndexError):
        return texts
    for _fno, wt, val in fields:
        if wt != WT_LEN:
            continue
        if _text_ok(val):
            texts.append(val.decode("utf-8"))
        else:
            texts.extend(extract_texts(val, max_depth, _depth + 1))
    return texts


def first_varint(data, field_no, _depth=0, max_depth=3):
    """取指定字段号的首个 varint 值（含嵌套），找不到返回 None。"""
    if _depth > max_depth:
        return None
    try:
        fields = parse(data)
    except (ValueError, IndexError):
        return None
    for fno, wt, val in fields:
        if fno == field_no and wt == WT_VARINT:
            return val
        if wt == WT_LEN and not _text_ok(val):
            sub = first_varint(val, field_no, _depth + 1, max_depth)
            if sub is not None:
                return sub
    return None
