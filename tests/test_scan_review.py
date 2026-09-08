import importlib
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from .support import PACKAGE

module = importlib.import_module(f"{PACKAGE}.services.scan_review")
service = importlib.import_module(f"{PACKAGE}.services.skill_install")


class ReviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = SimpleNamespace(
            llm_generate=AsyncMock(
                return_value=SimpleNamespace(
                    role="assistant", completion_text="扫描不完整，应保留阻断。"
                )
            )
        )
        self.result = service.failed_result("不支持的文件内容")

    async def test_empty_selection_leaves_review_to_session_without_extra_call(self):
        for selection in ("", "  "):
            result = await module.ScanReview(self.context, selection).review(
                self.result
            )
            self.assertIs(result, self.result)
        self.context.llm_generate.assert_not_awaited()

    async def test_selected_model_receives_report_only_and_preserves_decisions(self):
        result = await module.ScanReview(self.context, "review-provider").review(
            self.result
        )
        call = self.context.llm_generate.call_args.kwargs
        self.assertEqual(call["chat_provider_id"], "review-provider")
        self.assertNotIn("contexts", call)
        self.assertNotIn("tools", call)
        self.assertEqual(result["scan"], self.result["scan"])
        self.assertEqual(result["operation_status"], "not_performed")
        self.assertEqual(result["model_review"]["status"], "completed")
        self.assertNotIn("model_review", self.result)

    async def test_failed_empty_or_error_model_returns_original_report_with_failure(
        self,
    ):
        for response in (
            SimpleNamespace(role="err", completion_text="provider failed"),
            SimpleNamespace(role="assistant", completion_text="  "),
        ):
            self.context.llm_generate.return_value = response
            with self.assertLogs(module.logger, level="ERROR"):
                result = await module.ScanReview(
                    self.context, "review-provider"
                ).review(self.result)
            self.assertEqual(result["model_review"]["status"], "failed")
            self.assertEqual(result["scan"], self.result["scan"])
        self.context.llm_generate.side_effect = RuntimeError("missing provider")
        with self.assertLogs(module.logger, level="ERROR"):
            result = await module.ScanReview(self.context, "missing").review(
                self.result
            )
        self.assertEqual(result["model_review"]["status"], "failed")
        self.assertEqual(result["scan"]["decision"], "block")

    async def test_timeout_keeps_original_report(self):
        async def timeout(awaitable, timeout):
            awaitable.close()
            raise TimeoutError()

        with (
            patch.object(module.asyncio, "wait_for", timeout),
            self.assertLogs(module.logger, level="ERROR"),
        ):
            result = await module.ScanReview(self.context, "slow").review(self.result)
        self.assertEqual(result["model_review"]["status"], "failed")
        self.assertEqual(result["scan"], self.result["scan"])
