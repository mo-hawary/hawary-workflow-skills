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


def test_trust_and_security_docs_are_present_and_specific():
    trust = (ROOT / "TRUST.md").read_text(encoding="utf-8")
    security_model = (ROOT / "SECURITY_MODEL.md").read_text(encoding="utf-8")

    for phrase in [
        "report-first",
        "explicit approval",
        "No hidden credential collection",
        "No automatic package upgrades",
    ]:
        assert phrase in trust

    for phrase in [
        "dependency_audit.py",
        "Read scope",
        "Write scope",
        "Network behavior",
        "Command execution",
    ]:
        assert phrase in security_model
