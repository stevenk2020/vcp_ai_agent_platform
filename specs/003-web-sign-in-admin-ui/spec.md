# Feature Specification: Web Pages for Sign-in, Access Request and Admin Management

**Feature Branch**: `dev_spec_03` (spec directory `003-web-sign-in-admin-ui`)

**Created**: 2026-11-15

**Status**: Draft

**Input**: User description: "Web pages for sign-in, access request and admin management (Phase 0 UI), built on top of feature 002 (passwordless-auth). Visitors sign in with Google, unapproved people submit an access request and wait, approved people reach the inside of the product, admins manage everything from a separate admin area instead of calling the API by hand."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Sign in and reach the inside area (Priority: P1)

A person opens the site address. If they are not signed in they see one clear "Sign in with Google" button. After signing in with an approved account they land on the inside area, which for now is a simple placeholder showing their email, their role and a sign-out button. The inside area is where the chat experience will appear in a later feature.

**Why this priority**: Without a front door nobody but the operator can use the platform. This is the smallest slice that replaces hand-made requests for ordinary users.

**Independent Test**: With one approved person in the allowlist, open the site signed out, press the Google button, finish the Google step, and see the inside area with the correct email and role; press sign out and be back at the sign-in view.

**Acceptance Scenarios**:

1. **Given** a signed-out visitor and mail delivery disabled on the server, **When** they open the site, **Then** they see the Google button and no email-code form.
2. **Given** a server with mail delivery enabled, **When** a signed-out visitor opens the site, **Then** the email-code form is shown as well as the Google button.
3. **Given** an approved person who finishes Google sign-in, **When** they are returned to the site, **Then** they see the inside area with their email and role.
4. **Given** a signed-in person, **When** they press sign out, **Then** their session ends and the next page load shows the sign-in view.
5. **Given** a sign-in attempt that ends as refused or error, **When** the person is returned to the site, **Then** they see a plain message explaining the outcome without revealing whether any email is on the allowlist.

---

### User Story 2 - Request access while waiting for approval (Priority: P1)

A person whose Google account is not yet approved still signs in with Google, but receives only a *waiting* sign-in: it identifies them and lets them see their status and submit an access request, and nothing else. They land on an access request page that tells them the request is waiting and that nothing else is available until an admin approves. They may add an optional display name and a short reason, once. Admins see that text when deciding. When an admin approves them, reloading the page takes them inside, with no second sign-in.

**Why this priority**: This is the other half of the front door: new people must be able to ask for access without anyone sending emails or messages around.

**Independent Test**: Sign in with a Google account that is not in the allowlist, land on the request page, submit a name and a reason, and confirm the page now shows "request sent" and that an admin sees both texts in the queue.

**Acceptance Scenarios**:

1. **Given** a Google sign-in by an unapproved person, **When** they return to the site, **Then** they see the access request page, not the inside area, and hold a waiting session that unlocks nothing except their own status, the request form and sign-out.
2. **Given** the request page, **When** they submit a name and reason, **Then** the text is stored once against that person and the page confirms the request is with the admins.
3. **Given** a request already submitted, **When** the same person tries to submit again, **Then** the earlier text is kept and the page says the request is already with the admins.
4. **Given** a visitor who has not completed sign-in, **When** they try to read or submit an access request, **Then** it is refused and nothing is stored; a request can only ever belong to the person whose sign-in it came from.
5. **Given** a pending person who comes back later (still signed in, or signing in again), **When** they open the site, **Then** they see the same waiting page and any text already submitted; if they were approved meanwhile, the page shows the inside area with the new role.
6. **Given** a waiting person, **When** they call any protected action other than their own status, the request form or sign-out, **Then** it is refused exactly as for a signed-out visitor.
7. **Given** a rejected or suspended person, **When** they sign in, **Then** they see a plain refusal, have no sign-in of any kind, and see no request form.

---

### User Story 3 - Approve or reject people from the admin area (Priority: P1)

