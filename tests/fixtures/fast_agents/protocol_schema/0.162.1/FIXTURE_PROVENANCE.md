# Protocol compatibility fixture: Codex CLI 0.162.1

This is a **reduced synthetic schema fixture** used by the fake Codex app-server tests.
It is **not** a raw or complete recording from a live Codex CLI session.

Only Codex CLI 0.162.1 is currently in the explicitly verified release registry.
For each newly supported CLI version, create a distinct sibling directory,
regenerate the complete app-server JSON schema from the real CLI, update
the reduced fixture after verifying field differences, add sanitized real-session
transcript golden tests, and run authenticated live smoke on supported hosts
before adding the version to the tested registry.

Never expand the tested range based on version numbers alone.
