import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.codex_app_server import (
    _verify_installed_codex,
    _token_usage,
    extract_app_server_usage,
    run_app_server,
)


FAKE_SERVER = r'''
import json
import sys

def send(message):
    print(json.dumps(message, separators=(",", ":")), flush=True)

for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method == "initialize":
        send({"id": message["id"], "result": {}})
    elif method == "initialized":
        pass
    elif method == "thread/start":
        send({"id": message["id"], "result": {"thread": {"id": "thread-1"}}})
    elif method == "turn/start":
        send({
            "id": 90,
            "method": "item/commandExecution/requestApproval",
            "params": {"reason": "fixture"},
        })
        approval = json.loads(next(sys.stdin))
        if approval != {"id": 90, "result": {"decision": "decline"}}:
            raise SystemExit(9)
        send({"id": message["id"], "result": {"turn": {"id": "turn-1"}}})
        send({
            "method": "turn/started",
            "params": {
                "threadId": "thread-1",
                "turn": {"id": "turn-1", "status": "inProgress"},
            },
        })
        usage = {
            "inputTokens": 20,
            "cachedInputTokens": 5,
            "cacheWriteInputTokens": 3,
            "outputTokens": 7,
            "reasoningOutputTokens": 2,
            "totalTokens": 27,
        }
        send({
            "method": "rawResponse/completed",
            "params": {
                "responseId": "response-1",
                "threadId": "thread-1",
                "turnId": "turn-1",
                "usage": usage,
            },
        })
        send({
            "method": "thread/tokenUsage/updated",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "tokenUsage": {"last": usage, "total": usage},
            },
        })
        send({
            "method": "item/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "item": {
                    "id": "item-1",
                    "type": "agentMessage",
                    "status": "completed",
                    "text": "MODEL_READY",
                },
            },
        })
        send({
            "method": "turn/completed",
            "params": {
                "threadId": "thread-1",
                "turn": {"id": "turn-1", "status": "completed"},
            },
        })
'''

FAKE_REROUTE_SERVER = r'''
import json
import sys

def send(message):
    print(json.dumps(message, separators=(",", ":")), flush=True)

for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method == "initialize":
        send({"id": message["id"], "result": {}})
    elif method == "thread/start":
        send({"id": message["id"], "result": {"thread": {"id": "thread-1"}}})
    elif method == "turn/start":
        send({
            "method": "model/rerouted",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "fromModel": "gpt-5.6-sol",
                "toModel": "another-model",
                "reason": "fixture",
            },
        })
'''

FAKE_MCP_APPROVAL_SERVER = r'''
import json
import sys

def send(message):
    print(json.dumps(message, separators=(",", ":")), flush=True)

for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method == "initialize":
        send({"id": message["id"], "result": {}})
    elif method == "initialized":
        pass
    elif method == "thread/start":
        send({"id": message["id"], "result": {"thread": {"id": "thread-1"}}})
    elif method == "turn/start":
        send({
            "id": 91,
            "method": "mcpServer/elicitation/request",
            "params": {
                "_meta": {"codex_approval_kind": "mcp_tool_call"},
                "message": "fixture",
                "mode": "form",
                "requestedSchema": {"properties": {}, "type": "object"},
                "serverName": "fixture",
                "threadId": "thread-1",
                "turnId": "turn-1",
            },
        })
        approval = json.loads(next(sys.stdin))
        if approval != {"id": 91, "result": {"action": "decline"}}:
            raise SystemExit(9)
        send({"id": message["id"], "result": {"turn": {"id": "turn-1"}}})
        send({
            "method": "item/completed",
            "params": {
                "threadId": "thread-1", "turnId": "turn-1",
                "item": {"id": "item-1", "type": "agentMessage", "text": "DONE"},
            },
        })
        send({
            "method": "turn/completed",
            "params": {
                "threadId": "thread-1",
                "turn": {"id": "turn-1", "status": "completed"},
            },
        })
'''


