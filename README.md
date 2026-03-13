# Skills & MCP 管理器 (astrbot_plugin_skills_mcp_manager)

为astrbot提供函数工具和指令来管理 AstrBot Skills 和 MCP 服务器。

## 环境要求

| 依赖 | 版本要求 | 说明 |
|------|----------|------|
| Python | >= 3.10 | |
| AstrBot | >= 4.13.2 | Skills + MCP 管理 API |

## 功能

- 16 个 LLM Tool — 供 LLM 自动调用的函数工具，覆盖 Skills 和 MCP 全生命周期管理
- `/skill` 命令组 — 用户直接通过指令管理 Skills
- `/mcp` 命令组 — 用户直接通过指令管理 MCP 服务器
- 内置 Skill — 自动安装 `SKILL.md` 指令手册，引导 AI 正确使用管理工具

## 安装

### 俩种方式

1. 在 AstrBot 插件市场搜索 `Skills & MCP 管理器` 
2. 在插件界面右下角点击加号选择从链接安装输入 ` https://github.com/piexian/astrbot_plugin_skills_mcp_manager  `


## LLM 工具列表

### Skills 管理

| 工具 | 功能 | 权限 |
|------|------|------|
| `list_skills` | 列出所有 Skills 及状态 | 无 |
| `enable_skill` | 启用 Skill | 管理员 |
| `disable_skill` | 禁用 Skill | 管理员 |
| `delete_skill` | 删除 Skill（需确认） | 管理员 |
| `install_skill` | 从 ZIP 安装 Skill | 管理员 |
| `list_skill_files` | 列出 Skill 文件结构 | 无 |
| `read_skill_file` | 读取 Skill 文件内容 | 无 |
| `update_skill_file` | 更新 Skill 文件内容（支持编辑模式） | 管理员 |
| `update_skill_from_zip` | 从 ZIP 覆盖更新 Skill（需确认） | 管理员 |

### MCP 服务器管理

| 工具 | 功能 | 权限 |
|------|------|------|
| `list_mcp_servers` | 列出 MCP 服务器及运行状态 | 无 |
| `get_mcp_server_config` | 查看配置详情（自动脱敏） | 无 |
| `enable_mcp_server` | 启用 MCP 服务器 | 管理员 |
| `disable_mcp_server` | 禁用 MCP 服务器 | 管理员 |
| `add_mcp_server` | 添加 MCP 服务器（自动测试连接） | 管理员 |
| `update_mcp_server` | 更新配置（支持编辑模式，测试→禁用→保存→启用） | 管理员 |
| `remove_mcp_server` | 移除 MCP 服务器（需确认） | 管理员 |

## 使用

### 指令

```
/skill ls              # 列出 Skills
/skill on  <名称>      # 启用 Skill
/skill off <名称>      # 禁用 Skill
/skill del <名称>      # 删除 Skill
/skill files <名称>    # 查看文件结构
/skill read <名称> <文件>  # 读取文件
/skill install         # 交互式安装（发送 ZIP）
/skill update <名称>   # 交互式更新（发送 ZIP/文件）

/mcp ls                # 列出 MCP 服务器
/mcp config <名称>     # 查看配置详情
/mcp on  <名称>        # 启用
/mcp off <名称>        # 禁用
/mcp del <名称>        # 删除
/mcp add <名称>        # 交互式添加（发送 JSON 配置）
/mcp update <名称>     # 交互式更新（发送 JSON 配置）
```

### LLM Tool

当 LLM 需要管理 Skills 或 MCP 服务器时，会自动调用对应的管理工具。例如用户说「帮我添加一个 arxiv 搜索服务」，AI 会调用 `add_mcp_server`。

### 安全设计

- **管理员校验**: 所有修改操作通过 `event.role` 验证
- **名称校验**: 名称必须匹配 `^[A-Za-z0-9._-]+$`，防止路径注入
- **路径安全**: 文件读写有目录越权检查
- **二次确认**: 破坏性操作需 `confirm=true` 参数
- **配置脱敏**: 展示 MCP 配置时自动隐藏 API Key / Token
- **回滚机制**: MCP 添加/启用失败时自动回滚配置
- **状态一致性**: MCP 启用/禁用操作先执行再保存，避免配置态与运行态不一致
- **Zip Slip 防护**: ZIP 解压前验证所有成员路径
- **编辑模式**: 可选的 diff 编辑模式，AI 只能修改指定文本片段，防止意外覆盖整个文件

## 配置

在 AstrBot 管理面板的插件配置中可设置：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `diff_mode` | bool | `true` | 启用编辑模式：AI 编辑文件/配置时需提供原始文本和替换文本 |
| `diff_match_threshold` | int (滑块 50-100) | `100` | Diff 匹配阈值百分比，100 = 必须完全匹配 |

## 项目结构

```
astrbot_plugin_skills_mcp_manager/
├── main.py                          # 插件入口 (Star 类)
├── metadata.yaml                    # 插件元数据
├── _conf_schema.json                # 插件配置 Schema
├── CHANGELOG.md                     # 更新日志
├── README.md
├── tools/
│   ├── __init__.py                  # 工具导出
│   ├── skill_tools.py               # 9 个 Skills 管理 FunctionTool
│   └── mcp_tools.py                 # 7 个 MCP 管理 FunctionTool
└── skills/
    └── skills-mcp-manager/
        └── SKILL.md                 # 内置 AI 指令手册
```

## 支持

- [AstrBot 插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
- [Issues](https://github.com/piexian/astrbot_plugin_skills_mcp_manager/issues)

## 🔗 相关链接

- [AstrBot](https://docs.astrbot.app/)

## 许可

AGPL-3.0 License

<div align="center">

**如果这个插件对你有帮助，请给个 ⭐ Star 支持一下！**

</div>
