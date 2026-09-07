"""QQ.exe 内存密钥扫描 — 纯读取式（OpenProcess + ReadProcessMemory），不注入不 Hook。

算法移植自 QQBackup/x_key_scanner (scan.rs) 的公开思路：
- 锚点 = SQLCipher 编解码上下文旁的 b"\\x09HMAC_SHA1" 标记（长度前缀串）
- 密钥形态：16 字节、全可打印非空格、不全为字母数字、16 字节地址对齐
- 路径一：每个锚点 ±0x200 内按 16 字节步进找最近合格窗口
- 路径二：密集锚点带（≥4 个锚点）中心向外 ±0x20000 收集候选（Electron 布局）
- 候选按"字符类别数、出现次数"排序，逐一用数据库第 1 页 HMAC 校验

密钥只保存在本机 ~/.qq-cli/。
"""
import ctypes
import ctypes.wintypes as wt
import os
import re
import subprocess
import time

PAGE_SZ = 4096
ANCHOR = b"\x09HMAC_SHA1"
ALIGN = 16
RADIUS = 0x200
KEY_LEN = 16
CLUSTER_GAP = 0x4000
MIN_CLUSTER_ANCHORS = 4
CLUSTER_MAX_OUT = 0x20000
CLUSTER_KEYS_PER = 256

kernel32 = ctypes.windll.kernel32
MEM_COMMIT = 0x1000
READABLE = {0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80}


class _MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_uint64), ("AllocationBase", ctypes.c_uint64),
        ("AllocationProtect", wt.DWORD), ("_pad1", wt.DWORD),
        ("RegionSize", ctypes.c_uint64), ("State", wt.DWORD),
        ("Protect", wt.DWORD), ("Type", wt.DWORD), ("_pad2", wt.DWORD),
    ]


def get_pids(image="QQ.exe"):
    """返回 [(pid, mem_kb)]，按内存占用降序。"""
    r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"],
                       capture_output=True, text=True)
    pids = []
    for line in r.stdout.strip().splitlines():
        p = line.strip('"').split('","')
        if len(p) >= 5:
            try:
                pids.append((int(p[1]),
                             int(p[4].replace(",", "").replace(" K", "").strip() or "0")))
            except ValueError:
                continue
    pids.sort(key=lambda x: x[1], reverse=True)
    return pids


def _read_mem(h, addr, sz):
    buf = ctypes.create_string_buffer(sz)
    n = ctypes.c_size_t(0)
    if kernel32.ReadProcessMemory(h, ctypes.c_uint64(addr), buf, sz, ctypes.byref(n)):
        return buf.raw[:n.value]
    return None


