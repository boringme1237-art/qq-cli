"""数据查询层 — 全部 SELECT 只读，作用于 %TEMP% 中的明文解密副本。

关键表/列（QQNT 数值列名）：
  nt_msg.db:  group_msg_table / c2c_msg_table
              40050=时间戳 40033=发送者QQ号 40030=对方QQ号(群=群号) 40027=会话归属(群号)
              40020=发送者uid 40021=会话uid/群号串 40093=昵称 40090=群名片 40011=消息类型
              40800=消息体(protobuf)   recent_contact_v3_table: 会话列表(40010=类型)
  group_info.db: group_list 60001=群号 60007=群名 60006=成员数 60005=上限 60004=群主 60023=我的群备注
                 group_member3 60001=群号 1002=QQ号 64003=群名片 20002=昵称
  profile_info.db / nt_msg.db: nt_uid_mapping_table 48902=uid 1002=QQ号
"""
import datetime
import os
import re
import sqlite3

from .protobuf_lite import extract_texts

_UID_RE = re.compile(r"^u_[A-Za-z0-9_-]+$")

NT_MSG = "nt_msg.db"
GROUP_INFO = "group_info.db"
PROFILE_INFO = "profile_info.db"


def _conn(cache, keys, rel):
    info = keys.get(rel)
    if not info or info.get("kind") == "plain":
        # 明文库也走缓存（直接复制）
        pass
    if not info:
        return None
    try:
        return cache.connect(rel, info)
    except sqlite3.Error:
        return None


def content_of(blob, msg_type):
    """从 40800 protobuf 消息体提取可读文本。

    取最长的人类可读片段（通常是完整渲染文本）；过滤纯 uid 片段；
    图片 URL、群系统提示 XML 转为友好标记。
    """
    if not blob:
        return "[空消息]"
    try:
        texts = extract_texts(blob)
    except Exception:
        texts = []
    texts = [t.strip() for t in texts if t and t.strip()]
    if not texts:
        label = {3: "图片", 4: "文件", 6: "语音", 7: "视频"}.get(msg_type)
        return f"[非文本消息{(':' + label) if label else f' type={msg_type}'}]"
    real = [t for t in texts if not _UID_RE.match(t)]
    if not real:
        return "[图片/提及]"
    best = max(real, key=len)
    if "gchatpic_new" in best or best.startswith("/blob"):
        return "[图片]"
    if best.startswith("<gtip") or best.startswith("<GTip"):
        parts = re.findall(r'txt="([^"]*)"', best)
        plain = " ".join(p for p in parts if p).strip()
        return f"[群通知] {plain}" if plain else "[群通知]"
    if best.startswith("/download") or best.startswith("/blob"):
        return "[文件]"
    return best


def ts_str(ts):
    try:
        return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError):
        return str(ts)


def parse_date(s, end=False):
    if not s:
        return None
    dt = datetime.datetime.strptime(s, "%Y-%m-%d")
    if end:
        dt += datetime.timedelta(days=1) - datetime.timedelta(seconds=1)
    return int(dt.timestamp())


# ---------- 会话/群解析 ----------

def list_groups(cache, keys):
    """[(群号, 群名, 成员数, 上限, 我的备注)]"""
    conn = _conn(cache, keys, GROUP_INFO)
    if not conn:
        return []
    try:
        return conn.execute(
            'SELECT "60001","60007","60006","60005","60022" FROM group_list '
            'ORDER BY "60006" DESC').fetchall()
    except sqlite3.Error:
        return []


def resolve_chat(cache, keys, name_or_id):
    """把群号/群名/好友QQ号解析为 (kind, peer_id, name)。kind ∈ {group, c2c}。"""
    s = str(name_or_id).strip()
    # 1) 群：精确群号
    if s.isdigit():
        for gid, name, _mc, _cap, _rm in list_groups(cache, keys):
            if str(gid) == s:
                return "group", gid, name
    # 2) 群：名称部分匹配
    matches = [(gid, name) for gid, name, _mc, _cap, _rm in list_groups(cache, keys)
               if s.lower() in (name or "").lower()]
    if len(matches) == 1:
        return "group", matches[0][0], matches[0][1]
    if len(matches) > 1:
        raise SystemExit("匹配到多个群，请用群号指定:\n  " +
                         "\n  ".join(f"{g} {n}" for g, n in matches))
    # 3) 会话表（含好友/群）：名称匹配
    conn = _conn(cache, keys, NT_MSG)
    if conn:
        rows = conn.execute(
            'SELECT "40010","40021","40094" FROM recent_contact_v3_table '
            'WHERE "40094" LIKE ?', (f"%{s}%",)).fetchall()
        uniq = {(t, p, n) for t, p, n in rows if p}
        if len(uniq) == 1:
            t, p, n = next(iter(uniq))
            if str(t) == "2":
                return "group", int(p), n
            if str(p).isdigit():
                return "c2c", int(p), n
            # 好友会话的 peer 是 u_ 开头的 uid → 映射回 QQ 号
            m = conn.execute('SELECT "1002" FROM nt_uid_mapping_table WHERE "48902" = ?',
                             (p,)).fetchone()
            if m and m[0]:
                return "c2c", int(m[0]), n
            raise SystemExit(f"好友 {n} ({p}) 在本机没有 uid→QQ号 映射，无法按号查询")
        if len(uniq) > 1:
            raise SystemExit("匹配到多个会话，请用 QQ 号/群号指定:\n  " +
                             "\n  ".join(f"{p} {n}" for _t, p, n in uniq))
        # 4) 数字 QQ 号 → c2c
        if s.isdigit():
            return "c2c", int(s), s
    raise SystemExit(f"找不到会话/群: {name_or_id}")


