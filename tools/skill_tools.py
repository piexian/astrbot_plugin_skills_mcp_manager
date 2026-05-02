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


def _detect_runtime(context: ContextWrapper[AstrAgentContext]) -> str:
    """Detect current computer_use_runtime from config."""
    try:
        cfg = context.context.context.get_config(
            umo=context.context.event.unified_msg_origin
        )
        return cfg.get("provider_settings", {}).get("computer_use_runtime", "local")
    except Exception:
        return "local"


def _try_sync_to_sandboxes() -> None:
    """Best-effort sync skills to active sandboxes after install."""
    try:
        import asyncio

        from astrbot.core.computer.computer_client import (
            sync_skills_to_active_sandboxes,
        )

        asyncio.ensure_future(sync_skills_to_active_sandboxes())
    except Exception:
        pass


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
    description: str = "列出所有可用的 Skills。返回每个 Skill 的名称、描述、激活状态和来源信息。无需管理员权限。"
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
            result = [
                {
                    "name": s.name,
                    "description": s.description or "",
                    "active": s.active,
                    "source_type": s.source_type,
                    "local_exists": s.local_exists,
                }
                for s in skills
            ]
            return _ok(data={"skills": result})
        except Exception as e:
            logger.error(f"list_skills failed: {e}")
            return _err("列出 Skills 失败，请稍后重试。")


# ---------------------------------------------------------------------------
# EnableSkillTool
# ---------------------------------------------------------------------------


@dataclass
class EnableSkillTool(FunctionTool):
    """Enable a specified Skill."""

    name: str = "enable_skill"
    description: str = "启用指定的 Skill。需要管理员权限。"
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
        try:
            mgr = _get_skill_manager()
            mgr.set_skill_active(skill_name, True)
            return _ok(message=f"已启用 Skill: {skill_name}。{_REFRESH_HINT}")
        except Exception as e:
            logger.error(f"enable_skill failed: {e}")
            return _err("启用 Skill 失败，请检查 Skill 名称是否正确。")


# ---------------------------------------------------------------------------
# DisableSkillTool
# ---------------------------------------------------------------------------


