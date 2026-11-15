"""A person's own status and access request. These are the only routes (besides sign-out) that accept a
waiting session: a person who is still pending."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.auth.deps import CurrentPerson, current_person

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
