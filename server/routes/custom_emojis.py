"""Custom Emojis routes — v0.6.0"""

from fastapi import APIRouter, Depends, HTTPException, Query
from server.db import MessengerDB
from server.main import get_db
from server.schemas_v06 import CustomEmojiCreate, CustomEmojiOut

router = APIRouter(prefix="/custom-emojis", tags=["custom-emojis"])


@router.post("", response_model=CustomEmojiOut, status_code=201)
def create_custom_emoji(body: CustomEmojiCreate, agent_id: str, db: MessengerDB = Depends(get_db)):
    existing = db.get_custom_emoji_by_name(body.name)
    if existing:
        raise HTTPException(409, f"Emoji :{body.name}: already exists")
    return db.create_custom_emoji(body.name, body.image_url, agent_id, body.animated)


@router.get("", response_model=list[CustomEmojiOut])
def list_custom_emojis(limit: int = Query(100, ge=1, le=1000),
                        offset: int = Query(0, ge=0), db: MessengerDB = Depends(get_db)):
    return db.list_custom_emojis(limit, offset)


@router.get("/name/{name}", response_model=CustomEmojiOut)
def get_custom_emoji_by_name(name: str, db: MessengerDB = Depends(get_db)):
    emoji = db.get_custom_emoji_by_name(name)
    if not emoji:
        raise HTTPException(404, f"Emoji :{name}: not found")
    return emoji


@router.get("/{emoji_id}", response_model=CustomEmojiOut)
def get_custom_emoji(emoji_id: str, db: MessengerDB = Depends(get_db)):
    emoji = db.get_custom_emoji(emoji_id)
    if not emoji:
        raise HTTPException(404, "Emoji not found")
    return emoji


@router.delete("/{emoji_id}", status_code=204)
def delete_custom_emoji(emoji_id: str, db: MessengerDB = Depends(get_db)):
    emoji = db.get_custom_emoji(emoji_id)
    if not emoji:
        raise HTTPException(404, "Emoji not found")
    db.delete_custom_emoji(emoji_id)
