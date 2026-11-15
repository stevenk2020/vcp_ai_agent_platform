# Feature Specification: Foundation Environment and Data Layer

**Feature Branch**: `001-foundation-data-layer`

**Created**: 2026-11-15

**Status**: Draft

**Input**: User description: "Foundation environment and data layer (Phase 0) for the AI Agent Platform. A reproducible environment, started with one command, that hosts the shared data layer all later features depend on: isolated data areas, a locked-down read-only agent role, protected secrets, daily off-site backups with tested restore, repeatable schema migrations, and recorded Phase 0 verification items for the `sales_contract` source table."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Bring up the whole base stack with one command (Priority: P1)

The developer/operator starts the shared base services (database, cache, and on the VPS an HTTPS entry point) with a single command, on a developer machine or on the production VPS, and can see at a glance that everything is healthy.

**Why this priority**: Every later feature runs on this stack. Without a reproducible one-command start, nothing else can be built or demonstrated.

**Independent Test**: On a clean machine with only the documented prerequisites installed, run the start command and confirm all services report healthy within the target time, then stop and restart to confirm the same result.

**Acceptance Scenarios**:

1. **Given** a clean machine with the documented prerequisites, **When** the operator runs the single start command, **Then** all base services start in the correct order and each reports a healthy status.
2. **Given** the stack is running, **When** one service is stopped or crashes, **Then** the health status clearly shows which service is unhealthy.
3. **Given** the production VPS, **When** the stack is started, **Then** the entry point serves the platform over HTTPS with a valid certificate.
4. **Given** the stack was stopped, **When** it is started again, **Then** previously stored data is still present.

---

### User Story 2 - Separated data areas with a locked-down read-only agent role (Priority: P1)

The data store is divided into three isolated areas: platform data, a serving area holding copies of source tables, and a knowledge-base area for documents and embeddings. The AI agent will only ever connect through a dedicated role that can read the serving area and nothing else.

**Why this priority**: This is the core safety guarantee of the whole platform (constitution principles I and II): the agent must never be able to change data or reach platform data.

**Independent Test**: Connect as the agent role against a seeded test table in the serving area and run the permitted and forbidden operations listed below; every forbidden operation must fail and every permitted one succeed.

**Acceptance Scenarios**:

1. **Given** a table in the serving area, **When** connected as the agent role, **Then** reading rows succeeds.
2. **Given** the agent role, **When** it attempts to insert, update, delete, or change the structure of anything in the serving area, **Then** the operation is rejected.
3. **Given** the agent role, **When** it attempts to read or write anything in the platform-data or knowledge-base areas, **Then** the operation is rejected.
4. **Given** the agent role, **When** it runs a query lasting longer than 30 seconds, **Then** the query is cancelled.
5. **Given** the agent role, **When** it tries to start a writable transaction or turn off read-only behaviour, **Then** it cannot write.
6. **Given** a fresh environment, **When** the data areas are created, **Then** the required capabilities for vector search, fuzzy text search, and case-insensitive text are available.

---

### User Story 3 - Secrets stay out of the repository and out of unrelated services (Priority: P1)

The operator keeps credentials (source SQL Server access, upstream tokens, database passwords) in protected local files or volumes. They are never committed, and each service only receives the secrets it needs.

**Why this priority**: The project already holds real source-database credentials locally; a single accidental commit would be a serious incident.

**Independent Test**: Scan the repository and its history for credential values; inspect each running service's environment and confirm it has no secrets it does not need.

**Acceptance Scenarios**:

1. **Given** the repository, **When** it is scanned for credential values, **Then** none are found, and the local credentials file is ignored by version control.
2. **Given** the running stack, **When** a service that does not need source-database credentials is inspected, **Then** those credentials are not available to it.
3. **Given** a fresh clone, **When** the operator follows the documented setup, **Then** a template of required secret names (without values) tells them what to provide.

---

### User Story 4 - Daily off-site backup with a proven restore (Priority: P2)

The database is backed up automatically every day to a location outside the VPS, and there is a documented, tested procedure to restore it.

**Why this priority**: Protects against VPS loss; important, but the platform can be developed before the first real data exists.

**Independent Test**: Trigger a backup, restore it into an empty database, and compare structure and row counts against the original.

**Acceptance Scenarios**:

1. **Given** the stack is running, **When** a day passes, **Then** a new backup appears in the off-site location.
2. **Given** a backup, **When** it is restored into an empty database following the documented procedure, **Then** all areas and data match the original.
3. **Given** a backup job fails, **When** the next check runs, **Then** the operator can tell that the last backup is missing or stale.

---

### User Story 5 - Repeatable schema migrations (Priority: P2)

Later features can add or change tables by adding versioned migrations that are applied automatically and safely, without manual SQL.

**Why this priority**: Needed from the first feature that stores data, but not for the base stack to start.

**Independent Test**: Apply all migrations to an empty database, then apply them again; the second run must succeed with no changes.

**Acceptance Scenarios**:

1. **Given** an empty database, **When** migrations are applied, **Then** the expected structure exists.
2. **Given** an already-migrated database, **When** migrations are applied again, **Then** it completes without error and changes nothing.
3. **Given** a new migration is added, **When** migrations are applied, **Then** only the new one runs.

