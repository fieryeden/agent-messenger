"""File attachment routes — upload, download, list, delete."""

import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query

from server.auth import AuthIdentity, get_current_identity
from server.db import MessengerDB
from server.db_accessor import get_db
from server.security import sanitize_uuid, sanitize_agent_id, sanitize_string

router = APIRouter(prefix="/files", tags=["files"])

UPLOAD_DIR = Path(os.getenv("MESSENGER_UPLOAD_DIR", "./data/uploads"))
MAX_FILE_SIZE = int(os.getenv("MESSENGER_MAX_FILE_SIZE", str(50 * 1024 * 1024)))  # 50 MB default
ALLOWED_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp",
    "video/mp4", "video/webm",
    "audio/mpeg", "audio/ogg", "audio/wav",
    "application/pdf", "application/json",
    "text/plain", "text/csv", "text/markdown",
    "application/zip", "application/x-tar", "application/gzip",
}


@router.post("/upload")
async def upload_file(
    message_id: str = Query(...),
    uploader_id: str = Query(...),
    file: UploadFile = File(...),
    expires_hours: int = Query(None, ge=1, le=720),
    db: MessengerDB = Depends(get_db),
):
    """Upload a file and attach it to a message."""
    message_id = sanitize_uuid(message_id)
    uploader_id = sanitize_agent_id(uploader_id)

    # Verify message exists and sender matches
    msg = db.get_message(message_id)
    if not msg:
        raise HTTPException(404, "Message not found")
    if msg["sender_id"] != uploader_id:
        raise HTTPException(403, "Only the message sender can attach files")

    # Validate content type
    content_type = file.content_type or "application/octet-stream"
    if content_type not in ALLOWED_TYPES:
        raise HTTPException(415, f"Unsupported file type: {content_type}")

    # Read and check size
    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(413, f"File too large (max {MAX_FILE_SIZE // 1024 // 1024}MB)")

    # Save to disk
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    storage_name = f"{uuid.uuid4().hex}_{sanitize_string(file.filename or 'unnamed')}"
    storage_path = UPLOAD_DIR / storage_name
    storage_path.write_bytes(data)

    expires_at = None
    if expires_hours:
        from datetime import timedelta
        from server.db import _now
        from datetime import datetime, timezone
        dt = datetime.now(timezone.utc) + timedelta(hours=expires_hours)
        expires_at = dt.isoformat()

    file_record = db.store_file(
        message_id=message_id,
        uploader_id=uploader_id,
        filename=sanitize_string(file.filename or "unnamed"),
        content_type=content_type,
        size_bytes=len(data),
        storage_path=str(storage_path),
        expires_at=expires_at,
    )
    return file_record


@router.get("/{file_id}")
def get_file(file_id: str, db: MessengerDB = Depends(get_db)):
    """Get file metadata by ID."""
    file_id = sanitize_uuid(file_id)
    record = db.get_file(file_id)
    if not record:
        raise HTTPException(404, "File not found")
    return record


@router.get("/message/{message_id}")
def list_message_files(message_id: str, db: MessengerDB = Depends(get_db)):
    """List all files attached to a message."""
    message_id = sanitize_uuid(message_id)
    return db.get_files_by_message(message_id)


@router.delete("/{file_id}")
def delete_file(file_id: str, agent_id: str = Query(...), db: MessengerDB = Depends(get_db), identity: AuthIdentity = Depends(get_current_identity)):
    """Delete a file (only uploader or admin)."""
    file_id = sanitize_uuid(file_id)
    agent_id_local = sanitize_agent_id(agent_id)

    record = db.get_file(file_id)
    if not record:
        raise HTTPException(404, "File not found")
    # Auth scope: verify identity matches agent_id unless admin
    if not identity.has_scope("admin") and identity.agent_id != agent_id_local:
        raise HTTPException(403, "Not authorized to delete as another agent")
    if record["uploader_id"] != agent_id_local:
        raise HTTPException(403, "Only the uploader can delete this file")

    # Remove from disk
    try:
        Path(record["storage_path"]).unlink(missing_ok=True)
    except Exception:
        pass

    if not db.delete_file(file_id):
        raise HTTPException(500, "Failed to delete file")
    return {"status": "deleted", "file_id": file_id}


@router.post("/cleanup")
def cleanup_expired(db: MessengerDB = Depends(get_db)):
    """Remove expired files from disk and DB."""
    # Get expired files before cleanup so we can delete from disk
    now = __import__("server.db", fromlist=["_now"])._now()
    expired = db.conn.execute(
        "SELECT storage_path FROM files WHERE expires_at IS NOT NULL AND expires_at < ?",
        (now,),
    ).fetchall()
    for (path,) in expired:
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass
    count = db.cleanup_expired_files()
    return {"deleted": count}
