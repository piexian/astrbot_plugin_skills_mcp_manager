import asyncio
import base64
import hashlib
import importlib
import io
import json
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

from .support import PACKAGE

sources = importlib.import_module(f"{PACKAGE}.services.skill_sources")
SHA = "a" * 40


def blob(path, content, mode="100644"):
    return {
        "path": path,
        "mode": mode,
        "type": "blob",
        "size": len(content),
        "sha": hashlib.sha1(
            b"blob " + str(len(content)).encode() + b"\0" + content
        ).hexdigest(),
    }


def zip_bytes(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return output.getvalue()


class Client:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    async def get(self, url, limit=4 * 1024 * 1024):
        self.calls.append(url)
        if url not in self.routes:
            raise sources.SourceError("来源返回 HTTP 404")
        value = self.routes[url]
        if isinstance(value, dict):
            return json.dumps(value).encode(), "application/json"
        return value, "application/octet-stream"

    async def json(self, url):
        value, _ = await self.get(url)
        return json.loads(value)


class SourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_parallel_download_cancels_and_joins_other_downloads(self):
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def slow():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        async def failed():
            await started.wait()
            raise sources.SourceError("download failed")

        with self.assertRaises(sources.SourceError):
            await sources._gather_downloads([slow(), failed()])
        self.assertTrue(stopped.is_set())

    def fixture(self, private=False, extra=None):
        files = {
            "skills/demo/SKILL.md": b"---\nname: actual-name\n---\nHello",
            "skills/demo/scripts/run.py": b"print('hello')",
        }
        files.update(extra or {})
        entries = [blob(path, data) for path, data in files.items()]
        routes = {
            "https://api.github.com/repos/owner/repo": {
                "default_branch": "main",
                "private": private,
            },
            "https://api.github.com/repos/owner/repo/commits/main": {"sha": SHA},
            f"https://api.github.com/repos/owner/repo/git/trees/{SHA}?recursive=1": {
                "tree": entries,
                "truncated": False,
            },
        }
        for entry in entries:
            data = files[entry["path"]]
            if private:
                routes[
                    f"https://api.github.com/repos/owner/repo/git/blobs/{entry['sha']}"
                ] = {
                    "encoding": "base64",
                    "size": len(data),
                    "content": base64.b64encode(data).decode(),
                }
            else:
                routes[
                    f"https://raw.githubusercontent.com/owner/repo/{SHA}/{entry['path']}"
                ] = data
        return Client(routes), files

    async def test_github_directory_downloads_whole_skill_at_fixed_commit(self):
        client, _ = self.fixture()
        url = "https://github.com/owner/repo/tree/main/skills/demo"
        bundle = await sources.SkillSources()._resolve(client, url, "")
        self.assertEqual(bundle.name, "actual-name")
        self.assertEqual(set(bundle.files), {"SKILL.md", "scripts/run.py"})
        self.assertEqual(bundle.provenance["commit"], SHA)
        self.assertEqual(bundle.provenance["url"], url)

    async def test_skills_sh_matches_manifest_name_not_directory_name(self):
        client, _ = self.fixture()
        bundle = await sources.SkillSources()._resolve(
            client, "https://skills.sh/owner/repo/actual-name", ""
        )
        self.assertEqual(bundle.provenance["path"], "skills/demo")

    async def test_root_manifest_link_selects_root_even_with_nested_skills(self):
        client, _ = self.fixture(
            extra={"SKILL.md": b"---\nname: root-skill\n---\nRoot"}
        )
        bundle = await sources.SkillSources()._resolve(
            client, "https://github.com/owner/repo/blob/main/SKILL.md", ""
        )
        self.assertEqual(bundle.name, "root-skill")
        self.assertEqual(bundle.provenance["path"], "")
        self.assertIn("skills/demo/scripts/run.py", bundle.files)

    async def test_private_repo_uses_api_blobs_and_never_raw_download(self):
        client, _ = self.fixture(private=True)
        bundle = await sources.SkillSources(github_token="secret")._resolve(
            client, "https://github.com/owner/repo", ""
        )
        self.assertIn("scripts/run.py", bundle.files)
        self.assertFalse(
            any(
                urlsplit(call).hostname == "raw.githubusercontent.com"
                for call in client.calls
            )
        )
        self.assertNotIn("secret", json.dumps(bundle.provenance))

    async def test_multiple_skills_require_explicit_selection(self):
        client, _ = self.fixture(
            extra={"skills/other/SKILL.md": b"---\nname: other\n---\nOther"}
        )
        with self.assertRaisesRegex(sources.SourceError, "指定唯一"):
            await sources.SkillSources()._resolve(
                client, "https://github.com/owner/repo", ""
            )
        bundle = await sources.SkillSources()._resolve(
            client, "https://github.com/owner/repo", "other"
        )
        self.assertEqual(set(bundle.files), {"SKILL.md"})

    async def test_changed_blob_and_truncated_tree_are_rejected(self):
        client, _ = self.fixture()
        raw = next(
            url
            for url in client.routes
            if urlsplit(url).hostname == "raw.githubusercontent.com"
        )
        client.routes[raw] = b"changed"
        with self.assertRaisesRegex(sources.SourceError, "指纹不一致"):
            await sources.SkillSources()._resolve(
                client, "https://github.com/owner/repo", ""
            )
        client, _ = self.fixture()
        client.routes[
            f"https://api.github.com/repos/owner/repo/git/trees/{SHA}?recursive=1"
        ]["truncated"] = True
        with self.assertRaisesRegex(sources.SourceError, "不完整"):
            await sources.SkillSources()._resolve(
                client, "https://github.com/owner/repo", ""
            )

    async def test_clawhub_github_handoff_without_stored_version(self):
        client, _ = self.fixture()
        client.routes["https://clawhub.ai/api/v1/skills/demo?ownerHandle=alice"] = {
            "owner": {"handle": "alice"},
            "latestVersion": None,
        }
        client.routes[
            "https://clawhub.ai/api/v1/download?slug=demo&ownerHandle=alice"
        ] = {
            "sourceRef": "public-github",
            "repo": "owner/repo",
            "commit": SHA,
            "path": "skills/demo",
            "contentHash": "market-hash",
            "archiveUrl": "https://evil.invalid/not-used",
        }
        bundle = await sources.SkillSources()._resolve(
            client, "https://clawhub.ai/alice/skills/demo", ""
        )
        self.assertEqual(bundle.provenance["commit"], SHA)
        self.assertFalse(any("evil.invalid" in call for call in client.calls))

    async def test_skillhub_version_and_owner_pinned(self):
        client = Client(
            {
                "https://api.skillhub.cn/api/v1/skills/demo": {
                    "owner": {"handle": "alice"},
                    "latestVersion": {"version": "1.2.3"},
                },
                "https://api.skillhub.cn/api/v1/download?slug=%40alice%2Fdemo&version=1.2.3": zip_bytes(
                    {"SKILL.md": "---\nname: demo\n---\nHello"}
                ),
            }
        )
        bundle = await sources.SkillSources()._resolve(
            client, "https://skillhub.cn/skills/alice/demo", ""
        )
        self.assertEqual(bundle.provenance["version"], "1.2.3")
        with self.assertRaisesRegex(sources.SourceError, "作者"):
            await sources.SkillSources()._resolve(
                client, "https://skillhub.cn/skills/bob/demo", ""
            )

    async def test_skillsmp_only_parses_unique_install_metadata(self):
        client, _ = self.fixture()
        url = "https://skillsmp.com/creators/owner/repo/demo"
        client.routes[url] = (
            b"<code>npx skills add https://github.com/owner/repo --skill actual-name</code>"
        )
        bundle = await sources.SkillSources()._resolve(client, url, "")
        self.assertEqual(bundle.name, "actual-name")
        self.assertEqual(bundle.provenance["url"], url)
        client.routes[url] += (
            b"<code>npx skills add https://github.com/evil/repo --skill bad</code>"
        )
        with self.assertRaisesRegex(sources.SourceError, "唯一"):
            await sources.SkillSources()._resolve(client, url, "")

    async def test_archive_traversal_rejected_before_packaging(self):
        with self.assertRaises(ValueError):
            sources.SkillSources._unpack(
                zip_bytes({"../SKILL.md": "bad"}),
                "",
                "demo",
                "https://github.com/o/r/archive/a.zip",
            )


class Response:
    def __init__(self, status=200, headers=None, chunks=None):
        self.status = status
        self.headers = headers or {}
        self.content_length = None
        self.chunks = chunks or [b"data"]
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def iter_chunked(self, size):
        for chunk in self.chunks:
            yield chunk


class HTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_credentials_only_sent_to_their_exact_api_host(self):
        calls = []
        responses = [
            Response(302, {"Location": "https://cdn.example.test/package"}),
            Response(),
        ]

        def get(url, **kwargs):
            calls.append((url, kwargs))
            return responses.pop(0)

        client = sources.PublicHTTP("secret-token", "skillhub-secret")
        client.session = SimpleNamespace(get=get)
        await client.get("https://api.github.com/repos/o/r")
        self.assertEqual(
            calls[0][1]["headers"], {"Authorization": "Bearer secret-token"}
        )
        self.assertEqual(calls[1][1]["headers"], {})

    async def test_private_ip_redirect_and_oversized_stream_are_rejected(self):
        client = sources.PublicHTTP()
        client.session = SimpleNamespace(
            get=lambda *args, **kwargs: Response(
                302, {"Location": "https://127.0.0.1/private"}
            )
        )
        with self.assertRaisesRegex(sources.SourceError, "非公网"):
            await client.get("https://public.example.test/file")
        client.session = SimpleNamespace(
            get=lambda *args, **kwargs: Response(chunks=[b"x" * 10])
        )
        with self.assertRaisesRegex(sources.SourceError, "上限"):
            await client.get("https://public.example.test/file", limit=5)

    async def test_dns_private_address_is_rejected_by_connecting_resolver(self):
        resolver = sources.PublicResolver()
        await resolver.resolver.close()
        resolver.resolver = SimpleNamespace(
            resolve=AsyncMock(return_value=[{"host": "10.0.0.1"}])
        )
        with self.assertRaisesRegex(sources.SourceError, "非公网"):
            await resolver.resolve("attacker.example.test", 443)

    def test_url_and_address_boundaries(self):
        for url in (
            "http://github.com/o/r",
            "https://user:pass@github.com/o/r",
            "https://github.com:8443/o/r",
        ):
            with self.assertRaises(sources.SourceError):
                sources.safe_url(url)
        for ip in (
            "127.0.0.1",
            "::1",
            "::ffff:127.0.0.1",
            "169.254.169.254",
            "10.0.0.1",
        ):
            self.assertFalse(sources.public_address(ip))