---

### User Story 6 - Phase 0 verification items are tracked (Priority: P3)

The open questions about the `sales_contract` source table are recorded as explicit open items with an owner and status, so the sync design is not started on guesses.

**Why this priority**: These do not block the base stack, but they must be resolved before the sync worker is built (constitution, Development Workflow).

**Independent Test**: Open the feature's verification list and confirm each of the four items has an owner, status, and a place to record the answer.

**Acceptance Scenarios**:

1. **Given** the feature output, **When** the operator reads the verification list, **Then** it contains: obtaining read access to the source SQL Server, measuring real row counts and delete-insert cycle of `sales_contract`, confirming whether the source job runs in a single transaction, and asking the DBA about snapshot isolation.
2. **Given** an item is answered, **When** the operator records the answer, **Then** the item shows as resolved with the finding attached.

### Edge Cases

- The start command is run twice, or while the stack is already running.
- A service starts before the database is ready.
- The VPS cannot obtain an HTTPS certificate (for example DNS not yet pointing at it).
- The disk is nearly full during a backup, or the off-site location is unreachable.
- A restore is attempted into a non-empty database.
- The agent role tries to change its own settings, create temporary objects, or call functions that write.
- A migration fails halfway through.
- Credentials are accidentally placed in a tracked file.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST start the base stack (database, cache, and on the VPS an HTTPS entry point) with a single documented command on a developer machine and on the production VPS.
- **FR-002**: The system MUST start services in dependency order and expose a health status for each service.
- **FR-003**: The system MUST preserve stored data across stop and start of the stack.
- **FR-004**: The system MUST provide three isolated data areas: platform data, serving (copies of source tables, writable only by the sync process), and knowledge base.
- **FR-005**: The system MUST make available the capabilities for vector similarity search, fuzzy text matching, and case-insensitive text comparison.
- **FR-006**: The system MUST provide a dedicated agent role that can only read the serving area, always operates read-only, and has queries cancelled after 30 seconds.
- **FR-007**: The agent role MUST be unable to read or write the platform-data or knowledge-base areas, and unable to modify data or structure in the serving area.
- **FR-008**: The system MUST load secrets from ignored local files or volumes and MUST NOT contain credential values in the repository or its history.
- **FR-009**: Each service MUST receive only the secrets it needs.
- **FR-010**: The system MUST provide a template listing required secret names without values.
- **FR-011**: The system MUST run an automated backup of the database daily to a location outside the VPS.
- **FR-012**: The system MUST provide a documented restore procedure that has been executed successfully at least once into an empty database.
- **FR-013**: The system MUST make a missing or stale backup visible to the operator.
- **FR-014**: The system MUST provide a versioned, repeatable schema migration mechanism that is safe to re-run and applies only pending migrations.
- **FR-015**: The system MUST record the four Phase 0 verification items for the `sales_contract` source table as open items with owner, status, and a place for findings.
- **FR-016**: The system MUST NOT include authentication, chat interface, agent loop, LLM gateway, sync worker logic, or topic definitions; these are separate features.

### Key Entities *(include if feature involves data)*

- **Data Area**: One of three isolated groupings of data (platform, serving, knowledge base), each with its own access rules.
- **Agent Role**: The restricted identity used by the AI agent; read-only on the serving area, no access elsewhere.
- **Secret**: A credential or token with a name, a consuming service, and a protected storage location; never in version control.
- **Backup**: A dated copy of the database stored off the VPS, with a success/failure status.
- **Migration**: A versioned, ordered change to the data structure with an applied/pending status.
- **Verification Item**: A tracked Phase 0 question about the source system with owner, status, and recorded finding.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: From a clean machine, the operator brings all base services to healthy with one command in under 10 minutes.
- **SC-002**: 100% of the permitted agent-role operations succeed and 100% of the forbidden operations (writes, structure changes, access to platform data or knowledge base) are rejected in the acceptance test suite.
- **SC-003**: Any agent-role query running longer than 30 seconds is cancelled in 100% of test runs.
- **SC-004**: A backup is produced every day, and a restore into an empty database reproduces all data areas with matching structure and row counts.
- **SC-005**: A credential scan of the repository and its history finds zero credential values.
- **SC-006**: Re-running all migrations on an already-migrated database completes with zero errors and zero changes.
- **SC-007**: All four Phase 0 verification items are recorded with an owner and status before the sync worker feature starts.

## Assumptions

- The project's established stack and deployment model apply (constitution and technical spec): Postgres-based data layer, Redis cache, Docker Compose on a single Ubuntu VPS, HTTPS reverse proxy.
- The operator is a single developer; no multi-person access control is needed for this feature.
- A domain name for the VPS will be available before HTTPS can be validated; until then HTTPS acceptance is verified on the VPS only.
- The off-site backup destination is provided by the operator; its type is chosen during planning.
- The first source table served later is `dbo.sales_contract`; no source data is loaded in this feature.
- Source SQL Server credentials already exist locally in an ignored file and will be rotated or restricted to a read-only account if needed.
- The sync process role that writes to the serving area is defined here as a permission boundary only; its logic is a later feature.
