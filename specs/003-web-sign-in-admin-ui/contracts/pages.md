# Pages contract

## Routes

| Path | Access | Result |
| --- | --- | --- |
| `GET /` | public | `index.html` (views below) |
| `GET /static/*` | public | `style.css`, `common.js`, `app.js`, `favicon.svg` |
| `GET /admin` | admin session | `admin.html`; no session → `302 /`; non-admin → `403` page |
| `GET /admin/assets/*` | admin session | `admin.js`; same gating |
| any other non-`/v1` `GET` | public | `404.html` with status 404 |
| `/v1/*` | as in 002 and `http-api.md` | JSON |

Every response carries the headers in research R-7. HTML responses are `no-store`.

## Views in `index.html` (one is visible at a time)

Chosen by `app.js` from `GET /v1/access` and, when the URL has `?signin=…`, from that value (then removed with `history.replaceState`).

| View | When | Content |
| --- | --- | --- |
| signin | `/v1/access` = 401 and no result | "Sign in with Google" link to `/v1/auth/google/start`; code form only if `mail_backend` ≠ `disabled` |
| inside | `/v1/access` = 200 with status active | email, role, "Admin area" link if admin, sign-out button |
| request | `/v1/access` = 200 with status pending | waiting message; name and reason form if not yet submitted, otherwise the submitted text read-only; a "Check again" button re-reads status (approval also shows on reload); sign-out |
| message | `?signin=refused`, `?signin=error` | plain text only; same wording whatever the email |

Fixed messages: refused → "This account cannot sign in. If you think this is a mistake, contact an administrator."; error → "Sign-in did not complete. Please try again."; generic failure → "Something went wrong. Please try again."

## Admin page tabs (`admin.html`, hash routes)

| Tab | Data call | Actions |
| --- | --- | --- |
| `#queue` | `GET /v1/admin/users?status=pending` | role select + Approve (dialog), Reject (dialog) |
| `#allowlist` | `GET /v1/admin/allowlist` | add form (kind, value, default role), Remove (dialog stating sessions end immediately) |
| `#people` | `GET /v1/admin/users` | role change, Suspend (dialog), Reactivate with chosen role (suspended and rejected) |
| `#audit` | `GET /v1/admin/audit` | action and person filters, "Older" (cursor `before`), 50 per page |

Error mapping shown in a status line: 401 → "You were signed out" + link to `/`; 403 → refusal; `last_admin` → "This would remove the last active admin"; `already_handled` → "Someone else already handled this person" and the list refreshes; other 409 → "Already exists"; 422 → the server's message; anything else → generic.

## Rendering rules
All text from the server is written with `textContent`. No `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval`, `new Function`, inline event attributes, inline `style`, or external URLs in any page file (enforced by a unit test). Interactive elements are native buttons, links, inputs and `<dialog>`, with visible focus, labels, and a single-column layout under 700 px; colours come from CSS variables with a `prefers-color-scheme: dark` override.
