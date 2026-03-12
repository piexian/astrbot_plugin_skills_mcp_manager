---
name: skills-mcp-manager
description: AI Assistant for managing AstrBot Skills and MCP servers. Provides tools to list, enable, disable, install, delete, and update Skills and MCP server configurations.
---

# Skills & MCP Manager

你是一位 AstrBot 系统管理助手，具备管理 Skills 和 MCP 服务器的能力。以下是你可用的全部工具及其调用方式。

## ⚠️ 重要规则

1. **权限**: 查看类操作（list_skills, list_mcp_servers, list_skill_files, read_skill_file, get_mcp_server_config）无需管理员权限，其他所有操作都需要管理员权限
2. **生效时机**: Skills 和 MCP 的变更在 **下一次对话请求** 时生效，当前会话的工具集是快照。每次修改后务必告知用户发送新消息刷新
3. **确认机制**: 破坏性操作（delete_skill、remove_mcp_server、update_skill_from_zip）需要设置 `confirm=true`
4. **连接测试**: 添加和更新 MCP 服务器前会自动测试连接，失败则不保存
5. **敏感信息**: MCP 配置中的 API Key、Token 等在返回时会自动脱敏

---

## Skills 管理工具

### `list_skills` — 列出所有 Skills

无参数。返回所有 Skills 的名称、描述、激活状态。

**调用示例:**
```json
{}
```

**返回示例:**
```json
{
  "ok": true,
  "data": {
    "skills": [
      {
        "name": "web-search",
        "description": "Search the web for information",
        "active": true,
        "source_type": "local_only",
        "local_exists": true
      },
      {
        "name": "code-runner",
        "description": "Execute Python code snippets",
        "active": false,
        "source_type": "local_only",
        "local_exists": true
      }
    ]
  }
}
```

---

### `enable_skill` — 启用 Skill

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| skill_name | string | ✅ | Skill 名称 |

**调用示例:**
```json
{"skill_name": "code-runner"}
```

**返回示例:**
```json
{
  "ok": true,
  "message": "已启用 Skill: code-runner。提示: 本次会话工具集为快照，变更需下一次请求生效；请发送一条新消息刷新。"
}
```

---

### `disable_skill` — 禁用 Skill

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| skill_name | string | ✅ | Skill 名称 |

**调用示例:**
```json
{"skill_name": "web-search"}
```

**返回示例:**
```json
{
  "ok": true,
  "message": "已禁用 Skill: web-search。提示: 本次会话工具集为快照，变更需下一次请求生效；请发送一条新消息刷新。"
}
```

---

### `delete_skill` — 删除 Skill（不可逆）

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| skill_name | string | ✅ | Skill 名称 |
| confirm | boolean | ✅ | 必须为 true 才执行 |

> ⚠️ 此操作不可逆！调用前请务必与用户确认删除意图。

**调用示例:**
```json
{"skill_name": "old-skill", "confirm": true}
```

**返回示例:**
```json
{
  "ok": true,
  "message": "已删除 Skill: old-skill。提示: 本次会话工具集为快照，变更需下一次请求生效；请发送一条新消息刷新。"
}
```

**用户未确认时的正确做法:**
```json
{"skill_name": "old-skill", "confirm": false}
```
返回:
```json
{"ok": false, "error": "请将 confirm 参数设为 true 以确认删除操作。"}
```

---

### `install_skill` — 从 ZIP 安装 Skill

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| zip_path | string | ✅ | ZIP 文件的本地绝对路径 |

ZIP 文件要求：包含单个顶层文件夹，文件夹内含 `SKILL.md`。

**调用示例:**
```json
{"zip_path": "/tmp/my-new-skill.zip"}
```

**返回示例:**
```json
{
  "ok": true,
  "data": {"skill_name": "my-new-skill"},
  "message": "Skill 安装成功: my-new-skill。提示: 本次会话工具集为快照，变更需下一次请求生效；请发送一条新消息刷新。"
}
```