def _enum_regions(h):
    regs, addr, mbi = [], 0, _MBI()
    while addr < 0x7FFFFFFFFFFF:
        if kernel32.VirtualQueryEx(h, ctypes.c_uint64(addr),
                                   ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
            break
        if mbi.State == MEM_COMMIT and mbi.Protect in READABLE \
                and 0 < mbi.RegionSize < 500 * 1024 * 1024:
            regs.append((mbi.BaseAddress, mbi.RegionSize))
        nxt = mbi.BaseAddress + mbi.RegionSize
        if nxt <= addr:
            break
        addr = nxt
    return regs


# ---- 候选生成（x_key_scanner scan.rs 逻辑） ----

def _key_ok(win: bytes) -> bool:
    """16 字节、全可打印非空格、不全为字母数字。"""
    if len(win) != KEY_LEN:
        return False
    if not all(0x21 <= b <= 0x7E for b in win):
        return False
    return not all(chr(b).isalnum() for b in win)


def _class_count(key: bytes) -> int:
    """字符类别数：数字/大写/小写/符号。"""
    s = key.decode("ascii")
    return (any(c.isdigit() for c in s) + any(c.isupper() for c in s)
            + any(c.islower() for c in s)
            + any(not c.isalnum() for c in s))


def _find_anchors(data: bytes):
    """锚点：\\x09HMAC_SHA1 出现处往前 1 字节且 16 对齐的槽位。"""
    out = []
    start = 0
    while True:
        p = data.find(ANCHOR, start)
        if p == -1:
            break
        start = p + 1
        off = p - 1
        if off >= 0 and off % ALIGN == 0:
            out.append(off)
    return out


def _nearest_key(data: bytes, anchor: int):
    """锚点 ±RADIUS 内按 16 字节步进向外找最近合格窗口。"""
    a16 = (anchor // ALIGN) * ALIGN
    n = len(data)
    for step in range(0, RADIUS + 1, ALIGN):
        for cand in ({a16 - step, a16 + step} if step else {a16}):
            if 0 <= cand and cand + KEY_LEN <= n and _key_ok(data[cand:cand + KEY_LEN]):
                return data[cand:cand + KEY_LEN]
    return None


def _cluster_keys(data: bytes, anchors):
    """Electron 布局兜底：最密集锚点带中心向外走，收集至多 CLUSTER_KEYS_PER 个窗口。"""
    clusters, start = [], 0
    for i in range(1, len(anchors)):
        if anchors[i] - anchors[i - 1] > CLUSTER_GAP:
            clusters.append(anchors[start:i])
            start = i
    clusters.append(anchors[start:])
    best = max((c for c in clusters if len(c) >= MIN_CLUSTER_ANCHORS),
               key=len, default=None)
    if not best:
        return []
    center = (best[0] + best[-1]) // 2
    c16 = (center // ALIGN) * ALIGN
    n = len(data)
    found = []
    for step in range(0, CLUSTER_MAX_OUT + 1, ALIGN):
        if len(found) >= CLUSTER_KEYS_PER:
            break
        for cand in ({c16 - step, c16 + step} if step else {c16}):
            if 0 <= cand and cand + KEY_LEN <= n:
                k = data[cand:cand + KEY_LEN]
                if _key_ok(k) and k not in found:
                    found.append(k)
    return found


def collect_candidates(image="QQ.exe", log=print):
    """扫描所有目标进程，返回 {候选key(bytes): 出现次数}。"""
    counts = {}
    t0 = time.time()
    for pid, mem_kb in get_pids(image):
        h = kernel32.OpenProcess(0x0010 | 0x0400, False, pid)
        if not h:
            log(f"  [WARN] 无法打开进程 PID={pid}")
            continue
        try:
            regions = _enum_regions(h)
            total_mb = sum(s for _, s in regions) / 1024 / 1024
            log(f"  扫描 PID={pid} ({mem_kb // 1024}MB 任务管理器, "
                f"{total_mb:.0f}MB 可读提交, {len(regions)} 区域)")
            for base, size in regions:
                data = _read_mem(h, base, size)
                if not data or ANCHOR not in data:
                    continue
                anchors = _find_anchors(data)
                if not anchors:
                    continue
                keys = []
                for a in anchors:
                    k = _nearest_key(data, a)
                    if k:
                        keys.append(k)
                keys.extend(_cluster_keys(data, anchors))
                for k in keys:
                    counts[k] = counts.get(k, 0) + 1
        finally:
            kernel32.CloseHandle(h)
    log(f"  候选 {len(counts)} 个 ({time.time() - t0:.1f}s)")
    return counts


def scan_key(page1, combos, print_fn=print, image="QQ.exe"):
    """扫描并校验密钥。返回密钥信息 dict，或 None。

    page1: 目标库 SQLCipher 第 1 页；combos: [(hmac_alg, kdf_iter, kdf_hash)] 按优先序。
    """
    from . import qqcrypto

    counts = collect_candidates(image, print_fn)
    ordered = sorted(counts.items(),
                     key=lambda kv: (_class_count(kv[0]), kv[1], kv[0]),
                     reverse=True)
    for key_bytes, _cnt in ordered:
        for page_hmac, kdf_hash, kdf in combos:
            if qqcrypto.verify_key(key_bytes, page1, kdf, page_hmac, kdf_hash=kdf_hash):
                val = key_bytes.decode("ascii")
                print_fn(f"  [FOUND] key={val!r} "
                         f"(page_hmac={page_hmac}, kdf_hash={kdf_hash}, kdf_iter={kdf})")
                return {"kind": "passphrase", "key": val, "kdf_iter": kdf,
                        "hmac_alg": page_hmac, "kdf_hash": kdf_hash}
    return None
