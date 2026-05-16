"""Message Embeds routes — v0.6.0"""

from fastapi import APIRouter, Depends, HTTPException
from server.db import MessengerDB
from server.main import get_db
from server.schemas_v06 import EmbedCreate, EmbedOut

router = APIRouter(prefix="/messages/{message_id}/embeds", tags=["embeds"])


@router.post("", response_model=EmbedOut, status_code=201)
def create_embed(message_id: str, body: EmbedCreate, db: MessengerDB = Depends(get_db)):
    msg = db.get_message(message_id)
    if not msg:
        raise HTTPException(404, "Message not found")
    return db.create_embed(message_id, body.title, body.description, body.url,
                            body.image_url, body.thumbnail_url, body.embed_type)


@router.get("", response_model=list[EmbedOut])
def list_embeds(message_id: str, db: MessengerDB = Depends(get_db)):
    return db.get_embeds_for_message(message_id)


@router.delete("/{embed_id}", status_code=204)
def delete_embed(message_id: str, embed_id: str, db: MessengerDB = Depends(get_db)):
    emb = db.get_embed(embed_id)
    if not emb or emb["message_id"] != message_id:
        raise HTTPException(404, "Embed not found")
    db.delete_embed(embed_id)
