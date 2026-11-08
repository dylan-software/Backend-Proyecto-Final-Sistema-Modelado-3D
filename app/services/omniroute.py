import json

import httpx

from app.config import get_settings

SYSTEM_PROMPT = (
    "Eres un diseñador de interiores. Propón mobiliario para una habitación. "
    "Coordenadas en metros: x en [0, ancho], y hacia arriba en [0, alto], z en [0, profundidad]. "
    "Si recibes planta, es el polígono del piso como [{x, y}] en metros (su y es el eje z): cada mueble "
    "debe quedar entero dentro de ese polígono. Sin planta, la habitación es rectangular. "
    "posicion_x y posicion_z son el centro del mueble en planta; posicion_y es la altura de su base "
    "(0 = suelo). Ningún mueble puede salirse de la habitación ni solaparse con otro. "
    "Si recibes contexto_visual (brillo de 0 a 1 y colores dominantes de una foto de la habitación), "
    "elige mobiliario y colores que armonicen con él. "
    'Responde solo con JSON: {"descripcion": str, "muebles": [{"nombre": str, "ancho": float, '
    '"alto": float, "profundidad": float, "posicion_x": float, "posicion_y": float, "posicion_z": float}]}'
)


class OmniRouteError(Exception):
    """Raised when OmniRoute is not configured or returns an unusable answer."""


async def _completion_message(messages, temperature, tools=None):
    """Petición chat/completions compatible con OpenAI; devuelve el dict `message` de la respuesta.

    Con tools, el modelo puede pedir llamadas a funciones (tool_choice "auto").

    Raises:
        OmniRouteError: falta la configuración, la petición falló o la respuesta no tiene esa forma.
    """
    settings = get_settings()
    base_url = settings.omniroute_base_url.rstrip("/")
    api_key = settings.omniroute_api_key
    model = settings.omniroute_model
    if not api_key:
        raise OmniRouteError("OMNIROUTE_API_KEY is not set")
    if not model:
        raise OmniRouteError("OMNIROUTE_MODEL is not set")
    payload = {"model": model, "messages": messages, "temperature": temperature}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    try:
        async with httpx.AsyncClient(timeout=settings.omniroute_timeout) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise OmniRouteError(f"request failed: {exc}") from exc

    try:
        message = response.json()["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise OmniRouteError("unexpected response shape") from exc
    if not isinstance(message, dict):
        raise OmniRouteError("unexpected response shape")
    return message


async def chat_completion(messages, temperature=0.4):
    """Send an OpenAI-compatible chat completion request to OmniRoute and return the reply text."""
    message = await _completion_message(messages, temperature)
    content = message.get("content") or ""
    if not isinstance(content, str):
        raise OmniRouteError("unexpected response shape")
    return content


async def chat_completion_with_tools(messages, tools, temperature=0.4):
    """Como chat_completion, pero ofrece tools al modelo.

    Returns:
        (texto, tool_calls): el texto puede estar vacío si el modelo solo pidió llamadas; tool_calls es
        la lista tal como llega (cada una con function.name y function.arguments en JSON de texto).
        Quien la use debe validarla: son datos del modelo, no confiables.

    Raises:
        OmniRouteError: como chat_completion, o tool_calls no es una lista.
    """
    message = await _completion_message(messages, temperature, tools)
    content = message.get("content") or ""
    calls = message.get("tool_calls") or []
    if not isinstance(content, str) or not isinstance(calls, list):
        raise OmniRouteError("unexpected response shape")
    return content, calls


async def suggest_layout(dims, estilo, instrucciones=None, superficies=(), contexto_visual=None, planta=None):
    """Ask the model for a furniture layout. Returns {"descripcion": str, "muebles": [dict, ...]}.

    planta: optional floor polygon [{"x", "y"}, ...] in the same coordinates as dims, for rooms that are
    not rectangles. Without it the room is the box [0, ancho] x [0, alto] x [0, profundidad].
    """
    ancho, alto, profundidad = dims
    request = {
        "habitacion": {"ancho": ancho, "alto": alto, "profundidad": profundidad},
        "estilo": estilo,
        "instrucciones": instrucciones,
        "superficies_detectadas": [s["tipo"] for s in superficies],
        "contexto_visual": contexto_visual,
    }
    if planta is not None:
        request["planta"] = planta
    content = await chat_completion(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
        ]
    )
    data = _extract_json(content)
    muebles = data.get("muebles")
    if not isinstance(muebles, list):
        raise OmniRouteError("the reply has no 'muebles' list")
    return {"descripcion": str(data.get("descripcion", "")), "muebles": muebles}


def _extract_json(text):
    # Models often wrap JSON in prose or ``` fences; keep only the outermost object.
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise OmniRouteError("the reply does not contain JSON")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise OmniRouteError("the reply contains malformed JSON") from exc
    if not isinstance(data, dict):
        raise OmniRouteError("the reply JSON is not an object")
    return data
