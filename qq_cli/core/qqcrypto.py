"""SQLCipher 4 解密 — QQNT 变体（按 QQBackup/x_key_scanner 权威实现重写）。

文件布局：1024 字节 QQ 包装头 + SQLCipher 正文（page 4096，盐 = 第 1 页前 16 字节）。
页尾保留区 = IV(16) + 页HMAC(digest)，向上取整到 16 字节倍数：
  none→16, sha1→48, sha256→48, sha512→80
KDF：enc_key = PBKDF2(kdf_hash, 口令, 盐, 4000, 32)
     mac_key = PBKDF2(kdf_hash, enc_key, 盐^0x3A, 2, 32)
     页HMAC 输入 = 加密数据 + IV + 页号(LE)，kdf_iter 恒为 4000。
页 HMAC 与 KDF 哈希的组合因库/版本而异，需 12 种组合暴力确认。

本模块只做"读源文件 → 写解密副本"，绝不修改源数据库。
"""
import hashlib
import hmac as hmac_mod
import os
import shutil
import struct

from Crypto.Cipher import AES

PAGE_SZ = 4096
KEY_SZ = 32
SALT_SZ = 16
IV_SZ = 16
NT_HEADER_SZ = 1024
SQLITE_HDR = b"SQLite format 3\x00"
WAL_HEADER_SZ = 32
WAL_FRAME_HEADER_SZ = 24
KDF_ITER_DEFAULT = 4000
FAST_ITER = 2
HMAC_MASK = 0x3A

_DIGEST = {"none": 0, "sha1": 20, "sha256": 32, "sha512": 64}


