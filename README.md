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
- `/skill` 命令组，用户可直接通过指令管理 Skills
- `/mcp` 命令组，用户可直接通过指令管理 MCP 服务器
- 内置英文版 `skills-mcp-manager` Skill，引导 AI 正确调用管理工具

## 安装

**方式一**：在 AstrBot 插件市场搜索「Skills & MCP 管理器」，点击安装。

**方式二**：插件界面右下角点击加号 → 从链接安装，输入：
```
https://github.com/piexian/astrbot_plugin_skills_mcp_manager
```

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
/skill install         # 交互式安装（发送 ZIP 文件）
/skill update <名称>   # 交互式更新（发送 ZIP 文件）

/mcp ls                # 列出所有 MCP 服务器
/mcp config <名称>     # 查看配置详情
/mcp on  <名称>        # 启用 MCP 服务器
/mcp off <名称>        # 禁用 MCP 服务器
/mcp del <名称>        # 删除 MCP 服务器
/mcp add <名称>        # 交互式添加（发送 JSON 配置）
/mcp update <名称>     # 交互式更新（发送 JSON 配置）
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
