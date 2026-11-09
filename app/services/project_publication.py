"""Copia congelada (snapshot) de un proyecto para la red social y su resumen público (CONTRATO-FASE9.md).

Al publicar, el servidor arma el mismo documento que exporta el frontend ("habita-project" v1,
JsonProjectExporter en src/lib/projectExport.ts) a partir de la base, y lo valida con la importación
de la FASE 6 (load_project): así toda publicación se puede copiar. El documento no lleva ids, correos,
grupo ni la descripción privada del proyecto.
"""

import json
import secrets

from app.geometry.room import Room
from app.schemas.project_file import PROJECT_FILE_FORMAT, PROJECT_FILE_VERSION
from app.services.project_import import MAX_IMPORT_BYTES, load_project

# secrets.token_urlsafe(12) da 16 caracteres de [A-Za-z0-9_-] (96 bits aleatorios criptográficos).
PUBLIC_CODE_BYTES = 12
PUBLIC_CODE_LENGTH = 16
PUBLIC_CODE_PATTERN = r"^[A-Za-z0-9_-]{16}$"


class PublicationTooLarge(ValueError):
    """El documento pasa del tamaño máximo de un archivo importable (413)."""


def new_public_code():
    """Código público aleatorio de una publicación. Siempre con secrets, nunca con random."""
    return secrets.token_urlsafe(PUBLIC_CODE_BYTES)


def build_project_document(room_row, pieces, title):
    """El documento "habita-project" v1 de una sala guardada y sus objetos, con `title` como nombre.

    Mismo mapeo que el exportador del frontend: centro de la huella en metros (la base ya guarda el
    centro), medidas en cm redondeadas a 4 decimales, objeto levantado = "wall", sin pared, categoría
    ni partes, y estimated false. Los objetos van en orden de id.
    """
    room = Room.from_record(room_row.vertices, room_row.ancho, room_row.profundidad, room_row.alto)
    return {
        "format": PROJECT_FILE_FORMAT,
        "version": PROJECT_FILE_VERSION,
        "name": title,
        "room": {
            "height_m": room.height_m,
            "vertices": [[vertex["x"], vertex["y"]] for vertex in room.vertices_json()],
            "estimated": False,
        },
        "items": [
            {
                "name": piece.nombre,
                "category": None,
                "kind": "wall" if piece.posicion_y > 0 else "floor",
                "size_cm": [round(side * 100, 4) for side in (piece.ancho, piece.alto, piece.profundidad)],
                "position": {
                    "x_m": round(piece.posicion_x, 6),
                    "z_m": round(piece.posicion_z, 6),
                    "elevation_m": piece.posicion_y,
                    "rotation_deg": 0,
                },
                "wall": None,
                "parts": [],
            }
            for piece in sorted(pieces, key=lambda piece: piece.id)
        ],
    }


def encode_document(document):
    """El documento como bytes UTF-8, tal como lo recibiría POST /rooms/import."""
    return json.dumps(document, ensure_ascii=False, allow_nan=False).encode("utf-8")


def snapshot_project(room_row, pieces, title):
    """Documento listo para guardar: armado, del tamaño de un archivo importable e importable.

    Raises:
        PublicationTooLarge: pasa de MAX_IMPORT_BYTES.
        ProjectImportError: la importación lo rechazaría (message es el detail fijo del 422).
    """
    document = build_project_document(room_row, pieces, title)
    raw = encode_document(document)
    if len(raw) > MAX_IMPORT_BYTES:
        raise PublicationTooLarge
    load_project(raw)
    return document


def plan_copy(document):
    """Plan de importación de una publicación guardada (la copia reutiliza load_project).

    Raises:
        ProjectImportError: el documento guardado ya no es importable.
    """
    return load_project(encode_document(document))


def room_summary(document):
    """Lo público de la sala de un documento: planta, alto, área y número de objetos."""
    room_data = document["room"]
    room = Room([tuple(vertex) for vertex in room_data["vertices"]], room_data["height_m"])
    return {
        "vertices": room.vertices_json(),
        "height_m": room.height_m,
        "area_m2": round(room.area_m2(), 2),
        "item_count": len(document["items"]),
    }
