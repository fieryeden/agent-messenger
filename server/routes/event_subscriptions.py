"""Event Subscriptions routes — v0.6.0"""

from fastapi import APIRouter, Depends, HTTPException, Query
from server.db import MessengerDB
from server.main import get_db
from server.schemas_v06 import EventSubscriptionCreate, EventSubscriptionOut

router = APIRouter(prefix="/agents/{agent_id}/event-subscriptions", tags=["event-subscriptions"])


@router.post("", response_model=EventSubscriptionOut, status_code=201)
def create_event_subscription(agent_id: str, body: EventSubscriptionCreate,
                                db: MessengerDB = Depends(get_db)):
    return db.create_event_subscription(agent_id, body.event_type, body.conversation_id, body.callback_url)


@router.get("", response_model=list[EventSubscriptionOut])
def list_event_subscriptions(agent_id: str, event_type: str = None,
                               limit: int = Query(100, ge=1, le=1000),
                               offset: int = Query(0, ge=0), db: MessengerDB = Depends(get_db)):
    return db.list_event_subscriptions(agent_id, event_type, limit, offset)


@router.delete("/{subscription_id}", status_code=204)
def delete_event_subscription(agent_id: str, subscription_id: str, db: MessengerDB = Depends(get_db)):
    sub = db.get_event_subscription(subscription_id)
    if not sub or sub["agent_id"] != agent_id:
        raise HTTPException(404, "Event subscription not found")
    db.delete_event_subscription(subscription_id)