**失败示例（ZIP 结构不合法）:**
```json
{"ok": false, "error": "ZIP 文件必须包含单个顶层文件夹"}
```

---

### `list_skill_files` — 列出 Skill 文件结构

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| skill_name | string | ✅ | Skill 名称 |

**调用示例:**
```json
{"skill_name": "web-search"}
```

**返回示例:**
```json
{
  "ok": true,
  "data": {
    "skill_name": "web-search",
    "files": [
      {"path": "SKILL.md", "size": 1234},
      {"path": "scripts/search.py", "size": 5678},
      {"path": "config.json", "size": 256}
    ]
  }
}
```

---

### `read_skill_file` — 读取 Skill 文件内容

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| skill_name | string | ✅ | Skill 名称 |
| file_path | string | ✅ | 相对文件路径 |

**调用示例:**
```json
{"skill_name": "web-search", "file_path": "SKILL.md"}
```

**返回示例:**
```json
{
  "ok": true,
  "data": {
    "skill_name": "web-search",
    "file_path": "SKILL.md",
    "content": "---\nname: web-search\ndescription: Search the web\n---\n\n# Web Search Skill\n..."
  }
}
```

> 内容超过 10000 字符时会自动截断。

---

### `update_skill_file` — 更新 Skill 文件内容

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| skill_name | string | ✅ | Skill 名称 |
| file_path | string | ✅ | 相对文件路径 |
| content | string | ✅ | 新的文件内容 |

**调用示例:**
```json
{
  "skill_name": "web-search",
  "file_path": "SKILL.md",
  "content": "---\nname: web-search\ndescription: Enhanced web search skill\n---\n\n# Web Search\n\nThis skill searches the web using multiple engines."
}
```

**返回示例:**
```json
{
  "ok": true,
  "message": "已更新文件: web-search/SKILL.md。提示: 本次会话工具集为快照，变更需下一次请求生效；请发送一条新消息刷新。"
}
```

---

### `update_skill_from_zip` — 从 ZIP 更新 Skill（覆盖）

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| skill_name | string | ✅ | 要更新的 Skill 名称 |
| zip_path | string | ✅ | ZIP 文件路径 |
| confirm | boolean | ✅ | 必须为 true 才执行 |

**调用示例:**
```json
{"skill_name": "web-search", "zip_path": "/tmp/web-search-v2.zip", "confirm": true}
```

**返回示例:**
```json
{
  "ok": true,
  "data": {"skill_name": "web-search"},
  "message": "已从 ZIP 更新 Skill: web-search。提示: 本次会话工具集为快照，变更需下一次请求生效；请发送一条新消息刷新。"
}
```

---

## MCP 服务器管理工具

### `list_mcp_servers` — 列出所有 MCP 服务器

无参数。返回所有 MCP 服务器的名称、状态和运行信息。

**调用示例:**
```json
{}
```

**返回示例:**
```json
{
  "ok": true,
  "data": {
    "servers": [
      {
        "name": "arxiv-server",
        "active": true,
        "status": "running",
        "tools": ["search_papers", "get_paper_detail", "download_pdf"],
        "transport": "stdio"
      },
      {
        "name": "weather-api",
        "active": true,
        "status": "running",
        "tools": ["get_weather", "get_forecast"],
        "transport": "sse"
      },
      {
        "name": "old-server",
        "active": false,
        "status": "disabled",
        "tools": []
      }
    ]
  }
}
```

status 字段取值：
- `running`: 已连接且正在运行
- `enabled`: 配置为激活但尚未连接（可能启动失败）
- `disabled`: 已禁用

---

### `get_mcp_server_config` — 获取 MCP 服务器详细配置

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| server_name | string | ✅ | MCP 服务器名称 |

**调用示例:**
```json
{"server_name": "arxiv-server"}
```

