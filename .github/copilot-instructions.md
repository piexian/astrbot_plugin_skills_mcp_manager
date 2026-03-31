# Copilot Instructions — Skills & MCP 管理器

## 项目概述

AstrBot 插件（`star.Star` 子类），为 LLM 提供 16 个 FunctionTool 和 `/skill`、`/mcp` 两组用户指令，覆盖 Skills + MCP 服务器全生命周期管理。

- **运行环境**: Python ≥ 3.10, AstrBot ≥ 4.13.2
- **许可**: AGPL-3.0
- **无构建步骤**——AstrBot 直接加载 `.py` 文件

## 项目结构

```
main.py                   # 插件入口 (Main(star.Star))；命令组 + 工具注册 + 内置 Skill 安装
tools/
  __init__.py             # 导出全部 16 个 FunctionTool
  skill_tools.py          # 9 个 Skills 管理工具 (@dataclass + FunctionTool)
  mcp_tools.py            # 7 个 MCP 管理工具 (@dataclass + FunctionTool)
skills/
  skills-mcp-manager/
    SKILL.md              # 内置 AI 指令手册（自动安装到 AstrBot skills 目录）
metadata.yaml             # 插件元数据（name/version/astrbot_version）
_conf_schema.json         # 插件配置 schema（diff_mode, diff_match_threshold）
```

## 架构要点

### FunctionTool 模式

每个工具是一个 `@dataclass` 类，继承 `FunctionTool`，必须包含：
- `name` / `description`（中文）/ `parameters`（JSON Schema）
- `async def call(self, context, **kwargs) -> str`：返回 `_ok(data, message)` 或 `_err(error)` 的 JSON 字符串

```python
@dataclass
class MyTool(FunctionTool):
    name: str = "my_tool"
    description: str = "中文描述"
    parameters: dict = field(default_factory=lambda: {...})

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs: Any) -> str:
        if err := _ensure_admin(context): return err
        ...
        return _ok(data={...}, message="成功")
```

### 命令组模式

```python
@filter.command_group("skill")
def skill_group(self): ...

@filter.permission_type(filter.PermissionType.ADMIN)
@skill_group.command("on")
async def skill_on(self, event: AstrMessageEvent, name: str = ""): ...
```

交互式多消息流程使用 `@session_waiter(timeout=N)` + `SessionController`。

### Diff 编辑模式

`UpdateSkillFileTool` / `UpdateMcpServerTool` 支持双模式：
- **diff_mode=True**（默认）：`target_content` + `replacement_content`，`difflib.SequenceMatcher` 模糊匹配
- **diff_mode=False**：完整内容覆盖

阈值 `diff_match_threshold`（50-100）由插件配置控制。

### 快照机制

工具集在单次请求内不可变——启用/禁用/安装的变更在**下一次请求**生效。成功消息中务必携带 `_REFRESH_HINT`。

## 编码约定

| 项目 | 规范 |
|------|------|
| 类型标注 | 必须，使用 Python 3.10+ 语法（`str \| None`，非 `Optional`） |
| 用户文案 | 中文 |
| 错误处理 | 早期验证 → `_err()` 返回；`try-except` 包裹 + `logger.error()` |
| 赋值表达式 | 大量使用 `:=`（walrus operator），如 `if err := _ensure_admin(ctx): return err` |
| 返回格式 | 统一 JSON：`{"ok": true/false, "data": ..., "message": ..., "error": ...}` |
| 字符串 | f-string；`json.dumps(ensure_ascii=False)` |
| 状态标签 | `[运行中]` `[已启用]` `[已禁用]` `[成功]` `[失败]` `[警告]`——纯文本，不使用 emoji |

## 安全规则

修改代码时必须保持以下安全机制：

1. **管理员验证**：所有写操作及文件读取操作在入口处调用 `_ensure_admin(context)`
2. **名称白名单**：`^[A-Za-z0-9._-]+$`（main.py / mcp_tools.py）或 `^[\w.-]+$`（skill_tools.py）——所有接受名称的命令/工具必须校验
3. **路径遍历防护**：`Path.resolve()` + `relative_to()` 校验
4. **Zip Slip 防护**：解压前验证所有 ZIP 成员路径
5. **敏感信息脱敏**：输出配置时通过 `_mask_sensitive` / `_mask_sensitive_config` 隐藏密钥（递归处理 dict + list）
6. **破坏性操作二次确认**：`confirm=true` 参数
7. **失败回滚**：MCP 添加/更新/启用失败后自动回滚配置并恢复旧运行态
8. **配置持久化校验**：`save_mcp_config()` 返回值必须检查，失败时提示用户
9. **错误信息脱敏**：异常消息不直接透传给用户，使用通用错误描述，内部细节仅记录到日志
10. **原子更新**：ZIP 更新前备份现有文件，复制失败时自动回滚
11. **Diff 性能防护**：`target_content` 设有长度上限（50000 字符），防止大文本拖慢

## 已知注意事项

- **名称验证不一致**：`main.py` 用 `[A-Za-z0-9._-]`，`skill_tools.py` 用 `[\w.-]`（含 Unicode）。修改时注意保持一致。
- **sandbox 模式**：`InstallSkillTool` 通过 `_resolve_zip_path()` 支持沙箱（下载远程 ZIP → 临时文件 → 安装 → 清理）。
- **MCP 传输协议**：支持 `stdio`、`sse`、`streamable_http` 三种类型。
- **ZIP 预校验**：`UpdateSkillFromZipTool` 在覆盖前先验证 ZIP 内 Skill 名与目标一致，避免误写误删。

## 相关文档

- [README.md](../README.md)——功能概览、安装方式、命令速查
- [CHANGELOG.md](../CHANGELOG.md)——版本历史
- [skills/skills-mcp-manager/SKILL.md](../skills/skills-mcp-manager/SKILL.md)——内置 AI 指令手册（工具参数参考）
- [AstrBot 插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
