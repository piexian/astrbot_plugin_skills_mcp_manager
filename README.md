# Skills & MCP 管理器

在 AstrBot 对话中安装和管理 Skills、配置 MCP 服务器。支持从技能市场链接或 ZIP 安装 Skill，先查看审查报告，再确认安装。

- 支持 GitHub、skills.sh、ClawHub、腾讯 SkillHub 和 SkillsMP 链接。
- 安装与更新前执行静态扫描，可选择独立的审查模型。
- 提供 `/skill`、`/mcp` 命令和 13 个 AI 管理工具。
- 支持简体中文、English、日本語界面与审查报告。

## 安装

需要 **AstrBot ≥ 4.23.6**、**Python ≥ 3.10**。

在 AstrBot 插件市场搜索 **Skills & MCP 管理器**，或通过以下仓库地址安装：

```text
https://github.com/piexian/astrbot_plugin_skills_mcp_manager
```

安装、更新、删除等操作需要 AstrBot 管理员权限，请先在管理面板配置管理员。

## 快速使用

### 从链接安装 Skill

直接在对话中发送：

```text
/skill install https://github.com/anthropics/skills/tree/main/skills/frontend-design
```

也可以使用市场详情链接，或在仓库链接后指定技能名：

```text
/skill install https://skills.sh/vercel-labs/agent-skills/vercel-composition-patterns
/skill install https://github.com/anthropics/skills frontend-design
```

插件会下载 Skill、返回审查报告，并等待确认。默认 **5 分钟内**由发起者回复以下确认词，才会安装：

| 审查回复语言 | 确认词 |
|---|---|
| 简体中文 | `确认` |
| English | `confirm` |
| 日本語 | `確認` |

只接受对应词的纯文本精确匹配，不能附加空格、标点或附件。其他回复不会延长等待，超时自动取消。

### 上传安装与更新

发送 `/skill install` 进入交互模式，再发送链接或上传 ZIP。ZIP 可以在根目录包含 `SKILL.md`，也可以包含 Skill 文件夹；同一条消息可上传多个文件。

更新已安装的 Skill：

```text
/skill update frontend-design https://github.com/anthropics/skills/tree/main/skills/frontend-design
```

省略链接后可上传 ZIP 或单个文件。ZIP 更新需包含与目标 Skill 同名的顶层文件夹。更新同样先审查、再确认，并保留原有启用状态。

扫描未通过时不会安装。已核对风险、需要强制安装时，在发起命令时添加 `--force`：

```text
/skill install <链接> [技能名] --force
/skill update <名称> [链接] [技能名] --force
```

强制模式仍会展示报告并等待确认，也不能绕过不安全路径、包结构和大小限制。

### 支持的链接

| 来源 | 可使用的链接 |
|---|---|
| GitHub | 仓库、Skill 目录、`SKILL.md` 文件、归档及 Release 下载链接 |
| skills.sh | 技能详情页 |
| ClawHub | 技能详情页，支持托管包和 GitHub 来源 |
| 腾讯 SkillHub | `skillhub.cn` 或 `skillhub.cloud.tencent.com` 技能详情页 |
| SkillsMP | 技能详情页；解析失败时可改用页面中的 GitHub 目录链接 |

一个仓库包含多个 Skill 时，请指定技能名或目录。私有 GitHub 仓库需配置 Token，并使用仓库或目录链接。

### 通过 AI 管理

也可以直接对 AI 说：

- “列出已安装的 Skills。”
- “禁用 frontend-design。”
- “添加这个 MCP 服务器”，并附上 JSON 配置。

AI 工具支持 Skills 和 MCP 的查询、启停、安装、更新及删除。MCP 添加或更新时会测试连接，配置编辑支持差异替换。

AI 安装工具接收本地或沙盒 ZIP 路径；使用市场链接安装时，直接发送 `/skill install <链接>`。

## 命令

