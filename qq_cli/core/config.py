"""配置与多账号发现。

数据布局（QQ NT）：
  <文档>/Tencent Files/<QQ号>/nt_qq/nt_db/*.db   — 每个账号一个目录
  <文档>/Tencent Files/nt_qq/global/nt_db        — 全局数据（非账号）

密钥保存在 ~/.qq-cli/keys/<QQ号>.json（明文，含密钥——敏感文件，cleanup 可一键清除）。
"""
import json
import os
import re
import time

STATE_DIR = os.path.join(os.path.expanduser("~"), ".qq-cli")
KEYS_DIR = os.path.join(STATE_DIR, "keys")

UIN_RE = re.compile(r"^\d{5,12}$")


def default_db_root():
    return os.path.join(os.path.expanduser("~"), "Documents", "Tencent Files")


def discover_accounts(db_root=None):
    """枚举本地所有 QQ 账号。返回 [{uin, nt_db, last_active_ts}]，按最近活跃降序。"""
    db_root = db_root or default_db_root()
    accounts = []
    if not os.path.isdir(db_root):
        return accounts
    for name in sorted(os.listdir(db_root)):
        nt_db = os.path.join(db_root, name, "nt_qq", "nt_db")
        if not UIN_RE.match(name) or not os.path.isdir(nt_db):
            continue
        ts = 0.0
        for marker in ("nt_msg.db", "profile_info.db"):
            p = os.path.join(nt_db, marker)
            if os.path.exists(p):
                ts = max(ts, os.path.getmtime(p))
        accounts.append({"uin": name, "nt_db": nt_db, "last_active_ts": ts})
    accounts.sort(key=lambda a: a["last_active_ts"], reverse=True)
    return accounts


def select_account(account=None, db_root=None, assume_latest=False):
    """选定账号。account 指定 QQ 号；否则多账号时交互选择（assume_latest 时取最近活跃）。"""
    accounts = discover_accounts(db_root)
    if not accounts:
        raise SystemExit(f"未找到任何 QQ 账号数据目录: {db_root or default_db_root()}")
    if account:
        for a in accounts:
            if a["uin"] == str(account):
                return a
        raise SystemExit(f"账号 {account} 不存在。本地账号: "
                         + ", ".join(a["uin"] for a in accounts))
    if len(accounts) == 1 or assume_latest:
        return accounts[0]
    print("检测到多个 QQ 账号:")
    for i, a in enumerate(accounts, 1):
        t = time.strftime("%Y-%m-%d %H:%M", time.localtime(a["last_active_ts"])) \
            if a["last_active_ts"] else "未知"
        print(f"  {i}. {a['uin']}  (最近活跃: {t})")
    try:
        idx = input(f"请选择账号 [1-{len(accounts)}]: ").strip()
        num = int(idx) if idx else 1
        return accounts[num - 1]
    except (ValueError, IndexError):
        raise SystemExit("无效选择")


def keys_file(uin):
    return os.path.join(KEYS_DIR, f"{uin}.json")


def load_keys(uin):
    p = keys_file(uin)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_keys(uin, data):
    os.makedirs(KEYS_DIR, exist_ok=True)
    p = keys_file(uin)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return p


def cleanup_state():
    """删除 ~/.qq-cli（密钥与状态）。返回删除的路径列表。"""
    import shutil
    removed = []
    if os.path.isdir(STATE_DIR):
        shutil.rmtree(STATE_DIR)
        removed.append(STATE_DIR)
    return removed
