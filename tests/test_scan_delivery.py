import copy
import importlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from .support import PACKAGE

delivery_module = importlib.import_module(f"{PACKAGE}.services.scan_delivery")
service_module = importlib.import_module(f"{PACKAGE}.services.skill_install")


class Event:
    def __init__(self, umo="platform:private:alice"):
        self.unified_msg_origin = umo
        self.extras = {}
        self.sent = []

    async def send(self, result):
        self.sent.append(result)

    def plain_result(self, message):
        return message

    def set_extra(self, key, value):
        self.extras[key] = value

    def get_extra(self, key):
        return self.extras.get(key)

    def is_stopped(self):
        return False


class Owner:
    def __init__(self):
        self.store = {}
        self.context = SimpleNamespace(
            conversation_manager=SimpleNamespace(
                get_curr_conversation_id=AsyncMock(return_value="conversation"),
                new_conversation=AsyncMock(return_value="new-conversation"),
                get_conversation=AsyncMock(return_value=SimpleNamespace(history="[]")),
                add_message_pair=AsyncMock(),
            ),
            get_current_chat_provider_id=AsyncMock(return_value="session-model"),
            llm_generate=AsyncMock(
                return_value=SimpleNamespace(
                    role="assistant", completion_text="已阻止安装。"
                )
            ),
        )

    async def get_kv_data(self, key, default):
        return copy.deepcopy(self.store.get(key, default))

    async def put_kv_data(self, key, value):
        self.store[key] = copy.deepcopy(value)

    async def delete_kv_data(self, key):
        self.store.pop(key, None)


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.owner = Owner()
        self.delivery = delivery_module.ScanDelivery(self.owner)
        self.event = Event()
        self.result = service_module.failed_result("扫描输入失败")

    async def test_every_decision_is_delivered_to_session_model_and_history(self):
        for status, decision, operation in (
            ("complete", "allow", "completed"),
            ("complete", "warn", "completed"),
            ("complete", "block", "not_performed"),
            ("incomplete", "block", "not_performed"),
            ("complete", "allow", "failed"),
        ):
            with self.subTest(status=status, decision=decision, operation=operation):
                self.result["scan"].update(status=status, decision=decision)
                self.result["operation_status"] = operation
                await self.delivery.deliver(self.event, self.result)
                args = self.owner.context.llm_generate.call_args.kwargs
                self.assertEqual(args["chat_provider_id"], "session-model")
                self.assertNotIn("tools", args)
                self.assertIn(
                    json.dumps(self.result, ensure_ascii=True, separators=(",", ":")),
                    args["prompt"],
                )
                self.owner.context.get_current_chat_provider_id.assert_awaited_with(
                    self.event.unified_msg_origin
                )
                history_args = self.owner.context.conversation_manager.add_message_pair.call_args.args
                self.assertEqual(history_args[0], "conversation")
                self.assertEqual(history_args[1]["content"], args["prompt"])
                self.assertTrue(self.event.sent)
                self.assertFalse(self.owner.store)

    async def test_provider_failure_retains_and_reloads_queue(self):
        self.owner.context.llm_generate.side_effect = RuntimeError("offline")
        with self.assertLogs(delivery_module.logger, level="ERROR"):
            await self.delivery.deliver(self.event, self.result)
        self.assertTrue(self.owner.store)
        self.assertIn("报告已保存", self.event.sent[-1])
        reloaded = delivery_module.ScanDelivery(self.owner)
        request = SimpleNamespace(prompt="接着说")
        await reloaded.inject_pending(self.event, request)
        self.assertIn("扫描报告", request.prompt)
        self.assertTrue(self.owner.store)  # Injection is not delivery acknowledgment.
        await reloaded.acknowledge_response(
            self.event, SimpleNamespace(role="assistant", completion_text="已收到报告")
        )
        self.assertFalse(self.owner.store)

    async def test_different_sessions_do_not_receive_each_others_reports(self):
        await self.delivery._enqueue(self.event.unified_msg_origin, self.result)
        other = Event("platform:private:bob")
        request = SimpleNamespace(prompt="hello")
        await self.delivery.inject_pending(other, request)
        self.assertEqual(request.prompt, "hello")
        self.assertTrue(self.owner.store)

    async def test_external_review_still_goes_to_current_session_model(self):
        self.result["model_review"] = {
            "provider_id": "separate-reviewer",
            "status": "completed",
            "opinion": "保持阻断",
        }
        await self.delivery.deliver(self.event, self.result)
        args = self.owner.context.llm_generate.call_args.kwargs
        self.assertEqual(args["chat_provider_id"], "session-model")
        self.assertIn("separate-reviewer", args["prompt"])
        self.assertIn("model_review", args["prompt"])

    async def test_response_only_clears_report_ids_in_that_request(self):
        await self.delivery._enqueue(self.event.unified_msg_origin, self.result)
        request = SimpleNamespace(prompt="hello")
        await self.delivery.inject_pending(self.event, request)
        second, _ = await self.delivery._enqueue(
            self.event.unified_msg_origin, self.result
        )
        await self.delivery.acknowledge_response(
            self.event, SimpleNamespace(role="assistant", completion_text="收到")
        )
        pending = await self.delivery._load(self.event.unified_msg_origin)
        self.assertEqual([item["id"] for item in pending], [second["id"]])

    async def test_empty_or_error_response_does_not_acknowledge(self):
        await self.delivery._enqueue(self.event.unified_msg_origin, self.result)
        request = SimpleNamespace(prompt="hello")
        await self.delivery.inject_pending(self.event, request)
        for response in (
            SimpleNamespace(role="assistant", completion_text=""),
            SimpleNamespace(role="err", completion_text="request failed"),
        ):
            await self.delivery.acknowledge_response(self.event, response)
            self.assertTrue(self.owner.store)

    async def test_history_failure_retains_report(self):
        self.owner.context.conversation_manager.add_message_pair.side_effect = (
            RuntimeError("storage offline")
        )
        with self.assertLogs(delivery_module.logger, level="ERROR"):
            await self.delivery.deliver(self.event, self.result)
        self.assertTrue(self.owner.store)

    async def test_interrupted_agent_does_not_acknowledge_report(self):
        await self.delivery._enqueue(self.event.unified_msg_origin, self.result)
        await self.delivery.inject_pending(self.event, SimpleNamespace(prompt="hello"))
        self.event.set_extra("agent_stop_requested", True)
        await self.delivery.acknowledge_response(
            self.event, SimpleNamespace(role="assistant", completion_text="用户中止")
        )
        self.assertTrue(self.owner.store)

    async def test_no_current_conversation_is_created_for_report(self):
        self.owner.context.conversation_manager.get_curr_conversation_id.return_value = None
        await self.delivery.deliver(self.event, self.result)
        self.owner.context.conversation_manager.new_conversation.assert_awaited_with(
            self.event.unified_msg_origin
        )

    async def test_storage_failure_is_not_silently_claimed_persistent(self):
        self.owner.put_kv_data = AsyncMock(side_effect=RuntimeError("disk offline"))
        self.owner.context.llm_generate.side_effect = RuntimeError("model offline")
        with self.assertLogs(delivery_module.logger, level="ERROR"):
            await self.delivery.deliver(self.event, self.result)
        self.assertIn("暂存在内存", self.event.sent[-1])
        self.assertTrue(self.delivery._volatile)

    async def test_memory_report_can_be_injected_when_storage_read_fails(self):
        self.owner.get_kv_data = AsyncMock(side_effect=OSError("storage offline"))
        self.owner.context.llm_generate.side_effect = RuntimeError("model offline")
        with self.assertLogs(delivery_module.logger, level="ERROR"):
            await self.delivery.deliver(self.event, self.result)
            request = SimpleNamespace(prompt="hello")
            await self.delivery.inject_pending(self.event, request)
        self.assertIn("扫描报告", request.prompt)