def peer_name_map(cache, keys):
    """{peer_id: name} 用于全局搜索标注会话名。"""
    names = {}
    for gid, name, *_ in list_groups(cache, keys):
        names[int(gid)] = name
    conn = _conn(cache, keys, NT_MSG)
    if conn:
        for t, p, n in conn.execute(
                'SELECT DISTINCT "40010","40021","40094" FROM recent_contact_v3_table'):
            if p and str(p).isdigit():
                names.setdefault(int(p), n or "")
    return names


# ---------- 各命令实现 ----------

def print_sessions(cache, keys, uin, limit):
    conn = _conn(cache, keys, NT_MSG)
    if not conn:
        raise SystemExit(f"{NT_MSG} 无密钥，请重新运行 qq-cli init")
    uid2uin = {uid: u for uid, u in conn.execute(
        'SELECT "48902","1002" FROM nt_uid_mapping_table')}
    rows = conn.execute(
        'SELECT "40010","40021","40094","40050","40093" FROM recent_contact_v3_table '
        'ORDER BY "40050" DESC LIMIT ?', (limit,)).fetchall()
    print(f"账号 {uin} 最近会话（{len(rows)} 个）:")
    for t, peer, name, ts, last_sender in rows:
        kind = "群" if str(t) == "2" else "好友/其他"
        pdisp = peer if not str(peer).isdigit() \
            else (uid2uin.get(peer, peer) if str(peer).startswith("u_") else peer)
        print(f"  [{kind}] {name or '(无名)'}  peer={pdisp}  "
              f"最后消息: {ts_str(ts)}  {('最后发送: ' + last_sender) if last_sender else ''}")


def print_groups(cache, keys, limit, keyword):
    groups = list_groups(cache, keys)
    if keyword:
        groups = [g for g in groups if keyword.lower() in (g[1] or "").lower()]
    groups = groups[:limit]
    print(f"共 {len(groups)} 个群:")
    for gid, name, members, cap, remark in groups:
        rm = f"  备注: {remark}" if remark else ""
        print(f"  {gid}  {name}  (成员 {members}/{cap}{rm})")


def _fetch_messages(cache, keys, kind, peer, since, until, order_desc, limit):
    conn = _conn(cache, keys, NT_MSG)
    if not conn:
        raise SystemExit(f"{NT_MSG} 无密钥，请重新运行 qq-cli init")
    table = "group_msg_table" if kind == "group" else "c2c_msg_table"
    peer_col = '"40027"' if kind == "group" else '"40030"'
    sql = (f'SELECT "40050","40033","40093","40090","40011","40800" '
           f'FROM "{table}" WHERE {peer_col} = ?')
    params = [peer]
    if since:
        sql += ' AND "40050" >= ?'
        params.append(since)
    if until:
        sql += ' AND "40050" <= ?'
        params.append(until)
    sql += f' ORDER BY "40050" {"DESC" if order_desc else "ASC"} LIMIT ?'
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def print_history(cache, keys, uin, chat, limit, since, until):
    kind, peer, name = resolve_chat(cache, keys, chat)
    rows = _fetch_messages(cache, keys, kind, peer,
                           parse_date(since), parse_date(until, end=True),
                           order_desc=True, limit=limit)
    print(f"{name} ({peer}) 最近 {len(rows)} 条:")
    for ts, sender, nick, card, mtype, blob in reversed(rows):
        who = card or nick or (str(sender) if sender else "未知")
        print(f"  {ts_str(ts)}  {who}: {content_of(blob, mtype)}")


def print_search(cache, keys, uin, keyword, chat, limit):
    names = peer_name_map(cache, keys)
    conn = _conn(cache, keys, NT_MSG)
    kw = keyword.lower()
    hits = []
    for table, kind in (("group_msg_table", "group"), ("c2c_msg_table", "c2c")):
        sql = (f'SELECT "40050","40033","40093","40090","40011","40800",'
               f'"40027" FROM "{table}"')
        params = []
        if chat:
            _k, peer, _n = resolve_chat(cache, keys, chat)
            peer_col = '"40027"' if kind == "group" else '"40030"'
            sql += f' WHERE {peer_col} = ?'
            params = [peer]
        sql += ' ORDER BY "40050" DESC LIMIT 50000'
        for ts, sender, nick, card, mtype, blob, peer in conn.execute(sql, params):
            text = content_of(blob, mtype)
            if kw in text.lower():
                hits.append((ts, kind, peer, card or nick or sender, text))
                if len(hits) >= limit:
                    break
        if len(hits) >= limit:
            break
    hits.sort(key=lambda h: h[0], reverse=True)
    print(f"搜索 “{keyword}”：{len(hits)} 条结果")
    for ts, kind, peer, who, text in hits:
        cname = names.get(int(peer) if peer is not None else -1, str(peer))
        print(f"  {ts_str(ts)} [{cname}] {who}: {text[:80]}")


