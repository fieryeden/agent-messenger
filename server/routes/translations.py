"""Message Translations routes — v0.6.0"""

from fastapi import APIRouter, Depends, HTTPException
from server.db import MessengerDB
from server.main import get_db
from server.schemas_v06 import TranslationCreate, TranslationOut

router = APIRouter(prefix="/messages/{message_id}/translations", tags=["translations"])


@router.post("", response_model=TranslationOut, status_code=201)
def create_translation(message_id: str, body: TranslationCreate, db: MessengerDB = Depends(get_db)):
    msg = db.get_message(message_id)
    if not msg:
        raise HTTPException(404, "Message not found")
    return db.create_translation(message_id, body.language, body.content)


@router.get("", response_model=list[TranslationOut])
def list_translations(message_id: str, db: MessengerDB = Depends(get_db)):
    return db.get_translations_for_message(message_id)


@router.get("/{language}", response_model=TranslationOut)
def get_translation(message_id: str, language: str, db: MessengerDB = Depends(get_db)):
    tr = db.get_translation_for_message(message_id, language)
    if not tr:
        raise HTTPException(404, f"Translation for '{language}' not found")
    return tr
