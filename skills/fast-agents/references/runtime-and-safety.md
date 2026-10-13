# Runtime and Safety

## Preferred app-server backend

The default backend runs one `codex app-server --listen stdio://` process per task. It hosts separate logical threads with separate histories; it does not share the parent conversation heap. A standard-library `asyncio` supervisor uses one JSONL reader and a serialized writer, with distinct pending-client-response and server-request maps. Events and private logs are associated with their thread and turn IDs. Shared contracts and completed dependency reports are bounded before they cross roles.

The adapter is experimental and supports only Codex CLI 0.162.1. Before launch, `doctor` inspects the installed version and options, runs `app-server generate-json-schema`, checks the schema against the adapter's required methods and request shapes, and calls `model/list` to check available models, reasoning effort, and service tiers. `default` is the protocol's explicit standard-route sentinel, not a catalog tier ID; Fast `priority` must appear in the selected model's catalog. These checks make no model inference and do not prove an individual turn's provider-side tier. An unverified schema, version, model, effort, or requested Fast tier blocks launch. There is no automatic standard-tier retry or backend fallback.

Threads request `approvalPolicy: on-request` and `approvalsReviewer: auto_review`. Writers use `workspace-write`; reviewers use `read-only` plus a read-only, network-disabled turn sandbox. The supervisor never blanket-approves a request. It surfaces command/file/permission approvals, tool user-input prompts, and MCP elicitation requests as pending actions. A reply requires the current request, thread, and turn IDs and a validated method-specific payload. Guardian review events are recorded separately from a user's explicit response.

For each worker, status keeps three distinct facts: requested tier (`priority` for Fast or `default` otherwise), effective thread tier reported by app-server, and upstream execution, which remains `unverified` without provider telemetry. A reported null effective tier for a requested `default` route is recorded as effective `default`, while the original null is retained as the reported value. Token usage and cached-token fields are recorded only when supplied by app-server. The local feature list and model catalog are capability evidence, not proof of an individual session's Fast execution.

The task owns an authenticated loopback control endpoint bound to `127.0.0.1`. A protected discovery file identifies its port; separate random read and control capabilities live in a private file and are never printed. `status` and `events` use read-only access. `steer`, `interrupt`, `reply`, and `recover` require explicit commands and validate current IDs; each successful action returns an acknowledgement. The parent chat polls this endpoint through a tool or CLI and receives no unsolicited context injection. Reconnecting with `resume` talks to the existing supervisor and does not create a second writer or app-server.

If the app-server or supervisor stops, reconcile the persisted logical thread and turn state before continuing. Recovery resumes saved threads, reads their history, and confirms whether each submitted turn is active, terminal, or uncertain. It never replays an uncertain turn or starts a duplicate thread. If state cannot be proven, the task remains blocked for a deliberate user decision. A worker failure is isolated to its role and dependent roles; independent ready roles may continue.

## Explicit exec fallback

Set `"backend": "exec"` only when the user or project selects the fallback. The approved argument-array pattern is:

```text
codex exec -C WORKSPACE -m MODEL -c model_reasoning_effort="max" -c service_tier="fast" --enable fast_mode --approve-for-me --json --color never -o FINAL_REPORT -
```

The helper substitutes the selected effort and report path and sends the prompt on stdin. Reviewers use `-s read-only` instead of `--approve-for-me`. CLI flags and `features list` show the requested configuration only. Effective/upstream Fast execution remains unverified unless the worker event stream contains correlated evidence. Exec fallback does not provide app-server steering or turn-scoped server-request replies. A rejected option or failed launch is reported without changing backend, tier, or sandbox.

## Process ownership and private runtime

The runtime root is `%LOCALAPPDATA%/fast-agents` on Windows, `$XDG_STATE_HOME/fast-agents` when set, or `~/.local/state/fast-agents` otherwise. The task stores private prompts, per-role event streams and stderr, final reports, usage, IDs, status, and process identities. Treat these artifacts as source-sensitive. POSIX directories/files use owner-only `0700`/`0600` permissions. Windows applies and verifies a protected owner-only DACL; if the host cannot enforce it, setup fails closed.

Process identity uses Linux `/proc`, Windows process creation time and executable path through `ctypes`, or macOS `libproc`. Workspace locks use `fcntl` or `msvcrt`. Supervisor launch uses platform-specific detached-process flags and never invokes a shell. On Windows, Codex is launched through `codex.exe` or a resolved Node entrypoint; `.cmd`/`.bat` wrappers are not invoked. Launch and recovery refuse to proceed when a prior manager or app-server identity is missing, alive unexpectedly, or unknown.

Watcher cleanup signals only a registered watcher whose task identity and process start identity match. It never targets workers or the app-server. The helper does not promise process-tree termination; it does not use a Windows Job Object. Server shutdown closes the owned stdio and waits for that process. If the server outlives its supervisor or identity cannot be proven, no unrelated process is signaled or removed.

Writers can edit only under Codex's workspace-write sandbox, which is broader than their declared file list. Ownership is a coordination boundary in prompts and validation, not a per-file OS sandbox. Review the resulting diff. The helper itself makes no HTTP requests; Codex networking follows the user's normal Codex configuration.
