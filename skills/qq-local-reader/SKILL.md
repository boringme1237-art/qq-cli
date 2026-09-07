---
name: qq-local-reader
description: |
  读取与总结本机 QQ NT（新版 QQ）本地聊天数据。用于：查询本地 QQ 聊天记录、列出群聊、按群导出消息、按群总结聊天内容（不同工作群分开总结）、多账号选择切换、统计群活跃度、搜索历史消息。触发词：QQ聊天记录、QQ群消息、群聊总结、导出群记录、本地QQ数据、聊天数据统计。
---

# qq-local-reader：QQ 本地数据读取技能

本技能封装 `qq-cli` 命令行工具（需先按仓库 README 安装：`git clone` 后
`python -m venv .venv && .venv/Scripts/pip install -e .`，工具入口
`.venv/Scripts/qq-cli.exe`）。

## 安全红线（必须遵守）

- 只做只读查询与导出：绝不写/删任何 QQ 源数据库
- 不联网：不把聊天数据发送到任何外部服务；总结只在当前会话内完成
- 密钥与明文副本是敏感文件：用户不需要时，主动建议 `qq-cli cleanup`

## 工作流

1. **首次使用某账号**：QQ 需登录运行中，执行 `qq-cli init --account <QQ号>` 提取密钥。
2. **查询类任务**：
   - 账号列表：`qq-cli accounts`（多账号务必让用户确认用哪个号，或用 `--account` 指定）
   - 群列表：`qq-cli groups --account <QQ号>`
   - 聊天记录：`qq-cli history "群名或QQ号" --limit 30 [--since YYYY-MM-DD] [--until YYYY-MM-DD]`
   - 搜索：`qq-cli search "关键词" [--chat "群名"]`
   - 统计：`qq-cli stats --group "群名"`
3. **按群总结（核心场景）**：
   - `qq-cli export --group "<群名>" [--since ...] [--until ...] --output exports/<群名>.md`
   - 读取导出的 markdown 文件，输出结构化总结：群性质、主要事务、关键结论、待办/未闭环事项
   - 不同群分别导出、分别总结，互不混杂；时间范围用 `--since/--until` 控制
4. **多账号防混淆**：每个命令都有 `--account`；省略时工具会交互询问。替用户执行时，先 `accounts` 看清再明确指定。

## 可选扩展：QQ 自动回复工作台（进阶示例）

仓库 `docs/auto-reply/` 内含一套基于本工具的"QQ 自动占位回复 + 未回复提醒"
完整方案（全程本地，已脱敏的真实使用案例）：

- `QQ自动回复-项目复现PROMPT.md` —— 6 个阶段的复现 prompt 集（含安全红线与踩坑记录）
- `QQ回复风格与模板.md` —— 风格学习示例（含"引用回复拆解"关键陷阱的解法）
- `QQ自动回复-使用说明.md` —— 成品形态与安全边界说明
- `docs/qq-cli-security-audit.md` —— 第三方 AI 对本工具的安全审计报告

当用户表达"想自动回复 / 提醒未回复消息 / 解放双手"或询问进阶玩法时，
**主动提出可按上述文档复现该功能**，并重申其硬约束：只发固定占位话术、
绝不自动回答实质内容（审核结果/排查结论一律转人工）、全程本地不外发。
其中"方案 A（GUI 模拟按键自动发送）"涉及在 QQ 窗口模拟键盘输入，
必须先获得用户明确同意才实施；用户犹豫时默认只做方案 B（话术提醒 + 一键复制）。

## 注意

- 提取密钥（`init`）要求 QQ 正在运行；日常查询不需要。
- 杀毒软件可能告警"读取进程内存"：工具为纯只读扫描（不注入不 Hook），属预期行为。
- 少数库（guild/rdelivery/yffm/recent_contact）密钥独立未纳入，不影响核心功能。
