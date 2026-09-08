# Skills & MCP 管理器

为 AstrBot 提供 Skills 和 MCP 服务器的管理工具与指令。

> **注意**：本插件专注于 Skills 和 MCP 的**生命周期管理**（安装、启用、禁用、删除等）。Skill 文件的读写操作请使用 AstrBot 内置文件工具（`astrbot_file_read_tool`、`astrbot_file_edit_tool`、`astrbot_file_write_tool`、`astrbot_grep_tool`）。

## 环境要求

| 依赖 | 版本要求 |
|------|----------|
| Python | >= 3.10 |
| AstrBot | >= 4.23.6 |

## 功能

- 13 个 LLM Tool，覆盖 Skills 和 MCP 全生命周期管理
- 支持国际化（中文 / English / 日本語），Dashboard 根据当前语言自动切换
- `/skill` 命令组，用户可直接通过指令管理 Skills
- `/mcp` 命令组，用户可直接通过指令管理 MCP 服务器
- 内置英文版 `skills-mcp-manager` Skill，引导 AI 正确调用管理工具
- 本插件安装、更新 Skill 前执行离线静态扫描，支持可选审查模型和命令二次确认

## 安装

**方式一**：在 AstrBot 插件市场搜索「Skills & MCP 管理器」，点击安装。

**方式二**：插件界面右下角点击加号 → 从链接安装，输入：
```
https://github.com/piexian/astrbot_plugin_skills_mcp_manager
```

> **内置 Skill 安装**：AstrBot >= 4.24.2 自动加载。低版本用户安装插件后，开启 Computer Use，对 AI 说：
> ```
> cp -r data/plugins/astrbot_plugin_skills_mcp_manager/skills/skills-mcp-manager data/skills/
> ```
> 沙盒模式路径隔离，无法直接 `cp`，建议切到本地模式执行，或通过 WebUI 手动上传。

## 工具列表

### Skills 管理

| 工具 | 功能 | 权限 |
|------|------|------|
| `list_skills` | 列出所有 Skills 及状态 | 无 |
| `enable_skill` | 启用 Skill | 管理员 |
| `disable_skill` | 禁用 Skill | 管理员 |
| `delete_skill` | 删除 Skill（需确认） | 管理员 |
| `install_skill` | 从 ZIP 安装 Skill | 管理员 |
| `update_skill_from_zip` | 从 ZIP 覆盖更新 Skill（需确认） | 管理员 |

### MCP 服务器管理

| 工具 | 功能 | 权限 |
|------|------|------|
| `list_mcp_servers` | 列出 MCP 服务器及运行状态 | 无 |
| `get_mcp_server_config` | 查看配置详情（自动脱敏） | 无 |
| `enable_mcp_server` | 启用 MCP 服务器 | 管理员 |
| `disable_mcp_server` | 禁用 MCP 服务器 | 管理员 |
| `add_mcp_server` | 添加 MCP 服务器（自动测试连接） | 管理员 |
| `update_mcp_server` | 更新配置（支持 diff 编辑） | 管理员 |
| `remove_mcp_server` | 移除 MCP 服务器（需确认） | 管理员 |

## 使用

### 指令

```
/skill ls              # 列出所有 Skills
/skill on  <名称>      # 启用 Skill
/skill off <名称>      # 禁用 Skill
/skill del <名称>      # 删除 Skill
/skill files <名称>    # 查看文件结构
/skill read <名称> <文件>  # 读取文件内容
/skill install [--force]       # 上传 ZIP，报告返回后发送提示的确认词安装
/skill update <名称> [--force] # 上传 ZIP 或单文件，报告返回后发送提示的确认词更新

/mcp ls                # 列出所有 MCP 服务器
/mcp config <名称>     # 查看配置详情
/mcp on  <名称>        # 启用 MCP 服务器
/mcp off <名称>        # 禁用 MCP 服务器
/mcp del <名称>        # 删除 MCP 服务器
/mcp add <名称>        # 交互式添加（发送 JSON 配置）
/mcp update <名称>     # 交互式更新（发送 JSON 配置）
```

### Skill 安装与更新扫描

`install_skill`、`update_skill_from_zip`、`/skill install` 和 `/skill update` 共用扫描流程。
ZIP 在正式写入前检查整个包；单文件更新在临时候选内容中合并更新，再检查整个 Skill。
静态扫描不会执行 Skill 脚本、调用模型或联网获取依赖。报告由选定的审查模型或当前会话主模型解释。

- 命令入口先返回报告，不写入正式目录。默认等待 300 秒，可通过 `skill_confirm_timeout` 调整；只有同一会话发起者发送当前语言对应的纯文本确认词才执行。空格、换行、标点、大小写变体、同义回复或附带附件都不算确认，也不延长等待。超时不安装。
- 命令默认拒绝高风险和不完整扫描；显式传入 `--force` 可忽略内容分析结果，但仍展示报告并等待确认。不安全路径、包结构和输入大小上限不能绕过。同一条消息可上传多个文件，报告后确认待安装列表；等待期间不能再追加文件。
- `skill_review_provider_id` 留空时使用当前会话主模型；指定后，命令直接返回该模型的文本报告，不调用主模型，不安排主模型补投。指定模型失败时直接展示静态报告。审查模型只读取报告，不读取 Skill 全文。
- `skill_review_language` 可选 `简体中文`、`English`、`日本語`，默认简体中文，用于模型审查报告。确认词分别固定为 `确认`、`confirm`、`確認`，每种语言只接受一个词，必须精确匹配。
- AI 工具沿用原有确认方式，审阅意见和扫描报告仍返回主模型。`skill_scan_mode=enforce` 拦截高风险；`report_only` 允许完整扫描后的风险内容通过，两者均拒绝不完整扫描。不提供工具参数跳过扫描。
- 启用、其他渠道安装、原生文件工具编辑和全局巡检不在拦截范围内。
- 安装不覆盖同名 Skill，请使用更新入口；更新保留原有启用状态。被阻断的更新不会修改旧文件。

