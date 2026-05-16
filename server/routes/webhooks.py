"""Webhooks routes — v0.6.0"""

from fastapi import APIRouter, Depends, HTTPException, Query
from server.db import MessengerDB
from server.main import get_db
from server.schemas_v06 import WebhookCreate, WebhookUpdate, WebhookOut

router = APIRouter(prefix="/conversations/{conversation_id}/webhooks", tags=["webhooks"])


@router.post("", response_model=WebhookOut, status_code=201)
def create_webhook(conversation_id: str, body: WebhookCreate, agent_id: str,
                    db: MessengerDB = Depends(get_db)):
    conv = db.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    return db.create_webhook(conversation_id, body.url, body.events, body.secret, agent_id)


@router.get("", response_model=list[WebhookOut])
def list_webhooks(conversation_id: str, limit: int = Query(100, ge=1, le=1000),
                   offset: int = Query(0, ge=0), db: MessengerDB = Depends(get_db)):
    return db.list_webhooks(conversation_id, limit, offset)


@router.get("/{webhook_id}", response_model=WebhookOut)
def get_webhook(conversation_id: str, webhook_id: str, db: MessengerDB = Depends(get_db)):
    wh = db.get_webhook(webhook_id)
    if not wh or wh["conversation_id"] != conversation_id:
        raise HTTPException(404, "Webhook not found")
    return wh


@router.patch("/{webhook_id}", response_model=WebhookOut)
def update_webhook(conversation_id: str, webhook_id: str, body: WebhookUpdate,
                    db: MessengerDB = Depends(get_db)):
    wh = db.get_webhook(webhook_id)
    if not wh or wh["conversation_id"] != conversation_id:
        raise HTTPException(404, "Webhook not found")
    return db.update_webhook(webhook_id, body.url, body.events, body.active)


@router.delete("/{webhook_id}", status_code=204)
def delete_webhook(conversation_id: str, webhook_id: str, db: MessengerDB = Depends(get_db)):
    wh = db.get_webhook(webhook_id)
    if not wh or wh["conversation_id"] != conversation_id:
        raise HTTPException(404, "Webhook not found")
    db.delete_webhook(webhook_id)
