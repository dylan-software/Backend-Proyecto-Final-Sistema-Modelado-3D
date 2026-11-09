"""Snapshot de una publicación (app/services/project_publication.py): mismo documento que el exportador
del frontend, validado con la importación de la FASE 6, código público con secrets y resumen público."""

import re
from types import SimpleNamespace

import pytest

from app.services import project_publication
from app.services.project_import import ProjectImportError, object_outside
from app.services.project_publication import (
    PUBLIC_CODE_LENGTH,
    PUBLIC_CODE_PATTERN,
    PublicationTooLarge,
    build_project_document,
    new_public_code,
    plan_copy,
    room_summary,
    snapshot_project,
)
from app.tests.test_room_import_routes import export_file

ROOM = SimpleNamespace(
    vertices=[{"x": 0, "y": 0}, {"x": 4, "y": 0}, {"x": 4, "y": 3}, {"x": 0, "y": 3}],
    ancho=4,
    profundidad=3,
    alto=2.5,
)


def piece(piece_id, name, center_x, center_z, width, height, depth, elevation=0.0):
    return SimpleNamespace(
        id=piece_id,
        nombre=name,
        ancho=width,
        alto=height,
        profundidad=depth,
        posicion_x=center_x,
        posicion_y=elevation,
        posicion_z=center_z,
    )


# Números exactos en binario: el centro y la esquina + mitad del frontend dan lo mismo.
TABLE = piece(7, "Mesa", 1.5, 1.25, 1.0, 0.75, 0.5)
PICTURE = piece(3, "Cuadro", 2.0, 0.0625, 0.5, 0.375, 0.125, elevation=1.5)


def api_item(row):
    """La vista de la API (esquina mínima) de un objeto, como la recibe el exportador del frontend."""
    return {
        "name": row.nombre,
        "x": row.posicion_x - row.ancho / 2,
        "y": row.posicion_z - row.profundidad / 2,
        "widthM": row.ancho,
        "depthM": row.profundidad,
        "heightM": row.alto,
        "elevationM": row.posicion_y,
    }


def test_the_document_matches_the_frontend_exporter():
    document = build_project_document(ROOM, [TABLE, PICTURE], "Mi sala")

    expected = export_file(
        {"name": "Mi sala", "heightM": 2.5, "vertices": ROOM.vertices},
        [api_item(PICTURE), api_item(TABLE)],
    )
    assert document == expected


def test_the_document_has_only_the_file_keys_and_items_in_id_order():
    document = build_project_document(ROOM, [TABLE, PICTURE], "Mi sala")

    assert set(document) == {"format", "version", "name", "room", "items"}
    assert set(document["room"]) == {"height_m", "vertices", "estimated"}
    assert [item["name"] for item in document["items"]] == ["Cuadro", "Mesa"]
    for item in document["items"]:
        assert set(item) == {"name", "category", "kind", "size_cm", "position", "wall", "parts"}
    assert [item["kind"] for item in document["items"]] == ["wall", "floor"]


def test_a_room_without_vertices_is_its_rectangle():
    row = SimpleNamespace(vertices=None, ancho=5, profundidad=2, alto=3)

    document = build_project_document(row, [], "Pasillo")

    assert document["room"]["vertices"] == [[0, 0], [5, 0], [5, 2], [0, 2]]
    assert document["room"]["height_m"] == 3


def test_the_snapshot_is_importable_and_the_copy_plan_keeps_every_object():
    document = snapshot_project(ROOM, [TABLE, PICTURE], "Mi sala")
    plan = plan_copy(document)

    assert plan.name == "Mi sala"
    assert [p["nombre"] for p in plan.pieces] == ["Cuadro", "Mesa"]
    table = plan.pieces[1]
    assert (table["posicion_x"], table["posicion_z"], table["posicion_y"]) == (1.5, 1.25, 0.0)
    assert (table["ancho"], table["alto"], table["profundidad"]) == pytest.approx((1.0, 0.75, 0.5))
    assert plan.pieces[0]["posicion_y"] == 1.5


def test_an_object_outside_the_room_cannot_be_published():
    outside = piece(1, "Sofá", 3.9, 1.0, 1.0, 0.8, 0.8)

    with pytest.raises(ProjectImportError) as error:
        snapshot_project(ROOM, [outside], "Mi sala")
    assert error.value.message == object_outside(1)


def test_a_snapshot_over_the_import_size_is_too_large(monkeypatch):
    monkeypatch.setattr(project_publication, "MAX_IMPORT_BYTES", 100)

    with pytest.raises(PublicationTooLarge):
        snapshot_project(ROOM, [TABLE], "Mi sala")


def test_the_public_code_comes_from_secrets(monkeypatch):
    calls = []

    def fake_token(nbytes):
        calls.append(nbytes)
        return "x" * PUBLIC_CODE_LENGTH

    monkeypatch.setattr(project_publication.secrets, "token_urlsafe", fake_token)

    assert new_public_code() == "x" * PUBLIC_CODE_LENGTH
    assert calls == [12]
    assert "random" not in vars(project_publication)


def test_public_codes_have_16_url_safe_characters_and_do_not_repeat():
    codes = {new_public_code() for _ in range(500)}

    assert len(codes) == 500
    assert all(re.fullmatch(PUBLIC_CODE_PATTERN, code) for code in codes)


def test_the_room_summary_has_only_public_figures():
    document = snapshot_project(ROOM, [TABLE, PICTURE], "Mi sala")

    summary = room_summary(document)

    assert summary == {
        "vertices": ROOM.vertices,
        "height_m": 2.5,
        "area_m2": 12.0,
        "item_count": 2,
    }