An admin opens the admin area and sees the approval queue: each pending person with name, reason, email and time of request. They approve with a chosen role (admin, builder or user) or reject, each after a confirmation. A waiting person holds a waiting session, so after approval a reload takes them inside with the role the admin chose.

**Why this priority**: Approval is the gate that makes the access request meaningful and is the main reason to have a screen instead of `curl`.

**Independent Test**: With one pending person, an admin approves them as "user"; the person reloads and sees the inside area with role "user". Repeat with a second pending person and reject; they stay out.

**Acceptance Scenarios**:

1. **Given** pending people, **When** an admin opens the queue, **Then** each entry shows email, name, reason and request time, newest or oldest first as stated on the page.
2. **Given** a queue entry, **When** the admin chooses a role and confirms approval, **Then** the person becomes active with that role and disappears from the queue.
3. **Given** a queue entry, **When** the admin confirms rejection, **Then** the person leaves the queue and cannot sign in.
4. **Given** an empty queue, **When** the admin opens it, **Then** the page says there is nothing waiting.
5. **Given** two admins acting on the same person, **When** the second one acts after the first, **Then** the second sees a clear "already handled" message and nothing is changed twice (each page action states the status it saw, and the platform refuses it if the person has since changed).

---

### User Story 4 - Manage the allowlist and people (Priority: P2)

An admin can see the allowlist (email or domain, default role, who added it, when), add an entry, and remove an entry after a confirmation that explains removal ends that person's sessions at once. They can also see all people with status and role, change a role, suspend someone, and bring a suspended person back where the platform allows it. When an action is blocked because it would remove the last admin, the page says so clearly.

**Why this priority**: Approving one by one works for a few people; the allowlist and people list are how ~50 users are managed. It matters after the front door and approval work.

**Independent Test**: Add an email to the allowlist, confirm that person can sign in directly; remove the entry and confirm their session fails on the next request; change a role and suspend a person and confirm the effects.

**Acceptance Scenarios**:

1. **Given** the admin area, **When** an admin adds an email or a domain with a default role, **Then** it appears in the list with the admin's identity and the time.
2. **Given** an entry, **When** the admin confirms removal, **Then** the entry is gone and that person's existing session fails on the next request.
3. **Given** the people list, **When** an admin changes someone's role or suspends them, **Then** the list reflects it and the change takes effect on that person's next request.
4. **Given** the only active admin, **When** an admin tries to suspend or demote them, **Then** the action is refused with a clear message and nothing changes.
5. **Given** a suspended person, **When** an admin brings them back, **Then** they become active with a role the admin chooses (it uses the existing approve action; the person must sign in again because their earlier sessions were revoked).
6. **Given** a malformed email or domain, **When** an admin tries to add it, **Then** the page explains what is wrong and stores nothing.

---

### User Story 5 - Read the audit log (Priority: P2)

An admin opens the audit log and sees events newest first with time, who did it, what happened, target, outcome and IP address. They can page back through history and filter by action and by person or email.

**Why this priority**: It answers "who did what" without database access, but depends on nothing else being broken, so it follows the management screens.

**Independent Test**: After a few sign-ins and admin actions, open the log, filter by one action, filter by one person's email, and page to older events.

**Acceptance Scenarios**:

1. **Given** recorded events, **When** an admin opens the log, **Then** the newest events appear first with all stated columns.
2. **Given** more events than fit a page, **When** the admin asks for older events, **Then** the next older page is shown without repeats or gaps.
3. **Given** a filter by action or by person or email, **When** applied, **Then** only matching events are shown, and clearing it restores the full list.
4. **Given** no matching events, **When** a filter is applied, **Then** the page says nothing matched.

---

### User Story 6 - Everything stays locked to the right people (Priority: P1)

Pages never give admin function or data to anyone who is not a signed-in admin, whatever address they type. Anything people typed (names, reasons, emails) is shown as plain text, never as markup. The pages work on a phone, by keyboard, and in light or dark mode.

**Why this priority**: The pages sit in front of a security gate; a screen that leaks or can be abused undoes feature 002.

