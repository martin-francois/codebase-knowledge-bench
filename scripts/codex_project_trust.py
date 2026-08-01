from __future__ import annotations

import json
import tomllib
from pathlib import Path


PROJECT_TRUST_DISABLED_WARNING = (
    "project-local config, hooks, and exec policies are disabled"
)


def project_trust_disabled_warning(text: str) -> bool:
    lowered = text.lower()
    return (
        PROJECT_TRUST_DISABLED_WARNING in lowered
        and "until the project is trusted" in lowered
    )


def trusted_projects(config_text: str) -> dict[str, str]:
    parsed = tomllib.loads(config_text)
    projects = parsed.get("projects")
    if projects is None:
        return {}
    if not isinstance(projects, dict):
        raise ValueError("Codex projects configuration is not a table")
    result: dict[str, str] = {}
    for path, value in projects.items():
        if not isinstance(path, str) or not isinstance(value, dict):
            raise ValueError("Codex project trust entry is malformed")
        trust_level = value.get("trust_level")
        if not isinstance(trust_level, str):
            raise ValueError(f"Codex project trust level is missing: {path}")
        result[path] = trust_level
    return result


def exact_project_trust(config_path: Path, expected_repo: Path) -> bool:
    try:
        projects = trusted_projects(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        return False
    return projects == {str(expected_repo.resolve()): "trusted"}


def ensure_exact_project_trust(config_path: Path, expected_repo: Path) -> None:
    expected = str(expected_repo.resolve())
    text = config_path.read_text(encoding="utf-8")
    projects = trusted_projects(text)
    if projects:
        if projects != {expected: "trusted"}:
            raise RuntimeError(
                "isolated Codex config trusts a project other than its sealed repository"
            )
        return
    config_path.write_text(
        text.rstrip()
        + "\n\n# Enable only this run's reviewed project-local tool configuration.\n"
        + f"[projects.{json.dumps(expected)}]\n"
        + 'trust_level = "trusted"\n',
        encoding="utf-8",
    )
