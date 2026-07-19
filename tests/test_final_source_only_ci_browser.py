#!/usr/bin/env python3
"""Focused regressions for pinned source-only browser CI and release binding."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from final_source_only_release import (  # noqa: E402
    PACKAGED_PATHS,
    environment_image_identity_errors,
    release_descriptor_errors,
)
import source_only_ci  # noqa: E402
from source_only_ci import (  # noqa: E402
    BASE_COMMIT,
    BROWSER_COMMAND,
    BROWSER_SPEC_RELATIVE,
    EXPECTED_CHROMIUM_EXECUTABLE,
    EXPECTED_CHROMIUM_SHA256,
    EXPECTED_CHROMIUM_VERSION,
    EXPECTED_NODE_VERSION,
    EXPECTED_NPM_VERSION,
    EXPECTED_PYTHON_VERSION,
    REQUIRED_COMMAND_NAMES,
    ROUTING_NONCE,
    SOURCE_ONLY_USERSPACE_IMAGE,
    SOURCE_ONLY_USERSPACE_IMAGE_DIGEST,
    TASK_ID,
    browser_receipt_errors,
    canonical_bytes,
    command_plan,
    command_plan_errors,
    command_plan_identity,
    environment_identity_errors,
    git_safe_environment,
    playwright_result_summary,
    sha256_file,
    source_only_receipt_errors,
    workflow_image_errors,
    workflow_userspace_images,
)
from target_replay import _runtime_environment  # noqa: E402


SOURCE = {
    "commit": "1" * 40,
    "tree": "2" * 40,
    "worktree_clean": True,
}
SHA = "3" * 64


def valid_environment() -> dict:
    return {
        "source_only_userspace_image": SOURCE_ONLY_USERSPACE_IMAGE,
        "source_only_userspace_image_digest":
            SOURCE_ONLY_USERSPACE_IMAGE_DIGEST,
        "source_only_executed_image": SOURCE_ONLY_USERSPACE_IMAGE,
        "source_only_distribution": "Ubuntu 24.04.4 LTS",
        "source_only_distribution_id": "ubuntu",
        "source_only_distribution_version": "24.04",
        "source_only_glibc":
            "ldd (Ubuntu GLIBC 2.39-0ubuntu8.7) 2.39",
        "python_version": EXPECTED_PYTHON_VERSION,
        "python_executable": "/opt/python/bin/python",
        "python_executable_sha256": SHA,
        "node_version": EXPECTED_NODE_VERSION,
        "node_executable": "/opt/node/bin/node",
        "node_executable_sha256": SHA,
        "npm_version": EXPECTED_NPM_VERSION,
        "npm_entrypoint": "/opt/node/lib/node_modules/npm/bin/npm-cli.js",
        "npm_entrypoint_sha256": SHA,
        "chromium_version": EXPECTED_CHROMIUM_VERSION,
        "chromium_executable": EXPECTED_CHROMIUM_EXECUTABLE,
        "chromium_executable_sha256": EXPECTED_CHROMIUM_SHA256,
    }


def valid_browser_receipt() -> dict:
    return {
        "schema_id": "source-only-browser-receipt-current",
        "task_id": TASK_ID,
        "routing_nonce": ROUTING_NONCE,
        "status": "passed",
        "source": dict(SOURCE),
        "command": list(BROWSER_COMMAND),
        "command_exit_code": 0,
        "browser_spec": {
            "path": BROWSER_SPEC_RELATIVE,
            "bytes": (
                ROOT / BROWSER_SPEC_RELATIVE
            ).stat().st_size,
            "sha256": sha256_file(ROOT / BROWSER_SPEC_RELATIVE),
        },
        "source_only_userspace_image": SOURCE_ONLY_USERSPACE_IMAGE,
        "source_only_userspace_image_digest":
            SOURCE_ONLY_USERSPACE_IMAGE_DIGEST,
        "source_only_distribution": "Ubuntu 24.04.4 LTS",
        "source_only_glibc":
            "ldd (Ubuntu GLIBC 2.39-0ubuntu8.7) 2.39",
        "chromium_version": EXPECTED_CHROMIUM_VERSION,
        "chromium_executable": EXPECTED_CHROMIUM_EXECUTABLE,
        "chromium_executable_sha256": EXPECTED_CHROMIUM_SHA256,
        "errors": [],
        "browser_test_count": 1,
        "passed_test_count": 1,
        "failed_test_count": 0,
        "flaky_test_count": 0,
        "skipped_test_count": 0,
        "executed_test_files": [BROWSER_SPEC_RELATIVE],
        "result": {
            "path": "source-only-browser-result.json",
            "bytes": 100,
            "sha256": SHA,
        },
        "validation_errors": [],
    }


def valid_source_receipt(browser: dict | None = None) -> dict:
    browser = browser or valid_browser_receipt()
    plan = command_plan(Path("/evidence/source-only-methodology.json"))
    plan_identity = command_plan_identity(plan, Path("/evidence"))
    return {
        "schema_id": "source-only-ci-receipt-current",
        "task_id": TASK_ID,
        "routing_nonce": ROUTING_NONCE,
        "status": "passed",
        "execution_stratum": "source-only",
        "python_support": ">=3.14,<3.15",
        "plain_git_checkout_compatible": True,
        "canonical_target_required": False,
        "bench_target_repo_path_present": False,
        "bubblewrap_required": False,
        "privileged_namespaces_required": False,
        "canonical_output_directories_required": False,
        "builder_home_required": False,
        "builder_caches_required": False,
        "packaged_replay_runtimes_required": False,
        "artifact_backed_target_evidence_imported": False,
        "external_executable_command_tests_use_injection": True,
        "fixture": {},
        "source": dict(SOURCE),
        "source_identity_unchanged": True,
        "workflow_definition": ".github/workflows/ci.yml",
        "workflow_definition_sha256": sha256_file(
            ROOT / ".github/workflows/ci.yml"
        ),
        "command_plan": plan_identity,
        "commands": [
            {
                **row,
                "status": "passed",
                "exit_code": 0,
            }
            for row in plan_identity["commands"]
        ],
        "command_count": len(plan),
        "test_counts": {
            "python_unit": 1,
            "vitest": 1,
            "playwright": 1,
        },
        "source_only_browser_receipt": {
            "path": "source-only-browser-receipt.json",
            "bytes": len(canonical_bytes(browser)),
            "sha256": hashlib.sha256(
                canonical_bytes(browser)
            ).hexdigest(),
            "status": "passed",
        },
        "duration_seconds": 1.0,
        "validation_errors": [],
        **valid_environment(),
    }


def valid_descriptor() -> dict:
    artifact = {"path": "", "bytes": 1, "sha256": SHA}
    descriptor = {
        "schema_id": "final-source-only-release-descriptor-current",
        "status": "passed",
        "task_id": TASK_ID,
        "routing_nonce": ROUTING_NONCE,
        "source_commit": SOURCE["commit"],
        "source_tree": SOURCE["tree"],
        "source_only_userspace": {
            "image": SOURCE_ONLY_USERSPACE_IMAGE,
            "digest": SOURCE_ONLY_USERSPACE_IMAGE_DIGEST,
        },
        "chromium_identity": {
            "version": "Google Chrome for Testing 149.0.7827.55",
            "executable":
                "/ms-playwright/chromium-1228/chrome-linux64/chrome",
            "sha256": SHA,
        },
        "source_only_ci_status": "passed",
        "source_only_browser_status": "passed",
        "source_only_browser_result": {
            "path": "source-only-browser-result.json",
            "bytes": 1,
            "sha256": SHA,
        },
        "workflow_definition_sha256": SHA,
        "source_only_command_plan_sha256": SHA,
        "debian_12_exact_final_status": "passed",
        "debian_13_exact_final_status": "passed",
        "portability_status": "passed",
        "final_outer": {
            "filename": "final-outer.zip",
            "bytes": 1,
            "sha256": SHA,
        },
        "final_inner": {
            "filename": "review-handoff.zip",
            "bytes": 1,
            "sha256": SHA,
        },
        "inner_handoff_source_identity": {
            "commit": SOURCE["commit"],
            "tree": SOURCE["tree"],
        },
        "outer_delivery_source_identity": {
            "commit": SOURCE["commit"],
            "tree": SOURCE["tree"],
        },
    }
    for name, path in PACKAGED_PATHS.items():
        descriptor[name] = {**artifact, "path": path}
    return descriptor


class SourceOnlyCommandPlanTest(unittest.TestCase):
    def test_real_browser_spec_is_required(self) -> None:
        plan = command_plan(Path("/tmp/source-only-methodology.json"))
        self.assertEqual([], command_plan_errors(plan))
        self.assertEqual(
            BROWSER_COMMAND,
            dict(plan)["dashboard_browser"],
        )

        removed = [
            row for row in plan if row[0] != "dashboard_browser"
        ]
        self.assertTrue(
            any(
                "command names or order" in error
                for error in command_plan_errors(removed)
            )
        )

        mocked = copy.deepcopy(plan)
        index = next(
            index
            for index, row in enumerate(mocked)
            if row[0] == "dashboard_browser"
        )
        mocked[index] = (
            "dashboard_browser",
            ["python", "-c", "print('mock browser pass')"],
        )
        self.assertTrue(
            any(
                "must execute npm run test:browser" in error
                for error in command_plan_errors(mocked)
            )
        )

    def test_browser_spec_must_be_the_executed_test(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = Path(temporary) / "playwright.json"
            result.write_text(
                json.dumps(
                    {
                        "stats": {
                            "expected": 1,
                            "unexpected": 0,
                            "flaky": 0,
                            "skipped": 0,
                        },
                        "suites": [
                            {
                                "specs": [
                                    {
                                        "file": "tests/mock.spec.ts",
                                        "tests": [{"results": []}],
                                    }
                                ]
                            }
                        ],
                        "errors": [],
                    }
                ),
                encoding="utf-8",
            )
            summary = playwright_result_summary(result)
        self.assertEqual("failed", summary["status"])
        self.assertTrue(
            any(
                "browser.spec.ts" in error
                for error in summary["errors"]
            )
        )

    def test_playwright_test_dir_relative_spec_is_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = Path(temporary) / "playwright.json"
            result.write_text(
                json.dumps(
                    {
                        "stats": {
                            "expected": 1,
                            "unexpected": 0,
                            "flaky": 0,
                            "skipped": 0,
                        },
                        "suites": [
                            {
                                "specs": [
                                    {
                                        "file": "browser.spec.ts",
                                        "tests": [{"results": []}],
                                    }
                                ]
                            }
                        ],
                        "errors": [],
                    }
                ),
                encoding="utf-8",
            )
            summary = playwright_result_summary(result)
        self.assertEqual("passed", summary["status"], summary)
        self.assertEqual(
            [BROWSER_SPEC_RELATIVE],
            summary["executed_test_files"],
        )


class PinnedUserspaceWorkflowTest(unittest.TestCase):
    def test_workflow_pins_host_unzip_package_and_package_hash(
        self,
    ) -> None:
        source = (
            ROOT / ".github/workflows/ci.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'SOURCE_ONLY_UNZIP_PACKAGE: "unzip=6.0-28ubuntu4.1"',
            source,
        )
        self.assertIn(
            "SOURCE_ONLY_UNZIP_PACKAGE_SHA256: "
            '"a505b9d491386167bd8e14e3383315a4a7d6539e4406745901ccf009a7988271"',
            source,
        )
        self.assertIn(
            'apt-get download "$SOURCE_ONLY_UNZIP_PACKAGE"',
            source,
        )
        self.assertIn(
            '"$SOURCE_ONLY_UNZIP_PACKAGE_SHA256" "${packages[0]}"',
            source,
        )
        self.assertNotIn("apt-get install unzip", source)

    def test_workflow_and_runner_use_the_same_digest_pinned_image(
        self,
    ) -> None:
        self.assertEqual([], workflow_image_errors())
        images = workflow_userspace_images()
        self.assertGreaterEqual(len(images), 2)
        self.assertEqual({SOURCE_ONLY_USERSPACE_IMAGE}, set(images))
        self.assertIn("@sha256:", SOURCE_ONLY_USERSPACE_IMAGE)

        identity = valid_environment()
        self.assertEqual([], environment_identity_errors(identity))
        identity["source_only_executed_image"] = (
            "mcr.microsoft.com/playwright:other@sha256:" + "4" * 64
        )
        self.assertIn(
            "workflow and executed image differ",
            environment_identity_errors(identity),
        )

        identity = valid_environment()
        identity["source_only_userspace_image"] = (
            "mcr.microsoft.com/playwright:v1.61.1-noble"
        )
        self.assertTrue(environment_identity_errors(identity))

    def test_git_checkout_trust_does_not_require_builder_home(
        self,
    ) -> None:
        environment = git_safe_environment(
            {"HOME": "/unusable-builder-home"}
        )
        self.assertEqual("/dev/null", environment["GIT_CONFIG_GLOBAL"])
        self.assertEqual("1", environment["GIT_CONFIG_COUNT"])
        self.assertEqual(
            "safe.directory", environment["GIT_CONFIG_KEY_0"]
        )
        self.assertEqual(
            str(ROOT), environment["GIT_CONFIG_VALUE_0"]
        )

    def test_workflow_rejects_a_mutable_or_different_container(
        self,
    ) -> None:
        source = (
            ROOT / ".github/workflows/ci.yml"
        ).read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temporary:
            workflow = Path(temporary) / "ci.yml"
            workflow.write_text(
                source.replace(
                    SOURCE_ONLY_USERSPACE_IMAGE,
                    "mcr.microsoft.com/playwright:v1.61.1-noble",
                ),
                encoding="utf-8",
            )
            with patch("source_only_ci.WORKFLOW_PATH", workflow):
                self.assertTrue(workflow_image_errors())

            different = (
                "mcr.microsoft.com/playwright:v1.61.1-noble@sha256:"
                + "4" * 64
            )
            workflow.write_text(
                source.replace(
                    f'      image: "{SOURCE_ONLY_USERSPACE_IMAGE}"',
                    f'      image: "{different}"',
                    1,
                ),
                encoding="utf-8",
            )
            with patch("source_only_ci.WORKFLOW_PATH", workflow):
                self.assertTrue(workflow_image_errors())


class SourceOnlyReceiptTest(unittest.TestCase):
    def test_vitest_count_accepts_ansi_colored_ci_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stdout = root / "dashboard-unit.stdout.log"
            stderr = root / "dashboard-unit.stderr.log"
            stdout.write_text(
                "\x1b[2m      Tests \x1b[22m "
                "\x1b[1m\x1b[32m16 passed\x1b[39m\x1b[22m (16)\n",
                encoding="utf-8",
            )
            stderr.write_text("", encoding="utf-8")
            row = {
                "stdout": {"path": stdout.name},
                "stderr": {"path": stderr.name},
            }
            self.assertEqual(
                16,
                source_only_ci._test_count(
                    "dashboard_unit", row, root
                ),
            )

    def test_receipt_requires_userspace_runtime_and_browser_identity(
        self,
    ) -> None:
        browser = valid_browser_receipt()
        receipt = valid_source_receipt(browser)
        self.assertEqual(
            [], source_only_receipt_errors(receipt, browser)
        )
        for field in (
            "source_only_userspace_image_digest",
            "chromium_version",
            "chromium_executable_sha256",
        ):
            mutated = copy.deepcopy(receipt)
            mutated.pop(field)
            with self.subTest(field=field):
                self.assertTrue(
                    source_only_receipt_errors(mutated, browser)
                )

    def test_receipt_recomputes_plan_and_cross_binds_browser(
        self,
    ) -> None:
        browser = valid_browser_receipt()
        receipt = valid_source_receipt(browser)
        receipt["command_plan"]["commands"][-1]["command"] = [
            "python",
            "-c",
            "print('mock pass')",
        ]
        receipt["command_plan"]["sha256"] = hashlib.sha256(
            canonical_bytes(receipt["command_plan"]["commands"])
        ).hexdigest()
        self.assertTrue(
            any(
                "command-plan commands differ" in error
                for error in source_only_receipt_errors(
                    receipt, browser
                )
            )
        )

        browser = valid_browser_receipt()
        receipt = valid_source_receipt(browser)
        browser["source"]["tree"] = "4" * 40
        self.assertTrue(
            any(
                "CI/browser source identities differ" in error
                for error in source_only_receipt_errors(
                    receipt, browser
                )
            )
        )

    def test_source_only_receipt_rejects_artifact_backed_target_evidence(
        self,
    ) -> None:
        browser = valid_browser_receipt()
        receipt = valid_source_receipt(browser)
        receipt["artifact_backed_target_evidence_imported"] = True
        self.assertTrue(
            any(
                "artifact-backed evidence" in error
                for error in source_only_receipt_errors(
                    receipt, browser
                )
            )
        )

    def test_receipts_validate_against_current_schemas(self) -> None:
        browser = valid_browser_receipt()
        source = valid_source_receipt(browser)
        for schema_name, value in (
            ("source-only-browser-receipt.schema.json", browser),
            ("source-only-ci-receipt.schema.json", source),
        ):
            schema = json.loads(
                (ROOT / "schemas" / schema_name).read_text(
                    encoding="utf-8"
                )
            )
            with self.subTest(schema=schema_name):
                Draft202012Validator(schema).validate(value)


class ArtifactBackedBrowserGuardTest(unittest.TestCase):
    def test_artifact_browser_uses_packaged_chromium(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            environment = _runtime_environment(
                work, work / "benchmark"
            )
        self.assertEqual(
            str(work / "runtime/chromium/chromium"),
            environment["BENCH_CHROMIUM_EXECUTABLE"],
        )
        self.assertNotEqual(
            "/usr/bin/chromium",
            environment["BENCH_CHROMIUM_EXECUTABLE"],
        )


class ReleaseDescriptorGuardTest(unittest.TestCase):
    def test_old_task_and_stale_source_are_rejected(self) -> None:
        descriptor = valid_descriptor()
        self.assertEqual([], release_descriptor_errors(descriptor))

        old_task = copy.deepcopy(descriptor)
        old_task["task_id"] = "final-source-reproducible-offline-replay"
        self.assertTrue(
            any(
                "task ID" in error
                for error in release_descriptor_errors(old_task)
            )
        )

        stale = copy.deepcopy(descriptor)
        stale["source_commit"] = BASE_COMMIT
        stale["inner_handoff_source_identity"]["commit"] = BASE_COMMIT
        stale["outer_delivery_source_identity"]["commit"] = BASE_COMMIT
        self.assertTrue(
            any(
                "stale source commit" in error
                for error in release_descriptor_errors(stale)
            )
        )

    def test_environment_receipt_must_match_inspected_digest(self) -> None:
        actual = (
            "sha256:"
            "95416caefd1ffd129a991b4b8432862144c9386a64919f93ec14326b0986042c"
        )
        receipt = {
            "requested_image_reference":
                "ckg-replay-portability:debian13",
            "repo_digest": None,
            "image_id": actual,
            "inspected_digest": actual,
            "execution_image_reference": actual,
            "image_digest": actual,
            "image_identity_match": True,
        }
        self.assertEqual(
            [], environment_image_identity_errors(receipt)
        )
        receipt["image_digest"] = (
            "sha256:"
            "95416cae1a21c7c393cd39ee0356a1c17a38aad59eb08b06649147a92623c1ff"
        )
        self.assertTrue(
            any(
                "differs from inspection" in error
                for error in environment_image_identity_errors(receipt)
            )
        )


if __name__ == "__main__":
    unittest.main()