**Independent Test**: As a signed-out visitor, a normal user and an admin, open the admin address and check what each sees; submit a request whose name contains HTML and script text and check the admin sees it literally.

**Acceptance Scenarios**:

1. **Given** a signed-out visitor, **When** they open the admin address, **Then** they are sent to sign-in and receive no admin data.
2. **Given** a signed-in non-admin, **When** they open the admin address, **Then** they see a refusal page and receive no admin data.
3. **Given** a request name or reason, or an email, containing HTML or script text, **When** an admin views it anywhere, **Then** it is displayed literally as text and nothing executes.
4. **Given** any page, **When** its security headers are inspected, **Then** scripts are only allowed from the site itself (no inline script) and no third-party script, font or stylesheet is loaded.
5. **Given** a phone-width screen, **When** any page is used, **Then** it needs no sideways scrolling and every action is reachable by keyboard.
6. **Given** a signed-out visitor, **When** they use any public page, **Then** nothing on it reveals who is on the allowlist.

### Edge Cases

- A session ends (expiry, removal, suspension) while an admin page is open: the next action shows "you were signed out" and sends the person to sign-in, with no partial change.
- An approved person's role is changed while they have the inside area open: the new role shows on the next page load.
- A waiting person's sign-in ends (seven-day expiry, or an admin rejects them): the next action says they were signed out and offers sign-in; a rejected person then gets the plain refusal.
- A request name or reason is very long or contains control characters: it is limited to a sensible length and cleaned before storing; the form tells the person the limit.
- The server is unreachable or returns an error: pages show a plain "something went wrong, try again" message and never raw technical details.
- Two admins add the same email or domain: the second sees "already on the list".
- A Google person signs in with a different capitalisation of an address already known: they are treated as the same person.
- Someone opens an address that does not exist: a simple "page not found" page with a link to the start.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The site root MUST show a "Sign in with Google" button to signed-out visitors, and MUST show the email-code form only when the server reports that mail delivery is enabled.
- **FR-002**: A signed-in approved person MUST see an inside area placeholder showing their email and role and offering sign-out.
- **FR-003**: The pages MUST turn each sign-in outcome (signed in, pending, refused, error) into a plain, human message, and MUST NOT reveal whether any email is on the allowlist.
- **FR-004**: A person whose Google sign-in ends pending MUST receive a waiting session (a session whose person is not yet active) and MUST see an access request page stating that nothing else is available until approval. This amends feature 002 FR-008 ("MUST NOT create a session") at the operator's request; the waiting session grants no access to any protected action.
- **FR-005**: A pending person MUST be able to submit an optional display name and short reason once; later submissions MUST NOT overwrite the stored text, and the page MUST say so.
- **FR-006**: A waiting session MUST be accepted only for reading the person's own status and access request, submitting that request, and signing out; every other protected action (including all admin actions and the signed-in area's data) MUST treat it like no sign-in at all, and this MUST be proven by a test that walks every route. Reading or submitting a request without a valid sign-in MUST be refused and store nothing. Rejecting or suspending a person MUST end a waiting session as it ends any other.
- **FR-007**: Request name and reason MUST be stored with length limits (name up to 80 characters, reason up to 500) and MUST be visible to admins in the approval queue.
- **FR-008**: The admin area MUST list pending people with email, name, reason and request time, and let an admin approve with a chosen role (admin, builder, user) or reject, each with a confirmation step.
- **FR-009**: The admin area MUST list allowlist entries (email or domain, default role, who added, when), allow adding an entry with a default role, and allow removing an entry after a confirmation that states removal ends that person's sessions immediately.
- **FR-010**: The admin area MUST list all people with status and role, and let an admin change a role and suspend a person; it MUST offer reactivation for suspended and rejected people by approving them again with a role the admin chooses.
- **FR-011**: When the platform refuses an action to protect the last admin (or because another admin already handled the person), the page MUST show a clear message and MUST leave everything unchanged.
- **FR-012**: The admin area MUST show the audit log newest first and paged, with filters for action and for person or email, and columns for time, actor, action, target, outcome and IP address.
- **FR-013**: Every admin action performed from the pages MUST go through the platform's existing authenticated, origin-checked service actions, so all existing audit records, session revocations and last-admin protections apply unchanged.
- **FR-014**: Admin pages MUST show no admin data to signed-out visitors (who are sent to sign-in) or to non-admins (who see a refusal page), regardless of the address typed.
- **FR-015**: All user-supplied text (names, reasons, emails, audit details) MUST be displayed as plain text and never interpreted as markup or script.
- **FR-016**: Every page MUST be delivered with a strict content security policy that allows scripts, styles and images only from the site itself (no inline script) and MUST NOT load third-party scripts, fonts or stylesheets.
- **FR-017**: The session cookie MUST remain unreadable by page scripts, and pages MUST NOT contain or request any secret.
- **FR-018**: Pages MUST be usable at phone width without sideways scrolling, fully operable by keyboard with visible focus, and readable in both light and dark colour schemes; all text is English.
- **FR-019**: Pages and the service interface MUST be served from the same site address, so no cross-origin calls are needed.
- **FR-020**: The existing reverse proxy MUST serve the pages at the site root while continuing to route the service interface under `/v1/` unchanged; HTTPS and the production-only settings from feature 002 MUST be unchanged.
- **FR-021**: Apart from storing the optional request name and reason and issuing the waiting session of FR-004, this feature MUST NOT change how sessions, sign-in codes or the allowlist gate work; the sign-in form's response for allowed and unknown emails MUST stay identical.
- **FR-022**: Unexpected failures MUST show a plain "something went wrong, try again" message and MUST NOT display technical details.

