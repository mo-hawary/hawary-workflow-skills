# Security Model

This document describes the security-relevant behavior of the bundled helpers and repository validation workflow.

## `dependency_audit.py`

Path: `skills/dependency-security-auditor/scripts/dependency_audit.py`

### Read Scope

- Reads dependency manifests and lockfiles under the requested `--root`.
- Skips common generated or heavy directories such as `.git`, `node_modules`, build output, virtual environments, and test fixtures.
- Reads package-manager metadata only to detect ecosystem, lockfile status, and package-manager choice.
- Read scope: intentionally limited to dependency and project metadata needed for audit evidence.

### Write Scope

- Does not edit manifests, lockfiles, source code, hooks, or CI configuration.
- Writes a JSON report only when called with `--report <path>`.
- Uses process exit codes for severity gates when `--fail-on` or freshness failure flags are provided.
- Write scope: excludes application code and package state.

### Network Behavior

- Network behavior: the helper itself does not make HTTP requests directly.
- Invoked scanners or package managers may contact registries or vulnerability databases, depending on the tool:
  - `osv-scanner`
  - `npm audit`
  - `pnpm audit`
  - `yarn npm audit`
  - `pip-audit`
  - `npm outdated`
  - `pnpm outdated`
  - `dart pub outdated`
  - `flutter pub outdated`
- Example: `osv-scanner` may query OSV.dev over HTTPS, while native package-manager audits may contact their package registry or advisory service.

### Command Execution

- Commands are selected from discovered ecosystems and available tools.
- Missing scanners are reported as setup gaps; the helper does not install them.
- Scanner failures are preserved as `scanner_error` instead of being treated as clean results.
- Outdated packages are reported separately from CVEs.
- Command execution: deterministic from discovered stack evidence and explicit CLI flags.

## `fast_agents.py`

Path: `skills/fast-agents/scripts/fast_agents.py`

### Read Scope

- Reads the supplied manifest and workspace path, applicable saved defaults, selected task state, process identities, event summaries, and setup ownership records.
- The preferred app-server backend is pinned to Codex CLI 0.162.1. `doctor` inspects version and options, generates/checks the local protocol schema, reads `features list`, and calls `model/list` for model, effort, and service-tier capability. These checks make no model inference.

### Write Scope

- `launch` creates a private task directory with normalized settings, per-role prompts/events/stderr/final reports, token usage when supplied, current thread/turn IDs, status, and process identities. The helper does not edit workspace files itself.
- Codex writers can edit under the workspace-write sandbox. Reviewers use a read-only sandbox. Declared ownership is validated and included in prompts but is not a per-file OS sandbox; inspect the resulting diff.
- `install`, `defaults set`, and shared instruction setup write only their explicit target after preview confirmation. Rollback checks hashes and preserves modified files.
- POSIX runtime directories/files are owner-only (`0700`/`0600`). Windows uses a protected owner-only DACL and verifies the owner ACE and inheritance flags. Failure to enforce protection blocks setup.

### Network Behavior

- The helper itself makes no HTTP requests. `launch` uses the installed Codex CLI's normal local authentication and configuration; network behavior follows that Codex session.
- Fake app-server fixtures exercise the protocol, recovery, and control behavior without Codex, model inference, or paid CLI usage. A real doctor/schema/model-list check is capability evidence only.

### Command Execution, Approvals, and Process Ownership

- The Python supervisor uses `asyncio` and one stdio JSONL reader plus a serialized writer. Client response futures and app-server server requests are separate maps. It hosts separate logical threads and histories in one app-server process; it does not spawn native agents or a custom Rust process.
- Subprocesses use argument arrays with `shell=False`. Windows resolution accepts `codex.exe` or a Node entrypoint and refuses `.cmd`/`.bat` wrappers. POSIX and Windows use distinct lock and detached-launch adapters; process-start identity uses Linux `/proc`, Windows process times through `ctypes`, or macOS `libproc`.
- Threads request `on-request` approvals with Codex `auto_review`; writers are `workspace-write`, reviewers `read-only` with network disabled. The helper never blanket-approves a server request. Status exposes only pending request IDs, method, reply action, and thread/turn IDs. Raw approval, user-input, permission, and MCP parameters remain in a private task artifact; authenticated `inspect-request` returns them with a sensitivity warning. Replies require the exact live server request, transport epoch, thread/turn, role policy, and method schema.
- An authenticated loopback endpoint binds only `127.0.0.1`. Private capability files store separate read and control tokens; tokens are not printed. Status/events are read-only. Steering, interruption, replies, sensitive request inspection, and recovery require the control token and explicit current IDs. The parent polls; no unsolicited context is injected.
- A workspace lock covers writer tasks. Resume reconnects to the existing supervisor; recovery reads persisted threads/turns and never replays an uncertain turn. A state that cannot be proven remains blocked. No automatic backend or service-tier retry occurs.
- Watcher cleanup verifies and signals only a registered watcher; it never targets workers or the app-server. The helper makes no process-tree termination promise and does not use Windows Job Objects. It waits for its owned app-server stdio process and refuses risky actions when identity is unknown.
- Status contains role state, summarized events, tier evidence, and the minimal pending-request projection. Raw pending parameters, event payloads, prompts, stderr, and final reports stay in private task/role artifacts. Fake tests also cover malformed/partial messages, worker failure isolation, and recovery.

### Fast-Tier Evidence

- App-server uses `serviceTier: "priority"` for Fast and `"default"` otherwise, and passes the selected tier to each turn. `default` is the explicit standard-route sentinel and is not a catalog tier ID; Fast `priority` must be present in the selected model's catalog.
- Status records requested, effective thread tier, and upstream execution separately. A returned effective tier that differs from the requested tier blocks that worker before its turn starts. Upstream execution remains `unverified` without provider telemetry; feature listing, catalog membership, and fake tests do not prove paid model behavior.
- Explicit `exec` fallback retains the approved argument pattern with `service_tier="fast" --enable fast_mode` only for Fast-selected workers. A CLI rejection is surfaced without switching tier/backend or bypassing local configuration.

## Repository Validator

Path: `scripts/validate_skills.rb`

- Checks skill frontmatter, naming, referenced files, agent metadata, local Markdown links, tracked symlinks, and public-release hygiene patterns.
- Reads tracked files from git and validates Markdown links against tracked targets.
- Does not write files or change git state.

## Recommended Controls

- Run validation in CI on pull requests and pushes to `main`.
- Pin GitHub Actions by commit SHA.
- Use hash-checked Python test dependencies.
- Review generated release artifacts before publishing.
- Treat new scripts as security-sensitive and document their read, write, network, and command behavior here.
