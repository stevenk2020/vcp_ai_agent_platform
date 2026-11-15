# Quickstart: validating feature 003

## Prerequisites
Stack up in production mode as in `docs/auth.md` (`APP_ENV=production`, `MAIL_BACKEND=disabled`, Google configured, the first admin created). Two Google accounts: your admin account and a second account that is NOT on the allowlist (a test account or a friend).

## Automated checks
```bash
make test-api     # pages, gating, headers, waiting session and request flow, deny-by-default route walk, additive admin fields, source lint
make test         # foundation tests still pass
make scan-secrets
```

## Manual walk-through (browser)
1. Signed out, open `https://vcp.onepercentdata.site/`: only the Google button is shown (no code form).
2. Sign in as the admin: inside view shows your email, role `admin`, an "Admin area" link.
3. Open a private window, sign in with the second (unlisted) account: the request view appears. Enter a name and a reason containing `<b>x</b><script>alert(1)</script>`, submit. The page says the request was sent; submit again → "already with the admins", text unchanged.
4. In the admin window open `#queue`: the person shows with the text displayed literally (no bold, no alert). Approve as `user` (confirm dialog).
5. In the private window just reload: the inside view appears with role `user` (no second sign-in).
6. In `#allowlist` add that email as `builder`, then remove it: the dialog explains sessions end; the private window's next request (reload) shows signed out.
7. In `#people` change a role, suspend someone, reactivate them with a role; try to suspend yourself while you are the only admin: the last-admin message appears.
8. In `#audit` find those events; filter by action and by the person's email; press "Older".
9. As the `user` (or any non-admin), open `/admin`: refusal page, no admin data. Signed out, open `/admin`: redirected to `/`.
10. Browser developer tools → Network: every response has the CSP header; no request leaves the site; Console shows no CSP violation. Resize to phone width: no sideways scrolling; Tab reaches every control.
