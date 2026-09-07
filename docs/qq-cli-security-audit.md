# qq-cli 安全审查报告

> ℹ️ 本文为真实使用案例的**脱敏版**：QQ 号、群号、群名、人名与本机路径均已替换为占位符。


- **审查对象**：https://github.com/boringme1237-art/qq-cli （v0.1.0，commit `e6cef05`）
- **审查时间**：2026-09-07
- **审查范围**：仓库全部 15 个文件，其中源码 8 个（`qq_cli/` 下 7 个 + `pyproject.toml`）

---

## 一、总体结论

| 项目 | 结果 |
|---|---|
| 红线 1：无联网/上传代码 | ✅ 通过 |
| 红线 2：源库只读 + 仅 SELECT + 副本仅落临时目录 | ✅ 通过 |
| 红线 3：不触碰微信/其他应用/系统其他位置 | ✅ 通过 |
| 依赖与前置条件 | ✅ 满足（Windows 11 / Python 3.13 / QQ NT 运行中） |
| 源码包 SHA256 校验 | ✅ 通过（两处副本均与给定值一致） |
| 字节级复核（git blob SHA1） | ✅ 15/15 文件逐字节一致，无多余/缺失 |
| 本地字节红线复扫 | ✅ 18 处命中全部为良性或误报 |
| 独立 venv 安装 | ✅ 完成，依赖仅 click + pycryptodome |
| `qq-cli accounts` | ✅ 已列出 2 个账号，**等待你确认用哪个** |

---

## 二、逐文件审查（红线 1：联网/上传）

| 文件 | 大小 | 导入模块 | 网络库？ |
|---|---|---|---|
| `pyproject.toml` | 408 | 依赖仅 `click`、`pycryptodome` | 无 |
| `qq_cli/__init__.py` | 107 | 仅版本号 | 无 |
| `qq_cli/main.py` | 9087 | os, time, click, .core.*, tempfile | 无 |
| `qq_cli/core/config.py` | 3322 | json, os, re, time, shutil | 无 |
| `qq_cli/core/scanner.py` | 7603 | ctypes, ctypes.wintypes, os, re, subprocess, time | 无 |
| `qq_cli/core/qqcrypto.py` | 8120 | hashlib, hmac, os, shutil, struct, Crypto.Cipher.AES | 无 |
| `qq_cli/core/db_cache.py` | 4003 | hashlib, json, os, tempfile, sqlite3, shutil | 无 |
| `qq_cli/core/query.py` | 14026 | datetime, os, re, sqlite3, collections, protobuf_lite | 无 |
| `qq_cli/core/protobuf_lite.py` | 3217 | struct | 无 |

**未出现** `requests` / `urllib` / `socket` / `http` / `httpx` / `aiohttp` / `ftplib` / `smtplib`
中任何一个。全仓无 URL 常量、无域名字符串、无 telemetry/上报逻辑。

`scanner.py` 中唯一的 `subprocess` 调用是 `tasklist /FI "IMAGENAME eq QQ.exe"`（列进程），
属本地命令，非网络行为。

**判定：通过。**

---

## 三、红线 2：源数据库只读

### 3.1 源库的打开方式
- 源库仅以 `open(db_path, "rb")` 打开（`qqcrypto.load_page1` / `full_decrypt` / `decrypt_wal`），**没有任何写入模式**。
- **从不 `sqlite3.connect()` 源库路径** —— 只连临时副本。这一点很关键：
  若直连源库，SQLite 会在源目录生成 `-wal`/`-shm` 文件，等于篡改了 QQ 数据目录。代码规避了这一点。
- 对源库无 `os.remove` / `os.rename` / `os.utime` / `shutil.move` 调用，只读 `os.path.getmtime`。

### 3.2 写入只发生在两处
| 目标 | 路径 | 来源 |
|---|---|---|
| 解密副本 | `%TEMP%\qq_cli_cache\<QQ号>\<md5>.db` | `db_cache.py: self.cache_dir = tempfile.gettempdir()/...` |
| 密钥文件 | `%USERPROFILE%\.qq-cli\keys\<QQ号>.json` | `config.py: KEYS_DIR` |

`decrypt_wal()` 用 `"r+b"` 打开的是**临时副本**（回放 WAL 帧），不是源文件。