class CodexAppServerClientTest(unittest.TestCase):
    def test_exact_codex_package_can_use_a_content_identical_relocated_prefix(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package_root = root / "node_modules/@openai/codex"
            platform_root = root / "node_modules/@openai/codex-linux-x64"
            launcher = package_root / "bin/codex.js"
            native = (
                platform_root
                / "vendor/x86_64-unknown-linux-musl/bin/codex"
            )
            command = root / "bin/codex"
            launcher.parent.mkdir(parents=True)
            native.parent.mkdir(parents=True)
            command.parent.mkdir(parents=True)
            launcher.write_text(
                "#!/bin/sh\nprintf 'codex-cli 0.146.0\\n'\n",
                encoding="utf-8",
            )
            launcher.chmod(0o755)
            (package_root / "package.json").write_text(
                json.dumps({"name": "@openai/codex", "version": "0.146.0"}),
                encoding="utf-8",
            )
            (platform_root / "package.json").write_text(
                json.dumps(
                    {
                        "name": "@openai/codex",
                        "version": "0.146.0-linux-x64",
                    }
                ),
                encoding="utf-8",
            )
            native.write_bytes(b"exact native fixture")
            command.symlink_to(launcher)

            def digest(path: Path) -> str:
                import hashlib

                return hashlib.sha256(path.read_bytes()).hexdigest()

            lock = {
                "platform": {
                    "os": sys.platform,
                    "architecture": os.uname().machine,
                },
                "version_output": "codex-cli 0.146.0",
                "installation": {
                    "command_path": "/usr/bin/codex",
                    "launcher_path": "/usr/lib/node_modules/@openai/codex/bin/codex.js",
                    "package_json_path": "/usr/lib/node_modules/@openai/codex/package.json",
                    "platform_package_json_path": "/usr/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64/package.json",
                    "native_executable_path": "/usr/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex",
                    "package_name": "@openai/codex",
                    "package_version": "0.146.0",
                    "platform_package_name": "@openai/codex",
                    "platform_package_version": "0.146.0-linux-x64",
                    "launcher_sha256": digest(launcher),
                    "package_json_sha256": digest(package_root / "package.json"),
                    "platform_package_json_sha256": digest(
                        platform_root / "package.json"
                    ),
                    "native_executable_sha256": digest(native),
                },
            }
            resolved, identity, errors = _verify_installed_codex(
                str(command), lock
            )

        self.assertEqual(str(command), resolved)
        self.assertEqual([], errors)
        self.assertTrue(identity["relocated_from_frozen_installation_path"])
        self.assertEqual(str(platform_root / "package.json"), identity[
            "platform_package_json_path"
        ])

    def test_default_client_declines_mcp_elicitation_without_waiting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            server = root / "fake_mcp_server.py"
            server.write_text(FAKE_MCP_APPROVAL_SERVER, encoding="utf-8")
            result = run_app_server(
                [sys.executable, str(server)],
                cwd=root,
                environment=os.environ,
                prompt="Reply DONE",
                model="gpt-5.6-sol",
                reasoning_effort="high",
                yolo=False,
                writable_roots=[str(root)],
                journal_path=root / "app-server.jsonl",
                normalized_path=root / "run.jsonl",
                stderr_path=root / "run.stderr",
                final_path=root / "final.txt",
                timeout_seconds=10,
            )

        self.assertEqual(0, result["returncode"], result)
        self.assertEqual(1, result["approval_requests"])
        self.assertFalse(result["timed_out"])
    def test_missing_cache_write_usage_is_rejected_not_defaulted_to_zero(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "cacheWriteInputTokens"
        ):
            _token_usage(
                {
                    "inputTokens": 20,
                    "cachedInputTokens": 5,
                    "outputTokens": 7,
                    "reasoningOutputTokens": 2,
                    "totalTokens": 27,
                }
            )

    def test_runs_protocol_declines_approval_and_preserves_raw_usage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            server = root / "fake_server.py"
            server.write_text(FAKE_SERVER, encoding="utf-8")
            journal = root / "app-server.jsonl"
            normalized = root / "run.jsonl"
            stderr = root / "run.stderr"
            final = root / "final.txt"
            checkpoints = []

            def terminal_checkpoint(result):
                self.assertTrue(normalized.is_file())
                self.assertTrue(final.is_file())
                checkpoint_events = [
                    json.loads(line)
                    for line in normalized.read_text(encoding="utf-8").splitlines()
                ]
                self.assertEqual("turn.completed", checkpoint_events[-1]["type"])
                self.assertEqual("MODEL_READY", final.read_text(encoding="utf-8"))
                checkpoints.append(dict(result))

            result = run_app_server(
                [sys.executable, str(server)],
                cwd=root,
                environment=os.environ,
                prompt="Reply MODEL_READY",
                model="gpt-5.6-sol",
                reasoning_effort="high",
                yolo=False,
                writable_roots=[str(root)],
                journal_path=journal,
                normalized_path=normalized,
                stderr_path=stderr,
                final_path=final,
                timeout_seconds=10,
                terminal_checkpoint_handler=terminal_checkpoint,
            )

            self.assertEqual(0, result["returncode"], result)
            self.assertEqual(1, result["approval_requests"])
            self.assertFalse(result["timed_out"])
            self.assertEqual("", result["failure"])
            self.assertEqual("MODEL_READY", final.read_text(encoding="utf-8"))
            self.assertEqual(1, len(checkpoints))
            self.assertTrue(checkpoints[0]["terminal_checkpoint"])
            self.assertEqual(0, checkpoints[0]["returncode"])
            evidence = extract_app_server_usage(journal)
            self.assertEqual(1, len(evidence["raw_responses"]))
            self.assertEqual(
                3,
                evidence["raw_responses"][0]["usage"]["cache_write_tokens"],
            )
            events = [
                json.loads(line)
                for line in normalized.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual("turn.completed", events[-1]["type"])
            self.assertEqual(3, events[-1]["usage"]["cache_write_tokens"])
            entries = [
                json.loads(line)
                for line in journal.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                list(range(1, len(entries) + 1)),
                [entry["ordinal"] for entry in entries],
            )
            declined = [
                entry["message"]
                for entry in entries
                if entry["direction"] == "client_to_server"
                and entry["message"].get("id") == 90
            ]
            self.assertEqual(
                [{"id": 90, "result": {"decision": "decline"}}],
                declined,
            )

    def test_model_reroute_is_preserved_and_fails_the_child(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            server = root / "fake_reroute_server.py"
            server.write_text(FAKE_REROUTE_SERVER, encoding="utf-8")
            result = run_app_server(
                [sys.executable, str(server)],
                cwd=root,
                environment=os.environ,
                prompt="Reply MODEL_READY",
                model="gpt-5.6-sol",
                reasoning_effort="high",
                yolo=False,
                writable_roots=[str(root)],
                journal_path=root / "app-server.jsonl",
                normalized_path=root / "run.jsonl",
                stderr_path=root / "run.stderr",
                final_path=root / "final.txt",
                timeout_seconds=10,
            )
            self.assertEqual(1, result["returncode"])
            self.assertEqual(
                ["model/rerouted"],
                [
                    item["method"]
                    for item in result["invalidating_notifications"]
                ],
            )
            self.assertIn(
                "invalidating Codex model notification",
                result["failure"],
            )


if __name__ == "__main__":
    unittest.main()
