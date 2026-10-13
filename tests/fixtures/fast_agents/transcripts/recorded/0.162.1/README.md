# Real Codex 0.162.1 transcript goldens

No genuine Codex session transcript has been captured and reviewed in this environment.
Synthetic fake-server traces MUST NOT be committed here under a real-session label.

The authenticated main/nightly workflow records metadata-only request/response and
notification envelopes from Codex CLI. Review every artifact for credentials,
user content, prompts and local paths before promoting it here as a versioned
JSON golden. Run the replay suite and compare schema/behavior for each version
before adding it to the tested-release registry.

Use fake-server injected faults to test malformed JSON, transport timeouts,
repeated replies, cancellation and steering. Do not force live Codex into
unsafe commands or non-idempotent retries to create test records.
