from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = ROOT / "skills"


def skill_names() -> list[str]:
    return sorted(path.parent.name for path in SKILLS_DIR.glob("*/SKILL.md"))


def frontmatter_value(path: Path, key: str) -> str:
    content = path.read_text(encoding="utf-8")
    match = re.search(rf"^{key}:\s*\"?(.+?)\"?\s*$", content, re.MULTILINE)
    assert match is not None, f"{path} is missing {key}"
    return match.group(1)


def test_skills_index_matches_canonical_skill_folders():
    catalog_path = ROOT / "skills-index.json"
    assert catalog_path.exists()

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    indexed_names = sorted(skill["name"] for skill in catalog["skills"])

    assert catalog["schema_version"] == "1.0"
    assert indexed_names == skill_names()
    assert catalog["canonical_source"] == "skills/"
    assert "codex" in catalog["compatible_agents"]
    assert "claude-code" in catalog["compatible_agents"]
    assert "claude-api" in catalog["compatible_agents"]
    assert "claude-ai-api" not in catalog["compatible_agents"]

    for skill in catalog["skills"]:
        skill_file = ROOT / skill["path"]
        assert skill_file.exists()
        assert skill["name"] == skill_file.parent.name
        assert skill["description"] == frontmatter_value(skill_file, "description")
        assert skill["installable"] is True
        assert skill["category"]
        assert skill["outputs"]


def test_claude_marketplace_exposes_canonical_skills_without_duplication():
    marketplace_path = ROOT / ".claude-plugin" / "marketplace.json"
    assert marketplace_path.exists()

    marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
    assert marketplace["name"] == "hawary-workflow-skills"
    assert marketplace["plugins"][0]["name"] == "hawary-workflow-skills"
    assert marketplace["plugins"][0]["source"] == "./"
    assert marketplace["plugins"][0]["strict"] is False
    assert marketplace["plugins"][0]["skills"] == "./skills"


def test_compatibility_matrix_covers_recommendation_targets():
    compatibility_path = ROOT / "COMPATIBILITY.md"
    assert compatibility_path.exists()
    content = compatibility_path.read_text(encoding="utf-8")

    expected_agents = [
        "Codex",
        "Claude Code",
        "Claude.ai / API",
        "Cursor",
        "GitHub Copilot",
        "OpenCode",
        "Gemini CLI",
        "Windsurf",
        "Cline / Roo",
    ]
    for agent in expected_agents:
        assert agent in content

    assert "Tested" in content
    assert "Expected" in content
    assert "Known Limitations" in content
    assert "automatic selection are not equivalent to Claude Code" in content
    assert "install flow should remain `Expected`" in content


def test_trust_and_security_docs_are_present_and_specific():
    trust = (ROOT / "TRUST.md").read_text(encoding="utf-8")
    security_model = (ROOT / "SECURITY_MODEL.md").read_text(encoding="utf-8")

    for phrase in [
        "report-first",
        "explicit approval",
        "No hidden credential collection",
        "No automatic package upgrades",
        "review pull request changes before running them",
    ]:
        assert phrase in trust

    for phrase in [
        "dependency_audit.py",
        "Read scope",
        "Write scope",
        "Network behavior",
        "Command execution",
        "OSV.dev",
    ]:
        assert phrase in security_model


def test_dev_secrets_examples_are_value_free_and_status_driven():
    examples = (ROOT / "skills" / "dev-secrets" / "references" / "examples.md").read_text(
        encoding="utf-8"
    )
    output_templates = (
        ROOT / "skills" / "dev-secrets" / "references" / "output-templates.md"
    ).read_text(encoding="utf-8")
    combined = f"{examples}\n{output_templates}"

    forbidden_example_commands = [
        "cat .env",
        "printenv",
        "\nenv\n",
    ]
    for command in forbidden_example_commands:
        assert command not in combined

    for field in [
        "Status:",
        "Safe Evidence:",
        "Warnings:",
        "Approval Needed:",
    ]:
        assert field in examples

    assert "compatible CLI was not detected" in combined
    assert "If compatible CLI exists" in examples
    assert "Next Command: dev-secrets import .env --plan" in examples
    assert "Next Command: install or implement a compatible local CLI" in output_templates
    assert "Safe Evidence: 2 env files detected by name; no values read" in output_templates
    assert "Approval Needed: import target selection" in output_templates


def test_dev_secrets_recovery_prioritizes_rotation_before_history_cleanup():
    recovery = (
        ROOT / "skills" / "dev-secrets" / "references" / "recovery-playbook.md"
    ).read_text(encoding="utf-8")

    assert "If the repo is or was public" in recovery
    assert "permanently exposed" in recovery
    assert "git filter-repo" in recovery
    assert "BFG" in recovery
    assert "<leaked-env-file-path>" in recovery
    assert "<leaked-env-file-name>" in recovery
    assert ".env.local" in recovery
    assert "apps/web/.env.development" in recovery
    assert "bfg --delete-files .env.development" in recovery
    recovery_lines = {line.strip() for line in recovery.splitlines()}
    assert "git filter-repo --path .env --invert-paths" not in recovery_lines
    assert "bfg --delete-files .env" not in recovery_lines
    assert "do not default cleanup commands to `.env`" in recovery

    lower = recovery.lower()
    assert lower.index("rotate") < lower.index("history cleanup")


def test_dev_secrets_limits_package_script_inspection_to_names_or_redacted_metadata():
    paths = [
        ROOT / "skills" / "dev-secrets" / "SKILL.md",
        ROOT / "adapters" / "codex" / "AGENTS.md",
        ROOT / "adapters" / "claude" / "README.md",
        ROOT / "adapters" / "generic-agent.md",
    ]

    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)

    assert "package script names" in combined
    assert "full package script command strings" in combined
    assert "compatible local CLI or redacted manifest" in combined
    assert "redacted manifest metadata" in combined

    unsafe_phrases = [
        "package scripts, existing examples",
        "package scripts, examples",
        "Inspect safe repo signals only: file names, tracking status, ignore rules, package scripts",
    ]
    for phrase in unsafe_phrases:
        assert phrase not in combined
