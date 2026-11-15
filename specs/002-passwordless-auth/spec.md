# Feature Specification: Passwordless Sign-in with Admin Allowlist

**Feature Branch**: `002-passwordless-auth` (working on `main`; no feature branch created)

**Created**: 2026-11-15

**Status**: Draft

**Input**: User description: "Passwordless sign-in with an admin allowlist (Phase 0 auth) for the AI Agent Platform. Only people an admin has approved can sign in, without any password, and an admin can remove access at any time with immediate effect. Sign in with an emailed 6-digit code or with Google; one allowlist gate for both; no leaking of who is allowed; abuse limits; 7-day sessions that are revoked immediately on removal; three roles; a one-time way to create the first admin; email delivery with local capture in development and tests; and an audit trail that never stores codes or tokens in readable form."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - An approved person signs in with an emailed code (Priority: P1)

A person whose email is on the allowlist enters their email, receives a 6-digit code by email, types it in, and is signed in. They stay signed in for 7 days or until they sign out, and every request they make is recognised as theirs with their role.

**Why this priority**: Without a working sign-in nothing else in the platform can identify a user. This alone is a usable first slice.

**Independent Test**: With one allowlisted email and the mail capture turned on, request a code, enter it, and reach a protected action as that user; sign out and confirm the protected action is refused.

**Acceptance Scenarios**:

1. **Given** an allowlisted email, **When** the person requests a code and enters it correctly within 10 minutes, **Then** they are signed in with the role set on their allowlist entry.
2. **Given** a signed-in person, **When** they make any protected request, **Then** the system knows who they are and their role.
3. **Given** a signed-in person, **When** they sign out, **Then** their session stops working immediately.
4. **Given** a session that is 7 days old, **When** the person makes a request, **Then** it is refused and they must sign in again.
5. **Given** a valid code, **When** it is used once successfully, **Then** the same code is refused if tried again.

---

### User Story 2 - An admin decides who gets in and can cut access at once (Priority: P1)

An admin adds an email address or a whole email domain to the allowlist with a default role, lists the entries, and removes them. An admin also sees people who tried to sign in without being allowed (the approval queue) and approves them with a chosen role or rejects them. Removing someone ends all of their sessions immediately.

**Why this priority**: The promise "only approved people" is the point of this feature, and the ability to remove access right away is the safety valve.

**Independent Test**: As an admin add an email, sign in as that email, then remove the entry and confirm the existing session fails on the very next request; approve and reject a pending person.

**Acceptance Scenarios**:

1. **Given** an admin, **When** they add an email or a domain with a default role, **Then** it appears in the list with who added it and when.
2. **Given** an allowlisted domain, **When** a person with an email in that domain proves their email, **Then** they are let in with the domain entry's default role.
3. **Given** a signed-in user, **When** an admin removes their email from the allowlist (or rejects or suspends them), **Then** every session of that user fails on its next request.
4. **Given** people waiting in the approval queue, **When** an admin approves one and picks a role, **Then** that person can sign in; **When** an admin rejects one, **Then** they cannot.
5. **Given** a signed-in person who is not an admin, **When** they attempt any admin action, **Then** it is refused.
6. **Given** a pending person, **When** an admin later adds their email (or domain) to the allowlist, **Then** the next sign-in succeeds without a separate approval step.
7. **Given** a person who was suspended only because their allowlist entry was removed, **When** an admin adds a matching entry again, **Then** their next sign-in succeeds; **Given** a person suspended or rejected by an admin, **When** a matching entry exists, **Then** they stay blocked until an admin approves them.

---

### User Story 3 - Unapproved people are kept out without learning who is approved (Priority: P1)

A person who is not on the allowlist can still try to sign in and prove their email, but gets no session and ends up in the approval queue. The sign-in form never reveals whether an email is allowed.

**Why this priority**: It protects the list of approved people and still gives admins a way to discover new requests.

**Independent Test**: Submit one allowlisted and one non-allowlisted email through the sign-in form and compare the responses and timing; then confirm the non-allowlisted person has no access and shows up as pending.

**Acceptance Scenarios**:

1. **Given** an email that is not on the allowlist, **When** the person requests a code, **Then** the form's response is the same as for an allowlisted email.
2. **Given** a non-allowlisted email whose owner enters a valid code, **When** the code is checked, **Then** no session is created, the account is marked pending and appears in the approval queue.
3. **Given** a pending person, **When** they try to reach a protected action, **Then** it is refused until an admin approves them.
4. **Given** repeated requests for allowlisted and non-allowlisted emails, **When** their response times are compared, **Then** no consistent difference reveals which is which.

---

### User Story 4 - Sign in with a Google account (Priority: P2)

A person can choose "Sign in with Google" instead of the emailed code. Only an email address that Google reports as verified is accepted, and the same allowlist gate applies.

**Why this priority**: A convenience path; the emailed code already covers everyone, so this can follow.

**Independent Test**: With a stand-in identity provider, sign in with a verified allowlisted email and with an unverified email; the first gets a session, the second is refused.

