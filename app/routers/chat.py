from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.connection import get_session
from app.geometry.layout import footprint_corner
from app.models.space import Habitacion
from app.routers.auth import get_current_user
from app.routers.rooms import load_owned_room, room_geometry
from app.schemas.chat import ChatReply, ChatRequest
from app.services.omniroute import OmniRouteError
from app.services.scene_assistant import answer_about_room, room_context

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


def get_room_assistant():
    """FastAPI dependency returning the chat responder; tests override it with a fake."""
    return answer_about_room


@router.post("", response_model=ChatReply)
async def chat_about_room(
    body: ChatRequest,
    session: AsyncSession = Depends(get_session),
    assistant=Depends(get_room_assistant),
    current_user: str = Depends(get_current_user),
):
    """Answer the conversation about one of the account's rooms.

    The server builds the room's context (polygon, height, area and placed furniture) from the database
    after checking the owner; the client only sends roomId and the messages. The assistant receives no
    images, only that structured data. At most 20 messages of up to 2000 characters each.

    Returns:
        200 with {role: "assistant", content}.

    Raises:
        HTTPException: 401 no valid session, 404 no such room for this account, 422 invalid or too
            long conversation, 502 OmniRoute failure, 503 database failure.
    """
    row = await load_owned_room(session, body.room_id, current_user, selectinload(Habitacion.muebles))
    pieces = []
    for piece in sorted(row.muebles, key=lambda piece: piece.id):
        x, y = footprint_corner(piece.posicion_x, piece.posicion_z, piece.ancho, piece.profundidad)
        pieces.append((piece.nombre, x, y, piece.ancho, piece.profundidad, piece.alto))
    context = room_context(row.nombre, room_geometry(row), pieces)
    conversation = [{"role": m.role, "content": m.content} for m in body.messages]
    try:
        reply = await assistant(context, conversation)
    except OmniRouteError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"OmniRoute: {exc}") from exc
    return ChatReply(content=reply)
