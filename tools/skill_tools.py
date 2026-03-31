"""Skills management FunctionTool classes for LLM tool-calling."""

from __future__ import annotations

import json
import os
import re
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

from .utils import MAX_DIFF_TARGET_LEN

_SKILL_NAME_RE = re.compile(r"^[\w.-]+$")

_REFRESH_HINT = (
    "提示: 本次会话工具集为快照，变更需下一次请求生效；请发送一条新消息刷新。"
)


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
    await booter.download_file(zip_path, tmp_file)
    return tmp_file, tmp_file


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
            install_kwargs: dict[str, Any] = {}
            if skill_name_hint:
                install_kwargs["skill_name_hint"] = skill_name_hint
            try:
                skill_name = mgr.install_skill_from_zip(
                    actual_zip_path, **install_kwargs
                )
            except TypeError:
                # Backward compatibility: older SkillManager without skill_name_hint
                skill_name = mgr.install_skill_from_zip(actual_zip_path)
            _try_sync_to_sandboxes()
            return _ok(
                data={"skill_name": skill_name},
                message=f"Skill 安装成功: {skill_name}。{_REFRESH_HINT}",
            )
        except FileExistsError:
            return _err(
                "同名 Skill 已存在，请先删除或使用 update_skill_from_zip 更新。"
            )
        except Exception as e:
            logger.error(f"install_skill failed: {e}")
            return _err("安装 Skill 失败。请检查 ZIP 文件格式是否正确。")
        finally:
            if tmp_file and os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# ListSkillFilesTool
# ---------------------------------------------------------------------------


