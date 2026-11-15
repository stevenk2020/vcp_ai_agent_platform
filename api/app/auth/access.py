"""A person's own status and access request. These are the only routes (besides sign-out) that accept a
waiting session: a person who is still pending."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.auth import service
from app.auth.deps import CurrentPerson, client_ip, current_person

router = APIRouter(prefix="/v1")


@router.get("/access")
async def access_status(request: Request, person: CurrentPerson = Depends(current_person)):
    async with request.app.state.db.pool.connection() as conn:
        cursor = await conn.execute(
            "SELECT name, request_reason, request_submitted_at FROM app.users WHERE id = %s", (person.id,)
        )
        row = await cursor.fetchone()
    return {
        "email": person.email,
        "status": person.status,
        "role": person.role if person.status == "active" else None,
        "submitted": row["request_submitted_at"] is not None,
        "name": row["name"],
        "reason": row["request_reason"],
    }


class AccessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(None, max_length=80)
    reason: str | None = Field(None, max_length=500)


@router.post("/access/request")
async def submit_access_request(body: AccessRequest, request: Request,
                                person: CurrentPerson = Depends(current_person)):
    stored = await service.submit_request(request.app.state, person, body.name, body.reason, client_ip(request))
    if stored:
        return JSONResponse({"submitted": True}, status_code=201)
    return {"submitted": False, "already_submitted": True}