**Acceptance Scenarios**:

1. **Given** a Google account whose email is verified and allowlisted, **When** the person signs in with Google, **Then** a session is created with the entry's role.
2. **Given** a Google account whose email is verified but not allowlisted, **When** the person signs in, **Then** no session is created and they appear in the approval queue.
3. **Given** a Google account whose email is not verified, **When** the person signs in, **Then** they are refused and nothing is created.
4. **Given** the same email used through both paths, **When** the person signs in either way, **Then** they are the same user with the same role.

---

### User Story 5 - Abuse is limited (Priority: P2)

Requests for codes and failed attempts are capped per email and per IP address in a time window. When a cap is hit the person sees a clear message that does not reveal whether the email is allowed.

**Why this priority**: Prevents email flooding and code guessing; important before real exposure but not needed for a first demo.

**Independent Test**: Request codes repeatedly for one email and from one address until the cap trips; make wrong guesses until the code dies.

**Acceptance Scenarios**:

1. **Given** the cap on codes per email, **When** it is exceeded, **Then** further requests are refused with the same kind of message for allowlisted and non-allowlisted emails and no more emails are sent.
2. **Given** the cap on codes per IP address, **When** it is exceeded, **Then** further requests from that address are refused.
3. **Given** a code, **When** 5 wrong attempts are made, **Then** the code no longer works even if the correct digits are then entered.
4. **Given** a code older than 10 minutes, **When** it is entered, **Then** it is refused.

---

### User Story 6 - The first admin can be created exactly once (Priority: P2)

Nobody can approve the first person until an admin exists, so there is one documented way for the operator to create the first admin. After it has been used, it cannot silently create more admins.

**Why this priority**: Required to start the system at all, but a one-time operator action, so after the core flows.

**Independent Test**: On an empty system run the first-admin procedure, sign in as that admin; run it again and confirm it does not create another admin.

**Acceptance Scenarios**:

1. **Given** a system with no admin, **When** the operator runs the documented first-admin procedure, **Then** that email becomes an admin who can sign in.
2. **Given** a system that already has an admin, **When** the same procedure is run again, **Then** it creates no additional admin and tells the operator why.
3. **Given** the procedure was used, **When** the system runs normally, **Then** no setting or file left behind can grant admin rights again.

---

### User Story 7 - Sign-in activity is auditable (Priority: P3)

Admins can review a record of sign-ins (successes and failures), approvals, rejections, removals and session revocations, each with time, who did it, and IP address. Codes and session tokens are never stored in readable form.

**Why this priority**: Needed for accountability, but it only observes the flows above.

**Independent Test**: Perform each audited action once and confirm one record per action; inspect stored data and logs for any readable code or token.

**Acceptance Scenarios**:

1. **Given** each audited event, **When** it happens, **Then** exactly one record exists with time, actor and IP address.
2. **Given** the stored data and the system logs, **When** they are searched for a code, session token or Google token that was used, **Then** none is found in readable form.

### Edge Cases