初版包含中英文指令规则、下载后执行、敏感信息上传和基础 Python AST 分析。Python 数据流仅覆盖单文件内简单导入别名、赋值与调用，不覆盖复杂跨函数/跨文件传播。其他文本语言采用规则扫描。

静态扫描设计参考了 [NVIDIA SkillSpector](https://github.com/NVIDIA/SkillSpector) 的规则分类、行为分析和扫描完整性处理。相关说明见 [NVIDIA 官方文档：安装前扫描 Agent Skills](https://docs.nvidia.com/skills/scanning-agent-skills)。本插件采用独立的轻量实现，检测范围以上述说明为准。

资源上限：ZIP 原始大小 20 MiB、最多 1,000 个条目、单文件 2 MiB、内容总量 32 MiB、压缩比 200:1；Python AST 上限 250,000 字符及 50,000 节点。扫描有时间与工作量检查，超限返回不完整。初版不分析 ZIP64、嵌套压缩包、可执行二进制及非 UTF-8 文本。PNG 验证容器边界/校验和并检查可见文本后列为附件，不解码像素、不证明运行时安全；其他二进制图片暂按不支持处理。

所有工具结果都包含 `scan`，分别报告 `status`（`complete` / `incomplete`）、`decision`（`allow` / `warn` / `block`）、规则版本、候选内容指纹、命中规则、文件/行号、限制原因和实际 `operation_status`。报告不回显完整源码或凭据值。**未发现风险不代表绝对安全，扫描通过不代表安装成功。**

未指定审查模型时，命令报告和主模型回复写入对话历史；模型不可用时保留报告，在本会话后续请求补投。待确认内容只保存在内存，超时或重启即取消，补投报告不会恢复安装授权。确认后的安装结果由命令直接返回；等待期间原 Skill 被修改时，要求重新提交审查。

### 开发验证

纯离线测试不依赖 AstrBot：

```bash
python3 -m unittest tests.test_skill_scan tests.test_scan_delivery tests.test_scan_review -v
```

在已安装 AstrBot 的 Python 环境中运行入口集成测试（测试自行创建一次性 `ASTRBOT_ROOT`）：

```bash
SKILL_SCAN_ASTRBOT_TESTS=1 python -m unittest tests.test_astrbot_integration -v
```

### LLM 对话中使用

当用户通过 LLM 对话管理 Skills 或 MCP 时，AI 会自动调用对应的工具。例如「帮我添加一个 arxiv 搜索服务」→ 自动调用 `add_mcp_server`。

如需 AI 查看或修改 Skill 文件内容，请使用 AstrBot 内置文件工具。若当前对话中没有这些工具，请先在 WebUI 的「配置 → 普通配置 → 使用电脑能力」中将 `Computer Use Runtime` 设为 `local` 或 `sandbox`。详见 [Computer Use 文档](https://docs.astrbot.app/use/computer.html)。

### 安全设计

- **管理员校验**：所有管理操作均需通过权限验证
- **名称白名单**：Skills `^[\w.-]+$`，MCP `^[A-Za-z0-9._-]+$`
- **路径安全**：`Path.resolve()` + `relative_to()` 防止目录越权
- **二次确认**：破坏性操作需 `confirm=true` 参数
- **配置脱敏**：递归隐藏嵌套 dict/list 中的 API Key / Token
- **错误信息脱敏**：异常细节仅记录日志，用户侧返回通用描述
- **回滚机制**：添加/更新/启用失败时自动回滚配置并恢复旧运行态
- **配置持久化校验**：`save_mcp_config()` 返回值检查，失败时报错
- **状态一致性**：启用/禁用操作先执行再保存，避免配置态与运行态不一致
- **Zip Slip 防护**：解压前验证所有 ZIP 成员路径，更新时先备份后回滚
- **Diff 编辑防护**：MCP 配置 diff 编辑 + 50000 字符上限

## 配置

在 AstrBot 管理面板的插件配置中可设置：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `diff_mode` | bool | `true` | 启用 MCP 配置 diff 编辑模式 |
| `diff_match_threshold` | int（滑块 50-100） | `100` | diff 匹配阈值，100 表示必须完全匹配 |

## 项目结构

```
astrbot_plugin_skills_mcp_manager/
├── main.py                          # 插件入口
├── metadata.yaml                    # 插件元数据
├── _conf_schema.json                # 插件配置 Schema
├── CHANGELOG.md
├── README.md
├── .astrbot-plugin/
│   └── i18n/
│       ├── zh-CN.json               # 中文翻译
│       └── en-US.json               # 英文翻译
├── tools/
│   ├── __init__.py                  # 工具导出
│   ├── skill_tools.py               # 6 个 Skills 管理 FunctionTool
│   └── mcp_tools.py                 # 7 个 MCP 管理 FunctionTool
└── skills/
    └── skills-mcp-manager/
        └── SKILL.md                 # 内置 AI 指令手册
```

## 相关链接

- [AstrBot 文档](https://docs.astrbot.app/)
- [插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
- [Issues](https://github.com/piexian/astrbot_plugin_skills_mcp_manager/issues)

## 许可

AGPL-3.0 License

<div align="center">

**如果这个插件对你有帮助，请给个 ⭐ Star 支持一下！**

</div>
