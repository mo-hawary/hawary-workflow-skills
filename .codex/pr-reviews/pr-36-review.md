# PR 36 Review: Neural Architecture Families Lab

- PR: https://github.com/elhawary-creative/mohawary/pull/36
- Repo: `elhawary-creative/mohawary`
- Base: `e7a0b8e131d2d8c940c393dd7c87ec590eec6da2`
- Head reviewed: `7f8ba7ced35afcaff802eb002f95dd863bb14c2a`
- Review mode: attacker review only; no implementation edits approved.

## GitHub Contact Exception

This workspace does not contain the `mohawary` repo or PR branch, so the review used the GitHub connector for PR metadata, changed files, patch, comments, and file contents.

## PR Study Summary

The PR publishes a new Labs hub at `/labs/neural-network-architecture-families`, adds seven focused child Labs pages, hides those child pages from the main Labs index, adds a route-scoped clickable architecture map, extends the generic article renderer for links/images/visual-table cards/headings, and introduces a shared page theme registry used by nav and article shells.

Source artifacts read:

- PR title/body and changed-file list.
- `prod/specs/neural-network-architecture-families-lab.md`
- `prod/impp/neural-network-architecture-families-lab.md`
- `app/content/labs/neural-network-architecture-families.md`
- `app/components/labs/ArticleBody.tsx`
- `app/components/labs/ArticleArchitectureMap.tsx`
- `app/app/labs/[slug]/page.tsx`
- `app/app/labs/page.tsx`
- `app/lib/site/pageThemes.ts`
- Relevant tests under `app/lib/labs` and `app/lib/site`.

No DB, API, auth, payment, migration, or durable-state invariant gate applies.

## Extracted Requirements

- Hub article is published and includes professional quick-brief metadata.
- Hub becomes a shorter root map rather than one huge guide.
- Clickable map appears near the top of the hub only.
- Child pages are routable from the map and reading path.
- Child pages are hidden from the main Labs index.
- Map links must not route to 404 pages.
- Nodes must be accessible normal links.
- Article renderer must support inline links, images, visual cards, and heading IDs.
- Validation expected by PR body: `pnpm -C app run labs:validate`, `pnpm -C app run test`, `pnpm -C app run typecheck`, `pnpm -C app run build`, `git diff --check`.

## Review Plan

- [x] Confirm PR intent and source artifacts.
- [x] Check previous GitHub findings against latest head.
- [x] Review route/static-generation behavior for published and hidden child articles.
- [x] Review article renderer changes for user-visible rendering regressions.
- [x] Review theme registry for scope and drift.
- [x] Review tests for behavior coverage versus source-string assertions.
- [x] Record findings and residual risk.

## Attacker Round 1

### Finding 1: P2 - Markdown formatting in link labels still renders as raw text

Issue: `ArticleBody` still renders a markdown link label as a plain string instead of recursively rendering inline formatting. The new hub uses bold-formatted labels in its Reading Path, so users will see literal `**` markers inside the links.

Evidence:

- `app/components/labs/ArticleBody.tsx:245` returns `{label}` inside the `<a>`.
- `app/content/labs/neural-network-architecture-families.md:143` and following Reading Path entries use labels like `\[**Spatial and Structured Architectures**\]\(/labs/feedforward-spatial-architectures\)`.
- The existing GitHub review comment `discussion_r3349126618` flagged the same issue at reviewed commit `7f8ba7ced3`, and the latest head still contains the same renderer behavior.

Recommended fix: render the link label through a safe inline-label renderer, or strip supported inline markers before display. Add a focused test that would fail on `\[**Label**\]\(/path\)` by asserting the visible label does not contain `**`.

Status: unresolved.

## Validation

No local test commands were run because this workspace is not the `mohawary` repo and does not contain the app checkout.

Observed PR-declared validation:

- `pnpm -C app run labs:validate`
- `pnpm -C app run test`
- `pnpm -C app run typecheck`
- `pnpm -C app run build`
- `git diff --check`

## Residual Risk

- Tests in `app/lib/site/article-body-rendering.test.ts` mostly assert source strings, so they did not catch the nested inline rendering bug.
- No browser/manual smoke was performed from this workspace.

## Final Attacker Pass

Latest head reviewed: `7f8ba7ced35afcaff802eb002f95dd863bb14c2a`.

Result: not clean. One medium/P2 user-visible rendering issue remains.