@dataclass
class ListSkillFilesTool(FunctionTool):
    """List the file structure of a specified Skill."""

    name: str = "list_skill_files"
    description: str = (
        "列出指定 Skill 的文件结构。需要管理员权限。返回 Skill 目录下的所有文件列表。"
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Skill 名称",
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
            skills_root = Path(mgr.skills_root)
            skill_dir = skills_root / skill_name
            if not skill_dir.exists():
                return _err(f"Skill 不存在: {skill_name}")

            files: list[dict[str, Any]] = []
            for root, _dirs, filenames in os.walk(skill_dir):
                for fname in filenames:
                    fpath = Path(root) / fname
                    rel = fpath.relative_to(skill_dir)
                    files.append(
                        {
                            "path": str(rel).replace("\\", "/"),
                            "size": fpath.stat().st_size,
                        }
                    )
            return _ok(data={"skill_name": skill_name, "files": files})
        except Exception as e:
            logger.error(f"list_skill_files failed: {e}")
            return _err("列出文件失败。请检查 Skill 名称是否正确。")


# ---------------------------------------------------------------------------
# ReadSkillFileTool
# ---------------------------------------------------------------------------


@dataclass
class ReadSkillFileTool(FunctionTool):
    """Read the content of a file in a specified Skill."""

    name: str = "read_skill_file"
    description: str = (
        "读取指定 Skill 中的文件内容。需要管理员权限。"
        "可用于读取 SKILL.md 或其他脚本文件。"
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Skill 名称",
                },
                "file_path": {
                    "type": "string",
                    "description": "相对文件路径（如 SKILL.md, scripts/run.sh）",
                },
            },
            "required": ["skill_name", "file_path"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        skill_name: str = "",
        file_path: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        if err := _ensure_admin(context):
            return err
        if err := _validate_skill_name(skill_name):
            return err
        if not file_path:
            return _err("file_path 不能为空。")

        try:
            mgr = _get_skill_manager()
            skills_root = Path(mgr.skills_root)
            skill_dir = (skills_root / skill_name).resolve()
            target = (skill_dir / file_path).resolve()

            # Security check: ensure path stays within the specific skill directory
            try:
                target.relative_to(skill_dir)
            except ValueError:
                return _err("非法文件路径: 不允许访问该 Skill 目录外的文件。")

            if not target.exists():
                return _err(f"文件不存在: {file_path}")
            if not target.is_file():
                return _err(f"'{file_path}' 不是文件。")

            content = target.read_text(encoding="utf-8")
            if len(content) > 10000:
                content = content[:10000] + "\n\n... (内容过长，已截断至 10000 字符)"
            return _ok(
                data={
                    "skill_name": skill_name,
                    "file_path": file_path,
                    "content": content,
                }
            )
        except Exception as e:
            logger.error(f"read_skill_file failed: {e}")
            return _err("读取文件失败。请检查文件路径是否正确。")


# ---------------------------------------------------------------------------
# UpdateSkillFileTool
# ---------------------------------------------------------------------------

_FULL_REPLACE_DESC = (
    "更新指定 Skill 中的文件内容（全文覆盖模式）。需要管理员权限。用于修改 SKILL.md 或脚本文件。"
    "传入完整的文件内容以覆盖目标文件。"
)
_FULL_REPLACE_PARAMS: dict = {
    "type": "object",
    "properties": {
        "skill_name": {
            "type": "string",
            "description": "Skill 名称",
        },
        "file_path": {
            "type": "string",
            "description": "相对文件路径（如 SKILL.md, scripts/run.sh）",
        },
        "content": {
            "type": "string",
            "description": "完整的文件内容",
        },
    },
    "required": ["skill_name", "file_path", "content"],
}

_DIFF_DESC = (
    "更新指定 Skill 中的文件内容（Diff 模式）。需要管理员权限。"
    "请提供要替换的原始文本片段和替换后的文本。"
    "系统会在文件中查找原始文本并验证匹配度，匹配成功后执行替换。"
    "文件必须已存在。"
    f"注意: target_content 最大长度为 {MAX_DIFF_TARGET_LEN} 字符。"
    "如果需要修改的内容较多，请分多次调用，每次只替换一个片段。"
)
_DIFF_PARAMS: dict = {
    "type": "object",
    "properties": {
        "skill_name": {
            "type": "string",
            "description": "Skill 名称",
        },
        "file_path": {
            "type": "string",
            "description": "相对文件路径（如 SKILL.md, scripts/run.sh）",
        },
        "target_content": {
            "type": "string",
            "description": "要替换的原始文本片段（需与文件中的内容匹配）",
        },
        "replacement_content": {
            "type": "string",
            "description": "替换后的新文本",
        },
    },
    "required": ["skill_name", "file_path", "target_content", "replacement_content"],
}


@dataclass
class UpdateSkillFileTool(FunctionTool):
    """Update the content of a file in a specified Skill."""

    name: str = "update_skill_file"
    description: str = _FULL_REPLACE_DESC
    parameters: dict = field(default_factory=lambda: _FULL_REPLACE_PARAMS.copy())

    # Diff mode settings (injected at init time from plugin config)
    diff_mode: bool = False
    diff_match_threshold: int = 100

    def __post_init__(self) -> None:
        if self.diff_mode:
            self.description = _DIFF_DESC
            self.parameters = _DIFF_PARAMS.copy()

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        skill_name: str = "",
        file_path: str = "",
        content: str = "",
        target_content: str = "",
        replacement_content: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        if err := _ensure_admin(context):
            return err
        if err := _validate_skill_name(skill_name):
            return err
        if not file_path:
            return _err("file_path 不能为空。")

        try:
            mgr = _get_skill_manager()
            skills_root = Path(mgr.skills_root)
            skill_dir = (skills_root / skill_name).resolve()

            if not skill_dir.exists():
                return _err(f"Skill 不存在: {skill_name}")

            target = (skill_dir / file_path).resolve()

            # Security check: ensure path stays within the specific skill directory
            try:
                target.relative_to(skill_dir)
            except ValueError:
                return _err("非法文件路径: 不允许写入该 Skill 目录外的文件。")

            if self.diff_mode:
                return self._apply_diff(
                    target, skill_name, file_path, target_content, replacement_content
                )
            else:
                return self._apply_full_replace(target, skill_name, file_path, content)
        except Exception as e:
            logger.error(f"update_skill_file failed: {e}")
            return _err("更新文件失败。请检查文件路径和内容。")

    def _apply_full_replace(
        self, target: Path, skill_name: str, file_path: str, content: str
    ) -> str:
        """Full file replacement mode."""
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return _ok(message=f"已更新文件: {skill_name}/{file_path}。{_REFRESH_HINT}")

    def _apply_diff(
        self,
        target: Path,
        skill_name: str,
        file_path: str,
        target_content: str,
        replacement_content: str,
    ) -> str:
        """Diff-based replacement mode with match validation."""
        import difflib

        if not target_content:
            return _err("target_content 不能为空。")

        # Input length limit to prevent performance issues
        if len(target_content) > MAX_DIFF_TARGET_LEN:
            return _err(f"target_content 超出长度限制 ({MAX_DIFF_TARGET_LEN} 字符)。")

        if not target.exists():
            return _err(
                f"文件不存在: {file_path}。Diff 模式不支持创建新文件，请使用全文模式。"
            )

        file_text = target.read_text(encoding="utf-8")
        threshold = self.diff_match_threshold / 100.0

        # Try exact match first (fast path)
        if target_content in file_text:
            new_text = file_text.replace(target_content, replacement_content, 1)
            target.write_text(new_text, encoding="utf-8")
            return _ok(
                data={"match_ratio": 100},
                message=(
                    f"已更新文件: {skill_name}/{file_path}（精确匹配）。{_REFRESH_HINT}"
                ),
            )

        # Fuzzy match using SequenceMatcher
        best_ratio = 0.0
        best_start = 0
        best_end = 0
        target_len = len(target_content)

        # Sliding window: search for the best matching substring
        # Use SequenceMatcher to find the best match position
        sm = difflib.SequenceMatcher(None, file_text, target_content, autojunk=False)
        blocks = sm.get_matching_blocks()

        # Try windows around each matching block anchor
        for block in blocks:
            if block.size == 0:
                continue
            # Try different window sizes around this anchor
            anchor_start = block.a
            for offset in range(
                -target_len, target_len // 2 + 1, max(1, target_len // 20)
            ):
                start = max(0, anchor_start + offset)
                end = min(len(file_text), start + target_len)
                if end - start < target_len // 2:
                    continue
                candidate = file_text[start:end]
                ratio = difflib.SequenceMatcher(
                    None, candidate, target_content, autojunk=False
                ).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_start = start
                    best_end = end

        match_pct = int(best_ratio * 100)

        if best_ratio < threshold:
            return _err(
                f"匹配失败: 最佳匹配度 {match_pct}%，"
                f"要求 {self.diff_match_threshold}%。"
                f"请检查 target_content 是否与文件内容一致。"
            )

        # Apply replacement
        new_text = file_text[:best_start] + replacement_content + file_text[best_end:]
        target.write_text(new_text, encoding="utf-8")
        return _ok(
            data={"match_ratio": match_pct},
            message=(
                f"已更新文件: {skill_name}/{file_path}"
                f"（匹配度: {match_pct}%）。{_REFRESH_HINT}"
            ),
        )


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
        except Exception as e:
            logger.error(f"update_skill_from_zip failed: {e}")
            return _err("从 ZIP 更新 Skill 失败。请检查 ZIP 文件格式是否正确。")
        finally:
            if tmp_file and os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except OSError:
                    pass