### Key Entities

- **Person (user)**: someone who has signed in at least once; has status (pending, active, suspended, rejected), role, and now an optional request name and request reason with the time they were submitted.
- **Access request**: the optional name and reason a pending person submits once; belongs to that person; visible to admins.
- **Waiting session**: a normal seven-day session held by a person whose status is still pending; it only opens the status/request/sign-out actions and becomes a full session the moment the person is approved.
- **Allowlist entry**, **Session**, **Audit event**: unchanged from feature 002; the pages only display and drive them.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A new person can go from opening the site to having a waiting access request submitted in under 2 minutes, without any help.
- **SC-002**: An admin can approve a waiting person with a chosen role in at most 3 clicks from the admin area home.
- **SC-003**: An admin can add an allowlist entry, remove one, change a role, suspend a person and find a specific past event, all without using any command line tool.
- **SC-004**: In 100% of attempts, a signed-out visitor or non-admin who opens any admin address receives no admin data.
- **SC-005**: In 100% of test submissions, names, reasons and emails containing HTML or script text are displayed literally and nothing executes.
- **SC-006**: Every page loads and becomes usable in under 2 seconds on a normal connection for a team of about 50 people.
- **SC-007**: A removed or suspended person's next request after the admin action fails, verified from the pages alone.
- **SC-008**: No page, in any state, loads a resource from outside the site or runs an inline script.

## Assumptions

- This feature builds on feature 002; the sign-in, allowlist, approval, session and audit actions already exist and are reused as they are.
- Production runs with mail delivery disabled, so only Google sign-in is offered to visitors; the email-code form is built but hidden until a mail backend is enabled.
- The inside area is a placeholder; the real product experience arrives with the chat feature.
- "Reactivate" uses the existing approve action and needs no change to the gate logic.
- Waiting sessions reuse the existing session mechanism, expiry and revocation; the only change to feature 002 is that a pending person now receives one (amending 002 FR-008) while the allowlist gate, role checks and per-request status check stay as they are, so a pending person is still refused by every protected action.
- English-only text and a plain, framework-free look are acceptable for an internal tool of about 50 people.
- Audit log filtering by person uses the email address (exact or prefix match is acceptable).
- Role names stay admin, builder and user.
