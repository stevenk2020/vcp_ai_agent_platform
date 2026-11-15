# AI Agent Platform Constitution

## Core Principles

### I. Permission-First Security
The agent MUST only be able to do what the user it is chatting with is allowed to do. Every tool
call MUST carry the user's identity (short-lived JWT signed by the Agent Platform), and the target
service MUST enforce ACLs itself; permissions MUST NOT be enforced only in prompts. Authentication
is passwordless (email code or Google OAuth) and every path to an approved session MUST pass the
admin allowlist; removing an email from the allowlist MUST revoke all of that user's sessions. Login
responses MUST NOT reveal whether an email is on the allowlist. An unapproved person MAY hold a
*waiting session* that only allows reading their own status, submitting an access request and
signing out; every other protected action MUST still require an approved (active) person, and
removing, rejecting or suspending a person MUST end the waiting session like any other.
Rationale: no content filter stops all prompt injection, so privilege limits are the primary defense
(spec §7).

### II. Data Isolation and Safe SQL
The agent and Agent Platform MUST NOT access operational databases (e.g. SQL Server) directly; they
only reach data through the Topic service. Queries run on the Postgres `serving` schema with the
read-only `agent_ro` role (read-only transactions, `statement_timeout` 30s). Every SQL statement
MUST be parsed with `sqlglot`: a single SELECT only, only tables belonging to the topic, forced
`LIMIT`, system functions forbidden. Source reads MUST NOT use `NOLOCK`. Sync MUST load into a
staging table and atomically swap, guarded by a row-count sanity check, so the agent never sees a
partial or empty table.
Rationale: operational DBs must be protected and delete-insert jobs must never leak half-written
data (spec §6).

### III. Untrusted Content and Contained Execution
Tool results, RAG content, uploaded files and values stored in tables MUST be wrapped as data and
MUST NOT be treated as instructions. Tools with side effects (write, send, create ticket) MUST
require explicit user confirmation in the UI. The agent MUST NOT auto-render images or links from
model output. Python sandbox code MUST run in a fresh container per run with no network, non-root
user, read-only filesystem except `/out`, 1 CPU / 1 GB / 30 s limits and dropped capabilities;
only the sandbox runner may call Docker, and the API container MUST NOT mount `docker.sock`.
Uploads are validated by magic bytes, never by extension.
Rationale: model-written code and external content are the main routes to data leakage (spec §4.5,
§7).

### IV. Small Core, Pluggable Everything
The core is one agent loop with streaming and tool calling. Skills, memory, RAG, MCP servers and
guardrails MUST plug in through exactly three interfaces — `ContextProvider`, `Tool`, `Guard` —
without changing the core. The Data Topic service MUST be exposed to the agent as an internal MCP
server; the Agent Platform MUST NOT contain SQL Server or topic-specific logic. Model names in code
MUST be aliases (`main`, `small`) behind the LiteLLM gateway so upstreams (Codex proxy, API key,
BYOK) change by configuration only. Prompt prefixes (system prompt, skills, tools) MUST stay static
and ordered to preserve prompt caching. Multi-agent orchestration MUST NOT be added until usage
data shows one agent is insufficient.
Rationale: one developer on one VPS must keep the surface area small (spec §2, §3).

### V. Verifiable Answers
A wrong answer that sounds right is the biggest product risk. Every data answer MUST show the
executed SQL and `data_as_of`. Metrics MUST be predefined in the topic, not invented by the model.
A topic MUST NOT be published without at least 5 verified queries and an eval score of at least
85% on its eval set; the eval set MUST be re-run when the topic version, system prompt or model
changes. Published topic versions are immutable and old conversations keep pointing to the version
they used. Agent SQL MUST use the Postgres dialect only.
Rationale: user trust depends on correct, inspectable numbers (spec §4.1, §4.4, §10).

### VI. Observability and Auditability
Every chat turn MUST record prompt version, tool calls, SQL, row counts, tokens and cost in the
Agent Platform audit log, accessible only to admins. The Gateway MUST record per-user usage
(user, conversation, model, tokens in/out, latency) and MUST NOT log prompt content. Each user has
their own gateway virtual key for quota and revocation; users MUST never see upstream or virtual
tokens, and BYOK keys MUST be envelope-encrypted and never returned to the UI. Agent loops MUST be
bounded (default 12 iterations, 120 s, per-turn token budget).
Rationale: accountability and cost control with 50 users on a shared upstream (spec §3.1, §5, §7).

## Technology and Operational Constraints

- Stack: Next.js chat UI; Python/FastAPI with SSE for the Agent Platform; agent loop written on the
  OpenAI SDK; LiteLLM Proxy as gateway; MCP over streamable HTTP; Postgres 16 with pgvector,
  pg_trgm and citext; Redis; APScheduler sync worker. Deviations MUST be justified in the plan.
- Deployment: Docker Compose on a single Ubuntu VPS, Caddy for HTTPS, daily off-machine Postgres
  backups. The LLM proxy listens only on the internal Docker network.
- LLM upstream: Codex via proxy is a demo-only choice with a capped OpenAI API key as fallback in
  LiteLLM; the move to API keys MUST NOT require Agent Platform code changes.
- Secrets: credentials (e.g. `.env`, OAuth token files) MUST NOT be committed; they live in
  gitignored files or volumes. Database accounts used by the platform SHOULD be least-privilege.
- Limits are configuration, not constants: `MAX_UPLOAD_MB`, file count, PDF pages, compaction
  thresholds (50% / 75%), row limits and sandbox limits.
- Sensitive columns MUST be flagged in the column dictionary and masked or excluded from `serving`.

## Development Workflow

- The spec file "AI Agent Platform — Review & Technical Spec.md" is the architectural source of
  truth. Each feature follows the Spec Kit cycle (`specify → plan → tasks → implement`) and MUST
  cite the spec sections it implements instead of restating them; conflicts MUST be resolved by
  amending the spec or this constitution first.
- Every task MUST have an acceptance test. Security-relevant code (SQL validator, ACL checks,
  sandbox isolation, allowlist/session revocation) MUST ship with tests that cover the failure
  cases, including prompt-injection attempts that try to exceed the user's permissions.
- Delivery follows the roadmap phases (0 → 4); work from a later phase MUST NOT start before the
  earlier phase's "done when" criteria are met, unless explicitly decided and recorded.
- Open verification items from Phase 0 (real row counts and delete-insert cycle of source tables,
  whether the source job runs in one transaction) MUST be resolved before building the sync worker.
- Commits are small and focused; the `main` branch MUST always be deployable once Phase 1 begins.

## Governance

This constitution supersedes other practices for this project. Amendments require a written change
to this file stating the reason, an updated version and amendment date, and a check that dependent
specs, plans and tasks are still consistent. Versioning follows semantic versioning: MAJOR for
removing or redefining a principle, MINOR for adding a principle/section or materially expanding
guidance, PATCH for clarifications. Every plan MUST include a Constitution Check against the six
principles, and every review MUST verify compliance; any violation MUST be recorded and justified
in the plan's complexity tracking or the work is rejected.

**Version**: 1.0.1 | **Ratified**: 2026-11-15 | **Last Amended**: 2026-11-15

**Sync Impact (1.0.1, PATCH)**: Principle I clarified for the waiting session (feature 003). Checked: spec 002 FR-008 and its scenarios, 002 contracts/http-api.md, quickstart.md and tasks.md (amended, see the 002 Amendments section), spec 003 FR-004/FR-006; docs/auth.md and README are updated in feature 003 task T038. No other principle changed.
