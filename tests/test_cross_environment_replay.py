import hashlib
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from cross_environment_release import (  # noqa: E402
    build_detached_final_receipts,
    build_split_delivery,
    final_inner_identity,
    final_outer_identity,
    release_command_exit_code,
    validate_bootstrap_launcher,
    validate_detached_final_binding,
    validate_failure_preservation,
    validate_namespace_capability_receipt,
    validate_network_namespace_receipt,
    validate_source_generated_equality,
    validate_split_delivery,
)
from replay_rootfs import (  # noqa: E402
    _skip_package_member,
    required_rootfs_paths,
)
from safe_archive import inspect_tree  # noqa: E402
from target_replay import (  # noqa: E402
    REQUIRED_PACKAGED_SEMANTIC_RUNTIMES,
    ROOTFS_TOOL_PATHS,
    _copytree,
    _dashboard_node_modules_ignore,
    _generic_runtime_resolution,
    _release_fault_injection,
    _replay_script,
    _rootfs_artifacts,
    _validate_runtime_lock_shape,
    validate_namespace_root_boundary,
    validate_generated_script,
)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def portability_matrix(identity: dict, inner: dict) -> dict:
    generic = [
        "bash",
        "git",
        "ip",
        "mount",
        "tar",
        "unshare",
        "unzip",
        "zstd",
    ]
    return {
        "status": "passed",
        "final_outer": dict(identity),
        "final_inner": dict(inner),
        "environments": [
            {
                "status": "passed",
                "image_digest": "debian12@sha256:" + ("1" * 64),
                "host_userspace_distribution": "debian 12",
                "host_userspace_glibc": "2.36",
                "host_kernel": "Linux fixture",
                "packaged_bootstrap_glibc": "2.36",
                "packaged_replay_rootfs_glibc": "2.36",
                "namespace_mode": "privileged",
                "replay_exit_code": 0,
                "network_status": "passed",
                "final_outer": dict(identity),
                "final_inner": dict(inner),
                "verifier_receipt_final_outer_sha256":
                    identity["sha256"],
                "host_generic_tool_hashes_different_from_builder": [],
            },
            {
                "status": "passed",
                "image_digest": "debian13@sha256:" + ("2" * 64),
                "host_userspace_distribution": "debian 13",
                "host_userspace_glibc": "2.41",
                "host_kernel": "Linux fixture",
                "packaged_bootstrap_glibc": "2.36",
                "packaged_replay_rootfs_glibc": "2.36",
                "namespace_mode": "privileged",
                "replay_exit_code": 0,
                "network_status": "passed",
                "final_outer": dict(identity),
                "final_inner": dict(inner),
                "verifier_receipt_final_outer_sha256":
                    identity["sha256"],
                "host_generic_tool_hashes_different_from_builder":
                    generic,
            },
        ],
    }


class BootstrapBoundaryTests(unittest.TestCase):
    def test_production_outer_launcher_has_tiny_sanitized_boundary(self) -> None:
        source = (ROOT / "scripts/independent_verifier.sh").read_text(
            encoding="utf-8"
        )
        result = validate_bootstrap_launcher(source)
        self.assertEqual("passed", result["status"], result["errors"])
        self.assertTrue(result["environment_sanitized"])
        self.assertTrue(result["packaged_loader_invocation"])
        self.assertEqual(
            [],
            result["forbidden_host_semantic_utilities"],
        )

    def test_global_packaged_library_path_is_rejected(self) -> None:
        source = (ROOT / "scripts/independent_verifier.sh").read_text(
            encoding="utf-8"
        )
        fault = source.replace(
            "unset LD_LIBRARY_PATH",
            "export LD_LIBRARY_PATH=\"$STAGE/inner/runtime/"
            "bootstrap-python/system-libs\"",
            1,
        )
        result = validate_bootstrap_launcher(fault)
        self.assertEqual("failed", result["status"])
        self.assertIn(
            "global packaged LD_LIBRARY_PATH",
            result["errors"],
        )

    def test_bootstrap_does_not_call_host_awk_or_sha256sum(self) -> None:
        source = (ROOT / "scripts/independent_verifier.sh").read_text(
            encoding="utf-8"
        )
        for utility in ("awk", "sha256sum"):
            fault = source + f"\n{utility} --version\n"
            result = validate_bootstrap_launcher(fault)
            self.assertEqual("failed", result["status"])
            self.assertIn(
                utility,
                result["forbidden_host_semantic_utilities"],
            )


