"""Admin endpoints (role admin only)."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.admin import service
from app.auth.deps import CurrentUser, client_ip, require_role

router = APIRouter(prefix="/v1/admin")
admin_only = require_role("admin")
Role = Literal["admin", "builder", "user"]
Status = Literal["pending", "active", "suspended", "rejected"]
# optional guard sent by the pages: the status the admin saw; a different current status gives 409 already_handled
ExpectStatus = Query(None)


class EntryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["email", "domain"]
    value: str = Field(min_length=1, max_length=254)
    default_role: Role


class RoleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Role


@router.get("/allowlist")
async def list_allowlist(request: Request, _: CurrentUser = Depends(admin_only)):
    return {"items": await service.list_entries(request.app.state)}


@router.post("/allowlist", status_code=201)
async def add_allowlist(body: EntryCreate, request: Request, actor: CurrentUser = Depends(admin_only)):
    return await service.add_entry(request.app.state, actor, body.kind, body.value, body.default_role, client_ip(request))


@router.delete("/allowlist/{entry_id}", status_code=204)
async def remove_allowlist(entry_id: UUID, request: Request, actor: CurrentUser = Depends(admin_only)):
    await service.remove_entry(request.app.state, actor, entry_id, client_ip(request))
    return Response(status_code=204)


@router.get("/users")
async def list_users(request: Request, status: str | None = None, _: CurrentUser = Depends(admin_only)):
    return {"items": await service.list_users(request.app.state, status)}


@router.post("/users/{user_id}/approve")
async def approve(user_id: UUID, body: RoleBody, request: Request, expect_status: Status | None = ExpectStatus,
                  actor: CurrentUser = Depends(admin_only)):
    return await service.approve(request.app.state, actor, user_id, body.role, client_ip(request), expect_status)


@router.post("/users/{user_id}/reject")
async def reject(user_id: UUID, request: Request, expect_status: Status | None = ExpectStatus,
                 actor: CurrentUser = Depends(admin_only)):
    return await service.reject(request.app.state, actor, user_id, client_ip(request), expect_status)


@router.post("/users/{user_id}/suspend")
async def suspend(user_id: UUID, request: Request, expect_status: Status | None = ExpectStatus,
                  actor: CurrentUser = Depends(admin_only)):
    return await service.suspend(request.app.state, actor, user_id, client_ip(request), expect_status)


@router.post("/users/{user_id}/role")
async def change_role(user_id: UUID, body: RoleBody, request: Request, expect_status: Status | None = ExpectStatus,
                      actor: CurrentUser = Depends(admin_only)):
    return await service.set_role(request.app.state, actor, user_id, body.role, client_ip(request), expect_status)


@router.get("/audit")
async def read_audit(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    action: str | None = None,
    before: int | None = Query(None, ge=1),
    person: str | None = Query(None, max_length=254),
    _: CurrentUser = Depends(admin_only),
):
    from app import audit

    return {
        "items": await service.read_audit(request.app.state, limit, action, before, person),
        "actions": sorted(audit.ACTIONS),
    }
