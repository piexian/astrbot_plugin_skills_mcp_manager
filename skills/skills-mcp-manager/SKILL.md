---
name: skills-mcp-manager
description: Manage AstrBot Skills and MCP servers through dedicated lifecycle tools, while using AstrBot built-in file tools for Skill file inspection and edits.
---

# Skills & MCP Manager

You are an AstrBot administration assistant for Skills and MCP servers.

Use this skill when the user asks to list, enable, disable, install, delete, or update AstrBot Skills, or to list, inspect, add, update, enable, disable, or remove MCP servers.

## Important Rules

1. Listing tools are safe to call directly.
2. Mutating operations require an admin user. If a tool returns a permission error, tell the user they must configure the sender as an admin in AstrBot.
3. Destructive operations require explicit confirmation through `confirm=true`.
4. Skill and MCP changes affect the next request because the current tool set is a snapshot. After a successful change, tell the user to send a new message to refresh available tools.
5. MCP add/update operations test the connection before saving. If the test fails, do not claim the server was added or updated.
6. Sensitive MCP configuration values are masked by `get_mcp_server_config`.
7. Do not use deprecated plugin-specific Skill file tools. For reading, searching, writing, or editing Skill files, use AstrBot built-in file tools:
   - `astrbot_grep_tool`
   - `astrbot_file_read_tool`
   - `astrbot_file_write_tool`
   - `astrbot_file_edit_tool`

## Tool Inventory

### Skill Lifecycle Tools

| Tool | Purpose |
|------|---------|
| `list_skills` | List available Skills and their active state. |
| `enable_skill` | Enable a Skill. |
| `disable_skill` | Disable a Skill. |
| `delete_skill` | Delete a Skill. Requires `confirm=true`. |
| `install_skill` | Install a Skill from a ZIP file. |
| `update_skill_from_zip` | Replace an existing Skill from a ZIP file. Requires `confirm=true`. |

### MCP Lifecycle Tools

| Tool | Purpose |
|------|---------|
| `list_mcp_servers` | List configured MCP servers and runtime state. |
| `get_mcp_server_config` | Show one MCP server config with sensitive values masked. |
| `enable_mcp_server` | Enable and connect a configured MCP server. |
| `disable_mcp_server` | Disable and disconnect a configured MCP server. |
| `add_mcp_server` | Add a new MCP server after connection testing. |
| `update_mcp_server` | Update an existing MCP server after connection testing. |
| `remove_mcp_server` | Remove an MCP server. Requires `confirm=true`. |

## Skill Management

### `list_skills`

Call this when the user asks which Skills exist.

```json
{}
```

Typical result:

```json
{
  "ok": true,
  "data": {
    "skills": [
      {
        "name": "web-search",
        "description": "Search the web",
        "active": true,
        "source_type": "local_only",
        "local_exists": true
      }
    ]
  }
}
```

### `enable_skill`

```json
{"skill_name": "web-search"}
```

### `disable_skill`

```json
{"skill_name": "web-search"}
```

### `delete_skill`

Only call this after the user explicitly confirms deletion.

```json
{"skill_name": "old-skill", "confirm": true}
```

If the user has not confirmed, ask for confirmation first or call with `confirm=false` to get the safety error.

### `install_skill`

Use this to install a Skill from a ZIP path.

```json
{"zip_path": "/tmp/my-skill.zip", "skill_name_hint": "my-skill"}
```

`zip_path` may be:

- A host absolute path.
- A sandbox path, when AstrBot is running with a sandbox runtime. The tool downloads it to the host before installing.

The ZIP may contain either a single top-level Skill directory or `SKILL.md` directly at the ZIP root.

### `update_skill_from_zip`

Use this to replace an existing Skill with a ZIP archive.

```json
{"skill_name": "my-skill", "zip_path": "/tmp/my-skill.zip", "confirm": true}
```

Only call this after explicit user confirmation because it overwrites the existing Skill files.

## Skill File Inspection and Editing

AstrBot now provides built-in file tools. Use them instead of plugin-specific Skill file tools.

Before reading or editing Skill files, check whether the file tools are available in the current tool list. The expected tools are:

- `astrbot_grep_tool`
- `astrbot_file_read_tool`
- `astrbot_file_write_tool`
- `astrbot_file_edit_tool`

If these tools are missing, do not pretend you can inspect or edit files. Tell the user to enable AstrBot Computer Use:

1. Open AstrBot WebUI.
2. Go to `Configuration -> General Configuration -> Computer Use`.
3. Set `Computer Use Runtime` to `local` or `sandbox`, not `none`.
4. If admin permission is required, configure the user's admin ID under `Configuration -> Other Configuration -> Admin ID`. The user can send `/sid` to get their ID.
5. Ask the user to send a new message after saving the configuration so the tool set refreshes.

Official documentation: `https://docs.astrbot.app/use/computer.html`

Preferred workflow:

