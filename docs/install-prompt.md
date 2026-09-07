# qq-cli 安全安装 Prompt（可直接复制给任何 AI 助手）

> 让 AI 助手（ZCode / WorkBuddy / Claude 等）在本机安全安装 qq-cli。
> 约束词针对本工具的数据安全红线定制，可直接整段复制使用。

````text
帮我安装 qq-cli（QQ NT 本地聊天数据只读查询/导出工具），
地址：https://github.com/boringme1237-art/qq-cli
只安装这一个工具，不要修改、安装或配置其他任何项目。整个过程自动完成；
如果需要我授权（读进程内存、浏览器登录）或操作电脑界面，再提醒我。
若 git clone 失败，提醒我检查网络/代理后重试即可，不要自行更换其他
下载源或镜像。

一、安装前请先做一次安全检查（逐文件看代码，不看 README 自述），
   任何一条不满足就停下来告诉我：
   1. 不得包含联网/上传代码：不允许 requests/urllib/socket/http/httpx
      等网络库，不允许把聊天数据、密钥、统计结果发送到任何外部服务。
   2. 对 QQ 源数据库（文档\Tencent Files\<QQ号>\nt_qq\nt_db\*.db）
      只能只读：不得写入/修改/删除/重命名源文件；所有 SQL 只能是
      SELECT；不得直接 sqlite3.connect() 源库（会在 QQ 数据目录产生
      -wal/-shm 文件）；解密副本只允许写系统临时目录并以只读方式打开。
   3. 不得触碰微信、其他应用或系统其他位置的数据与配置。

二、阅读 README.md、docs\qqnt-database-notes.md、skills\qq-local-reader\SKILL.md，
   确认依赖（Windows 10/11、Python ≥3.10、QQ NT 已登录运行）、
   安装步骤与潜在风险后再开始。

三、用独立 venv 安装，不污染全局 Python：
   python -m venv .venv
   .venv\Scripts\pip install -e .
   .venv\Scripts\qq-cli.exe accounts

四、初始化约定：
   1. 首次 qq-cli init 需要读取正在运行的 QQ.exe 进程内存以提取本地
      数据库密钥：纯读取（不注入/不 Hook/不写入），杀毒软件可能告警，
      属预期行为，无需处理。
   2. 我本机可能有多个 QQ 号：init 前先运行 qq-cli accounts 列出账号
      让我确认用哪个，不要替我猜；之后所有命令都带 --account 指定，
      避免多账号数据混乱。
   3. 密钥明文保存在 ~/.qq-cli/keys/，属于敏感文件：不得复制到别处、
      不得上传或写入任何公开位置；工具不用时提醒我运行
      qq-cli cleanup 清除密钥和明文副本。
   4. 导出的聊天记录 markdown 含个人敏感信息：导出位置先问我；总结/
      搜索只在本地完成，禁止把聊天内容粘贴到网页或外部服务。
````

## 常见安装问题

- **git clone 超时 / TLS 握手失败**：国内网络直连 GitHub 常被阻断，需自行
  解决网络访问（代理或镜像加速），这也是本仓库只发布在 GitHub 的原因。
- **`init` 找不到密钥**：确认 QQ 正在运行且已登录；刚启动的话先打开几个
  聊天会话再试；必要时用管理员权限运行。
- **杀毒软件告警**：工具只调用 `ReadProcessMemory` 做只读扫描，不注入、
  不写入，可放心放行。
