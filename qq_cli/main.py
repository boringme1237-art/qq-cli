"""qq-cli 命令行入口。

数据安全红线：
- 源数据库一律只读；解密副本只写 %TEMP%/qq_cli_cache/
- 密钥只存本机 ~/.qq-cli/；cleanup 子命令可一键清除全部敏感文件
- 无任何网络代码
"""
import os
import time

import click

from . import __version__
from .core import config, db_cache, qqcrypto, scanner


def _account(account, assume_latest=False):
    return config.select_account(account, assume_latest=assume_latest)


def _load_keys_or_fail(uin):
    keys = config.load_keys(uin)
    if not keys:
        raise SystemExit(f"账号 {uin} 还没有密钥文件，请先运行:  qq-cli init --account {uin}"
                         "\n（需要 QQ 正在运行，读取 QQ.exe 内存提取密钥）")
    return keys


@click.group()
@click.version_option(__version__, prog_name="qq-cli")
def cli():
    """qq-cli — QQ NT 本地数据只读查询/导出工具。

    示例:
      qq-cli init                          # 提取密钥（交互选账号）
      qq-cli accounts                      # 列出本地账号
      qq-cli groups --account 123456       # 群列表
      qq-cli history "某工作群" --since 2026-08-01
      qq-cli export --group "某工作群" --output group.md
    """


@cli.command()
@click.option("--account", help="QQ 号（多账号时不指定则交互选择）")
@click.option("--db-root", default=None, help="QQ 数据根目录（默认 文档/Tencent Files）")
@click.option("--image", default="QQ.exe", show_default=True, help="内存扫描的目标进程名")
@click.option("--force", is_flag=True, help="已有密钥也重新提取")
def init(account, db_root, image, force):
    """提取本机 QQ 数据库密钥（需 QQ 正在运行）。"""
    acct = _account(account, assume_latest=force is False and account is None)
    uin = acct["uin"]
    if not force and config.load_keys(uin):
        click.echo(f"账号 {uin} 已有密钥文件（{config.keys_file(uin)}），如需重新提取加 --force")
        return

    db_rel = "nt_msg.db"
    db_path = os.path.join(acct["nt_db"], db_rel)
    if not os.path.exists(db_path):
        raise SystemExit(f"找不到 {db_path}")
    page1, kind = qqcrypto.load_page1(db_path)
    if kind == "plain":
        click.echo("nt_msg.db 是明文库（未加密），无需密钥")
        return
    click.echo(f"账号 {uin} | nt_msg.db 布局={kind} 盐={page1[:16].hex()}")
    click.echo("扫描 QQ.exe 内存中（不注入不 Hook，纯读取）...")
    res = scanner.scan_key(page1, qqcrypto.COMBOS, image=image)
    if not res:
        raise SystemExit("\n未能在内存中找到密钥。可能原因：\n"
                         "  1) QQ 刚启动还没打开过聊天数据 → 打开几个会话后重试\n"
                         f"  2) QQ 版本过新改了加密 → 用 --image 指定其他进程名试试\n"
                         "  3) 杀毒软件拦截了内存读取 → 查看杀软告警")
    click.echo(f"密钥: {res['key']!r}  (hmac={res['hmac_alg']}, kdf_iter={res['kdf_iter']}"
               f", kdf_hash={res['kdf_hash']})")

    # 交叉验证该账号所有数据库
    click.echo("\n校验账号下所有数据库...")
    keys = {}
    for name in sorted(os.listdir(acct["nt_db"])):
        if not name.endswith(".db"):
            continue
        p1, k = qqcrypto.load_page1(os.path.join(acct["nt_db"], name))
        if k == "plain":
            keys[name] = {"kind": "plain"}
            click.echo(f"  {name}: 明文")
            continue
        kb = res["key"].encode("ascii") if res["kind"] == "passphrase" \
            else bytes.fromhex(res["key"])
        if qqcrypto.verify_key(kb, p1, res["kdf_iter"], res["hmac_alg"],
                               raw=res["kind"] == "rawkey", kdf_hash=res["kdf_hash"]):
            keys[name] = {**res, "salt": p1[:16].hex()}
            click.echo(f"  {name}: OK")
        else:
            click.echo(f"  {name}: 不匹配（该库密钥可能不同，暂不纳入）")
    out = config.save_keys(uin, keys)
    n_ok = sum(1 for v in keys.values() if v.get("kind", "plain") not in ("plain",) and v.get("key"))
    click.echo(f"\n已保存 {n_ok} 个库的密钥到: {out}")
    click.echo("提示: 密钥文件是明文敏感文件，不需要时运行 qq-cli cleanup 清除。")


