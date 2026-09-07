# qq-cli — QQ NT 本地聊天数据只读查询/导出工具

`qq-cli` 是一个运行在 Windows 上的命令行工具，用于**读取、检索、导出你自己电脑上的新版 QQ（QQNT）本地聊天数据**：多账号管理、群列表、聊天记录、全局搜索、消息统计、按群导出 Markdown（可直接喂给 AI 做"按群总结"）。

> **设计红线**：源数据库一律只读；密钥通过纯内存读取获得（**不注入、不 Hook、不调试** QQ 进程）；工具本身**零网络代码**，任何数据都不离开你的电脑。

## 功能

| 命令 | 说明 |
|---|---|
| `accounts` | 列出本机所有 QQ 账号（含最近活跃时间、是否已提取密钥） |
| `init` | 从正在运行的 QQ 进程内存提取数据库密钥（多账号交互选择） |
| `groups` | 群列表（群号/群名/成员数/我的群备注，支持名称过滤） |
| `sessions` | 最近会话列表（群聊 + 好友） |
| `history` | 指定群/好友的聊天记录（支持 `--since/--until` 时间范围） |
| `search` | 全局或限定会话的关键词搜索（带会话名标注） |
| `members` | 群成员列表（QQ 只缓存部分群的成员，属正常现象） |
| `stats` | 消息统计：总量、Top 发送者、时段分布、每日消息量 |
| `export` | 按群导出干净 Markdown —— 供 AI 阅读后做"按群总结" |
| `cleanup` | 一键清除全部敏感数据（密钥文件 + 明文解密副本） |

所有命令支持 `--account <QQ号>` 多账号切换；省略时若本机有多个账号会交互询问，避免多账号数据混乱。

## 环境要求

- Windows 10/11
- Python 3.10+
- QQ NT 桌面版**已登录且正在运行**（仅提取密钥时需要；日常查询不需要）
- 杀毒软件可能对"读取进程内存"行为告警 —— 本工具只调用 `ReadProcessMemory` 做只读扫描，不写入、不注入

## 安装

```powershell
git clone https://github.com/boringme1237-art/qq-cli.git
cd qq-cli
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\qq-cli.exe accounts
```

## 快速上手

```powershell
$qc = ".venv\Scripts\qq-cli.exe"

& $qc init --account <QQ号>          # ① 提取密钥（QQ 需登录运行中）
& $qc groups --account <QQ号>        # ② 看有哪些群
& $qc history "某工作群" --limit 30  # ③ 看最近消息
& $qc search "关键词"                # ④ 全局搜索

# ⑤ 按群导出 → 交给 AI 做按群总结（不同群分开导出、分开总结）
& $qc export --group "某工作群" --since 2026-09-01 --output exports\某工作群.md

# ⑥ 不用了，清掉敏感数据
& $qc cleanup
```

## 实现原理（要点）

1. **密钥提取**：SQLCipher 的编解码上下文在内存中会紧邻 `\x09HMAC_SHA1` 标记串存放密钥。扫描所有 `QQ.exe` 进程的可读内存区域，以该标记为锚点（±0x200 邻域 + 密集锚点带中心外扩两条路径），收集 16 字节、全可打印、不全为字母数字、16 字节对齐的候选窗口，逐个用数据库第 1 页 HMAC 校验。
2. **解密**：QQNT 数据库 = 1024 字节私有头 + SQLCipher 4 正文（页 4096，PBKDF2 派生 4000 次迭代）。页 HMAC 算法与 KDF 哈希的组合在不同库/版本间不固定，工具内置 12 种组合自动匹配。
3. **查询**：解密副本写入系统临时目录（按 mtime 增量复用），所有 SQL 均为 SELECT。消息体（protobuf）由内置的极简 wire-format 解析器抽取可读文本，@提及、图片、文件、群系统消息自动转为友好标记。
4. **多账号**：每个账号一个密钥文件与副本子目录，互不混淆。

已在 QQ NT **9.9.20（Windows x64）** 实测通过；算法组合矩阵覆盖社区验证至 9.9.32 的配置。更详细的表结构与密码学参数见 [docs/qqnt-database-notes.md](docs/qqnt-database-notes.md)。

## 常见问题

- **`init` 找不到密钥？** 确认 QQ 正在运行且已登录；刚启动的话先打开几个聊天会话再试；管理员权限运行可提高内存读取成功率。
- **个别库提示"不匹配"？** 少数库（guild/rdelivery/yffm 等）密钥独立，不影响聊天、群、统计等核心功能。
- **微信支持？** 不支持。微信 4.1.10+ 已移除内存密钥字面量，需要注入类方案（有账号风控风险），本工具刻意不涉及。若只需要"读微信"，推荐参考 [wechat-cli](https://github.com/huohuoer/wechat-cli)（旧版微信可用）。

## 免责声明

- 本工具仅供**个人**备份、检索、分析**自己设备上**的聊天数据，请勿用于读取他人数据或任何违法用途。
- 使用本工具可能违反腾讯 QQ 用户协议，由此产生的账号风险由使用者自行承担。
- 本工具按"现状"提供，不含任何担保；作者不对数据损坏、账号异常等后果负责。
- 请妥善保管 `~/.qq-cli/` 下的密钥文件与导出内容 —— 它们是明文的。

## 致谢与参考

思路与格式知识来自这些优秀的开源项目（本仓库代码为独立实现）：

- [QQBackup/x_key_scanner](https://github.com/QQBackup/x_key_scanner) — 内存锚点扫描密钥的算法思路
- [QQBackup/nt_msg_db_util](https://github.com/QQBackup/nt_msg_db_util) — QQNT 数据库格式与解密参数文档
- [huohuoer/wechat-cli](https://github.com/huohuoer/wechat-cli) — 架构参考（微信版同类工具）

## License

[MIT](LICENSE)