def reserve_of(hmac_alg):
    """页尾保留区 = IV + HMAC 摘要，向上取整到 AES 块大小。"""
    digest = _DIGEST[hmac_alg]
    return -(-(IV_SZ + digest) // 16) * 16


def hmac_alg_of(hmac_alg):
    return {"sha1": hashlib.sha1, "sha256": hashlib.sha256,
            "sha512": hashlib.sha512}[hmac_alg]


def load_page1(db_path):
    """定位 SQLCipher 正文第 1 页。

    返回 (page1_bytes, kind)，kind ∈ {"plain", "nt", "raw"}。
    """
    with open(db_path, "rb") as f:
        head = f.read(NT_HEADER_SZ + PAGE_SZ)
    if head[:16] == SQLITE_HDR:
        return head[:PAGE_SZ], "plain"
    if len(head) >= NT_HEADER_SZ + PAGE_SZ:
        page1 = head[NT_HEADER_SZ:NT_HEADER_SZ + PAGE_SZ]
        if page1[:16] not in (b"\x00" * 16, b"\xff" * 16):
            return page1, "nt"
    return head[:PAGE_SZ], "raw"


def derive_keys(key_bytes, salt, kdf_iter, raw=False, kdf_hash="sha512"):
    """派生 (enc_key, mac_key)。mac 盐 = 盐 ^ 0x3A，fast_kdf_iter = 2。"""
    if raw:
        enc_key = key_bytes
    else:
        enc_key = hashlib.pbkdf2_hmac(kdf_hash, key_bytes, salt, kdf_iter, dklen=KEY_SZ)
    mac_salt = bytes(b ^ HMAC_MASK for b in salt)
    mac_key = hashlib.pbkdf2_hmac(kdf_hash, enc_key, mac_salt, FAST_ITER, dklen=KEY_SZ)
    return enc_key, mac_key


def _header_tail_ok(body: bytes) -> bool:
    """page-HMAC 关闭时用解密结果的 SQLite 头不变量验证（页大小 + 三个固定常数）。"""
    if len(body) < 8:
        return False
    page_size = int.from_bytes(body[0:2], "big")
    page_ok = page_size == 1 or (512 <= page_size and (page_size & (page_size - 1)) == 0)
    return page_ok and body[5] == 64 and body[6] == 32 and body[7] == 32


def derive_mac_key(enc_key, salt, kdf_hash="sha512"):
    mac_salt = bytes(b ^ HMAC_MASK for b in salt)
    return hashlib.pbkdf2_hmac(kdf_hash, enc_key, mac_salt, FAST_ITER, dklen=KEY_SZ)


def verify_key(key_bytes, page1, kdf_iter, hmac_alg, raw=False, kdf_hash="sha512"):
    """用第 1 页验证 (密钥, 参数) 是否正确。"""
    salt = page1[:SALT_SZ]
    enc_key, _ = derive_keys(key_bytes, salt, kdf_iter, raw, kdf_hash)
    res = reserve_of(hmac_alg)
    data_end = PAGE_SZ - res
    digest = _DIGEST[hmac_alg]
    if digest == 0:
        ct = page1[SALT_SZ:data_end]
        iv = page1[data_end:data_end + IV_SZ]
        if len(ct) % 16:
            return False
        body = AES.new(enc_key, AES.MODE_CBC, iv).decrypt(ct)
        return _header_tail_ok(body)
    mac_key = derive_mac_key(enc_key, salt, kdf_hash)
    hmac_data = page1[SALT_SZ:data_end] + page1[data_end:data_end + IV_SZ] \
        + struct.pack("<I", 1)
    hm = hmac_mod.new(mac_key, hmac_data, hmac_alg_of(hmac_alg))
    stored = page1[data_end + IV_SZ: data_end + IV_SZ + digest]
    return hmac_mod.compare_digest(hm.digest(), stored)


# (page_hmac, kdf_hash, kdf_iter) — 12 种算法组合（x_key_scanner 同款穷举序）
COMBOS = [(p, k, KDF_ITER_DEFAULT)
          for p in ("none", "sha1", "sha256", "sha512")
          for k in ("sha1", "sha256", "sha512")]


def decrypt_page(page_data, enc_key, pgno, hmac_alg):
    """解密单页 → 明文页（4096 字节）。"""
    res = reserve_of(hmac_alg)
    data_end = PAGE_SZ - res
    iv = page_data[data_end:data_end + IV_SZ]
    skip = SALT_SZ if pgno == 1 else 0
    ct = page_data[skip:data_end]
    plain = AES.new(enc_key, AES.MODE_CBC, iv).decrypt(ct)
    if pgno == 1:
        out = bytearray(PAGE_SZ)
        out[:16] = SQLITE_HDR
        out[16:16 + len(plain)] = plain
        out[16:18] = (PAGE_SZ >> 8).to_bytes(1, "big") + bytes([PAGE_SZ & 0xFF])
        return bytes(out)
    return plain + b"\x00" * res


def full_decrypt(db_path, out_path, key_bytes, kdf_iter, hmac_alg, raw=False,
                 kdf_hash="sha512"):
    """整体解密：读源库（含 NT 头剥离 + WAL 回放由调用方负责），写明文副本。返回页数。"""
    page1, kind = load_page1(db_path)
    if kind == "plain":
        shutil.copyfile(db_path, out_path)
        return os.path.getsize(db_path) // PAGE_SZ
    body_off = NT_HEADER_SZ if kind == "nt" else 0
    enc_key, _ = derive_keys(key_bytes, page1[:SALT_SZ], kdf_iter, raw, kdf_hash)
    file_size = os.path.getsize(db_path)
    total_pages = (file_size - body_off) // PAGE_SZ
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(db_path, "rb") as fin, open(out_path, "wb") as fout:
        fin.seek(body_off)
        for pgno in range(1, total_pages + 1):
            page = fin.read(PAGE_SZ)
            if len(page) < PAGE_SZ:
                if len(page) > 0:
                    page = page + b"\x00" * (PAGE_SZ - len(page))
                else:
                    break
            fout.write(decrypt_page(page, enc_key, pgno, hmac_alg))
    return total_pages


def _wal_body_offset(wal_path):
    """QQNT 的 -wal 文件可能也带 1024 字节包装头；用 WAL 魔数探测。"""
    with open(wal_path, "rb") as f:
        head = f.read(WAL_HEADER_SZ + NT_HEADER_SZ)
    for off in (0, NT_HEADER_SZ):
        if len(head) >= off + 4 and head[off:off + 4] in (b"\x37\x7f\x06\x82",
                                                          b"\x37\x7f\x06\x83"):
            return off
    return 0


def decrypt_wal(wal_path, out_path, key_bytes, kdf_iter, hmac_alg, raw=False,
                kdf_hash="sha512"):
    """将 -wal 中的新页解密后回放到明文副本（只读 wal，写副本）。返回回放帧数。"""
    if not os.path.exists(wal_path):
        return 0
    wal_size = os.path.getsize(wal_path)
    hdr_at = _wal_body_offset(wal_path)
    with open(wal_path, "rb") as f:
        f.seek(hdr_at)
        wal_hdr = f.read(WAL_HEADER_SZ)
    if len(wal_hdr) < WAL_HEADER_SZ:
        return 0
    wal_salt1 = struct.unpack(">I", wal_hdr[16:20])[0]
    wal_salt2 = struct.unpack(">I", wal_hdr[20:24])[0]
    # 解密密钥从主库第 1 页的盐派生
    src_p1, kind = load_page1(wal_path[:-4] if wal_path.endswith("-wal") else wal_path)
    if kind == "plain":
        return 0
    enc_key, _ = derive_keys(key_bytes, src_p1[:SALT_SZ], kdf_iter, raw, kdf_hash)
    frame_size = WAL_FRAME_HEADER_SZ + PAGE_SZ
    patched = 0
    with open(wal_path, "rb") as wf, open(out_path, "r+b") as df:
        wf.seek(hdr_at + WAL_HEADER_SZ)
        while wf.tell() + frame_size <= hdr_at + wal_size:
            fh = wf.read(WAL_FRAME_HEADER_SZ)
            if len(fh) < WAL_FRAME_HEADER_SZ:
                break
            pgno = struct.unpack(">I", fh[0:4])[0]
            fs1 = struct.unpack(">I", fh[8:12])[0]
            fs2 = struct.unpack(">I", fh[12:16])[0]
            ep = wf.read(PAGE_SZ)
            if len(ep) < PAGE_SZ:
                break
            if pgno == 0 or pgno > 10_000_000:
                continue
            if fs1 != wal_salt1 or fs2 != wal_salt2:
                continue
            df.seek((pgno - 1) * PAGE_SZ)
            df.write(decrypt_page(ep, enc_key, pgno, hmac_alg))
            patched += 1
    return patched