@cli.command("accounts")
def accounts_cmd():
    """列出本机所有 QQ 账号。"""
    accts = config.discover_accounts()
    if not accts:
        raise SystemExit("未找到账号数据目录（文档/Tencent Files/<QQ号>/nt_qq/nt_db）")
    click.echo(f"共 {len(accts)} 个本地账号:")
    for a in accts:
        t = a["last_active_ts"]
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "未知"
        has_keys = "✓" if config.load_keys(a["uin"]) else "✗"
        click.echo(f"  {a['uin']}  最近活跃: {ts}  密钥: {has_keys}")


@cli.command()
@click.option("--account", help="QQ 号")
@click.option("--limit", default=50, show_default=True)
def sessions(account, limit):
    """最近会话列表（含群聊）。"""
    from .core import query
    acct = _account(account)
    keys = _load_keys_or_fail(acct["uin"])
    cache = db_cache.DBCache(acct)
    try:
        query.print_sessions(cache, keys, acct["uin"], limit)
    finally:
        pass


@cli.command()
@click.option("--account", help="QQ 号")
@click.option("--limit", default=200, show_default=True)
@click.argument("keyword", required=False)
def groups(account, limit, keyword):
    """群列表（可按名称过滤）。"""
    from .core import query
    acct = _account(account)
    keys = _load_keys_or_fail(acct["uin"])
    cache = db_cache.DBCache(acct)
    query.print_groups(cache, keys, limit, keyword)


@cli.command()
@click.argument("chat")
@click.option("--account", help="QQ 号")
@click.option("--limit", default=30, show_default=True)
@click.option("--since", default=None, help="起始时间 YYYY-MM-DD")
@click.option("--until", default=None, help="结束时间 YYYY-MM-DD")
def history(chat, account, limit, since, until):
    """查看某群/某人的最近消息。CHAT 可以是群号、群名(部分匹配)或好友 QQ 号。"""
    from .core import query
    acct = _account(account)
    keys = _load_keys_or_fail(acct["uin"])
    cache = db_cache.DBCache(acct)
    query.print_history(cache, keys, acct["uin"], chat, limit, since, until)


@cli.command()
@click.argument("keyword")
@click.option("--account", help="QQ 号")
@click.option("--chat", default=None, help="限定群/好友")
@click.option("--limit", default=20, show_default=True)
def search(keyword, account, chat, limit):
    """全局或限定会话内搜索消息。"""
    from .core import query
    acct = _account(account)
    keys = _load_keys_or_fail(acct["uin"])
    cache = db_cache.DBCache(acct)
    query.print_search(cache, keys, acct["uin"], keyword, chat, limit)


@cli.command()
@click.option("--group", "group_name", default=None, help="群名/群号（不指定=全部会话）")
@click.option("--account", help="QQ 号")
@click.option("--since", default=None, help="起始时间 YYYY-MM-DD")
@click.option("--until", default=None, help="结束时间 YYYY-MM-DD")
@click.option("--limit", default=5000, show_default=True, help="最多导出条数")
@click.option("--output", required=True, help="输出 markdown 文件路径")
def export(group_name, account, since, until, limit, output):
    """导出某群（或全部会话）的消息为 markdown——按群总结的数据源。"""
    from .core import query
    acct = _account(account)
    keys = _load_keys_or_fail(acct["uin"])
    cache = db_cache.DBCache(acct)
    n = query.export_markdown(cache, keys, acct["uin"], group_name, since, until, limit, output)
    click.echo(f"已导出 {n} 条消息到 {output}")


@cli.command()
@click.option("--account", help="QQ 号")
@click.option("--group", "group_name", default=None)
def stats(account, group_name):
    """消息统计（总量/按发送者/按小时分布）。"""
    from .core import query
    acct = _account(account)
    keys = _load_keys_or_fail(acct["uin"])
    cache = db_cache.DBCache(acct)
    query.print_stats(cache, keys, acct["uin"], group_name)


@cli.command()
@click.argument("chat")
@click.option("--account", help="QQ 号")
def members(chat, account):
    """查看群成员列表。CHAT 可以是群号或群名(部分匹配)。"""
    from .core import query
    acct = _account(account)
    keys = _load_keys_or_fail(acct["uin"])
    cache = db_cache.DBCache(acct)
    query.print_members(cache, keys, chat)


@cli.command("cleanup")
def cleanup_cmd():
    """清除本工具的全部敏感数据：密钥文件 + 明文解密副本缓存。"""
    import tempfile
    removed = []
    p = config.cleanup_state()
    if p:
        removed.append(p)
    c = db_cache.cleanup_all()
    if c:
        removed.append(c)
    if removed:
        for r in removed:
            click.echo(f"已删除: {r}")
    else:
        click.echo("没有需要清理的内容")


if __name__ == "__main__":
    cli()