@dataclass
class DisableSkillTool(FunctionTool):
    """Disable a specified Skill."""

    name: str = "disable_skill"
    description: str = (
        "禁用指定的 Skill。需要管理员权限。禁用后该 Skill 的指令将不会被加载。"
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
        try:
            mgr = _get_skill_manager()
            mgr.set_skill_active(skill_name, False)
            return _ok(message=f"已禁用 Skill: {skill_name}。{_REFRESH_HINT}")
        except Exception as e:
            logger.error(f"disable_skill failed: {e}")
            return _err("禁用 Skill 失败，请检查 Skill 名称是否正确。")


# ---------------------------------------------------------------------------
# DeleteSkillTool
# ---------------------------------------------------------------------------


@dataclass
class DeleteSkillTool(FunctionTool):
    """Delete a specified Skill (irreversible)."""

    name: str = "delete_skill"
    description: str = (
        "删除指定的 Skill。需要管理员权限。此操作不可逆，将删除 Skill 的所有文件。"
        "调用前务必确认用户意图。"
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
        try:
            mgr = _get_skill_manager()
            mgr.delete_skill(skill_name)
            return _ok(message=f"已删除 Skill: {skill_name}。{_REFRESH_HINT}")
        except Exception as e:
            logger.error(f"delete_skill failed: {e}")
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
    )
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
            return err
        if not zip_path:
            return _err("zip_path 不能为空。")

        tmp_file: str | None = None
        try:
            actual_zip_path, tmp_file = await _resolve_zip_path(zip_path, context)
            mgr = _get_skill_manager()
            install_kwargs: dict[str, Any] = {"overwrite": False}
            if skill_name_hint:
                install_kwargs["skill_name_hint"] = skill_name_hint
            try:
                skill_name = mgr.install_skill_from_zip(
                    actual_zip_path, **install_kwargs
                )
            except TypeError:
                # Backward compatibility: older SkillManager without overwrite/name hints
                legacy_kwargs = {
                    key: value
                    for key, value in install_kwargs.items()
                    if key != "overwrite"
                }
                try:
                    skill_name = mgr.install_skill_from_zip(
                        actual_zip_path, **legacy_kwargs
                    )
                except TypeError:
                    skill_name = mgr.install_skill_from_zip(actual_zip_path)
            _try_sync_to_sandboxes()
            return _ok(
                data={"skill_name": skill_name},
                message=f"Skill 安装成功: {skill_name}。{_REFRESH_HINT}",
            )
        except SkillZipPathError as e:
            logger.error(f"install_skill failed: {e}")
            return _err(str(e))
        except FileExistsError:
            return _err(
                "同名 Skill 已存在，请先删除或使用 update_skill_from_zip 更新。"
            )
        except Exception as e:
            logger.exception(f"install_skill failed: {e}")
            return _unknown_err("安装 Skill 失败", e)
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
    )
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
            return err
        if not confirm:
            return _err("请将 confirm 参数设为 true 以确认覆盖更新操作。")
        if err := _validate_skill_name(skill_name):
            return err
        if not zip_path:
            return _err("zip_path 不能为空。")

        tmp_file: str | None = None
        try:
            actual_zip_path, tmp_file = await _resolve_zip_path(zip_path, context)
            mgr = _get_skill_manager()
            skills_root = Path(mgr.skills_root)
            skill_dir = skills_root / skill_name
            if not skill_dir.exists():
                return _err(
                    f"Skill 不存在: {skill_name}。请使用 install_skill 安装新 Skill。"
                )

            # Pre-validate: check ZIP skill name matches target before overwriting
            import zipfile

            with zipfile.ZipFile(actual_zip_path) as zf:
                members = [
                    n.replace("\\", "/")
                    for n in zf.namelist()
                    if n and not n.endswith("/")
                ]
                if members:
                    top_dirs = {
                        Path(n).parts[0]
                        for n in members
                        if n.strip() and Path(n).parts[0] not in ("__MACOSX",)
                    }
                    if len(top_dirs) == 1:
                        zip_skill_name = next(iter(top_dirs))
                        if zip_skill_name != skill_name:
                            return _err(
                                f"ZIP 内 Skill 名 '{zip_skill_name}' 与目标 "
                                f"'{skill_name}' 不一致，请检查 ZIP 文件。"
                            )
                    else:
                        # Files are in the ZIP root (no single top-level dir)
                        # or multiple top-level dirs exist — ambiguous structure
                        return _err(
                            f"ZIP 文件结构不明确：期望包含单一顶层目录 "
                            f"'{skill_name}'，但发现 {len(top_dirs)} 个"
                            f"顶层条目 ({', '.join(sorted(top_dirs)[:5])})。"
                            f"请将 Skill 文件放在以 Skill 名命名的目录中再打包。"
                        )

            # Use install_skill_from_zip with overwrite=True
            installed_name = mgr.install_skill_from_zip(actual_zip_path, overwrite=True)

            # Post-install verification: ensure installed name matches target
            if installed_name != skill_name:
                # Rollback: remove the incorrectly installed skill
                try:
                    installed_dir = skills_root / installed_name
                    if installed_dir.exists():
                        import shutil

                        shutil.rmtree(installed_dir)
                except Exception as rollback_err:
                    logger.error(
                        f"update_skill_from_zip rollback failed for "
                        f"'{installed_name}': {rollback_err}"
                    )
                return _err(
                    f"ZIP 实际安装到 '{installed_name}'，与目标 "
                    f"'{skill_name}' 不一致，已回滚。请检查 ZIP 文件结构。"
                )

            _try_sync_to_sandboxes()
            return _ok(
                data={"skill_name": installed_name},
                message=f"已从 ZIP 更新 Skill: {installed_name}。{_REFRESH_HINT}",
            )
        except SkillZipPathError as e:
            logger.error(f"update_skill_from_zip failed: {e}")
            return _err(str(e))
        except Exception as e:
            logger.exception(f"update_skill_from_zip failed: {e}")
            return _unknown_err("从 ZIP 更新 Skill 失败", e)
        finally:
            if tmp_file and os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except OSError:
                    pass