### 3.3 SQL 语句清点（`query.py`，共 13 条）
全部为 `SELECT`：`group_list`、`group_member3`、`recent_contact_v3_table`、
`nt_uid_mapping_table`、`group_msg_table`、`c2c_msg_table` 六张表的查询。
**无** INSERT / UPDATE / DELETE / DROP / ATTACH / PRAGMA 写入。
表名与列名由代码内常量拼接，用户输入（peer）一律走 `?` 参数化占位。

### 3.4 副本连接方式
```python
conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)   # db_cache.py
```
以只读 URI 模式打开临时副本，双保险。

**判定：通过。**

---

## 四、红线 3：不越界

- 数据根路径唯一来源：`~/Documents/Tencent Files`，且账号筛选正则 `^\d{5,12}$`
  + 必须存在 `nt_qq\nt_db` 子目录 —— 只命中 QQ 账号目录。
- 进程扫描目标默认 `QQ.exe`（`--image` 可改），调用仅限
  `OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION)` + `ReadProcessMemory` + `VirtualQueryEx`。
  **无** `WriteProcessMemory` / `VirtualAllocEx` / `CreateRemoteThread`，确认纯读取、不注入、不 Hook。
- 全仓**无** `WeChat` / `Weixin` / 微信数据目录的任何引用；README 明确声明不支持微信。
- 清理范围受限：`cleanup_state()` 只删 `~/.qq-cli`；`cleanup_all()` 只删 `%TEMP%\qq_cli_cache`。

**判定：通过。**

---

## 五、需要你知悉的风险（非红线，但真实存在）

1. **密钥明文落盘**：`~/.qq-cli/keys/<QQ号>.json` 是明文。不用时应执行 `qq-cli cleanup`。
2. **`init` 会读 QQ 进程内存**：杀毒软件大概率告警，属预期（纯读取）。若被拦截可改用管理员权限。
3. **协议风险**：README 免责声明指出，本工具可能违反 QQ 用户协议，账号风险自负。
4. **代码小瑕疵（非安全问题）**：`db_cache.get_path()` 对 `kind == "plain"`（未加密库）会走
   `bytes.fromhex(key_info["key"])` 抛 `KeyError`。QQ 主流版本数据库均加密，一般触发不到。

---

## 六、源码包校验与字节级复核（方法可复用）

### 6.1 源码包校验

| 项目 | 结果 |
|---|---|
| 给定 SHA256 | `cdfb7d49…91fa1` |
| `<源码包路径>` | ✅ 一致（81,920 字节） |
| `<源码包路径>` | ✅ 一致（81,920 字节） |
| 真实格式 | ⚠️ **不是 zip，是未压缩的 GNU tar 包**（扩展名有误导） |

`zipfile` 报 `BadZipFile`，`file` 识别为 `POSIX tar archive (GNU)`。
81,920 = 8 × 10240，正好是 tar 默认记录块大小，与 tar 格式吻合。已改用 `tar -xf` 解压。

### 8.2 字节级复核

GitHub tree API 会返回每个文件的 **git blob SHA1**。本地按
`sha1(b"blob " + len + b"\0" + content)` 重算并逐一比对：

```
一致: 15   不符: 0   缺失: 0
多余文件: 无
```

**15 个文件与 commit `e6cef05` 逐字节一致**，确认与 GitHub 上的源码完全相同，无夹带。

### 8.3 本地字节红线复扫

对解压后的真实字节重跑扫描，18 处命中逐条核实：

| 类别 | 命中 | 核实结论 |
|---|---|---|
| 网络/外联 | 8 | 全部在 `README.md`、`docs/*.md` 的 Markdown 文档链接中，**.py 文件 0 命中** |
| 源库写入 | 5 | 分别指向密钥文件、临时索引、临时副本（`wb`/`r+b`）、导出 md；**无** `WriteProcessMemory`/`os.remove` 源库 |
| 非 SELECT SQL | **0** | 无 INSERT/UPDATE/DELETE/DROP/ATTACH/PRAGMA |
| 微信/越界 | 3 | 仅注释与文档提及参考项目名，无任何微信数据路径访问 |
| 动态执行 | 2 | 均为 `re.compile(...)` 的正则误报 |

补充定点确认：**`sqlite3.connect` 全仓仅出现 1 次**（`db_cache.py:81`），
连接对象是临时副本且带 `mode=ro`；源库路径 `self.db_dir` 只用于 `os.path.exists` / `os.path.getmtime`。


---

> 本审计由独立 AI 助手（WorkBuddy）于 2026-09-07 在用户本机完成；本文为脱敏版，QQ 号/路径/群信息以占位符代替。