class PackagedRuntimeBoundaryTests(unittest.TestCase):
    def test_dashboard_runtime_excludes_volatile_vitest_cache(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "node_modules"
            cache = source / ".vite/vitest/key"
            cache.mkdir(parents=True)
            (source / "stable.js").write_text(
                "export const stable = true;\n",
                encoding="utf-8",
            )
            (cache / "results.json").write_text(
                '{"duration": 1}\n', encoding="utf-8"
            )
            first = root / "first/node_modules"
            _copytree(
                source,
                first,
                ignore=_dashboard_node_modules_ignore,
            )
            (cache / "results.json").write_text(
                '{"duration": 999}\n', encoding="utf-8"
            )
            second = root / "second/node_modules"
            _copytree(
                source,
                second,
                ignore=_dashboard_node_modules_ignore,
            )
            self.assertEqual(inspect_tree(first), inspect_tree(second))
            self.assertFalse((first / ".vite").exists())

    def test_replay_syntax_validation_uses_packaged_bash_without_sh(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            binary_root = Path(temporary)
            os.symlink("/usr/bin/bash", binary_root / "bash")
            with mock.patch.dict(
                os.environ,
                {"PATH": str(binary_root)},
                clear=False,
            ):
                result = validate_generated_script(_replay_script())
        self.assertEqual("passed", result["status"], result["errors"])

    def _lock(self, root: Path) -> dict:
        tools = {}
        for name in (
            "posix_sh",
            "bash",
            "git",
            "ip",
            "mount",
            "tar",
            "unshare",
            "unzip",
            "zstd",
            "sha256sum",
            "awk",
        ):
            executable = (
                "dash" if name == "posix_sh" else name
            )
            path = root / f"runtime/replay-rootfs/usr/bin/{executable}"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"packaged-{name}\n".encode())
            tools[name] = {
                "role": "packaged_semantic_runtime",
                "path": path.relative_to(root).as_posix(),
                "execution_path": (
                    "/bin/sh" if name == "posix_sh"
                    else f"/usr/bin/{name}"
                ),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "version": f"fixture {name}",
                "validation_mode": "exact_identity",
            }
        for name in (
            "java",
            "javac",
            "node",
            "npm",
            "chromium",
            "python",
            "maven",
            "maven_wrapper",
        ):
            path = root / f"runtime/{name}"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"packaged-{name}\n".encode())
            tools[name] = {
                "role": "packaged_semantic_runtime",
                "path": path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "version": f"fixture {name}",
                "validation_mode": "exact_identity",
            }
        return {
            "schema_id": "offline-runtime-lock-current",
            "python_support": {
                "requires_python": ">=3.14,<3.15",
                "runtime": "CPython 3.14",
            },
            "platform": {"system": "Linux", "architecture": "x86_64"},
            "host_bootstrap_prerequisites": [
                {
                    "name": "unzip",
                    "role": "host_bootstrap_prerequisite",
                    "path": "$PATH/unzip",
                    "version": "capability-tested",
                    "validation_mode": "capability",
                }
            ],
            "kernel_capabilities": [
                {
                    "name": "user_namespace",
                    "role": "kernel_capability",
                    "path": "/proc/self/ns/user",
                    "version": "runtime-measured",
                    "validation_mode": "capability",
                }
            ],
            "packaged_semantic_runtime": tools,
            "archive_manifests": {},
            "shared_library_closure": {},
            "replay_rootfs": {
                "manifest_root": "a" * 64,
                "entry_count": 1,
                "lock_sha256": "b" * 64,
                "source_image_digest": "debian@sha256:" + ("d" * 64),
            },
            "namespace_launcher": {
                "role": "packaged_semantic_runtime",
                "path": "target/namespace-launcher",
                "sha256": "c" * 64,
                "version": "namespace-launcher-v1",
                "validation_mode": "exact_identity",
            },
        }

    def test_host_generic_hashes_are_irrelevant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows, errors = _generic_runtime_resolution(
                self._lock(root), root
            )
            self.assertEqual([], errors)
            self.assertTrue(all(row["matches_lock"] for row in rows.values()))
            self.assertTrue(
                all(
                    row["role"] == "packaged_semantic_runtime"
                    for row in rows.values()
                )
            )

    def test_missing_and_mutated_packaged_tools_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock = self._lock(root)
            missing = (
                root / lock["packaged_semantic_runtime"]["git"]["path"]
            )
            missing.unlink()
            _, errors = _generic_runtime_resolution(lock, root)
            self.assertIn("packaged semantic tool missing: git", errors)
            missing.write_bytes(b"mutated\n")
            _, errors = _generic_runtime_resolution(lock, root)
            self.assertIn(
                "packaged semantic tool identity mismatch: git",
                errors,
            )
            lock["packaged_semantic_runtime"]["git"]["path"] = (
                "/usr/bin/git"
            )
            _, errors = _generic_runtime_resolution(lock, root)
            self.assertIn(
                "unbundled semantic tool path is forbidden: git",
                errors,
            )

    def test_runtime_lock_requires_role_and_validation_classification(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lock = self._lock(Path(temporary))
            self.assertEqual([], _validate_runtime_lock_shape(lock))
            del lock["packaged_semantic_runtime"]["bash"]["role"]
            self.assertIn(
                "runtime boundary entry classification is incomplete: bash",
                _validate_runtime_lock_shape(lock),
            )

    def test_rootfs_contract_contains_every_semantic_generic_tool(self) -> None:
        required = required_rootfs_paths()
        for name in (
            "posix_sh",
            "bash",
            "git",
            "ip",
            "mount",
            "tar",
            "unshare",
            "unzip",
            "zstd",
            "sha256sum",
            "awk",
        ):
            self.assertIn(name, required)

    def test_runtime_contract_names_every_semantic_runtime(self) -> None:
        self.assertEqual(
            {
                "java",
                "javac",
                "node",
                "npm",
                "chromium",
                "python",
                "maven",
                "maven_wrapper",
                "posix_sh",
                "bash",
                "git",
                "ip",
                "mount",
                "tar",
                "unshare",
                "unzip",
                "zstd",
                "sha256sum",
                "awk",
            },
            REQUIRED_PACKAGED_SEMANTIC_RUNTIMES,
        )
        source = (ROOT / "scripts/target_replay.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'manifests["maven-repository"]["archive_sha256"]',
            source,
        )
        self.assertNotIn(
            'manifests["maven-home"]["archive_sha256"]',
            source,
        )

    def test_packaging_rejects_missing_packaged_shebang_interpreter(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            rootfs = Path(temporary) / "rootfs"
            for name, execution_path in ROOTFS_TOOL_PATHS.items():
                if name == "posix_sh":
                    continue
                path = rootfs.joinpath(*Path(execution_path).parts[1:])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(f"fixture-{name}\n".encode())
            loader = rootfs / "usr/lib64/ld-linux-x86-64.so.2"
            loader.parent.mkdir(parents=True, exist_ok=True)
            loader.write_bytes(b"fixture-loader\n")
            receipt = {
                "source_image_digest": (
                    "debian@sha256:" + ("d" * 64)
                ),
                "packages": [],
            }
            with self.assertRaisesRegex(
                ValueError,
                "rootfs semantic tool is missing: /bin/sh",
            ):
                _rootfs_artifacts(rootfs, receipt)

            dash = rootfs / "usr/bin/dash"
            dash.parent.mkdir(parents=True, exist_ok=True)
            dash.write_bytes(b"fixture-dash\n")
            (rootfs / "bin").symlink_to("usr/bin")
            (rootfs / "usr/bin/sh").symlink_to("dash")
            _, lock, _ = _rootfs_artifacts(rootfs, receipt)
            self.assertEqual(
                "/bin/sh",
                lock["tools"]["posix_sh"]["execution_path"],
            )
            self.assertEqual(
                hashlib.sha256(dash.read_bytes()).hexdigest(),
                lock["tools"]["posix_sh"]["sha256"],
            )

    def test_rootfs_prunes_only_optional_casefold_collisions(self) -> None:
        self.assertTrue(
            _skip_package_member(
                "/usr/share/perl/5.36.0/pod/perldiag.pod"
            )
        )
        self.assertTrue(
            _skip_package_member(
                "/usr/lib/x86_64-linux-gnu/perl/5.36.0/"
                "sys/socket.ph"
            )
        )
        self.assertFalse(
            _skip_package_member(
                "/usr/share/perl/5.36.0/Pod/Simple.pm"
            )
        )
        self.assertFalse(
            _skip_package_member(
                "/usr/lib/x86_64-linux-gnu/perl/5.36.0/"
                "Sys/Hostname.pm"
            )
        )

    def test_chromium_packaging_has_no_host_font_dependency(self) -> None:
        source = (ROOT / "scripts/target_replay.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn(
            'source_fonts = Path("/usr/share/fonts',
            source,
        )
        self.assertIn(
            "content-addressed Chromium fonts are missing",
            source,
        )

    def test_generated_package_has_an_explicit_larger_bounded_manifest(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "fixture"
            root.mkdir()
            (root / "two-bytes").write_bytes(b"xx")
            with self.assertRaisesRegex(
                ValueError, "source expanded-size limit"
            ):
                inspect_tree(root, max_total_bytes=1)
            rows = inspect_tree(root, max_total_bytes=2)
            self.assertEqual(2, sum(row["bytes"] for row in rows))


class NamespaceAndNetworkReceiptTests(unittest.TestCase):
    def _namespace(self, mode: str = "rootless") -> dict:
        return {
            "schema_id": "namespace-capability-receipt-current",
            "status": "passed",
            "mode": mode,
            "effective_uid": 0,
            "effective_gid": 0,
            "uid_map": "0 65534 1",
            "gid_map": "0 65534 1",
            "new_user_namespace": mode == "rootless",
            "new_mount_namespace": True,
            "new_network_namespace": True,
            "new_pid_namespace": True,
            "mount_receipt": {
                "package": True,
                "work": True,
                "evidence": True,
                "proc": True,
                "empty_resolver": True,
            },
            "capability_check": {
                "rootless_user_namespace": mode == "rootless",
                "privileged_cap_sys_admin": mode == "privileged",
                "privileged_cap_net_admin": mode == "privileged",
            },
            "launcher_sha256": "d" * 64,
        }

    def _network(self) -> dict:
        return {
            "schema_id": "network-namespace-receipt-current",
            "status": "passed",
            "new_namespace": True,
            "default_external_route_present": False,
            "dns_configuration": {
                "host_dns_used": False,
                "sha256": hashlib.sha256(b"").hexdigest(),
            },
            "external_tcp_probe": {"succeeded": False},
            "external_dns_probe": {"succeeded": False},
            "loopback_probe": {"succeeded": True},
            "network_enabled": False,
            "network_enabled_derivation": {
                "expression": "tcp or dns or external-default-route"
            },
        }

    def test_rootless_and_privileged_capabilities_are_explicit(self) -> None:
        for mode in ("rootless", "privileged"):
            receipt = self._namespace(mode)
            self.assertEqual(
                "passed",
                validate_namespace_capability_receipt(receipt)["status"],
            )
        missing_rootless = self._namespace()
        missing_rootless["capability_check"][
            "rootless_user_namespace"
        ] = False
        self.assertEqual(
            "failed",
            validate_namespace_capability_receipt(missing_rootless)["status"],
        )
        missing_privileged = self._namespace("privileged")
        missing_privileged["capability_check"][
            "privileged_cap_sys_admin"
        ] = False
        self.assertEqual(
            "failed",
            validate_namespace_capability_receipt(missing_privileged)[
                "status"
            ],
        )

    def test_namespace_root_is_pivoted_for_runtime_mount_discovery(
        self,
    ) -> None:
        source = (
            ROOT / "scripts/replay_namespace_launcher.c"
        ).read_text(encoding="utf-8")
        positive = validate_namespace_root_boundary(source)
        self.assertEqual("passed", positive["status"], positive["errors"])
        self.assertTrue(positive["pivot_root"])
        self.assertTrue(positive["old_root_detached"])

        missing_pivot = source.replace(
            "SYS_pivot_root", "SYS_pivot_root_removed"
        )
        negative = validate_namespace_root_boundary(missing_pivot)
        self.assertEqual("failed", negative["status"])
        self.assertTrue(
            any("SYS_pivot_root" in error for error in negative["errors"])
        )

        chroot_only = missing_pivot.replace(
            "pivot_to_rootfs(rootfs);", "chroot(rootfs);"
        )
        negative = validate_namespace_root_boundary(chroot_only)
        self.assertEqual("failed", negative["status"])
        self.assertIn(
            "chroot-only namespace root boundary is forbidden",
            negative["errors"],
        )

    def test_external_route_and_dns_success_are_rejected(self) -> None:
        positive = self._network()
        self.assertEqual(
            "passed",
            validate_network_namespace_receipt(positive)["status"],
        )
        for field in ("default_external_route_present",):
            fault = json.loads(json.dumps(positive))
            fault[field] = True
            self.assertEqual(
                "failed",
                validate_network_namespace_receipt(fault)["status"],
            )
        fault = json.loads(json.dumps(positive))
        fault["external_dns_probe"]["succeeded"] = True
        self.assertEqual(
            "failed",
            validate_network_namespace_receipt(fault)["status"],
        )


class FailureAndFinalDeliveryTests(unittest.TestCase):
    def test_release_cli_exit_code_uses_exact_command_status(self) -> None:
        self.assertEqual(
            0, release_command_exit_code("readiness", {"status": "GO"})
        )
        self.assertEqual(
            1,
            release_command_exit_code(
                "readiness", {"status": "NO_GO"}
            ),
        )
        self.assertEqual(
            1,
            release_command_exit_code(
                "readiness", {"status": "passed"}
            ),
        )
        self.assertEqual(
            0,
            release_command_exit_code(
                "fault-matrix", {"status": "passed"}
            ),
        )
        self.assertEqual(
            1,
            release_command_exit_code(
                "fault-matrix", {"status": "GO"}
            ),
        )

    def test_release_fault_injection_is_explicit_and_narrow(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"BENCH_RELEASE_FAULT_INJECTION_STAGE": "runtime_resolution"},
            clear=False,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "deterministic release fault injected",
            ):
                _release_fault_injection("runtime_resolution")
        with mock.patch.dict(
            os.environ,
            {"BENCH_RELEASE_FAULT_INJECTION_STAGE": "unknown"},
            clear=False,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "unsupported release fault-injection stage",
            ):
                _release_fault_injection("runtime_resolution")

    def test_source_generated_bytes_must_match_exactly(self) -> None:
        positive = validate_source_generated_equality(
            b"generated\n",
            b"generated\n",
            artifact="fixture",
        )
        negative = validate_source_generated_equality(
            b"generated\n",
            b"generated\n# drift\n",
            artifact="fixture",
        )
        self.assertEqual("passed", positive["status"])
        self.assertEqual("failed", negative["status"])
        self.assertFalse(negative["byte_equal"])

    def test_injected_failure_evidence_remains_diagnosable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in (
                "failure-receipt.json",
                "command-log.json",
                "stdout.log",
                "stderr.log",
                "partial-evidence-manifest.json",
                "last-completed-stage.json",
            ):
                path = root / name
                if name.endswith(".json"):
                    write_json(path, {"status": "failed"})
                else:
                    path.write_text("fixture\n", encoding="utf-8")
            (root / "replay").mkdir()
            (root / "fresh-work").mkdir()
            result = validate_failure_preservation(root)
            self.assertEqual("passed", result["status"])
            (root / "partial-evidence-manifest.json").unlink()
            self.assertEqual(
                "failed",
                validate_failure_preservation(root)["status"],
            )

    def test_candidate_receipt_cannot_authenticate_final_outer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outer = root / "final.zip"
            with zipfile.ZipFile(outer, "w") as archive:
                archive.writestr("fixture", b"final")
            identity = final_outer_identity(outer)
            inner = final_inner_identity(outer)
            receipt = {
                "final_outer": dict(identity),
                "final_inner": dict(inner),
                "status": "passed",
            }
            matrix = portability_matrix(identity, inner)
            self.assertEqual(
                "passed",
                validate_detached_final_binding(
                    outer, receipt, matrix
                )["status"],
            )
            receipt["final_outer"]["sha256"] = "0" * 64
            self.assertEqual(
                "failed",
                validate_detached_final_binding(
                    outer, receipt, matrix
                )["status"],
            )
            receipt["final_outer"] = dict(identity)
            receipt["final_inner"]["manifest_root"] = "0" * 64
            self.assertEqual(
                "failed",
                validate_detached_final_binding(
                    outer, receipt, matrix
                )["status"],
            )

    def test_detached_receipt_builder_binds_exact_outer_and_inner(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            outer = Path(temporary) / "final.zip"
            with zipfile.ZipFile(outer, "w") as archive:
                archive.writestr("fixture", b"final")
            identity = final_outer_identity(outer)
            inner = final_inner_identity(outer)
            fixture = portability_matrix(identity, inner)
            validation, matrix = build_detached_final_receipts(
                outer=outer,
                environments=fixture["environments"],
                source_commit="1" * 40,
                source_tree="2" * 40,
                failure_evidence_validation={
                    "status": "passed",
                    "final_outer": identity,
                },
            )
            self.assertEqual("passed", validation["status"])
            self.assertEqual("passed", matrix["status"])
            self.assertEqual(
                "passed",
                validate_detached_final_binding(
                    outer, validation, matrix
                )["status"],
            )

    def test_split_parts_reconstruct_exact_outer_and_carry_receipts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outer = root / "final-outer.zip"
            bootstrap_bytes = b"static bootstrap fixture"
            bootstrap_checksum_bytes = (
                hashlib.sha256(bootstrap_bytes).hexdigest()
                + "  independent-verifier-bootstrap\n"
            ).encode()
            with zipfile.ZipFile(
                outer, "w", compression=zipfile.ZIP_STORED
            ) as archive:
                archive.writestr(
                    "portable-fixture.bin",
                    (b"portable-final-outer\n" * 1024) + b"end",
                )
                archive.writestr(
                    "independent-verifier-bootstrap", bootstrap_bytes
                )
                archive.writestr(
                    "independent-verifier-bootstrap.sha256",
                    bootstrap_checksum_bytes,
                )
            identity = final_outer_identity(outer)
            inner = final_inner_identity(outer)
            validation = root / "final-outer.zip.independent-validation.json"
            matrix = root / "final-outer.zip.portability-matrix.json"
            checksum = root / "final-outer.zip.sha256"
            response = root / "agent-response.md"
            write_json(
                validation,
                {
                    "status": "passed",
                    "final_outer": identity,
                    "final_inner": inner,
                    "source": {
                        "commit": "1" * 40,
                        "tree": "2" * 40,
                    },
                },
            )
            write_json(
                matrix,
                portability_matrix(identity, inner),
            )
            checksum.write_text(
                f"{identity['sha256']}  {outer.name}\n",
                encoding="utf-8",
            )
            response.write_text("# Agent response\n", encoding="utf-8")
            bootstrap = root / "independent-verifier-bootstrap"
            bootstrap.write_bytes(bootstrap_bytes)
            bootstrap_checksum = (
                root / "independent-verifier-bootstrap.sha256"
            )
            bootstrap_checksum.write_text(
                hashlib.sha256(bootstrap.read_bytes()).hexdigest()
                + "  independent-verifier-bootstrap\n",
                encoding="utf-8",
            )
            source_ci = root / "source-only-ci-receipt.json"
            write_json(
                source_ci,
                {
                    "status": "passed",
                    "execution_stratum": "source-only",
                    "source": {
                        "commit": "1" * 40,
                        "tree": "2" * 40,
                        "worktree_clean": True,
                    },
                },
            )
            parts = build_split_delivery(
                outer=outer,
                checksum=checksum,
                validation=validation,
                portability_matrix=matrix,
                agent_response=response,
                static_bootstrap=bootstrap,
                static_bootstrap_checksum=bootstrap_checksum,
                source_only_ci_receipt=source_ci,
                output=root / "parts",
                payload_bytes=4096,
                maximum_part_zip_bytes=100_000,
            )
            self.assertGreater(len(parts), 1)
            result = validate_split_delivery(parts, root / "reconstructed")
            self.assertEqual("passed", result["status"], result["errors"])
            self.assertEqual(identity, result["final_outer"])
            with zipfile.ZipFile(parts[0]) as archive:
                self.assertEqual(
                    {
                        "agent-response.md",
                        "final-outer.independent-validation.json",
                        "final-outer.portability-matrix.json",
                        "final-outer.sha256",
                        "independent-verifier-bootstrap",
                        "independent-verifier-bootstrap.sha256",
                        "reconstruct.sh",
                        "source-only-ci-receipt.json",
                        "split-delivery-manifest.json",
                        "split-index.json",
                        "split-index.md",
                    },
                    set(archive.namelist())
                    - {
                        name
                        for name in archive.namelist()
                        if name.startswith("payload.part-")
                    },
                )

    def test_source_generated_replay_has_one_packaged_boundary(self) -> None:
        source = _replay_script()
        self.assertIn("namespace-launcher", source)
        self.assertIn("replay-rootfs", source)
        self.assertNotIn("unshare --net --mount", source)


if __name__ == "__main__":
    unittest.main()
