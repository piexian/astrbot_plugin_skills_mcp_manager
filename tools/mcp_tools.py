"""MCP server management FunctionTool classes for LLM tool-calling."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import FunctionTool, logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.provider.func_tool_manager import FunctionToolManager

from .utils import MAX_DIFF_TARGET_LEN, mask_sensitive

_REFRESH_HINT = (
    "提示: 本次会话工具集为快照，变更需下一次请求生效；请发送一条新消息刷新。"
)
_MCP_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _ensure_admin(context: ContextWrapper[AstrAgentContext]) -> str | None:
    """Return an error JSON string if the user is not admin, else None."""
    if context.context.event.role != "admin":
        sender = context.context.event.get_sender_id()
        return json.dumps(
            {
                "ok": False,
                "error": f"权限不足。用户 {sender} 不是管理员。请在管理面板配置管理员。",
            },
            ensure_ascii=False,
        )
    return None


def _validate_mcp_name(name: str) -> str | None:
    """Return an error JSON string if MCP server name is invalid, else None."""
    if not name or not _MCP_NAME_RE.fullmatch(name):
        return json.dumps(
            {
                "ok": False,
                "error": f"无效的 MCP 服务器名称: '{name}'。只允许字母、数字、点、横线、下划线。",
            },
            ensure_ascii=False,
        )
    return None


def _ok(data: Any = None, message: str = "") -> str:
    result: dict[str, Any] = {"ok": True}
    if data is not None:
        result["data"] = data
    if message:
        result["message"] = message
    return json.dumps(result, ensure_ascii=False)


def _err(error: str) -> str:
    return json.dumps({"ok": False, "error": error}, ensure_ascii=False)


def _get_tool_mgr(
    context: ContextWrapper[AstrAgentContext],
) -> FunctionToolManager:
    return context.context.context.get_llm_tool_manager()


def _rollback_mcp_server(tool_mgr: FunctionToolManager, name: str) -> bool:
    """Remove a server entry from config as rollback."""
    try:
        config = tool_mgr.load_mcp_config()
        if name in config.get("mcpServers", {}):
            config["mcpServers"].pop(name)
            return tool_mgr.save_mcp_config(config)
        return True
    except Exception as e:
        logger.error(f"MCP rollback failed for {name}: {e}")
        return False


# ---------------------------------------------------------------------------
# ListMcpServersTool
# ---------------------------------------------------------------------------


@dataclass
class ListMcpServersTool(FunctionTool):
    """List all configured MCP servers."""

    name: str = "list_mcp_servers"
    description: str = (
        "列出所有已配置的 MCP 服务器。返回每个 MCP 服务器的名称、激活状态和运行状态。"
        "无需管理员权限。"
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "required": [],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> ToolExecResult:
        try:
            tool_mgr = _get_tool_mgr(context)
            config = tool_mgr.load_mcp_config()
            runtime = tool_mgr.mcp_server_runtime_view

            servers = []
            for sname, cfg in config.get("mcpServers", {}).items():
                if not isinstance(cfg, dict):
                    continue
                active = cfg.get("active", True)
                is_running = sname in runtime

                server_info: dict[str, Any] = {
                    "name": sname,
                    "active": active,
                    "status": (
                        "running"
                        if is_running
                        else ("enabled" if active else "disabled")
                    ),
                }

                # Add tool names if running
                if is_running:
                    rt = runtime[sname]
                    mcp_client = rt.client
                    server_info["tools"] = [t.name for t in mcp_client.tools]
                else:
                    server_info["tools"] = []

                # Add transport info (non-sensitive)
                if "command" in cfg:
                    server_info["transport"] = "stdio"
                elif "url" in cfg:
                    server_info["transport"] = cfg.get("transport", "sse")

                servers.append(server_info)

            return _ok(data={"servers": servers})
        except Exception as e:
            logger.error(f"list_mcp_servers failed: {e}")
            return _err("列出 MCP 服务器失败，请稍后重试。")


# ---------------------------------------------------------------------------
# GetMcpServerConfigTool
# ---------------------------------------------------------------------------


@dataclass
class GetMcpServerConfigTool(FunctionTool):
    """Get detailed config of a specified MCP server."""

    name: str = "get_mcp_server_config"
    description: str = (
        "获取指定 MCP 服务器的详细配置信息。需要管理员权限。敏感信息会被脱敏。"
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "server_name": {
                    "type": "string",
                    "description": "MCP 服务器名称",
                }
            },
            "required": ["server_name"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        server_name: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        if err := _ensure_admin(context):
            return err
        if err := _validate_mcp_name(server_name):
            return err
        try:
            tool_mgr = _get_tool_mgr(context)
            config = tool_mgr.load_mcp_config()
            servers = config.get("mcpServers", {})

            if server_name not in servers:
                return _err(f"MCP 服务器不存在: {server_name}")

            server_config = servers[server_name]
            runtime = tool_mgr.mcp_server_runtime_view
            is_running = server_name in runtime
            active = server_config.get("active", True)

            result: dict[str, Any] = {
                "name": server_name,
                "active": active,
                "status": (
                    "running" if is_running else ("enabled" if active else "disabled")
                ),
                "config": mask_sensitive(server_config),
            }

            if is_running:
                rt = runtime[server_name]
                result["tools"] = [t.name for t in rt.client.tools]

            return _ok(data=result)
        except Exception as e:
            logger.error(f"get_mcp_server_config failed: {e}")
            return _err("获取配置失败，请稍后重试。")


# ---------------------------------------------------------------------------
# EnableMcpServerTool
# ---------------------------------------------------------------------------


@dataclass
class EnableMcpServerTool(FunctionTool):
    """Enable a specified MCP server."""

    name: str = "enable_mcp_server"
    description: str = (
        "启用指定的 MCP 服务器。需要管理员权限。启用后将连接该 MCP 服务器并加载其工具。"
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "server_name": {
                    "type": "string",
                    "description": "要启用的 MCP 服务器名称",
                }
            },
            "required": ["server_name"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        server_name: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        if err := _ensure_admin(context):
            return err
        if err := _validate_mcp_name(server_name):
            return err
        try:
            tool_mgr = _get_tool_mgr(context)
            config = tool_mgr.load_mcp_config()
            servers = config.get("mcpServers", {})

            if server_name not in servers:
                return _err(f"MCP 服务器不存在: {server_name}")

            server_config = servers[server_name]

            # Connect first, then persist on success
            await tool_mgr.enable_mcp_server(server_name, server_config, timeout=30)

            server_config["active"] = True
            config["mcpServers"][server_name] = server_config
            if not tool_mgr.save_mcp_config(config):
                logger.error(f"enable_mcp_server: save_mcp_config failed for {server_name}")
                return _err("已启用但保存配置失败，重启后需要手动重新启用。")

            return _ok(message=f"已启用 MCP 服务器: {server_name}。{_REFRESH_HINT}")
        except TimeoutError:
            return _err(
                f"启用 MCP 服务器 {server_name} 超时。请检查服务器配置和可用性。"
            )
        except Exception as e:
            logger.error(f"enable_mcp_server failed: {e}")
            return _err("启用失败。请检查服务器配置和可用性。")


# ---------------------------------------------------------------------------
# DisableMcpServerTool
# ---------------------------------------------------------------------------


@dataclass
class DisableMcpServerTool(FunctionTool):
    """Disable a specified MCP server."""

    name: str = "disable_mcp_server"
    description: str = (
        "禁用指定的 MCP 服务器。需要管理员权限。禁用后将断开连接并卸载其工具。"
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "server_name": {
                    "type": "string",
                    "description": "要禁用的 MCP 服务器名称",
                }
            },
            "required": ["server_name"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        server_name: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        if err := _ensure_admin(context):
            return err
        if err := _validate_mcp_name(server_name):
            return err
        try:
            tool_mgr = _get_tool_mgr(context)
            config = tool_mgr.load_mcp_config()
            servers = config.get("mcpServers", {})

            if server_name not in servers:
                return _err(f"MCP 服务器不存在: {server_name}")

            # Stop runtime first, then persist on success
            if server_name in tool_mgr.mcp_server_runtime_view:
                await tool_mgr.disable_mcp_server(server_name, timeout=10)

            # Update config after successful disable
            servers[server_name]["active"] = False
            if not tool_mgr.save_mcp_config(config):
                logger.error(f"disable_mcp_server: save_mcp_config failed for {server_name}")
                return _err("已禁用但保存配置失败，重启后需要手动重新禁用。")

            return _ok(message=f"已禁用 MCP 服务器: {server_name}。{_REFRESH_HINT}")
        except TimeoutError:
            return _err(f"禁用 MCP 服务器 {server_name} 超时。")
        except Exception as e:
            logger.error(f"disable_mcp_server failed: {e}")
            return _err("禁用失败。请稍后重试。")


# ---------------------------------------------------------------------------
# AddMcpServerTool
# ---------------------------------------------------------------------------


@dataclass
class AddMcpServerTool(FunctionTool):
    """Add a new MCP server configuration."""

    name: str = "add_mcp_server"
    description: str = (
        "添加新的 MCP 服务器配置。需要管理员权限。"
        "支持三种传输方式: stdio（本地进程）、sse（Server-Sent Events）、streamable_http（HTTP 流式）。"
        "添加前会先测试连接，失败则不保存。"
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "server_name": {
                    "type": "string",
                    "description": "MCP 服务器名称",
                },
                "config": {
                    "type": "object",
                    "description": (
                        "MCP 服务器配置。stdio 方式需要 command 和 args 字段，"
                        "sse/streamable_http 方式需要 url 和 transport 字段。"
                        "可选 env（环境变量）和 headers（请求头）。"
                    ),
                },
            },
            "required": ["server_name", "config"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        server_name: str = "",
        config: dict | None = None,
        **kwargs: Any,
    ) -> ToolExecResult:
        if err := _ensure_admin(context):
            return err
        if err := _validate_mcp_name(server_name):
            return err
        if not config or not isinstance(config, dict):
            return _err("config 不能为空，必须是一个 JSON 对象。")

        try:
            tool_mgr = _get_tool_mgr(context)
            mcp_config = tool_mgr.load_mcp_config()

            if server_name in mcp_config.get("mcpServers", {}):
                return _err(
                    f"MCP 服务器 '{server_name}' 已存在。请使用 update_mcp_server 更新。"
                )

            # Test connection first
            try:
                await tool_mgr.test_mcp_server_connection(config)
            except Exception as e:
                return _err(f"连接测试失败: {e}")

            # Save config
            config["active"] = True
            mcp_config.setdefault("mcpServers", {})[server_name] = config
            if not tool_mgr.save_mcp_config(mcp_config):
                logger.error(f"add_mcp_server: save_mcp_config failed for {server_name}")
                return _err("保存配置失败。")

            # Enable the server
            try:
                await tool_mgr.enable_mcp_server(server_name, config, timeout=30)
            except TimeoutError:
                _rollback_mcp_server(tool_mgr, server_name)
                return _err(f"启用 MCP 服务器 {server_name} 超时，已回滚配置。")
            except Exception as e:
                _rollback_mcp_server(tool_mgr, server_name)
                return _err(f"启用 MCP 服务器 {server_name} 失败: {e}。已回滚配置。")

            return _ok(message=f"MCP 服务器 '{server_name}' 添加成功！{_REFRESH_HINT}")
        except Exception as e:
            logger.error(f"add_mcp_server failed: {e}")
            return _err("添加失败。请检查配置格式和服务器可用性。")


# ---------------------------------------------------------------------------
# UpdateMcpServerTool
# ---------------------------------------------------------------------------

_MCP_FULL_REPLACE_DESC = (
    "更新已存在的 MCP 服务器配置（全量替换模式）。需要管理员权限。"
    "会先测试新配置，成功后禁用旧配置，保存并启用新配置。"
)
_MCP_FULL_REPLACE_PARAMS: dict = {
    "type": "object",
    "properties": {
        "server_name": {
            "type": "string",
            "description": "要更新的 MCP 服务器名称",
        },
        "config": {
            "type": "object",
            "description": "新的 MCP 服务器配置",
        },
    },
    "required": ["server_name", "config"],
}

_MCP_DIFF_DESC = (
    "更新已存在的 MCP 服务器配置（Diff 模式）。需要管理员权限。"
    "请提供要替换的原始配置文本片段和替换后的文本。"
    "系统会在当前 JSON 配置中查找原始文本并验证匹配度，匹配成功后执行替换。"
    "替换后会自动测试新配置连接。"
    f"注意: target_content 最大长度为 {MAX_DIFF_TARGET_LEN} 字符。"
    "如果需要修改的内容较多，请分多次调用，每次只替换一个片段。"
)
_MCP_DIFF_PARAMS: dict = {
    "type": "object",
    "properties": {
        "server_name": {
            "type": "string",
            "description": "要更新的 MCP 服务器名称",
        },
        "target_content": {
            "type": "string",
            "description": "要替换的原始配置文本片段（需与当前 JSON 配置中的内容匹配）",
        },
        "replacement_content": {
            "type": "string",
            "description": "替换后的新文本",
        },
    },
    "required": ["server_name", "target_content", "replacement_content"],
}


@dataclass
class UpdateMcpServerTool(FunctionTool):
    """Update an existing MCP server configuration."""

    name: str = "update_mcp_server"
    description: str = _MCP_FULL_REPLACE_DESC
    parameters: dict = field(default_factory=lambda: _MCP_FULL_REPLACE_PARAMS.copy())

    # Diff mode settings (injected at init time from plugin config)
    diff_mode: bool = False
    diff_match_threshold: int = 100

    def __post_init__(self) -> None:
        if self.diff_mode:
            self.description = _MCP_DIFF_DESC
            self.parameters = _MCP_DIFF_PARAMS.copy()

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        server_name: str = "",
        config: dict | None = None,
        target_content: str = "",
        replacement_content: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        if err := _ensure_admin(context):
            return err
        if err := _validate_mcp_name(server_name):
            return err

        try:
            tool_mgr = _get_tool_mgr(context)
            mcp_config = tool_mgr.load_mcp_config()
            servers = mcp_config.get("mcpServers", {})

            if server_name not in servers:
                return _err(f"MCP 服务器不存在: {server_name}")

            old_config = servers[server_name]

            if self.diff_mode:
                new_config, match_info = self._resolve_diff(
                    old_config, target_content, replacement_content
                )
                if isinstance(new_config, str):
                    # Error message returned
                    return _err(new_config)
                config = new_config
            else:
                if not config or not isinstance(config, dict):
                    return _err("config 不能为空，必须是一个 JSON 对象。")

            # Preserve active state if not specified
            if "active" not in config:
                config["active"] = old_config.get("active", True)

            # Test connection with new config
            try:
                await tool_mgr.test_mcp_server_connection(config)
            except Exception as e:
                return _err(f"新配置连接测试失败: {e}")

            # Disable old server if running
            was_active = old_config.get("active", True)
            was_running = server_name in tool_mgr.mcp_server_runtime_view
            if was_active and was_running:
                try:
                    await tool_mgr.disable_mcp_server(server_name, timeout=10)
                except Exception:
                    pass  # Best-effort disable

            # Save new config
            mcp_config["mcpServers"][server_name] = config
            if not tool_mgr.save_mcp_config(mcp_config):
                # Rollback: restore old config
                mcp_config["mcpServers"][server_name] = old_config
                if not tool_mgr.save_mcp_config(mcp_config):
                    logger.error(
                        f"update_mcp_server: rollback save also failed for {server_name}"
                    )
                    return _err("保存配置失败，且回滚也未成功，请手动检查配置文件。")
                if was_running:
                    try:
                        await tool_mgr.enable_mcp_server(
                            server_name, old_config, timeout=30
                        )
                    except Exception:
                        pass
                return _err("保存配置失败，已回滚旧配置。")

            # Re-enable with new config if should be active
            if config.get("active", True):
                try:
                    await tool_mgr.enable_mcp_server(server_name, config, timeout=30)
                except Exception:
                    # Rollback: restore old config and re-enable
                    mcp_config["mcpServers"][server_name] = old_config
                    if not tool_mgr.save_mcp_config(mcp_config):
                        logger.error(
                            f"update_mcp_server: rollback save failed for {server_name}"
                        )
                        return _err(
                            "启用新配置失败，且回滚也未成功，请手动检查配置文件。"
                        )
                    if was_running:
                        try:
                            await tool_mgr.enable_mcp_server(
                                server_name, old_config, timeout=30
                            )
                        except Exception:
                            pass
                    return _err("启用新配置失败，已回滚旧配置。请检查配置。")

            msg = f"MCP 服务器 '{server_name}' 更新成功！{_REFRESH_HINT}"
            if self.diff_mode and match_info:
                return _ok(data=match_info, message=msg)
            return _ok(message=msg)
        except Exception as e:
            logger.error(f"update_mcp_server failed: {e}")
            return _err("更新失败。请检查配置格式和服务器可用性。")

    def _resolve_diff(
        self,
        old_config: dict,
        target_content: str,
        replacement_content: str,
    ) -> tuple[dict | str, dict | None]:
        """Apply diff to the JSON string of old_config. Returns (new_config, match_info) or (error_msg, None)."""
        import difflib

        if not target_content:
            return "target_content 不能为空。", None

        # Input length limit to prevent performance issues
        if len(target_content) > MAX_DIFF_TARGET_LEN:
            return f"target_content 超出长度限制 ({MAX_DIFF_TARGET_LEN} 字符)。", None

        # Serialize old config to formatted JSON for diff
        config_text = json.dumps(old_config, ensure_ascii=False, indent=2)
        threshold = self.diff_match_threshold / 100.0

        # Try exact match first (fast path)
        if target_content in config_text:
            new_text = config_text.replace(target_content, replacement_content, 1)
            try:
                new_config = json.loads(new_text)
            except json.JSONDecodeError as e:
                return f"替换后 JSON 格式无效: {e}", None
            return new_config, {"match_ratio": 100}

        # Fuzzy match using SequenceMatcher
        best_ratio = 0.0
        best_start = 0
        best_end = 0
        target_len = len(target_content)

        sm = difflib.SequenceMatcher(None, config_text, target_content, autojunk=False)
        blocks = sm.get_matching_blocks()

        for block in blocks:
            if block.size == 0:
                continue
            anchor_start = block.a
            for offset in range(
                -target_len, target_len // 2 + 1, max(1, target_len // 20)
            ):
                start = max(0, anchor_start + offset)
                end = min(len(config_text), start + target_len)
                if end - start < target_len // 2:
                    continue
                candidate = config_text[start:end]
                ratio = difflib.SequenceMatcher(
                    None, candidate, target_content, autojunk=False
                ).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_start = start
                    best_end = end

        match_pct = int(best_ratio * 100)

        if best_ratio < threshold:
            return (
                f"匹配失败: 最佳匹配度 {match_pct}%，"
                f"要求 {self.diff_match_threshold}%。"
                f"请检查 target_content 是否与当前配置内容一致。"
            ), None

        # Apply replacement and parse
        new_text = (
            config_text[:best_start] + replacement_content + config_text[best_end:]
        )
        try:
            new_config = json.loads(new_text)
        except json.JSONDecodeError as e:
            return f"替换后 JSON 格式无效: {e}", None

        return new_config, {"match_ratio": match_pct}


# ---------------------------------------------------------------------------
# RemoveMcpServerTool
# ---------------------------------------------------------------------------


@dataclass
class RemoveMcpServerTool(FunctionTool):
    """Remove an MCP server configuration."""

    name: str = "remove_mcp_server"
    description: str = (
        "移除 MCP 服务器配置。需要管理员权限。将删除配置并断开连接。此操作不可逆。"
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "server_name": {
                    "type": "string",
                    "description": "要移除的 MCP 服务器名称",
                },
                "confirm": {
                    "type": "boolean",
                    "description": "确认删除，必须为 true 才会执行",
                },
            },
            "required": ["server_name", "confirm"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        server_name: str = "",
        confirm: bool = False,
        **kwargs: Any,
    ) -> ToolExecResult:
        if err := _ensure_admin(context):
            return err
        if not confirm:
            return _err("请将 confirm 参数设为 true 以确认删除操作。")
        if err := _validate_mcp_name(server_name):
            return err

        try:
            tool_mgr = _get_tool_mgr(context)
            config = tool_mgr.load_mcp_config()

            if server_name not in config.get("mcpServers", {}):
                return _err(f"MCP 服务器不存在: {server_name}")

            # Disable first if running
            if server_name in tool_mgr.mcp_server_runtime_view:
                try:
                    await tool_mgr.disable_mcp_server(server_name, timeout=10)
                except TimeoutError:
                    return _err(f"禁用 MCP 服务器 {server_name} 超时，无法安全删除。")

            # Remove from config
            del config["mcpServers"][server_name]
            if not tool_mgr.save_mcp_config(config):
                logger.error(f"remove_mcp_server: save_mcp_config failed for {server_name}")
                return _err("保存配置失败。")

            return _ok(message=f"已移除 MCP 服务器: {server_name}。{_REFRESH_HINT}")
        except Exception as e:
            logger.error(f"remove_mcp_server failed: {e}")
            return _err("移除失败。请稍后重试。")
