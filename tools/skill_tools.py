"""Skills management FunctionTool classes for LLM tool-calling."""

from __future__ import annotations

import json
import os
import re
import shlex
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from astrbot.api import FunctionTool, logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.skills.skill_manager import SkillManager
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

from ..services.scan_review import ScanReview
from ..services.skill_install import SkillInstallService, failed_result

_SKILL_NAME_RE = re.compile(r"^[\w.-]+$")

_REFRESH_HINT = (
    "提示: 本次会话工具集为快照，变更需下一次请求生效；请发送一条新消息刷新。"
)


class SkillZipPathError(RuntimeError):
    """Raised when a ZIP path cannot be resolved from local or sandbox storage."""


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


def _validate_skill_name(name: str) -> str | None:
    """Return an error JSON string if skill name is invalid, else None."""
    if not name or not _SKILL_NAME_RE.fullmatch(name):
        return json.dumps(
            {
                "ok": False,
                "error": f"无效的 Skill 名称: '{name}'。只允许字母、数字、点、横线、下划线。",
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


def _unknown_err(prefix: str, exc: Exception) -> str:
    detail = str(exc).strip() or exc.__class__.__name__
    return _err(f"{prefix}: {detail}")


def _get_skill_manager() -> SkillManager:
    return SkillManager()


def _extract_session_scope(context: Any) -> tuple[str, Any]:
    inner = getattr(context, "context", None)
    event = getattr(inner, "event", None)
    umo = str(getattr(event, "unified_msg_origin", "") or "")
    star_ctx = getattr(inner, "context", None)
    return umo, star_ctx


def _detect_runtime(context: ContextWrapper[AstrAgentContext]) -> str:
    """Detect current computer_use_runtime from config."""
    try:
        cfg = context.context.context.get_config(
            umo=context.context.event.unified_msg_origin
        )
        return cfg.get("provider_settings", {}).get("computer_use_runtime", "local")
    except Exception:
        logger.debug("Could not read runtime configuration; using local", exc_info=True)
        return "local"


async def resolve_session_workspace_root(
    umo: str, star_context: Any = None
) -> Path | None:
    """Resolve the current session's workspace directory."""
    if not umo:
        return None
    try:
        from astrbot.core.db import BaseDatabase
        from astrbot.core.workspace import (
            default_workspace_root,
            resolve_workspace_root_for_umo,
        )

        db = getattr(star_context, "_db", None)
        if isinstance(db, BaseDatabase):
            return await resolve_workspace_root_for_umo(umo, db)
        return default_workspace_root(umo)
    except Exception:
        pass
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_workspaces_path

        normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", umo.strip()) or "unknown"
        return (Path(get_astrbot_workspaces_path()) / normalized).resolve(strict=False)
    except Exception:
        return None


async def list_session_workspace_skills(
    mgr: SkillManager, umo: str, star_context: Any = None
) -> list[Any]:
    """List request-scoped workspace skills for the current session."""
    list_ws = getattr(mgr, "list_workspace_skills", None)
    if not callable(list_ws) or not umo:
        return []
    workspace_root = await resolve_session_workspace_root(umo, star_context)
    if workspace_root is None:
        return []
    try:
        return list(list_ws(workspace_root))
    except Exception:
        logger.debug("Could not list workspace skills", exc_info=True)
        return []


def is_global_skill_present(mgr: SkillManager, name: str) -> bool:
    """Return True if the skill exists in local skills_root, plugin skills, or sandbox cache."""
    skills_root = getattr(mgr, "skills_root", "")
    if skills_root and (Path(skills_root) / name).is_dir():
        return True
    if getattr(mgr, "is_plugin_skill", lambda _: False)(name):
        return True
    if getattr(mgr, "is_sandbox_only_skill", lambda _: False)(name):
        return True
    return False


def is_workspace_only_skill(
    mgr: SkillManager, name: str, workspace_skills: list[Any]
) -> bool:
    """Return True when the skill exists only in the current session workspace."""
    return not is_global_skill_present(mgr, name) and any(
        getattr(s, "name", "") == name for s in workspace_skills
    )


def get_plugin_skill_origins(mgr: SkillManager) -> dict[str, tuple[str, bool]]:
    """Return {skill_name: (plugin_name, preset)} for all plugin-provided skills."""
    iter_dirs = getattr(mgr, "_iter_plugin_skill_dirs", None)
    if not callable(iter_dirs):
        return {}
    try:
        return {
            str(skill_name): (str(plugin_name), bool(preset))
            for skill_name, plugin_name, _skill_dir, preset in iter_dirs()
        }
    except Exception:
        return {}


def format_skill_permission_error(
    mgr: SkillManager, name: str, action: str, exc: Exception | None = None
) -> str:
    """Return an explicit Chinese explanation for read-only/protected skill errors."""
    msg = str(exc or "").lower()
    if getattr(mgr, "is_plugin_skill", lambda _: False)(name) or "plugin" in msg:
        origins = get_plugin_skill_origins(mgr)
        plugin_name = origins.get(name, ("", False))[0]
        plugin_part = (
            f"（所属插件: {plugin_name}，只读）" if plugin_name else "（只读）"
        )
        return f"Skill '{name}' 为插件级 Skill{plugin_part}，不支持{action}；请管理对应插件。"
    if getattr(mgr, "is_sandbox_only_skill", lambda _: False)(name) or "sandbox" in msg:
        return f"Skill '{name}' 是沙盒预置 Skill，不支持通过本地管理{action}。"
    return f"Skill '{name}' 为只读或受保护状态，不支持{action}。"


def _try_sync_to_sandboxes() -> None:
    """Best-effort sync skills to active sandboxes after install."""
    try:
        import asyncio

        from astrbot.core.computer.computer_client import (
            sync_skills_to_active_sandboxes,
        )

        asyncio.ensure_future(sync_skills_to_active_sandboxes())
    except Exception:
        logger.warning("Could not schedule Skill synchronization", exc_info=True)


async def _resolve_zip_path(
    zip_path: str, context: ContextWrapper[AstrAgentContext]
) -> tuple[str, str | None]:
    """Resolve a ZIP path, downloading from sandbox if needed.

    Returns:
        (actual_local_path, tmp_file_or_none)
        If tmp_file is not None, caller must clean it up.
    """
    if os.path.exists(zip_path):
        return zip_path, None

    # Path doesn't exist locally — try downloading from sandbox
    from astrbot.core.computer.computer_client import get_booter

    session_id = context.context.event.unified_msg_origin
    booter = await get_booter(context.context.context, session_id)
    tmp_dir = get_astrbot_temp_path()
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_file = os.path.join(tmp_dir, f"skill_install_{uuid.uuid4().hex[:8]}.zip")
    try:
        await _download_sandbox_zip(booter, zip_path, tmp_file)
    except SkillZipPathError:
        raise
    except Exception as exc:
        raise SkillZipPathError(
            f"无法从沙盒下载 ZIP 文件: {zip_path}。"
            "请确认文件存在，并且路径位于当前沙盒工作区内。"
        ) from exc
    return tmp_file, tmp_file


async def _download_sandbox_zip(booter: Any, remote_path: str, local_path: str) -> None:
    try:
        await booter.download_file(remote_path, local_path)
        return
    except Exception as exc:
        if not _is_workspace_path_error(exc):
            raise

        try:
            fixed_path = await _prepare_workspace_download_path(booter, remote_path)
        except Exception as fix_exc:
            raise SkillZipPathError(
                f"沙盒 ZIP 路径不可访问: {remote_path}。"
                "旧版 Shipyard 需要 workspace 内真实路径，但自动解析失败。"
            ) from fix_exc
        if not fixed_path or fixed_path == remote_path:
            raise
        try:
            await booter.download_file(fixed_path, local_path)
        except Exception as retry_exc:
            raise SkillZipPathError(
                f"沙盒 ZIP 文件下载失败: {remote_path}。"
                "请将 ZIP 放在当前沙盒工作区内后再安装。"
            ) from retry_exc


def _is_workspace_path_error(exc: Exception) -> bool:
    message = str(exc)
    return (
        "path must be within workspace" in message
        or "Access denied" in message
        or "Ship returned 403" in message
    )


async def _prepare_workspace_download_path(booter: Any, remote_path: str) -> str:
    quoted_path = shlex.quote(remote_path)
    copy_name = f"skill_install_{uuid.uuid4().hex[:8]}.zip"
    quoted_copy_name = shlex.quote(copy_name)
    command = (
        "set -e; "
        f"resolved=$(realpath {quoted_path}); "
        'case "$resolved" in '
        "*/workspace/*) printf '%s\\n' \"$resolved\" ;; "
        f'*) cp "$resolved" {quoted_copy_name}; realpath {quoted_copy_name} ;; '
        "esac"
    )
    result = await booter.shell.exec(command)
    stdout = str(result.get("stdout") or result.get("output") or "").strip()
    stderr = str(result.get("stderr") or result.get("error") or "").strip()
    success = result.get("success")
    if success is False or not stdout:
        raise RuntimeError(
            "无法解析沙盒 ZIP 路径" + (f": {stderr}" if stderr else f": {remote_path}")
        )
    return stdout.splitlines()[-1].strip()


# ---------------------------------------------------------------------------
# ListSkillsTool
# ---------------------------------------------------------------------------


@dataclass
class ListSkillsTool(FunctionTool):
    """List all available Skills."""

    name: str = "list_skills"
    description: str = (
        "列出所有可用的 Skills，分别返回全局 Skills（本地安装、沙盒预置）、"
        "插件级 Skills（由插件提供，全局可用但只读）与当前会话工作区 Skills"
        "（仅当前会话 local 运行时生效，不可全局加载）。无需管理员权限。"
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
            mgr = _get_skill_manager()
            runtime = _detect_runtime(context)
            skills = mgr.list_skills(runtime=runtime)
            umo, star_ctx = _extract_session_scope(context)
            workspace_skills = await list_session_workspace_skills(mgr, umo, star_ctx)
            plugin_origins = get_plugin_skill_origins(mgr)
            all_global_names = {s.name for s in skills}
            plugin_names = {
                s.name for s in skills if getattr(s, "source_type", "") == "plugin"
            } | set(plugin_origins)

            all_skills_result: list[dict[str, Any]] = []
            global_result: list[dict[str, Any]] = []
            plugin_result: list[dict[str, Any]] = []

            for s in skills:
                source_type = getattr(s, "source_type", "local_only")
                if source_type == "plugin":
                    origin_name, origin_preset = plugin_origins.get(s.name, ("", False))
                    plugin_name = (
                        getattr(s, "plugin_name", "")
                        or getattr(s, "source_label", "")
                        or origin_name
                    )
                    preset = bool(getattr(s, "preset", False) or origin_preset)
                    item = {
                        "name": s.name,
                        "description": s.description or "",
                        "active": s.active,
                        "level": "plugin",
                        "scope": "plugin",
                        "global_loadable": True,
                        "source_type": source_type,
                        "local_exists": s.local_exists,
                        "readonly": True,
                        "plugin_name": plugin_name,
                        "preset": preset,
                    }
                    plugin_result.append(item)
                else:
                    level = "sandbox" if source_type == "sandbox_only" else "global"
                    overridden_plugin = plugin_origins.get(s.name, ("", False))[0]
                    item = {
                        "name": s.name,
                        "description": s.description or "",
                        "active": s.active,
                        "level": level,
                        "scope": "global",
                        "global_loadable": True,
                        "source_type": source_type,
                        "local_exists": s.local_exists,
                        "readonly": bool(
                            getattr(s, "readonly", False)
                            or source_type == "sandbox_only"
                        ),
                        **(
                            {"overrides_plugin": overridden_plugin}
                            if overridden_plugin
                            else {}
                        ),
                    }
                    global_result.append(item)
                all_skills_result.append(item)

            workspace_result = [
                {
                    "name": s.name,
                    "description": s.description or "",
                    "active": runtime == "local",
                    "level": "workspace",
                    "scope": "workspace",
                    "global_loadable": False,
                    "source_type": getattr(s, "source_type", "workspace"),
                    "local_exists": getattr(s, "local_exists", True),
                    "readonly": True,
                    "overrides_global": s.name in all_global_names,
                    "overrides_plugin": s.name in plugin_names,
                }
                for s in workspace_skills
            ]
            return _ok(
                data={
                    "skills": all_skills_result,
                    "global_skills": global_result,
                    "plugin_skills": plugin_result,
                    "workspace_skills": workspace_result,
                    "runtime": runtime,
                    "workspace_note": (
                        "工作区 Skills 仅在当前会话（local 运行时）按请求注入，不能全局加载，"
                        "也不支持通过全局配置启用、禁用或删除。"
                    ),
                }
            )
        except Exception:
            logger.exception("list_skills failed")
            return _err("列出 Skills 失败，请稍后重试。")


# ---------------------------------------------------------------------------
# EnableSkillTool
# ---------------------------------------------------------------------------


@dataclass
class EnableSkillTool(FunctionTool):
    """Enable a specified Skill."""

    name: str = "enable_skill"
    description: str = "启用指定的全局 Skill。需要管理员权限。"
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "要启用的 Skill 名称",
                }
            },
            "required": ["skill_name"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        skill_name: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        if err := _ensure_admin(context):
            return err
        if err := _validate_skill_name(skill_name):
            return err
        mgr = _get_skill_manager()
        try:
            umo, star_ctx = _extract_session_scope(context)
            workspace_skills = await list_session_workspace_skills(mgr, umo, star_ctx)
            if is_workspace_only_skill(mgr, skill_name, workspace_skills):
                return _err(
                    f"Skill '{skill_name}' 是当前会话工作区 Skill（仅在当前会话 local 运行时按请求加载，不能全局加载），不支持全局启用或禁用。"
                )
            mgr.set_skill_active(skill_name, True)
            return _ok(message=f"已启用 Skill: {skill_name}。{_REFRESH_HINT}")
        except PermissionError as exc:
            return _err(format_skill_permission_error(mgr, skill_name, "启用", exc))
        except Exception:
            logger.exception("enable_skill failed")
            return _err("启用 Skill 失败，请检查 Skill 名称是否正确。")


# ---------------------------------------------------------------------------
# DisableSkillTool
# ---------------------------------------------------------------------------


@dataclass
class DisableSkillTool(FunctionTool):
    """Disable a specified Skill."""

    name: str = "disable_skill"
    description: str = (
        "禁用指定的全局 Skill。需要管理员权限。禁用后该 Skill 的指令将不会被加载。"
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "要禁用的 Skill 名称",
                }
            },
            "required": ["skill_name"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        skill_name: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        if err := _ensure_admin(context):
            return err
        if err := _validate_skill_name(skill_name):
            return err
        mgr = _get_skill_manager()
        try:
            umo, star_ctx = _extract_session_scope(context)
            workspace_skills = await list_session_workspace_skills(mgr, umo, star_ctx)
            if is_workspace_only_skill(mgr, skill_name, workspace_skills):
                return _err(
                    f"Skill '{skill_name}' 是当前会话工作区 Skill（仅在当前会话 local 运行时按请求加载，不能全局加载），不支持全局启用或禁用。"
                )
            mgr.set_skill_active(skill_name, False)
            return _ok(message=f"已禁用 Skill: {skill_name}。{_REFRESH_HINT}")
        except PermissionError as exc:
            return _err(format_skill_permission_error(mgr, skill_name, "禁用", exc))
        except Exception:
            logger.exception("disable_skill failed")
            return _err("禁用 Skill 失败，请检查 Skill 名称是否正确。")


# ---------------------------------------------------------------------------
# DeleteSkillTool
# ---------------------------------------------------------------------------


@dataclass
class DeleteSkillTool(FunctionTool):
    """Delete a specified Skill (irreversible)."""

    name: str = "delete_skill"
    description: str = (
        "删除指定的本地全局 Skill。需要管理员权限。此操作不可逆，将删除 Skill 的所有文件。"
        "插件内置、沙盒预置与工作区 Skill 不支持通过此工具删除。调用前务必确认用户意图。"
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "要删除的 Skill 名称",
                },
                "confirm": {
                    "type": "boolean",
                    "description": "确认删除，必须为 true 才会执行",
                },
            },
            "required": ["skill_name", "confirm"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        skill_name: str = "",
        confirm: bool = False,
        **kwargs: Any,
    ) -> ToolExecResult:
        if err := _ensure_admin(context):
            return err
        if not confirm:
            return _err("请将 confirm 参数设为 true 以确认删除操作。")
        if err := _validate_skill_name(skill_name):
            return err
        mgr = _get_skill_manager()
        try:
            umo, star_ctx = _extract_session_scope(context)
            workspace_skills = await list_session_workspace_skills(mgr, umo, star_ctx)
            if is_workspace_only_skill(mgr, skill_name, workspace_skills):
                return _err(
                    f"Skill '{skill_name}' 是当前会话工作区 Skill，不支持通过全局 Skill 管理删除。"
                )
            mgr.delete_skill(skill_name)
            return _ok(message=f"已删除 Skill: {skill_name}。{_REFRESH_HINT}")
        except PermissionError as exc:
            return _err(format_skill_permission_error(mgr, skill_name, "删除", exc))
        except Exception:
            logger.exception("delete_skill failed")
            return _err("删除 Skill 失败，请检查 Skill 名称是否正确。")


# ---------------------------------------------------------------------------
# InstallSkillTool
# ---------------------------------------------------------------------------


@dataclass
class InstallSkillTool(FunctionTool):
    """Install a Skill from a ZIP file."""

    name: str = "install_skill"
    description: str = (
        "从 ZIP 文件安装 Skill。需要管理员权限。"
        "ZIP 可包含单个顶层文件夹或在根目录直接包含 SKILL.md。"
        "路径可以是本地绝对路径或沙盒路径（自动下载）。"
        "安装前进行静态扫描，所有结果均包含 scan 报告；阻断时不会安装。"
        "请按返回的 review_language 向用户说明审查结果。"
    )
    installer: SkillInstallService = field(default_factory=SkillInstallService)
    reviewer: ScanReview | None = None
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "zip_path": {
                    "type": "string",
                    "description": "ZIP 文件路径（本地绝对路径或沙盒路径）",
                },
                "skill_name_hint": {
                    "type": "string",
                    "description": "可选，指定安装后的 Skill 名称（覆盖 ZIP 内目录名）",
                },
            },
            "required": ["zip_path"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        zip_path: str = "",
        skill_name_hint: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        if err := _ensure_admin(context):
            return json.dumps(
                failed_result(json.loads(err)["error"], code="permission_denied"),
                ensure_ascii=False,
            )
        if not zip_path:
            return json.dumps(failed_result("zip_path 不能为空。"), ensure_ascii=False)

        tmp_file: str | None = None
        try:
            actual_zip_path, tmp_file = await _resolve_zip_path(zip_path, context)
            result = await self.installer.run(
                _get_skill_manager(),
                actual_zip_path,
                skill_name_hint=skill_name_hint,
                archive_name=Path(zip_path).name,
            )
            if result["ok"]:
                _try_sync_to_sandboxes()
            if self.reviewer is not None:
                result = await self.reviewer.review(result)
            return json.dumps(result, ensure_ascii=False)
        except SkillZipPathError:
            logger.exception("install_skill failed")
            return json.dumps(
                failed_result("无法取得 Skill ZIP 文件。"), ensure_ascii=False
            )
        except Exception:
            logger.exception("install_skill failed")
            return json.dumps(
                failed_result("安装准备失败，请查看日志。"), ensure_ascii=False
            )
        finally:
            if tmp_file and os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# UpdateSkillFromZipTool
# ---------------------------------------------------------------------------


@dataclass
class UpdateSkillFromZipTool(FunctionTool):
    """Update an existing Skill from a ZIP file."""

    name: str = "update_skill_from_zip"
    description: str = (
        "从 ZIP 文件更新已存在的 Skill。需要管理员权限。会覆盖 Skill 的所有文件。"
        "更新前进行静态扫描，所有结果均包含 scan 报告；阻断时保留旧版本。"
        "请按返回的 review_language 向用户说明审查结果。"
    )
    installer: SkillInstallService = field(default_factory=SkillInstallService)
    reviewer: ScanReview | None = None
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "要更新的 Skill 名称",
                },
                "zip_path": {
                    "type": "string",
                    "description": "ZIP 文件的本地绝对路径",
                },
                "confirm": {
                    "type": "boolean",
                    "description": "确认覆盖更新，必须为 true 才会执行",
                },
            },
            "required": ["skill_name", "zip_path", "confirm"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        skill_name: str = "",
        zip_path: str = "",
        confirm: bool = False,
        **kwargs: Any,
    ) -> ToolExecResult:
        if err := _ensure_admin(context):
            return json.dumps(
                failed_result(json.loads(err)["error"], code="permission_denied"),
                ensure_ascii=False,
            )
        if not confirm:
            return json.dumps(
                failed_result(
                    "请将 confirm 参数设为 true 以确认覆盖更新操作。",
                    code="confirmation_required",
                ),
                ensure_ascii=False,
            )
        if err := _validate_skill_name(skill_name):
            return json.dumps(
                failed_result(json.loads(err)["error"], code="invalid_name"),
                ensure_ascii=False,
            )
        if not zip_path:
            return json.dumps(failed_result("zip_path 不能为空。"), ensure_ascii=False)

        tmp_file: str | None = None
        try:
            mgr = _get_skill_manager()
            umo, star_ctx = _extract_session_scope(context)
            workspace_skills = await list_session_workspace_skills(mgr, umo, star_ctx)
            if is_workspace_only_skill(mgr, skill_name, workspace_skills):
                return json.dumps(
                    failed_result(
                        f"Skill '{skill_name}' 是当前会话工作区 Skill，不支持通过全局 Skill 管理更新。",
                        code="readonly_skill",
                    ),
                    ensure_ascii=False,
                )
            actual_zip_path, tmp_file = await _resolve_zip_path(zip_path, context)
            result = await self.installer.run(
                mgr,
                actual_zip_path,
                operation="replace",
                skill_name=skill_name,
            )
            if result["ok"]:
                _try_sync_to_sandboxes()
            if self.reviewer is not None:
                result = await self.reviewer.review(result)
            return json.dumps(result, ensure_ascii=False)
        except SkillZipPathError:
            logger.exception("update_skill_from_zip failed")
            return json.dumps(
                failed_result("无法取得 Skill ZIP 文件。"), ensure_ascii=False
            )
        except Exception:
            logger.exception("update_skill_from_zip failed")
            return json.dumps(
                failed_result("更新准备失败，请查看日志。"), ensure_ascii=False
            )
        finally:
            if tmp_file and os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except OSError:
                    pass
