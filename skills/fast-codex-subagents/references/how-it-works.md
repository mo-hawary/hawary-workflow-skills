# How Fast Codex Subagents Works

This page shows the moving parts at a glance. The rules behind each step live in [the skill](../SKILL.md), [the manifest guide](manifest-guide.md), and [runtime and safety](runtime-and-safety.md). This skill runs on Codex only.

## End-to-end flow

The Codex chat stays the coordinator. It plans the team, checks the local Codex install, launches a detached task, and then only polls or sends explicit control commands.

```mermaid
flowchart TD
    A["User request in Codex CLI or Codex app"] --> B{"Substantive execution?"}
    B -- "No: question, chat, planning" --> Z["Answer in the foreground"]
    B -- "Yes" --> C["Pick the smallest useful team<br/>disjoint writer ownership, optional Sol reviewer"]
    C --> D["Resolve model, effort, Fast, backend<br/>user choice > saved defaults > rules > recommendation"]
    D --> E["Write the task manifest<br/>roles, owns, depends_on, shared_contract"]
    E --> F["fast_agents.py doctor<br/>CLI version, schema, model/list, no inference"]
    F -- "fails" --> X["Stop and report<br/>no silent fallback"]
    F -- "passes" --> G["Show team, models, Fast and config"]
    G --> H["fast_agents.py launch"]
    H --> I{"backend"}
    I -- "auto: one bounded independent role" --> J["codex exec worker"]
    I -- "auto: multi-role, dependent, interactive<br/>or explicit app-server" --> K["Detached supervisor<br/>+ one codex app-server"]
    J --> L["status / watch<br/>read-only polling"]
    K --> L
    L --> M["Review role reports, scope audit, workspace diff"]
    M --> N["Run real verification<br/>agent success is not proof"]
```

## App-server runtime

One supervisor owns one `codex app-server` process per task. Each role is a separate logical thread with its own history. The parent chat talks to the supervisor only through the authenticated loopback endpoint.

```mermaid
flowchart LR
    subgraph Chat["Codex chat (coordinator)"]
        CLI["fast_agents.py<br/>status, events, steer,<br/>interrupt, reply, recover"]
    end

    subgraph Task["Private task runtime (owner-only files)"]
        CAP["capabilities file<br/>read token, control token"]
        STATE["status.json, events,<br/>prompts, reports"]
    end

    subgraph Sup["Detached supervisor (asyncio)"]
        IPC["Loopback IPC<br/>127.0.0.1, bounded"]
        SCHED["Scheduler<br/>default 3, max 8 concurrent,<br/>depends_on order"]
        PEND["Pending requests<br/>approvals, user input, MCP"]
    end

    subgraph AS["codex app-server (stdio JSONL)"]
        W1["Writer thread<br/>workspace-write, on-request + auto_review"]
        W2["Writer thread<br/>disjoint owns"]
        R1["Reviewer thread<br/>read-only, network off"]
    end

    CLI -- "token from" --> CAP
    CLI -- "explicit commands" --> IPC
    IPC --> SCHED
    IPC --> PEND
    SCHED -- "thread/start, turn/start" --> W1
    SCHED --> W2
    SCHED -- "after dependencies settle" --> R1
    W1 -- "server requests" --> PEND
    SCHED --> STATE
    PEND --> STATE
```

Key boundaries:

- `status` and `events` use the read token. `steer`, `interrupt`, `reply` and `recover` need the control token; `steer`, `interrupt` and `reply` also need the current thread, turn or request IDs.
- The supervisor never blanket-approves. Approval requests wait in the pending list until an explicit scoped `reply`.
- Declared `owns` paths coordinate writers; they are not a per-file sandbox. Review the diff.
- Closing the chat or a `watch` terminal does not stop workers. Only an explicit `control interrupt` cancels a turn.

## Recovery

When a task is in `recovery_required`, `resume --recover` reconciles saved state instead of starting over. Recovery only continues when every role is proven; any doubt keeps the whole task blocked.

```mermaid
flowchart TD
    A["resume --recover<br/>task state is recovery_required"] --> B{"Recorded supervisor identity"}
    B -- "alive" --> C["Send recover to the running supervisor<br/>over control IPC"]
    B -- "dead or stale" --> D["Start a replacement supervisor<br/>and one app-server"]
    B -- "unknown" --> STOP["Refuse: cannot prove the old one stopped"]
    C --> E["For each role: resume saved thread,<br/>read thread history"]
    D --> E
    E --> F{"Role outcome"}
    F -- "never submitted" --> G["Requeue as waiting"]
    F -- "turn terminal" --> H["Record result"]
    F -- "turn still active, lost,<br/>ambiguous or settings changed" --> U["Add to unresolved findings<br/>never replayed"]
    G --> J{"Any unresolved?"}
    H --> J
    U --> J
    J -- "no" --> K["Requeue blocked dependents,<br/>state running, scheduler continues"]
    J -- "yes" --> L["Stay recovery_required<br/>no new turn starts, user decides"]
```

A recover that runs longer than the control call's wait keeps running inside the supervisor; the caller gets an `in_progress` reply and should poll `status`.

## Fast evidence

Each worker reports three separate facts: the requested tier, the effective thread tier reported by app-server, and upstream execution, which stays `unverified` without provider telemetry. On the app-server backend, a requested `priority` tier that is not confirmed effective fails before its turn starts. The `exec` backend can only pass the requested tier as CLI flags; its effective tier is read from the worker's events afterwards and stays `unverified` without correlated evidence.
