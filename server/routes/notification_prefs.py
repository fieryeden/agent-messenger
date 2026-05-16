"""Notification Preferences routes — v0.6.0"""

from fastapi import APIRouter, Depends, HTTPException
from server.db import MessengerDB
from server.main import get_db
from server.schemas_v06 import NotificationPrefsSet, NotificationPrefsOut

router = APIRouter(prefix="/agents/{agent_id}/notification-prefs", tags=["notification-prefs"])


@router.put("/conversations/{conversation_id}", response_model=NotificationPrefsOut)
def set_notification_prefs(agent_id: str, conversation_id: str, body: NotificationPrefsSet,
                            db: MessengerDB = Depends(get_db)):
    return db.set_notification_prefs(
        agent_id, conversation_id,
        muted=body.muted or False,
        mute_until=body.mute_until,
        mention_only=body.mention_only or False,
    )


@router.get("/conversations/{conversation_id}", response_model=NotificationPrefsOut)
def get_notification_prefs(agent_id: str, conversation_id: str, db: MessengerDB = Depends(get_db)):
    prefs = db.get_notification_prefs(agent_id, conversation_id)
    if not prefs:
        raise HTTPException(404, "No notification preferences found")
    return prefs


@router.get("", response_model=list[NotificationPrefsOut])
def list_notification_prefs(agent_id: str, db: MessengerDB = Depends(get_db)):
    return db.list_notification_prefs(agent_id)
