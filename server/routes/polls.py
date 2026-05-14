"""Poll routes — create, vote, close, list, get results."""

from fastapi import APIRouter, Depends, HTTPException, Query

from server.db import MessengerDB
from server.db_accessor import get_db
from server.security import sanitize_uuid, sanitize_agent_id, sanitize_content

router = APIRouter(prefix="/polls", tags=["polls"])


@router.post("/create")
def create_poll(
    conversation_id: str = Query(...),
    creator_id: str = Query(...),
    question: str = Query(..., max_length=500),
    options: list[str] = Query(..., min_length=2, max_length=10),
    multi_vote: bool = Query(False),
    db: MessengerDB = Depends(get_db),
):
    """Create a new poll in a conversation."""
    conversation_id = sanitize_uuid(conversation_id)
    creator_id = sanitize_agent_id(creator_id)
    question = sanitize_content(question, max_length=500)

    # Sanitize each option
    clean_options = [sanitize_content(o, max_length=200) for o in options]
    if len(set(clean_options)) < len(clean_options):
        raise HTTPException(400, "Duplicate options not allowed")

    result = db.create_poll(conversation_id, creator_id, question, clean_options, multi_vote)
    return result


@router.get("/{poll_id}")
def get_poll(poll_id: str, db: MessengerDB = Depends(get_db)):
    """Get poll details and current results."""
    poll_id = sanitize_uuid(poll_id)
    poll = db.get_poll(poll_id)
    if not poll:
        raise HTTPException(404, "Poll not found")
    return poll


@router.post("/{poll_id}/vote")
def vote_poll(
    poll_id: str,
    option_index: int = Query(..., ge=0),
    agent_id: str = Query(...),
    db: MessengerDB = Depends(get_db),
):
    """Vote on a poll option."""
    poll_id = sanitize_uuid(poll_id)
    agent_id = sanitize_agent_id(agent_id)

    result = db.vote_poll(poll_id, agent_id, option_index)
    if result is None:
        raise HTTPException(400, "Cannot vote — poll closed, not found, or invalid option")
    return result


@router.post("/{poll_id}/close")
def close_poll(
    poll_id: str,
    agent_id: str = Query(...),
    db: MessengerDB = Depends(get_db),
):
    """Close a poll (only creator can close)."""
    poll_id = sanitize_uuid(poll_id)
    agent_id = sanitize_agent_id(agent_id)

    poll = db.get_poll(poll_id)
    if not poll:
        raise HTTPException(404, "Poll not found")
    if poll["creator_id"] != agent_id:
        raise HTTPException(403, "Only the poll creator can close it")
    if poll["closed_at"]:
        raise HTTPException(400, "Poll already closed")

    result = db.close_poll(poll_id)
    return result


@router.get("/conversation/{conversation_id}")
def list_polls(
    conversation_id: str,
    include_closed: bool = Query(False),
    db: MessengerDB = Depends(get_db),
):
    """List polls in a conversation."""
    conversation_id = sanitize_uuid(conversation_id)
    return db.list_polls(conversation_id, include_closed)
