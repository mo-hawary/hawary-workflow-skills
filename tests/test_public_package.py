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


def test_fast_codex_subagents_public_identity_matches_ui_metadata():
    skill_file = SKILLS_DIR / "fast-codex-subagents" / "SKILL.md"
    assert frontmatter_value(skill_file, "name") == "fast-codex-subagents"

    ui_path = skill_file.parent / "agents" / "openai.yaml"
    ui_metadata = ui_path.read_text(encoding="utf-8")
    assert 'display_name: "Fast Codex Subagents"' in ui_metadata
    assert "$fast-codex-subagents" in ui_metadata


def test_fast_codex_subagents_evidence_remains_historical():
    evidence_path = SKILLS_DIR / "fast-codex-subagents" / "references" / "evidence" / "2026-10-13-summary.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))

    identity = evidence["skill_identity_at_recording"]
    assert identity["public_skill_id"] == "fast-agents"
    assert identity["current_public_skill_id"] == "fast-codex-subagents"
    assert identity["release_version"] is None

    pair = evidence["corrected_fast_on_off_pair"]
    assert pair["runs"]["fast_on"]["full_turn_seconds"] == 47.917
    assert pair["runs"]["fast_off"]["full_turn_seconds"] == 61.657
    assert pair["runs"]["fast_on"]["combined_total_tokens"] == 325163
    assert pair["runs"]["fast_off"]["combined_total_tokens"] == 300566
    assert pair["prelaunch_source_snapshot"]["files"]["skills/fast-agents/SKILL.md"] == (
        "bd6e036149fc7f4d7b4c9086824118251937caff15a2b74efe81bea85a348a6f"
    )


def skill_agents(name: str) -> list[str] | None:
    content = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
    match = re.search(r"^agents:\s*(.+?)\s*$", content, re.MULTILINE)
    return json.loads(match.group(1)) if match else None


def test_claude_marketplace_exposes_canonical_skills_without_duplication():
    marketplace_path = ROOT / ".claude-plugin" / "marketplace.json"
    assert marketplace_path.exists()

    marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
    assert marketplace["name"] == "hawary-workflow-skills"
    assert marketplace["plugins"][0]["name"] == "hawary-workflow-skills"
    assert marketplace["plugins"][0]["source"] == "./"
    assert marketplace["plugins"][0]["strict"] is False

    exposed = marketplace["plugins"][0]["skills"]
    assert isinstance(exposed, list)
    assert all(isinstance(entry, str) for entry in exposed)
    assert len(exposed) == len(set(exposed))
    assert all(re.fullmatch(r"\./skills/[a-z0-9-]{1,64}", entry) for entry in exposed)

    expected = [f"./skills/{name}" for name in skill_names() if skill_agents(name) != ["codex"]]
    assert exposed == expected


def test_claude_marketplace_never_exposes_codex_only_skills():
    marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    for entry in marketplace["plugins"][0]["skills"]:
        name = entry.removeprefix("./skills/")
        assert skill_agents(name) != ["codex"], f"{entry} is codex-only"

    assert "./skills/fast-codex-subagents" not in marketplace["plugins"][0]["skills"]


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
