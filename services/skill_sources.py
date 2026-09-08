"""Resolve public skill links into bounded file snapshots, without running installers."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import ipaddress
import json
import re
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver

from ..security.scanner import (
    MAX_ARCHIVE_BYTES,
    MAX_ENTRIES,
    MAX_FILE_BYTES,
    MAX_TOTAL_BYTES,
    load_zip,
    member_path,
    valid_skill_name,
)

GITHUB_API = "https://api.github.com"


class SourceError(ValueError):
    """A public, fixed download/selection error safe to include in the scan report."""


async def _gather_downloads(coroutines):
    tasks = [asyncio.create_task(coro) for coro in coroutines]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


def safe_url(url: str) -> str:
    if len(url) > 2048 or any(ord(c) < 33 for c in url) or "\\" in url:
        raise SourceError("链接格式无效。")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
    ):
        raise SourceError("只支持不含用户名密码的 HTTPS 链接。")
    return url


def public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


class PublicResolver(AbstractResolver):
    def __init__(self):
        self.resolver = aiohttp.resolver.DefaultResolver()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        answers = await self.resolver.resolve(host, port, family)
        if not answers or any(not public_address(item["host"]) for item in answers):
            raise SourceError("下载地址指向非公网 IP，已拒绝。")
        return answers

    async def close(self):
        await self.resolver.close()


class PublicHTTP:
    def __init__(self, github_token="", skillhub_key=""):
        self.github_token, self.skillhub_key = github_token, skillhub_key
        self.session = None
        self.resolver = None
        self.downloaded = 0

    async def __aenter__(self):
        self.resolver = PublicResolver()
        self.session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(resolver=self.resolver, limit=4),
            timeout=aiohttp.ClientTimeout(total=40, connect=10),
            trust_env=False,
            auto_decompress=False,
            headers={
                "User-Agent": "AstrBot-Skills-MCP-Manager",
                "Accept-Encoding": "identity",
            },
        )
        return self

    async def __aexit__(self, *args):
        await self.session.close()
        await self.resolver.close()

    async def get(self, url: str, limit: int = 4 * 1024 * 1024) -> tuple[bytes, str]:
        for _ in range(6):
            safe_url(url)
            parsed = urlsplit(url)
            try:
                if not public_address(parsed.hostname):
                    raise SourceError("不允许下载非公网地址。")
            except ValueError as exc:
                if isinstance(exc, SourceError):
                    raise
            headers = {}
            if parsed.hostname == "api.github.com" and self.github_token:
                headers["Authorization"] = f"Bearer {self.github_token}"
            if parsed.hostname == "api.skillhub.cn" and self.skillhub_key:
                headers["X-API-Key"] = self.skillhub_key
            async with self.session.get(
                url, headers=headers, allow_redirects=False
            ) as response:
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location:
                        raise SourceError("下载重定向缺少目标地址。")
                    url = urljoin(url, location)
                    continue
                if response.status != 200:
                    raise SourceError(
                        f"来源返回 HTTP {response.status}，未下载或安装；请检查链接、访问权限或稍后重试。"
                    )
                if (
                    response.headers.get("Content-Encoding", "identity").lower()
                    != "identity"
                ):
                    raise SourceError("来源返回了不支持的传输压缩。")
                if (
                    response.content_length is not None
                    and response.content_length > limit
                ):
                    raise SourceError("下载内容超过大小上限。")
                data = bytearray()
                async for block in response.content.iter_chunked(65536):
                    self.downloaded += len(block)
                    if (
                        len(data) + len(block) > limit
                        or self.downloaded > MAX_TOTAL_BYTES + 12 * 1024 * 1024
                    ):
                        raise SourceError("下载内容超过大小上限。")
                    data.extend(block)
                return bytes(data), response.headers.get("Content-Type", "")
        raise SourceError("下载重定向次数过多。")

    async def json(self, url):
        data, _ = await self.get(url)
        try:
            return json.loads(data)
        except (ValueError, UnicodeError):
            raise SourceError("来源未返回有效 JSON 数据。") from None


@dataclass
class SourceBundle:
    name: str
    files: dict[str, bytes]
    provenance: dict


def _name(content: bytes, fallback: str) -> str:
    # Read only a scalar name, not arbitrary YAML from an untrusted manifest.
    text = content[:65536].decode("utf-8-sig", errors="replace")
    frontmatter = re.match(r"\A---\s*\n(.*?)\n---(?:\s|$)", text, re.S)
    if frontmatter:
        match = re.search(r"^name:\s*([^\r\n]+)", frontmatter[1], re.M)
        if match:
            name = match[1].strip().strip("\"'")
            if valid_skill_name(name):
                return name
    return fallback


def _choose(candidates: list[tuple[str, str]], selection: str) -> tuple[str, str]:
    matches = [(path, name) for path, name in candidates if selection in {path, name}]
    if selection and len(matches) == 1:
        return matches[0]
    if not selection and len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise SourceError("来源中未找到 SKILL.md。")
    choices = "；".join(f"{name} ({path or '.'})" for path, name in candidates[:20])
    raise SourceError("请在链接后指定唯一技能名或目录；候选：" + choices)


class SkillSources:
    def __init__(self, github_token="", skillhub_key=""):
        self.github_token, self.skillhub_key = github_token, skillhub_key

    async def resolve(self, url: str, selection: str = "") -> SourceBundle:
        safe_url(url)
        async with PublicHTTP(self.github_token, self.skillhub_key) as client:
            try:
                return await asyncio.wait_for(
                    self._resolve(client, url, selection), timeout=75
                )
            except (asyncio.TimeoutError, aiohttp.ClientError):
                raise SourceError("来源下载失败或超时，未安装 Skill。") from None

    async def _resolve(self, client, url, selection):
        parsed = urlsplit(url)
        host = parsed.hostname
        parts = [unquote(p) for p in parsed.path.strip("/").split("/") if p]
        if host == "github.com" and len(parts) >= 2:
            repo = "/".join(parts[:2]).removesuffix(".git")
            if len(parts) == 2:
                return await self._github(
                    client, repo, selection=selection, original=url
                )
            if parts[2] in {"tree", "blob"} and len(parts) >= 4:
                # Resolve the longest valid ref; refs can contain slashes.
                ref_parts = parts[3:]
                for count in range(min(len(ref_parts), 8), 0, -1):
                    ref = "/".join(ref_parts[:count])
                    try:
                        commit = await client.json(
                            f"{GITHUB_API}/repos/{repo}/commits/{quote(ref, safe='')}"
                        )
                    except SourceError as exc:
                        if "HTTP 404" in str(exc) or "HTTP 422" in str(exc):
                            continue
                        raise
                    subpath = "/".join(ref_parts[count:])
                    if parts[2] == "blob":
                        if not subpath.endswith("SKILL.md"):
                            raise SourceError(
                                "文件链接必须指向 SKILL.md；请提供 Skill 目录链接。"
                            )
                        subpath = subpath.rpartition("/")[0]
                    return await self._github(
                        client,
                        repo,
                        commit=commit["sha"],
                        subpath=subpath,
                        selection=selection,
                        original=url,
                    )
                raise SourceError("GitHub 链接的分支、标签或路径不存在。")
            if parts[2] in {"archive", "releases"}:
                return await self._zip(client, url, selection, original=url)
        if host == "skills.sh" and len(parts) in {2, 3}:
            return await self._github(
                client,
                "/".join(parts[:2]),
                selection=selection or (parts[2] if len(parts) == 3 else ""),
                original=url,
            )
        if host in {"clawhub.ai", "clawhub.com"}:
            if len(parts) == 3 and parts[1] == "skills":
                owner, slug = parts[0], parts[2]
            elif len(parts) == 2:
                owner, slug = parts
            else:
                raise SourceError("请提供 ClawHub 的具体 Skill 详情链接。")
            return await self._registry(
                client, "https://clawhub.ai", owner, slug, parsed.query, url
            )
        if host in {"skillhub.cn", "www.skillhub.cn", "skillhub.cloud.tencent.com"}:
            if parts and parts[0] == "skills":
                parts = parts[1:]
            if len(parts) == 2:
                owner, slug = parts
            elif len(parts) == 1:
                owner, slug = "", parts[0]
            else:
                raise SourceError("请提供 SkillHub 的具体 Skill 详情链接。")
            return await self._registry(
                client, "https://api.skillhub.cn", owner, slug, parsed.query, url
            )
        if (
            host in {"skillsmp.com", "www.skillsmp.com"}
            and parts
            and parts[0] in {"skills", "creators"}
        ):
            page, _ = await client.get(url, 2 * 1024 * 1024)
            text = (
                html.unescape(page.decode("utf-8"))
                .replace('\\"', '"')
                .replace("\\/", "/")
            )
            matches = set(
                re.findall(
                    r"npx\s+skills\s+add\s+(https://github\.com/[\w.-]+/[\w.-]+)\s+--skill\s+[\"']?([\w.-]+)",
                    text,
                )
            )
            if len(matches) != 1:
                raise SourceError(
                    "SkillsMP 页面未提供唯一的源仓库与技能名，请改用页面中的 GitHub 目录链接。"
                )
            repo_url, name = matches.pop()
            bundle = await self._resolve(client, repo_url, selection or name)
            bundle.provenance["url"] = url
            return bundle
        if host in {"raw.githubusercontent.com", "codeload.github.com"}:
            if (
                host == "raw.githubusercontent.com"
                and len(parts) >= 4
                and parts[-1] == "SKILL.md"
            ):
                return await self._resolve(
                    client,
                    "https://github.com/"
                    + "/".join(parts[:2])
                    + "/blob/"
                    + "/".join(parts[2:]),
                    selection,
                )
            if host == "codeload.github.com":
                return await self._zip(client, url, selection, original=url)
        raise SourceError(
            "暂不支持该链接。支持 GitHub、skills.sh、ClawHub、腾讯 SkillHub 和 SkillsMP 的技能详情链接。"
        )

    async def _github(
        self, client, repo, *, commit="", subpath=None, selection="", original=""
    ):
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
            raise SourceError("GitHub 仓库标识无效。")
        if not commit:
            metadata = await client.json(f"{GITHUB_API}/repos/{repo}")
            revision = await client.json(
                f"{GITHUB_API}/repos/{repo}/commits/{quote(metadata['default_branch'], safe='')}"
            )
            commit = revision["sha"]
        if not re.fullmatch(r"[a-fA-F0-9]{40}", commit):
            raise SourceError("GitHub 未返回有效的固定提交。")
        tree = await client.json(
            f"{GITHUB_API}/repos/{repo}/git/trees/{commit}?recursive=1"
        )
        if tree.get("truncated") or len(tree.get("tree", [])) > 20000:
            raise SourceError("仓库目录过大或目录清单不完整，请提供独立 Skill 包。")
        entries = tree.get("tree", [])
        manifests = [
            item
            for item in entries
            if item["type"] == "blob" and item["path"].split("/")[-1] == "SKILL.md"
        ]
        if subpath is not None:
            subpath = member_path(subpath) if subpath else ""
            prefix = subpath + "/" if subpath else ""
            exact = [m for m in manifests if m["path"] == prefix + "SKILL.md"]
            manifests = exact or [m for m in manifests if m["path"].startswith(prefix)]
        if len(manifests) > 64:
            raise SourceError("仓库包含过多 Skill，请提供具体 Skill 目录链接。")
        raw_base = f"https://raw.githubusercontent.com/{repo}/{commit}/"
        cache = {}
        candidates = []
        semaphore = asyncio.Semaphore(4)

        async def fetch_entry(entry):
            async with semaphore:
                if entry.get("mode") not in {"100644", "100755"}:
                    raise SourceError("Skill 文件不是普通文件。")
                path = member_path(entry["path"])
                if self.github_token:
                    blob = await client.json(
                        f"{GITHUB_API}/repos/{repo}/git/blobs/{entry['sha']}"
                    )
                    if (
                        blob.get("encoding") != "base64"
                        or blob.get("size", MAX_FILE_BYTES + 1) > MAX_FILE_BYTES
                    ):
                        raise SourceError("GitHub 文件编码不支持或大小超过上限。")
                    data = base64.b64decode(
                        blob["content"].replace("\n", ""), validate=True
                    )
                    if len(data) > MAX_FILE_BYTES:
                        raise SourceError("GitHub 文件大小超过上限。")
                else:
                    data, _ = await client.get(
                        raw_base + quote(path, safe="/"), MAX_FILE_BYTES
                    )
                self._verify_blob(data, entry)
                return path, data

        fetched = await _gather_downloads(fetch_entry(entry) for entry in manifests)
        for path, data in fetched:
            cache[path] = data
            directory = path.rpartition("/")[0]
            candidates.append(
                (
                    directory,
                    _name(data, directory.rsplit("/", 1)[-1] or repo.split("/")[-1]),
                )
            )
        directory, name = _choose(candidates, selection)
        if not valid_skill_name(name):
            raise SourceError("Skill 名称无效。")
        prefix = directory + "/" if directory else ""
        selected = [
            item
            for item in entries
            if item["path"].startswith(prefix) and item["type"] != "tree"
        ]
        if (
            len(selected) > MAX_ENTRIES
            or sum(item.get("size", 0) for item in selected) > MAX_TOTAL_BYTES
        ):
            raise SourceError("Skill 文件数量或总大小超过上限。")
        files = {}
        for entry in selected:
            member_path(entry["path"][len(prefix) :])
            if entry["type"] != "blob" or entry.get("mode") not in {"100644", "100755"}:
                raise SourceError("Skill 包含符号链接或 Git 子模块，未下载。")
            if entry.get("size", 0) > MAX_FILE_BYTES:
                raise SourceError("Skill 单文件大小超过上限。")
        missing = [entry for entry in selected if entry["path"] not in cache]
        cache.update(await _gather_downloads(fetch_entry(entry) for entry in missing))
        for entry in selected:
            files[member_path(entry["path"][len(prefix) :])] = cache[entry["path"]]
        if sum(map(len, files.values())) > MAX_TOTAL_BYTES:
            raise SourceError("Skill 总大小超过上限。")
        return SourceBundle(
            name,
            files,
            {"url": original, "repository": repo, "commit": commit, "path": directory},
        )

    @staticmethod
    def _verify_blob(data, entry):
        actual = hashlib.sha1(
            b"blob " + str(len(data)).encode() + b"\0" + data
        ).hexdigest()
        if actual != entry["sha"]:
            raise SourceError("GitHub 文件内容与固定提交的指纹不一致。")

    async def _registry(self, client, base, owner, slug, query, original):
        if not valid_skill_name(slug):
            raise SourceError("市场技能标识无效。")
        owner_query = (
            "?ownerHandle=" + quote(owner, safe="")
            if owner and base == "https://clawhub.ai"
            else ""
        )
        metadata = await client.json(
            f"{base}/api/v1/skills/{quote(slug, safe='')}{owner_query}"
        )
        if (
            owner
            and metadata.get("owner", {}).get("handle", "").lower() != owner.lower()
        ):
            raise SourceError("市场返回的作者与链接不一致，未下载。")
        params = parse_qs(query)
        version = params.get("version", [""])[0] or (
            metadata.get("latestVersion") or {}
        ).get("version")
        if (
            not isinstance(version, str) or not version
        ) and base != "https://clawhub.ai":
            raise SourceError("市场未返回可固定的版本号。")
        download_slug = (
            f"@{owner}/{slug}" if owner and base == "https://api.skillhub.cn" else slug
        )
        download = f"{base}/api/v1/download?slug={quote(download_slug, safe='')}"
        if version:
            download += "&version=" + quote(version, safe="")
        if owner_query:
            download += "&" + owner_query[1:]
        data, content_type = await client.get(download, MAX_ARCHIVE_BYTES)
        if "json" in content_type or data.lstrip().startswith(b"{"):
            descriptor = json.loads(data)
            if (
                base != "https://clawhub.ai"
                or descriptor.get("sourceRef") != "public-github"
            ):
                raise SourceError("市场返回了不支持的下载描述。")
            bundle = await self._github(
                client,
                descriptor["repo"],
                commit=descriptor["commit"],
                subpath=descriptor["path"],
                original=original,
            )
            bundle.provenance.update(
                market_version=version,
                market_content_hash=descriptor.get("contentHash"),
            )
            return bundle
        if not version:
            raise SourceError("托管压缩包缺少可固定版本，未安装。")
        bundle = await asyncio.to_thread(self._unpack, data, "", slug, original)
        bundle.provenance.update(
            version=version,
            owner=metadata.get("owner", {}).get("handle"),
            registry=base,
        )
        return bundle

    async def _zip(self, client, url, selection, original):
        data, _ = await client.get(url, MAX_ARCHIVE_BYTES)
        return await asyncio.to_thread(self._unpack, data, selection, "skill", original)

    @staticmethod
    def _unpack(data, selection, fallback, original):
        with tempfile.TemporaryDirectory(prefix="skill_download_") as tmp:
            path = Path(tmp) / "bundle.zip"
            path.write_bytes(data)
            files = load_zip(path)
        candidates = []
        for path, content in files.items():
            if path.split("/")[-1] == "SKILL.md":
                directory = path.rpartition("/")[0]
                candidates.append((directory, _name(content, fallback)))
        directory, name = _choose(candidates, selection)
        prefix = directory + "/" if directory else ""
        selected = {
            path[len(prefix) :]: content
            for path, content in files.items()
            if path.startswith(prefix)
        }
        return SourceBundle(
            name,
            selected,
            {
                "url": original,
                "archive_sha256": hashlib.sha256(data).hexdigest(),
                "path": directory,
            },
        )
