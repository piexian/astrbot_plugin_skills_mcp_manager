"""Opt-in public download checks. Never install or execute downloaded skills."""

import importlib
import os
import unittest

from .support import PACKAGE


@unittest.skipUnless(
    os.environ.get("SKILL_SOURCES_LIVE") == "1", "public network checks are opt-in"
)
class LiveSourceTests(unittest.IsolatedAsyncioTestCase):
    async def resolve(self, url):
        module = importlib.import_module(f"{PACKAGE}.services.skill_sources")
        bundle = await module.SkillSources().resolve(url)
        self.assertIn("SKILL.md", bundle.files)
        self.assertTrue(bundle.name)
        print(f"Resolved {bundle.name}: {len(bundle.files)} files", flush=True)

    async def test_github(self):
        await self.resolve(
            "https://github.com/anthropics/skills/tree/main/skills/frontend-design"
        )

    async def test_skills_sh(self):
        await self.resolve(
            "https://skills.sh/vercel-labs/agent-skills/vercel-composition-patterns"
        )

    async def test_clawhub(self):
        await self.resolve("https://clawhub.ai/steipete/skills/weather")

    async def test_skillhub(self):
        await self.resolve("https://skillhub.cn/skills/find-skill-skillhub")

    async def test_skillsmp(self):
        await self.resolve(
            "https://skillsmp.com/skills/anthropics-skills-skills-frontend-design-skill-md"
        )