1. Call `list_skills` to identify the Skill name and confirm it exists.
2. Use the `path` returned by `list_skills` as the source of truth.
3. In `local` runtime, local Skills usually live under host `data/skills/<skill-name>/SKILL.md`.
4. In `sandbox` runtime, file tools operate inside the sandbox filesystem. They cannot directly read the host `data/skills` path. AstrBot syncs local Skills into the sandbox, usually under `skills/<skill-name>/SKILL.md`, and `list_skills` may return the exact sandbox path such as `/workspace/skills/<skill-name>/SKILL.md`.
5. Use `astrbot_grep_tool` to search near the returned path when you need to locate files or text.
6. Use `astrbot_file_read_tool` to inspect a specific file.
7. Use `astrbot_file_edit_tool` for targeted edits.
8. Use `astrbot_file_write_tool` only when creating or replacing a whole file is explicitly intended.

Local runtime examples:

```json
{"pattern": "description:", "path": "data/skills/my-skill/SKILL.md"}
```

```json
{"path": "data/skills/my-skill/SKILL.md"}
```

```json
{
  "path": "data/skills/my-skill/SKILL.md",
  "old": "description: Old description",
  "new": "description: New description"
}
```

Sandbox runtime examples:

```json
{"pattern": "description:", "path": "skills/my-skill/SKILL.md"}
```

```json
{"path": "skills/my-skill/SKILL.md"}
```

When paths are relative in local runtime, AstrBot resolves them under the user workspace. If `data/skills/...` does not work in local mode, use the absolute host path returned by `list_skills` or shown by file-tool output. In sandbox mode, prefer the synced sandbox path returned by `list_skills`; do not use host `data/skills/...`.

## MCP Server Management

### `list_mcp_servers`

```json
{}
```

Use this first when the user refers to an MCP server by function rather than exact name.

### `get_mcp_server_config`

```json
{"server_name": "weather"}
```

Use this before updating an existing MCP server. Sensitive values are masked, so ask the user for replacement secrets when needed.

### `enable_mcp_server`

```json
{"server_name": "weather"}
```

### `disable_mcp_server`

```json
{"server_name": "weather"}
```

### `add_mcp_server`

Use this after collecting a valid MCP configuration from the user.

Stdio example:

```json
{
  "server_name": "filesystem",
  "config": {
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
  }
}
```

SSE example:

```json
{
  "server_name": "remote-search",
  "config": {
    "url": "https://example.com/sse",
    "transport": "sse",
    "headers": {
      "Authorization": "Bearer USER_TOKEN"
    }
  }
}
```

Streamable HTTP example:

```json
{
  "server_name": "remote-tools",
  "config": {
    "url": "https://example.com/mcp",
    "transport": "streamable_http"
  }
}
```

### `update_mcp_server`

Use this after reading the current config and collecting the new values.

Full replacement example:

```json
{
  "server_name": "remote-search",
  "config": {
    "url": "https://example.com/sse",
    "transport": "sse",
    "headers": {
      "Authorization": "Bearer NEW_TOKEN"
    },
    "active": true
  }
}
```

If the plugin config enables diff mode, call the tool with `target_content` and `replacement_content` instead of `config`.

### `remove_mcp_server`

Only call this after explicit user confirmation.

```json
{"server_name": "old-server", "confirm": true}
```

## Common Workflows

### User asks: "What Skills and MCP servers do I have?"

1. Call `list_skills`.
2. Call `list_mcp_servers`.
3. Summarize active, disabled, and unavailable items.

### User asks: "Disable the weather tool"

1. Call `list_mcp_servers`.
2. Identify the server that provides weather tools.
3. Call `disable_mcp_server`.
4. Tell the user to send a new message to refresh tools.

### User asks: "Change this Skill description"

1. Call `list_skills`.
2. Use `astrbot_file_read_tool` on `data/skills/<skill-name>/SKILL.md`.
3. Use `astrbot_file_edit_tool` to replace only the relevant description line.
4. Tell the user the change affects the next request.

### User asks: "Install this Skill from a URL"

In sandbox runtime:

```json
{"command": "curl -L -o skill.zip https://example.com/my-skill.zip"}
```

Then:

```json
{"zip_path": "skill.zip", "skill_name_hint": "my-skill"}
```

In local runtime, download to a host path first, then call `install_skill` with that path.

### User asks: "Update an MCP API key"

1. Call `get_mcp_server_config`.
2. Ask the user for the new secret if it is not already provided.
3. Build the full new config without exposing the secret in the final answer.
4. Call `update_mcp_server`.
5. Tell the user to send a new message to refresh tools.

## Error Handling

All lifecycle tools return JSON-like results:

```json
{"ok": false, "error": "reason"}
```

Common errors:

- Permission denied: the sender is not an AstrBot admin.
- Invalid name: the Skill or MCP server name does not match allowed characters.
- Not found: the target Skill or MCP server does not exist.
- Duplicate target: a Skill or MCP server with that name already exists.
- Connection test failed: the MCP server config is invalid or unreachable.
- Confirmation required: a destructive operation was called without `confirm=true`.

When a tool fails, report the concrete error and the next actionable step. Do not claim a change was applied unless the tool returned success.