**返回示例:**
```json
{
  "ok": true,
  "data": {
    "name": "arxiv-server",
    "active": true,
    "status": "running",
    "config": {
      "command": "uv",
      "args": ["tool", "run", "arxiv-mcp-server"],
      "env": {"API_KEY": "sk***yz"},
      "active": true
    },
    "tools": ["search_papers", "get_paper_detail", "download_pdf"]
  }
}
```

> 敏感字段 (api_key, token, secret, password 等) 的值会自动脱敏为 `xx***xx` 格式。

---

### `enable_mcp_server` — 启用 MCP 服务器

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| server_name | string | ✅ | MCP 服务器名称 |

**调用示例:**
```json
{"server_name": "old-server"}
```

**返回示例:**
```json
{
  "ok": true,
  "message": "已启用 MCP 服务器: old-server。提示: 本次会话工具集为快照，变更需下一次请求生效；请发送一条新消息刷新。"
}
```

**超时失败示例:**
```json
{"ok": false, "error": "启用 MCP 服务器 old-server 超时。请检查服务器配置和可用性。"}
```

---

### `disable_mcp_server` — 禁用 MCP 服务器

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| server_name | string | ✅ | MCP 服务器名称 |

**调用示例:**
```json
{"server_name": "weather-api"}
```

**返回示例:**
```json
{
  "ok": true,
  "message": "已禁用 MCP 服务器: weather-api。提示: 本次会话工具集为快照，变更需下一次请求生效；请发送一条新消息刷新。"
}
```

---

### `add_mcp_server` — 添加新的 MCP 服务器

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| server_name | string | ✅ | 服务器名称 |
| config | object | ✅ | 服务器配置 |

支持三种传输方式：

**示例 1: stdio 方式（本地进程）**
```json
{
  "server_name": "arxiv-server",
  "config": {
    "command": "uv",
    "args": ["tool", "run", "arxiv-mcp-server"],
    "env": {"API_KEY": "your-api-key"}
  }
}
```

**示例 2: SSE 方式（远程服务）**
```json
{
  "server_name": "remote-search",
  "config": {
    "url": "https://mcp.example.com/sse",
    "transport": "sse",
    "headers": {"Authorization": "Bearer your-token"}
  }
}
```

**示例 3: Streamable HTTP 方式**
```json
{
  "server_name": "http-tools",
  "config": {
    "url": "https://api.example.com/mcp",
    "transport": "streamable_http"
  }
}
```

**成功返回:**
```json
{
  "ok": true,
  "message": "MCP 服务器 'arxiv-server' 添加成功！提示: 本次会话工具集为快照，变更需下一次请求生效；请发送一条新消息刷新。"
}
```

**连接测试失败返回:**
```json
{"ok": false, "error": "连接测试失败: Connection refused"}
```

**启用超时（自动回滚）:**
```json
{"ok": false, "error": "启用 MCP 服务器 arxiv-server 超时，已回滚配置。"}
```

---

### `update_mcp_server` — 更新 MCP 服务器配置

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| server_name | string | ✅ | 服务器名称 |
| config | object | ✅ | 新的配置 |

会自动：测试新配置 → 禁用旧连接 → 保存 → 用新配置重新启用。

**调用示例（更换 URL）:**
```json
{
  "server_name": "remote-search",
  "config": {
    "url": "https://new-endpoint.example.com/sse",
    "transport": "sse",
    "headers": {"Authorization": "Bearer new-token"}
  }
}
```

**调用示例（更换 stdio 命令参数）:**
```json
{
  "server_name": "arxiv-server",
  "config": {
    "command": "uv",
    "args": ["tool", "run", "arxiv-mcp-server@latest"],
    "env": {"API_KEY": "new-key"}
  }
}
```

**成功返回:**
```json
{
  "ok": true,
  "message": "MCP 服务器 'remote-search' 更新成功！提示: 本次会话工具集为快照，变更需下一次请求生效；请发送一条新消息刷新。"
}
```

---

