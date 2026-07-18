#!/usr/bin/env python3
"""Rebuild and prove the checked-in static verifier bootstrap."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/independent_verifier_bootstrap.c"
BINARY = ROOT / "scripts/independent-verifier-bootstrap"
CHECKSUM = ROOT / "scripts/independent-verifier-bootstrap.sha256"
COMPILE_FLAGS = (
    "-static",
    "-Os",
    "-s",
    "-std=c17",
    "-Wall",
    "-Wextra",
    "-Werror",
    "-fno-ident",
    "-fno-asynchronous-unwind-tables",
    "-fno-unwind-tables",
    "-Wl,--build-id=none",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def compile_bootstrap(
    output: Path, *, shell: str | None = None
) -> list[str]:
    command = ["gcc", *COMPILE_FLAGS]
    if shell is not None:
        command.append(
            f'-DINDEPENDENT_VERIFIER_SHELL="{shell}"'
        )
    command.extend(["-o", str(output), str(SOURCE)])
    subprocess.run(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return command


def run(output: Path) -> dict[str, Any]:
    expected = CHECKSUM.read_text(encoding="utf-8").split()[0]
    with tempfile.TemporaryDirectory(
        prefix="independent-verifier-bootstrap-build-"
    ) as temporary:
        root = Path(temporary)
        first = root / "bootstrap-first"
        second = root / "bootstrap-second"
        command = compile_bootstrap(first)
        compile_bootstrap(second)
        file_result = subprocess.run(
            ["file", str(first)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        readelf_result = subprocess.run(
            ["readelf", "-l", str(first)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        ldd_result = subprocess.run(
            ["ldd", str(first)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        bad_arguments = subprocess.run(
            [str(first)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        missing_shell = root / "bootstrap-missing-shell"
        compile_bootstrap(
            missing_shell,
            shell="/independent-verifier-missing-shell",
        )
        missing_shell_result = subprocess.run(
            [str(missing_shell), "script", "outer", "output"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        checks = {
            "double_compilation_equal":
                first.read_bytes() == second.read_bytes(),
            "checked_in_binary_equal":
                first.read_bytes() == BINARY.read_bytes(),
            "checksum_file_matches":
                expected == sha256_file(BINARY),
            "file_reports_static":
                "statically linked" in file_result.stdout,
            "no_dynamic_interpreter":
                "INTERP" not in readelf_result.stdout,
            "ldd_rejects_dynamic_loading":
                "not a dynamic executable" in ldd_result.stdout,
            "bad_arguments_structured":
                bad_arguments.returncode == 64
                and '"code":"bad_arguments"'
                in bad_arguments.stderr,
            "missing_shell_structured":
                missing_shell_result.returncode == 69
                and '"code":"shell_exec_failed"'
                in missing_shell_result.stderr,
            "source_has_no_proc_exe_dependency":
                "/proc/" not in SOURCE.read_text(encoding="utf-8"),
        }
        result = {
            "schema_id":
                "independent-verifier-static-bootstrap-build-current",
            "status": (
                "passed" if all(checks.values()) else "failed"
            ),
            "checks": checks,
            "source": {
                "path": SOURCE.relative_to(ROOT).as_posix(),
                "sha256": sha256_file(SOURCE),
            },
            "binary": {
                "path": BINARY.relative_to(ROOT).as_posix(),
                "bytes": BINARY.stat().st_size,
                "sha256": sha256_file(BINARY),
            },
            "compile_command": [
                (
                    "$OUTPUT"
                    if value == str(first)
                    else "$SOURCE"
                    if value == str(SOURCE)
                    else value
                )
                for value in command
            ],
            "static_inspection": {
                "file": file_result.stdout.strip(),
                "readelf_exit_code": readelf_result.returncode,
                "ldd_exit_code": ldd_result.returncode,
                "ldd": ldd_result.stdout.strip(),
            },
            "bad_arguments": {
                "exit_code": bad_arguments.returncode,
                "stderr": bad_arguments.stderr.strip(),
            },
            "missing_shell": {
                "exit_code": missing_shell_result.returncode,
                "stderr": missing_shell_result.stderr.strip(),
            },
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.output.resolve())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
