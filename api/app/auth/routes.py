"""Sign-in, sign-out and the protected `me` check."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.auth import service
from app.auth.deps import COOKIE_NAME, CurrentUser, client_ip, current_user
from app.security import hash_token

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
