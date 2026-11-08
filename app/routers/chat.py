from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.connection import get_session
from app.geometry.arrangement import rectangle_of
from app.models.space import Habitacion
from app.routers.auth import get_current_user
from app.routers.rooms import load_owned_room
from app.schemas.chat import ChatChangeOut, ChatReply, ChatRequest
from app.services.chat_actions import apply_actions, parse_tool_calls, prompt_objects
from app.services.omniroute import OmniRouteError
from app.services.room_objects import room_shape
from app.services.room_undo import commit_with_undo, get_room_undo_store, snapshot_room
from app.services.scene_assistant import AssistantReply, answer_about_room, room_context
from app.utils.rate_limit import enforce_ai_quota

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


def get_room_assistant():
    """FastAPI dependency returning the chat responder; tests override it with a fake.

    El responder recibe (context, conversation) y devuelve un AssistantReply; un str equivale a una
    respuesta sin acciones.
    """
    return answer_about_room


def _as_reply(reply):
    return reply if isinstance(reply, AssistantReply) else AssistantReply(str(reply))


async def _apply_and_save(session, row, room, reply, undo_store):
    """Aplica las acciones pedidas por el modelo y, si hubo cambios, los guarda en UN commit con su
    copia "chat" para Deshacer (CONTRATO-FASE2.md, 2.2). Devuelve el ChatReply con las frases del
    servidor."""
    before = snapshot_room(row, "chat")
    outcome = await apply_actions(session, row, parse_tool_calls(reply.tool_calls), room)
    if outcome.changes:
        await commit_with_undo(session, row, undo_store, before)
    changes = [ChatChangeOut(action=c.action, name=c.name, where=c.where) for c in outcome.changes]
    return ChatReply(content=" ".join(outcome.sentences), changes=changes)


@router.post("", response_model=ChatReply)
async def chat_about_room(
    body: ChatRequest,
    session: AsyncSession = Depends(get_session),
    assistant=Depends(get_room_assistant),
    undo_store=Depends(get_room_undo_store),
    current_user: str = Depends(get_current_user),
):
    """Responde la conversación sobre una sala de la cuenta y aplica las acciones que pida el modelo.

    El servidor arma los datos de la sala (medidas y objetos en palabras, sin coordenadas) desde la base
    de datos tras comprobar el dueño; el cliente solo envía roomId y los mensajes (máx. 20 de hasta 2000
    caracteres). Una sola llamada al modelo por mensaje, que gasta 1 de cuota. Si el modelo pide
    acciones (máx. 3), el texto de la respuesta lo escribe el servidor y los cambios se pueden deshacer
    con POST /rooms/{id}/undo.

    Returns:
        200 con {role: "assistant", content, changes: [{action, name, where}]}.

    Raises:
        HTTPException: 401 sin sesión válida, 404 sala inexistente o ajena, 422 conversación inválida o
            demasiado larga, 429 sin cupo de IA, 502 fallo de la IA, 503/500 fallo de base de datos
            (con rollback de todas las acciones del mensaje).
    """
    row = await load_owned_room(session, body.room_id, current_user, selectinload(Habitacion.muebles))
    room = room_shape(row)
    context = room_context(row.nombre, room, prompt_objects(row, room), rectangle_of(room) is not None)
    conversation = [{"role": m.role, "content": m.content} for m in body.messages]
    enforce_ai_quota(current_user)
    try:
        reply = _as_reply(await assistant(context, conversation))
    except OmniRouteError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"OmniRoute: {exc}") from exc
    if not reply.tool_calls:
        return ChatReply(content=reply.text)
    return await _apply_and_save(session, row, room, reply, undo_store)