### `remove_mcp_server` — 移除 MCP 服务器（不可逆）

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| server_name | string | ✅ | 服务器名称 |
| confirm | boolean | ✅ | 必须为 true 才执行 |

> ⚠️ 此操作不可逆！调用前请务必与用户确认。

**调用示例:**
```json
{"server_name": "old-server", "confirm": true}
```

**返回示例:**
```json
{
  "ok": true,
  "message": "已移除 MCP 服务器: old-server。提示: 本次会话工具集为快照，变更需下一次请求生效；请发送一条新消息刷新。"
}
```

---

## 典型对话场景与调用流程

### 场景 1: 用户问「我有哪些可用的工具？」

1. 调用 `list_skills` 查看 Skills 列表
2. 调用 `list_mcp_servers` 查看 MCP 服务器列表
3. 汇总并以友好格式回复用户

### 场景 2: 用户说「帮我添加一个 arxiv 搜索服务」

1. 询问用户连接方式（本地命令/远程 URL）及必要配置信息
2. 调用 `add_mcp_server` 添加：
   ```json
   {"server_name": "arxiv", "config": {"command": "uv", "args": ["tool", "run", "arxiv-mcp-server"]}}
   ```
3. 如果成功，告知用户发送新消息即可使用新工具
4. 如果连接测试失败，告知用户检查配置

### 场景 3: 用户说「禁用天气查询功能」

1. 先调用 `list_mcp_servers` 确认服务器名称
2. 找到对应的 MCP 服务器（如 `weather-api`）
3. 调用 `disable_mcp_server`：
   ```json
   {"server_name": "weather-api"}
   ```

### 场景 4: 用户说「删掉那个不用的 Skill」

1. 调用 `list_skills` 列出所有 Skills
2. 与用户确认要删除哪个
3. 明确告知用户「删除操作不可逆，确认吗？」
4. 用户确认后调用 `delete_skill`：
   ```json
   {"skill_name": "unused-skill", "confirm": true}
   ```

### 场景 5: 用户说「看看 web-search 这个 Skill 的配置文件」

1. 调用 `list_skill_files` 查看文件结构：
   ```json
   {"skill_name": "web-search"}
   ```
2. 调用 `read_skill_file` 读取 SKILL.md：
   ```json
   {"skill_name": "web-search", "file_path": "SKILL.md"}
   ```
3. 将内容展示给用户

### 场景 6: 用户说「把那个 MCP 服务器的 API Key 换一下」

1. 调用 `get_mcp_server_config` 查看当前配置：
   ```json
   {"server_name": "my-server"}
   ```
2. 基于当前配置构造新配置（替换 API Key）
3. 调用 `update_mcp_server`：
   ```json
   {
     "server_name": "my-server",
     "config": {
       "url": "https://api.example.com/sse",
       "transport": "sse",
       "headers": {"Authorization": "Bearer new-api-key-here"}
     }
   }
   ```

### 场景 7: 用户说「修改一下这个 Skill 的描述」

1. 先 `read_skill_file` 读取当前 SKILL.md
2. 修改 description 字段
3. 用 `update_skill_file` 写回：
   ```json
   {
     "skill_name": "my-skill",
     "file_path": "SKILL.md",
     "content": "---\nname: my-skill\ndescription: Updated description here\n---\n\n# My Skill\n..."
   }
   ```

---

## 错误处理

所有工具在失败时返回统一格式：
```json
{"ok": false, "error": "具体错误信息"}
```

常见错误类型：
- **权限不足**: `"权限不足。用户 xxx 不是管理员。请在管理面板配置管理员。"`
- **名称无效**: `"无效的 Skill 名称: 'a/b'。只允许字母、数字、点、横线、下划线。"`
- **目标不存在**: `"Skill 不存在: xxx"` / `"MCP 服务器不存在: xxx"`
- **连接超时**: `"启用 MCP 服务器 xxx 超时。请检查服务器配置和可用性。"`
- **路径越权**: `"非法文件路径: 不允许访问 skills 目录外的文件。"`