| 命令 | 用途 |
|---|---|
| `/skill ls` | 列出 Skills |
| `/skill install [链接] [技能名] [--force]` | 安装 Skill，省略链接进入上传模式 |
| `/skill update <名称> [链接] [技能名] [--force]` | 更新 Skill，省略链接进入上传模式 |
| `/skill on <名称>` / `/skill off <名称>` | 启用 / 禁用 Skill |
| `/skill del <名称>` | 删除 Skill |
| `/skill files <名称>` | 查看文件结构 |
| `/skill read <名称> <文件>` | 查看文件内容 |
| `/mcp ls` | 列出 MCP 服务器 |
| `/mcp config <名称>` | 查看配置，敏感字段会被隐藏 |
| `/mcp add <名称>` / `/mcp update <名称>` | 添加 / 更新，按提示发送 JSON 配置 |
| `/mcp on <名称>` / `/mcp off <名称>` | 启用 / 禁用 MCP 服务器 |
| `/mcp del <名称>` | 删除 MCP 服务器 |

Skill 安装或变更后，发送一条新消息以刷新当前对话的可用能力。

## 配置

在 AstrBot 管理面板打开本插件的配置：

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `skill_github_token` | 留空 | 访问私有 GitHub 仓库或提高 API 限额 |
| `skillhub_api_key` | 留空 | 腾讯 SkillHub API 凭证 |
| `skill_review_provider_id` | 留空 | 审查模型；留空使用当前会话主模型 |
| `skill_review_language` | `简体中文` | 可选 `简体中文`、`English`、`日本語`，同时决定确认词 |
| `skill_confirm_timeout` | `300` | 命令返回报告后的确认等待时间，单位为秒 |
| `skill_scan_mode` | `enforce` | AI 工具的扫描策略；`report_only` 仅报告内容风险 |
| `diff_mode` | `true` | 使用差异替换编辑 MCP 配置 |
| `diff_match_threshold` | `100` | 差异替换的文本匹配度，100 为完全匹配 |

指定审查模型后，命令直接返回该模型的报告，不再调用主模型；调用失败时展示静态报告。AI 工具会将报告及审阅意见返回当前对话。模型审阅的是扫描报告，不是 Skill 完整源码。

`skill_scan_mode` 仅影响 AI 工具；命令默认严格检查，覆盖内容风险判定需显式使用 `--force`。GitHub Token 只需目标仓库的 Contents 读取权限。

## 常见问题

**链接下载失败？** 检查 AstrBot 所在环境能否访问来源网站。仅支持 HTTPS 公网地址，内网和 Fake-IP DNS 结果会被拒绝。GitHub 私有仓库需确认 Token 有读取权限；遇到限流可配置 Token 或稍后重试。

**扫描未完成？** 检查报告中的原因。ZIP 上限为 20 MiB，单文件上限为 2 MiB；ZIP64、嵌套压缩包、可执行二进制和部分文件编码暂不支持。

**AI 找不到文件编辑工具？** 在 AstrBot 的“使用电脑能力”设置中选择 `local` 或 `sandbox`，再发送新消息。Skill 文件编辑使用 AstrBot 内置文件工具，详见 [Computer Use 文档](https://docs.astrbot.app/use/computer.html)。

**审查是否保证 Skill 安全？** 静态扫描可帮助识别危险指令和代码模式，但不能保证绝对安全。扫描仅覆盖通过本插件安装或更新的内容。

## 参考与反馈

静态扫描设计参考了 [NVIDIA SkillSpector](https://github.com/NVIDIA/SkillSpector)。进一步了解：[NVIDIA 官方文档：安装前扫描 Agent Skills](https://docs.nvidia.com/skills/scanning-agent-skills)。

- [AstrBot 文档](https://docs.astrbot.app/)
- [更新记录](CHANGELOG.md)
- [问题反馈](https://github.com/piexian/astrbot_plugin_skills_mcp_manager/issues)

## 许可

[AGPL-3.0](LICENSE)
