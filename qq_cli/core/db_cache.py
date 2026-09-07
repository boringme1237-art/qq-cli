"""解密副本缓存 — 源库只读，明文副本放 %TEMP%/qq_cli_cache/<QQ号>/，按 mtime 失效。"""
import hashlib
import json
import os
import tempfile

from . import qqcrypto


class DBCache:
    def __init__(self, account):
        self.uin = account["uin"]
        self.db_dir = account["nt_db"]
        self.cache_dir = os.path.join(tempfile.gettempdir(), "qq_cli_cache", self.uin)
        self.index_path = os.path.join(self.cache_dir, "_index.json")
        self._index = {}
        self._open = {}  # rel -> sqlite3.Connection
        os.makedirs(self.cache_dir, exist_ok=True)
        self._load_index()

    def _load_index(self):
        if os.path.exists(self.index_path):
            try:
                with open(self.index_path, encoding="utf-8") as f:
                    self._index = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._index = {}

    def _save_index(self):
        try:
            with open(self.index_path, "w", encoding="utf-8") as f:
                json.dump(self._index, f)
        except OSError:
            pass

    def _paths(self, rel):
        rel_norm = rel.replace("\\", "/").replace("/", os.sep)
        db_path = os.path.join(self.db_dir, rel_norm)
        h = hashlib.md5(f"{self.uin}:{rel}".encode()).hexdigest()[:12]
        tmp_path = os.path.join(self.cache_dir, f"{h}.db")
        return db_path, tmp_path

    def get_path(self, rel, key_info):
        """返回源库 rel 对应的明文副本路径；需要时解密（mtime 变化才重解）。"""
        db_path, tmp_path = self._paths(rel)
        if not os.path.exists(db_path):
            return None
        db_mt = os.path.getmtime(db_path)
        wal_path = db_path + "-wal"
        wal_mt = os.path.getmtime(wal_path) if os.path.exists(wal_path) else 0.0
        hit = self._index.get(rel)
        if hit and hit["db_mt"] == db_mt and hit["wal_mt"] == wal_mt \
                and os.path.exists(tmp_path):
            return tmp_path

        key_bytes = key_info["key"].encode("ascii") \
            if key_info.get("kind", "passphrase") == "passphrase" \
            else bytes.fromhex(key_info["key"])
        kdf_iter = key_info.get("kdf_iter", 4000)
        hmac_alg = key_info.get("hmac_alg", "sha1")
        raw = key_info.get("kind") == "rawkey"
        kdf_hash = key_info.get("kdf_hash", "sha512")
        qqcrypto.full_decrypt(db_path, tmp_path, key_bytes, kdf_iter, hmac_alg,
                              raw=raw, kdf_hash=kdf_hash)
        if os.path.exists(wal_path):
            qqcrypto.decrypt_wal(wal_path, tmp_path, key_bytes, kdf_iter, hmac_alg,
                                 raw=raw, kdf_hash=kdf_hash)
        self._index[rel] = {"db_mt": db_mt, "wal_mt": wal_mt, "path": tmp_path}
        self._save_index()
        return tmp_path

    def connect(self, rel, key_info):
        """打开明文副本的只读 sqlite 连接（幂等）。"""
        import sqlite3
        key = (rel, key_info["key"], key_info.get("kdf_iter"), key_info.get("hmac_alg"))
        if key in self._open:
            return self._open[key]
        path = self.get_path(rel, key_info)
        if not path:
            return None
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        self._open[key] = conn
        return conn

    def cleanup(self):
        """删除本账号的解密副本与索引。"""
        import shutil
        closed = set()
        for (rel, *_rest), conn in list(self._open.items()):
            conn.close()
            closed.add(rel)
        self._open.clear()
        if os.path.isdir(self.cache_dir):
            shutil.rmtree(self.cache_dir, ignore_errors=True)
        self._index = {}
        return self.cache_dir


def cleanup_all():
    """删除所有账号的解密副本缓存目录。"""
    import shutil
    root = os.path.join(tempfile.gettempdir(), "qq_cli_cache")
    if os.path.isdir(root):
        shutil.rmtree(root, ignore_errors=True)
    return root
