"""Roles & Permissions routes — v0.6.0"""

from fastapi import APIRouter, Depends, HTTPException
from server.db import MessengerDB
from server.main import get_db
from server.schemas_v06 import RoleCreate, RoleUpdate, RoleOut, MemberPermissionUpdate

router = APIRouter(prefix="/conversations/{conversation_id}/roles", tags=["roles"])


@router.post("", response_model=RoleOut, status_code=201)
def create_role(conversation_id: str, body: RoleCreate, db: MessengerDB = Depends(get_db)):
    conv = db.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    return db.create_role(conversation_id, body.name, body.permissions, body.is_default)


@router.get("", response_model=list[RoleOut])
def list_roles(conversation_id: str, db: MessengerDB = Depends(get_db)):
    return db.list_roles(conversation_id)


@router.get("/{role_id}", response_model=RoleOut)
def get_role(conversation_id: str, role_id: str, db: MessengerDB = Depends(get_db)):
    role = db.get_role(role_id)
    if not role or role["conversation_id"] != conversation_id:
        raise HTTPException(404, "Role not found")
    return role


@router.delete("/{role_id}", status_code=204)
def delete_role(conversation_id: str, role_id: str, db: MessengerDB = Depends(get_db)):
    role = db.get_role(role_id)
    if not role or role["conversation_id"] != conversation_id:
        raise HTTPException(404, "Role not found")
    db.delete_role(role_id)


@router.put("/members/{agent_id}/permissions")
def update_member_permissions(conversation_id: str, agent_id: str,
                               body: MemberPermissionUpdate, db: MessengerDB = Depends(get_db)):
    if body.permissions:
        db.set_member_permissions(conversation_id, agent_id, body.permissions)
    if body.role:
        db.set_member_role(conversation_id, agent_id, body.role)
    return {"status": "ok"}


@router.get("/members/{agent_id}/check")
def check_permission(conversation_id: str, agent_id: str, permission: str,
                      db: MessengerDB = Depends(get_db)):
    has = db.check_permission(conversation_id, agent_id, permission)
    return {"agent_id": agent_id, "permission": permission, "granted": has}