- A code is requested twice in a row: only the newest code works (earlier ones are cancelled).
- The same email is typed in different letter case or with surrounding spaces: it is the same person.
- An email matches both a single-email entry and a domain entry: the single-email entry's role wins.
- A user is removed from the allowlist and added back: they sign in again as normal and old sessions stay revoked, unless an admin suspended or rejected them in the meantime (then they stay blocked until approved).
- A signed-in user's role is changed by an admin: the new role applies on their next request.
- An admin removes their own entry or the last admin: the system refuses so it can never be left without an admin.
- Mail delivery fails: the person is told to try again later, no code stays valid that was never sent, and the failure is audited.
- Two sign-ins at the same time with the same code: only one succeeds.
- A person with a pending account signs in again before being approved: still no session, still one entry in the queue.
- A Google account's email changes after sign-up: the user is identified by the verified email at each sign-in.
- Cookies are blocked or the browser is closed: the person simply signs in again.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST let an admin add an allowlist entry for an email address or a whole email domain, with a default role (admin, builder or user).
- **FR-002**: The system MUST let an admin list and remove allowlist entries, and MUST record who added each entry and when.
- **FR-003**: The system MUST let a person request a 6-digit sign-in code for an email address and deliver it by email.
- **FR-004**: A sign-in code MUST expire 10 minutes after it is issued, MUST work only once, and MUST become unusable after 5 wrong attempts.
- **FR-005**: Requesting a new code MUST cancel any earlier unused code for the same email.
- **FR-006**: The system MUST let a person sign in with Google, and MUST accept only an email address that Google reports as verified.
- **FR-007**: After an email is proven by either path, the system MUST check the allowlist; if allowed it MUST create a session with the entry's default role.
- **FR-008**: If the email is not allowed, the system MUST create the account in a pending state, MUST NOT create a session, and MUST show the person in an admin approval queue.
- **FR-009**: The system MUST let an admin approve a pending person (choosing a role) or reject them, and MUST let an admin change a user's role or suspend a user.
- **FR-010**: When an email is removed from the allowlist, or a user is rejected or suspended, all of that user's sessions MUST stop working no later than their next request.
- **FR-011**: The sign-in form MUST give the same response, and no consistent timing difference, whether or not the email is allowlisted.
- **FR-012**: The system MUST limit codes sent per email and per IP address within a time window, and failed attempts per code, with a message that does not reveal whether the email is allowed.
- **FR-013**: A session MUST last 7 days, MUST be carried in a secure cookie that page scripts cannot read, and MUST be endable by the user (sign out).
- **FR-014**: The system MUST support three roles (admin, builder, user), MUST refuse admin actions to other roles, and MUST make the caller's identity and role available on every protected request.
- **FR-015**: The system MUST provide a documented one-time way to create the first admin, MUST refuse to use it to create further admins, and MUST NOT leave a permanent backdoor.
- **FR-016**: The system MUST refuse to remove, suspend or demote the last remaining admin.
- **FR-017**: The system MUST deliver codes through a transactional email service, and MUST capture codes locally instead of sending real email in development and tests.
- **FR-018**: The system MUST record sign-in successes and failures, approvals, rejections, removals, role changes and session revocations with time, actor and IP address.
- **FR-019**: The system MUST NOT store sign-in codes, session tokens or Google tokens in readable form in storage or logs; only irreversible keyed hashes of codes and hashes of session tokens may be kept. The one exception is the development-only mail capture sink of FR-017: it is refused in production, its files are deleted after at most one hour, and it is not part of the stored data this requirement covers.
- **FR-020**: The system MUST treat email addresses as case-insensitive and ignore surrounding spaces.
- **FR-021**: When an admin adds an email or domain that matches a pending person, or a person who was suspended only because their allowlist entry was removed, that person's next sign-in MUST succeed without a separate approval. People suspended or rejected by an admin MUST stay blocked until an admin approves them, even if they match an allowlist entry.
- **FR-022**: The system MUST NOT include a chat interface, agent loop, LLM gateway, topics, user profile pages, multi-factor authentication, passwords, or a visual admin dashboard; admin actions are offered as a service interface only.

### Key Entities *(include if feature involves data)*

- **User**: a person identified by a verified email; has a status (pending, active, suspended, rejected) and a role (admin, builder, user).
- **Allowlist entry**: an email address or a domain, with a default role, who added it and when.
- **Sign-in code**: a short-lived, single-use code tied to an email, with expiry time and attempt count; stored only as a hash.
- **Session**: a signed-in period for a user, with expiry (7 days) and a revoked marker; its token is stored only as a hash.
- **Approval request**: a pending person waiting in the queue for an admin decision.
- **Audit event**: a record of one action with time, actor, target, outcome and IP address.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An allowlisted person can go from entering their email to being signed in in under 60 seconds after the code email arrives.
- **SC-002**: 100% of codes are refused after 10 minutes, after 5 wrong attempts, and after one successful use, in the acceptance tests.
- **SC-003**: After an admin removes, rejects or suspends a person, 100% of that person's existing sessions are refused on their next request, with no waiting period.
- **SC-004**: For allowlisted and non-allowlisted emails the sign-in form returns identical visible responses, and over 200 trials the difference between median response times is under 20%.
- **SC-005**: 100% of admin actions attempted by non-admin users are refused.
- **SC-006**: A search of the stored data (database tables and logs, excluding the development-only mail capture sink) after a full test run finds zero readable codes, session tokens or Google tokens.
- **SC-007**: The first-admin procedure creates exactly one admin on an empty system and 0 additional admins when repeated.
- **SC-008**: 100% of the audited event types listed in FR-018 produce exactly one audit record each in the acceptance tests.
- **SC-009**: Once the per-email cap is reached, 0 additional emails are sent for that address within the window.

## Assumptions

- Email delivery uses a transactional email service chosen during planning; its credentials are provided by the operator later. Until then codes are captured locally (FR-017).
- Google sign-in needs a Google OAuth client created by the operator; it is not available yet, so Google sign-in is tested with a stand-in identity provider and the real hookup is a later operator task.
- A domain entry matches only that exact domain, not its subdomains.
- The sign-in email is written in Vietnamese followed by English, because the users are Vietnamese speakers; the code and its 10-minute lifetime are stated in both.
- The success criterion SC-001 (under 60 seconds) is an experience target checked by hand in the quickstart, not by an automated test.
- One account per email address; the same person using both paths is the same user.
- Default limits (adjustable settings, not fixed in code): 5 codes per email and 20 per IP address per hour; the 5-attempt and 10-minute rules are fixed by FR-004.
- The chat and admin screens come in a later feature; here admin actions are available only as a service interface, and a signed-in session is demonstrated with a simple protected check.
- The platform's shared data layer from feature 001 is available (database, schemas, roles, migrations, secrets handling, HTTPS entry point).
- Constitution principles I (permission-first security) and VI (auditability) apply: access control is enforced in the service that owns the data, not in the user interface.
