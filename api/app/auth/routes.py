"""Sign-in, sign-out and the protected `me` check."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from app.auth import oauth_state, service
from app.auth.deps import COOKIE_NAME, CurrentUser, client_ip, current_user
from app.errors import not_configured
from app.google import IdentityError, pkce_challenge
from app.security import InvalidEmail, hash_token, normalize_email

router = APIRouter(prefix="/v1")


class CodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=254)


class CodeVerify(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=254)
    code: str = Field(pattern=r"^\d{6}$")


def set_session_cookie(response: Response, token: str, max_age: int) -> None:
    response.set_cookie(
        COOKIE_NAME, token, max_age=max_age, path="/", secure=True, httponly=True, samesite="lax"
    )


@router.post("/auth/code/request", status_code=202)
async def request_code(body: CodeRequest, request: Request):
    await service.request_code(request.app.state, body.email, client_ip(request))
    return {"message": "If this address can receive mail, a code has been sent."}


@router.post("/auth/code/verify")
async def verify_code(body: CodeVerify, request: Request):
    state = request.app.state
    outcome = await service.verify_code(state, body.email, body.code, client_ip(request))
    payload = {"status": outcome.status}
    if outcome.status == "signed_in":
        payload["role"] = outcome.role
    response = JSONResponse(payload)
    if outcome.token:
        set_session_cookie(response, outcome.token, state.settings.session_seconds)
    return response


@router.post("/auth/logout", status_code=204)
async def logout(request: Request):
    state = request.app.state
    token = request.cookies.get(COOKIE_NAME)
    if token:
        async with state.db.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT s.id AS session_id, u.id, u.email, u.role FROM app.sessions s "
                "JOIN app.users u ON u.id = s.user_id "
                "WHERE s.token_hash = %s AND s.revoked_at IS NULL",
                (hash_token(token),),
            )
            row = await cursor.fetchone()
        if row:
            user = CurrentUser(id=row["id"], email=row["email"], role=row["role"], session_id=row["session_id"])
            await service.revoke_session(state, row["session_id"], user, client_ip(request))
    response = Response(status_code=204)
    response.delete_cookie(COOKIE_NAME, path="/", secure=True, httponly=True, samesite="lax")
    return response


@router.get("/me")
async def me(user: CurrentUser = Depends(current_user)):
    return {"id": str(user.id), "email": user.email, "role": user.role, "status": "active"}


# ---- Google ------------------------------------------------------------------------------------

def _redirect_uri(state) -> str:
    return f"{state.settings.public_origin}/v1/auth/google/callback"


def _require_google(state) -> None:
    if not state.settings.google_configured:
        raise not_configured("Google sign-in is not configured.")


def _finish(location: str, clear_flow: bool = True) -> RedirectResponse:
    response = RedirectResponse(location, status_code=302)
    if clear_flow:
        response.delete_cookie(oauth_state.COOKIE_NAME, path="/", secure=True, httponly=True, samesite="lax")
    return response


@router.get("/auth/google/start")
async def google_start(request: Request):
    state = request.app.state
    _require_google(state)
    flow_state, verifier, cookie = oauth_state.new_flow(state.settings.pepper, state.clock)
    url = state.identity.authorization_url(flow_state, pkce_challenge(verifier), _redirect_uri(state))
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        oauth_state.COOKIE_NAME, cookie, max_age=oauth_state.MAX_AGE, path="/", secure=True, httponly=True,
        samesite="lax",
    )
    return response


@router.get("/auth/google/callback")
async def google_callback(request: Request, code: str | None = None, state: str | None = None,
                          error: str | None = None):
    app_state = request.app.state
    _require_google(app_state)
    ip = client_ip(request)

    async def fail(reason: str):
        await service.record_google_failure(app_state, ip, reason)
        return _finish("/?signin=error")

    if error or not code or not state:
        return await fail("provider_error_or_missing_parameters")
    flow = oauth_state.read_flow(app_state.settings.pepper, app_state.clock, request.cookies.get(oauth_state.COOKIE_NAME))
    if flow is None or not hmac.compare_digest(flow[0], state):
        return await fail("bad_or_expired_state")
    try:
        email = normalize_email(await app_state.identity.verify_callback(code, flow[1], _redirect_uri(app_state)))
    except (IdentityError, InvalidEmail):
        return await fail("identity_not_verified")

    outcome = await service.complete_sign_in(app_state, email, ip, via="google")
    location = {"signed_in": "/?signin=ok", "pending": "/?signin=pending", "refused": "/?signin=refused"}[outcome.status]
    response = _finish(location)
    if outcome.token:
        set_session_cookie(response, outcome.token, app_state.settings.session_seconds)
    return response
