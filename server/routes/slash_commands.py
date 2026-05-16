"""Slash Commands routes — v0.6.0"""

from fastapi import APIRouter, Depends, HTTPException, Query
from server.db import MessengerDB
from server.main import get_db
from server.schemas_v06 import SlashCommandCreate, SlashCommandOut

router = APIRouter(prefix="/slash-commands", tags=["slash-commands"])


@router.post("", response_model=SlashCommandOut, status_code=201)
def create_slash_command(body: SlashCommandCreate, agent_id: str, db: MessengerDB = Depends(get_db)):
    existing = db.get_slash_command_by_name(body.name, body.conversation_id)
    if existing:
        raise HTTPException(409, f"Command {body.name} already exists")
    return db.create_slash_command(body.name, body.description, body.handler_url,
                                     body.conversation_id, agent_id)


@router.get("", response_model=list[SlashCommandOut])
def list_slash_commands(conversation_id: str = None, limit: int = Query(100, ge=1, le=1000),
                         offset: int = Query(0, ge=0), db: MessengerDB = Depends(get_db)):
    return db.list_slash_commands(conversation_id, limit, offset)


@router.get("/name/{name}", response_model=SlashCommandOut)
def get_slash_command_by_name(name: str, conversation_id: str = None, db: MessengerDB = Depends(get_db)):
    cmd = db.get_slash_command_by_name(name, conversation_id)
    if not cmd:
        raise HTTPException(404, f"Command {name} not found")
    return cmd


@router.get("/{command_id}", response_model=SlashCommandOut)
def get_slash_command(command_id: str, db: MessengerDB = Depends(get_db)):
    cmd = db.get_slash_command(command_id)
    if not cmd:
        raise HTTPException(404, "Command not found")
    return cmd


@router.delete("/{command_id}", status_code=204)
def delete_slash_command(command_id: str, db: MessengerDB = Depends(get_db)):
    cmd = db.get_slash_command(command_id)
    if not cmd:
        raise HTTPException(404, "Command not found")
    db.delete_slash_command(command_id)