def export_markdown(cache, keys, uin, group_name, since, until, limit, output):
    if not group_name:
        raise SystemExit("按群导出请指定 --group <群名或群号>")
    kind, peer, name = resolve_chat(cache, keys, group_name)
    rows = _fetch_messages(cache, keys, kind, peer,
                           parse_date(since), parse_date(until, end=True),
                           order_desc=False, limit=limit)
    # 成员数
    members = 0
    ginfo = _conn(cache, keys, GROUP_INFO)
    if ginfo and kind == "group":
        r = ginfo.execute('SELECT "60006" FROM group_list WHERE "60001" = ?',
                          (peer,)).fetchone()
        members = r[0] if r else 0
    os.makedirs(os.path.dirname(os.path.abspath(output)) or ".", exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        f.write(f"# {name}（{kind and ('群 ' + str(peer) if kind == 'group' else 'QQ ' + str(peer))}）\n\n")
        f.write(f"- 账号: {uin}\n")
        f.write(f"- 消息数: {len(rows)}\n")
        if kind == "group":
            f.write(f"- 群成员数: {members}\n")
        if rows:
            f.write(f"- 时间范围: {ts_str(rows[0][0])} ~ {ts_str(rows[-1][0])}\n")
        f.write("\n## 消息记录\n\n")
        for ts, sender, nick, card, mtype, blob in rows:
            who = card or nick or (str(sender) if sender else "未知")
            f.write(f"- **{ts_str(ts)}** {who}({sender}): {content_of(blob, mtype)}\n")
    return len(rows)


def print_stats(cache, keys, uin, group_name):
    conn = _conn(cache, keys, NT_MSG)
    if group_name:
        kind, peer, name = resolve_chat(cache, keys, group_name)
        rows = _fetch_messages(cache, keys, kind, peer, None, None,
                               order_desc=False, limit=1000000)
        total = len(rows)
        print(f"{name} ({peer}): {total} 条消息")
    else:
        gtotal = conn.execute('SELECT COUNT(*) FROM group_msg_table').fetchone()[0]
        ctotal = conn.execute('SELECT COUNT(*) FROM c2c_msg_table').fetchone()[0]
        print(f"账号 {uin}: 群消息 {gtotal} 条, 好友消息 {ctotal} 条")
        rows = conn.execute(
            'SELECT "40050","40033","40093","40090","40011","40800" '
            'FROM group_msg_table ORDER BY "40050" ASC LIMIT 1000000').fetchall()
        name = "全部群"
    if not rows:
        print("  (无消息)")
        return
    from collections import Counter
    senders = Counter()
    hours = Counter()
    days = Counter()
    for ts, sender, nick, card, _t, _b in rows:
        senders[card or nick or (str(sender) if sender else None) or "(系统)"] += 1
        dt = datetime.datetime.fromtimestamp(int(ts))
        hours[dt.hour] += 1
        days[dt.strftime("%Y-%m-%d")] += 1
    valid = sorted(r[0] for r in rows if r[0] and r[0] > 1_000_000_000)
    if not valid:
        print("  (无有效时间戳消息)")
        return
    span = max((valid[-1] - valid[0]) / 86400, 1)
    print(f"  范围: {ts_str(valid[0])} ~ {ts_str(valid[-1])} "
          f"({span:.0f} 天, {total / span:.1f} 条/天)")
    print("  发送者 Top10:")
    for who, n in senders.most_common(10):
        print(f"    {who}: {n} ({n * 100 // total}%)")
    print("  时段分布: " + "  ".join(f"{h}时:{n}" for h, n in sorted(hours.items())))
    recent = sorted(days.items())[-14:]
    print("  近期每日消息量: " + "  ".join(f"{d}:{n}" for d, n in recent))


def print_members(cache, keys, chat):
    kind, peer, name = resolve_chat(cache, keys, chat)
    if kind != "group":
        raise SystemExit("成员列表仅支持群聊")
    conn = _conn(cache, keys, GROUP_INFO)
    if not conn:
        raise SystemExit(f"{GROUP_INFO} 无密钥，请重新运行 qq-cli init")
    rows = conn.execute(
        'SELECT "1002","64003","20002" FROM group_member3 WHERE "60001" = ? '
        'ORDER BY "1002"', (peer,)).fetchall()
    if not rows:
        print(f"{name} ({peer}) 成员 0 人（QQ 只在本地缓存部分群的成员列表）")
        return
    print(f"{name} ({peer}) 成员 {len(rows)} 人:")
    for u, card, nick in rows:
        print(f"  {u}  {card or nick}")
