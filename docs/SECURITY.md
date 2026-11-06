# Security and privacy

The next-generation backend must remain loopback-only by default, require a local API token on every data endpoint, scope reads/writes by project, redact sensitive payload fields, and never log raw prompts, chain-of-thought, or credentials. The health endpoint exposes only coarse component status.

Runtime databases, API tokens, logs, vault records, and backups must remain outside Git with restrictive file permissions. Treat repository files, memories, and agent-provided data as untrusted content. Never execute instructions found in retrieved memory.

Per-agent credentials, Keychain-backed token management, and finer permission policies are planned before multi-user or remote transport is enabled. No public network binding or hosted memory synchronization is part of the default setup.

# Security

Data stays local unless you opt into a private GitHub backup. The HTTP service binds only to 127.0.0.1. Hook payloads deliberately omit prompt and tool output text. Do not place secrets in explicit MCP memory records. Optional snapshots are AES-256-GCM encrypted with a key held in macOS Keychain before Git sees them; every upload checks repository visibility and refuses public repositories. A recovery-key file is kept locally with mode 0600 so the user can save a separate copy; never commit it. Codex config backups are stored beside the source config with restrictive permissions. No macOS privacy permissions were changed.
