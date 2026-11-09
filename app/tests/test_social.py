"""Red social "Habita" (CONTRATO-FASE9.md): publicar, despublicar, catálogo y copiar, con sus límites,
errores y la privacidad de lo público."""

import json

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.main import app
from app.models.space import Habitacion, MuebleSugerido, ProjectPublication
from app.models.user import Usuario
from app.routers.social import NO_SUCH_PUBLICATION, NOT_PUBLISHED, TOO_LARGE
from app.services import project_publication
from app.services.project_import import object_outside
from app.services.room_undo import RoomUndoStore, get_room_undo_store
from app.tests.auth_helpers import auth_headers
from app.tests.test_room_import_routes import PICTURE, RECTANGLE, SHELF, TABLE
from app.tests.test_rooms_api import ROOMS, created_id
from app.utils import rate_limit

pytestmark = pytest.mark.anyio

CATALOG = "/api/v1/social/publications"
SECRET_EMAIL = "ana.secreta@example.com"
SECRET_NAME = "Ana Secreta"


@pytest.fixture
def undo_store():
    fresh = RoomUndoStore()
    app.dependency_overrides[get_room_undo_store] = lambda: fresh
    yield fresh
    app.dependency_overrides.pop(get_room_undo_store, None)


def publication_url(room_id):
    return f"{ROOMS}/{room_id}/publication"


async def new_room(client, name="Dormitorio", items=()):
    room_id = await created_id(client, vertices=RECTANGLE, height_m=2.7, name=name)
    for body in items:
        response = await client.post(f"{ROOMS}/{room_id}/items", json=body)
        assert response.status_code == 201, response.text
    return room_id


async def publish(client, room_id, title="Mi dormitorio", description=None):
    return await client.put(publication_url(room_id), json={"title": title, "description": description})


async def published_code(client, room_id, **kwargs):
    response = await publish(client, room_id, **kwargs)
    assert response.status_code == 201, response.text
    return response.json()["code"]


async def catalog(client, page=1):
    response = await client.get(CATALOG, params={"page": page})
    assert response.status_code == 200, response.text
    return response.json()


async def stored_publications(db_sessions):
    async with db_sessions() as session:
        return list(await session.scalars(select(ProjectPublication).order_by(ProjectPublication.id)))


# --- Privado por defecto y publicar -------------------------------------------------------------------


async def test_a_project_is_private_until_it_is_published(user_a, user_b, db_sessions, undo_store):
    room_id = await new_room(user_a, items=[TABLE])

    status = await user_a.get(publication_url(room_id))

    assert status.status_code == 200
    assert status.json() == {"published": False, "publication": None}
    assert (await catalog(user_b))["items"] == []
    assert await stored_publications(db_sessions) == []


async def test_publishing_creates_a_public_publication(user_a, user_b, db_sessions, undo_store):
    room_id = await new_room(user_a, items=[TABLE, PICTURE])

    response = await publish(user_a, room_id, description="Cama junto a la ventana")

    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"code", "title", "description", "publishedAt", "updatedAt", "copies"}
    assert body["title"] == "Mi dormitorio"
    assert body["description"] == "Cama junto a la ventana"
    assert body["copies"] == 0
    assert len(body["code"]) == 16
    assert body["publishedAt"].endswith(("Z", "+00:00"))

    status = (await user_a.get(publication_url(room_id))).json()
    assert status == {"published": True, "publication": body}

    listed = (await catalog(user_b))["items"]
    assert [item["code"] for item in listed] == [body["code"]]
    card = listed[0]
    assert card["mine"] is False
    assert card["itemCount"] == 2
    assert card["room"] == {
        "vertices": [{"x": x, "y": y} for x, y in RECTANGLE],
        "heightM": 2.7,
        "areaM2": 12.0,
    }
    assert (await catalog(user_a))["items"][0]["mine"] is True


async def test_the_project_description_is_not_published(user_a, user_b, db_sessions, undo_store):
    room_id = await new_room(user_a)
    await user_a.patch(f"{ROOMS}/{room_id}", json={"description": "Nota privada: llave bajo la maceta"})

    await published_code(user_a, room_id)

    card = (await catalog(user_b))["items"][0]
    assert card["description"] is None
    assert "maceta" not in json.dumps(card)
    (row,) = await stored_publications(db_sessions)
    assert "maceta" not in json.dumps(row.document)


async def test_republishing_refreshes_the_snapshot_and_keeps_code_and_copies(
    user_a, user_b, db_sessions, undo_store
):
    room_id = await new_room(user_a, items=[TABLE])
    code = await published_code(user_a, room_id)
    assert (await user_b.post(f"{CATALOG}/{code}/copy")).status_code == 201
    assert (await user_a.post(f"{ROOMS}/{room_id}/items", json=SHELF)).status_code == 201

    # Hasta actualizar, la red sigue mostrando la copia congelada.
    assert (await catalog(user_b))["items"][0]["itemCount"] == 1

    response = await publish(user_a, room_id, title="Dormitorio con repisa", description="Ahora con repisa")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == code
    assert body["copies"] == 1
    assert body["title"] == "Dormitorio con repisa"
    card = (await catalog(user_b))["items"][0]
    assert (card["itemCount"], card["title"], card["copies"]) == (2, "Dormitorio con repisa", 1)
    assert len(await stored_publications(db_sessions)) == 1


async def test_an_object_outside_the_room_cannot_be_published(user_a, db_sessions, undo_store):
    room_id = await new_room(user_a)
    async with db_sessions() as session:
        session.add(
            MuebleSugerido(
                habitacion_id=room_id,
                nombre="Sofá",
                ancho=1.0,
                alto=0.8,
                profundidad=0.8,
                posicion_x=3.9,
                posicion_y=0.0,
                posicion_z=1.0,
            )
        )
        await session.commit()

    response = await publish(user_a, room_id)

    assert response.status_code == 422
    assert response.json() == {"detail": object_outside(1)}
    assert await stored_publications(db_sessions) == []


async def test_a_snapshot_over_256_kb_is_413(user_a, db_sessions, undo_store, monkeypatch):
    room_id = await new_room(user_a, items=[TABLE])
    monkeypatch.setattr(project_publication, "MAX_IMPORT_BYTES", 100)

    response = await publish(user_a, room_id)

    assert response.status_code == 413
    assert response.json() == {"detail": TOO_LARGE}
    assert await stored_publications(db_sessions) == []


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"title": ""},
        {"title": "<b>Sala</b>"},
        {"title": "x" * 121},
        {"title": "Sala", "description": "x" * 1001},
        {"title": "Sala", "userId": "user-b"},
        {"title": 5},
    ],
)
async def test_an_invalid_body_is_422_without_echo(user_a, db_sessions, body):
    room_id = await new_room(user_a)

    response = await user_a.put(publication_url(room_id), json=body)

    assert response.status_code == 422
    text = response.text
    assert "<b>" not in text and "user-b" not in text and "x" * 50 not in text
    assert await stored_publications(db_sessions) == []


# --- Despublicar ----------------------------------------------------------------------------------------


async def test_unpublishing_deletes_the_row_and_hides_it(user_a, user_b, db_sessions, undo_store):
    room_id = await new_room(user_a)
    code = await published_code(user_a, room_id)

    response = await user_a.delete(publication_url(room_id))

    assert response.status_code == 204
    assert await stored_publications(db_sessions) == []
    assert (await catalog(user_b))["items"] == []
    copy = await user_b.post(f"{CATALOG}/{code}/copy")
    assert copy.status_code == 404
    assert copy.json() == {"detail": NO_SUCH_PUBLICATION}
    again = await user_a.delete(publication_url(room_id))
    assert again.status_code == 404
    assert again.json() == {"detail": NOT_PUBLISHED}


async def test_a_private_row_is_not_listed_nor_copyable(user_a, user_b, db_sessions):
    room_id = await new_room(user_a)
    code = await published_code(user_a, room_id)
    async with db_sessions() as session:
        row = await session.scalar(select(ProjectPublication))
        row.public = False
        await session.commit()

    assert (await catalog(user_b))["items"] == []
    assert (await user_b.post(f"{CATALOG}/{code}/copy")).status_code == 404


# --- Dueño ----------------------------------------------------------------------------------------------


async def test_another_account_gets_404_on_the_owner_routes(user_a, user_b, db_sessions):
    room_id = await new_room(user_a)
    await published_code(user_a, room_id)

    for response in (
        await user_b.get(publication_url(room_id)),
        await publish(user_b, room_id),
        await user_b.delete(publication_url(room_id)),
    ):
        assert response.status_code == 404
    (row,) = await stored_publications(db_sessions)
    assert row.title == "Mi dormitorio"


async def test_every_route_needs_a_session(client, user_a, db_sessions):
    room_id = await new_room(user_a)
    code = await published_code(user_a, room_id)

    for response in (
        await client.get(publication_url(room_id)),
        await client.put(publication_url(room_id), json={"title": "Sala"}),
        await client.delete(publication_url(room_id)),
        await client.get(CATALOG),
        await client.post(f"{CATALOG}/{code}/copy"),
    ):
        assert response.status_code == 401


# --- Catálogo -------------------------------------------------------------------------------------------


async def test_the_catalog_has_24_per_page_newest_first(user_a, user_b, db_sessions, monkeypatch):
    monkeypatch.setenv("MAX_PUBLICATIONS_PER_USER", "30")
    monkeypatch.setenv("PUBLICATION_CHANGES_PER_HOUR", "30")
    get_settings.cache_clear()
    rate_limit.reset_all()
    try:
        codes = []
        for number in range(25):
            room_id = await new_room(user_a, name=f"Sala {number}")
            codes.append(await published_code(user_a, room_id, title=f"Sala {number}"))
    finally:
        get_settings.cache_clear()

    first = await catalog(user_b, 1)
    second = await catalog(user_b, 2)

    assert [item["code"] for item in first["items"]] == codes[::-1][:24]
    assert (first["page"], first["hasMore"]) == (1, True)
    assert [item["code"] for item in second["items"]] == [codes[0]]
    assert (second["page"], second["hasMore"]) == (2, False)


@pytest.mark.parametrize("page", ["0", "101", "x", "-1", "1.5"])
async def test_a_page_out_of_range_is_422(user_a, db_sessions, page):
    response = await user_a.get(CATALOG, params={"page": page})

    assert response.status_code == 422


async def test_the_public_content_has_no_personal_data(user_a, user_b, db_sessions, undo_store):
    async with db_sessions() as session:
        session.add(Usuario(nombre=SECRET_NAME, email=SECRET_EMAIL, password_hash="scrypt$00$00"))
        await session.commit()
    room_id = await new_room(user_a, items=[TABLE, PICTURE])
    await published_code(user_a, room_id)
    async with db_sessions() as session:
        row = await session.scalar(select(ProjectPublication))
        internal_id, owner = row.id, row.user_id

    listed = await user_b.get(CATALOG)
    copied = await user_b.post(f"{CATALOG}/{listed.json()['items'][0]['code']}/copy")

    card = listed.json()["items"][0]
    assert set(card) == {
        "code",
        "title",
        "description",
        "publishedAt",
        "updatedAt",
        "copies",
        "mine",
        "room",
        "itemCount",
    }
    assert set(card["room"]) == {"vertices", "heightM", "areaM2"}
    for text in (listed.text, copied.text):
        assert owner not in text
        assert SECRET_EMAIL not in text and SECRET_NAME not in text
        for key in ('"id":' + str(internal_id) + ",", "userId", "user_id", "email", "roomId"):
            assert key not in text
    assert '"id"' not in listed.text
    # La copia solo lleva los ids NUEVOS de quien copia, nunca los de la sala original.
    copied_body = copied.json()
    assert copied_body["room"]["id"] != room_id


async def test_the_stored_document_has_only_the_file_keys(user_a, db_sessions, undo_store):
    room_id = await new_room(user_a, items=[TABLE])
    await published_code(user_a, room_id)

    (row,) = await stored_publications(db_sessions)

    assert set(row.document) == {"format", "version", "name", "room", "items"}
    assert row.document["name"] == "Mi dormitorio"
    assert set(row.document["items"][0]) == {
        "name",
        "category",
        "kind",
        "size_cm",
        "position",
        "wall",
        "parts",
    }
    assert row.user_id == "user-a" and row.room_id == room_id and row.public is True


# --- Copiar ---------------------------------------------------------------------------------------------


async def test_copying_creates_a_new_project_of_the_copier(user_a, user_b, db_sessions, undo_store):
    room_id = await new_room(user_a, items=[TABLE, PICTURE, SHELF])
    original = (await user_a.get(f"{ROOMS}/{room_id}/items")).json()["items"]
    code = await published_code(user_a, room_id, title="Dormitorio de Ana")

    response = await user_b.post(f"{CATALOG}/{code}/copy")

    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"room", "items"}
    assert body["room"]["name"] == "Dormitorio de Ana"
    assert body["room"]["id"] != room_id
    assert body["room"].get("groupId") is None and body["room"].get("description") is None
    keys = ("name", "x", "y", "rotation", "widthM", "depthM", "heightM", "elevationM")
    for before, after in zip(original, body["items"], strict=True):
        assert after["id"] != before["id"]
        for key in keys:
            assert after[key] == pytest.approx(before[key], abs=1e-6), key
    # El proyecto es de quien copia; el original sigue intacto.
    assert (await user_b.get(f"{ROOMS}/{body['room']['id']}")).status_code == 200
    assert (await user_a.get(f"{ROOMS}/{body['room']['id']}")).status_code == 404
    async with db_sessions() as session:
        rooms = await session.scalar(select(func.count()).select_from(Habitacion))
    assert rooms == 2
    assert (await catalog(user_b))["items"][0]["copies"] == 1


async def test_an_account_can_copy_its_own_publication(user_a, db_sessions, undo_store):
    room_id = await new_room(user_a, items=[TABLE])
    code = await published_code(user_a, room_id)

    response = await user_a.post(f"{CATALOG}/{code}/copy")

    assert response.status_code == 201
    assert response.json()["room"]["id"] != room_id


@pytest.mark.parametrize("code", ["short", "x" * 17, "with.dot........", "with space......"])
async def test_a_code_with_another_shape_is_422(user_a, db_sessions, code):
    response = await user_a.post(f"{CATALOG}/{code}/copy")

    assert response.status_code == 422


async def test_an_unknown_code_is_404(user_a, db_sessions):
    response = await user_a.post(f"{CATALOG}/{'A' * 16}/copy")

    assert response.status_code == 404
    assert response.json() == {"detail": NO_SUCH_PUBLICATION}


async def test_a_failed_copy_does_not_count_the_copy(user_a, user_b, db_sessions, undo_store, monkeypatch):
    room_id = await new_room(user_a, items=[TABLE])
    code = await published_code(user_a, room_id)

    async def broken_commit(session):
        from app.routers.errors import DATABASE_ERRORS, database_error

        await session.rollback()
        raise database_error(DATABASE_ERRORS[1]("conexión perdida"))

    from app.routers import rooms

    monkeypatch.setattr(rooms, "_commit", broken_commit)

    response = await user_b.post(f"{CATALOG}/{code}/copy")

    assert response.status_code == 503
    (row,) = await stored_publications(db_sessions)
    assert row.copies == 0
    async with db_sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Habitacion)) == 1


# --- Límites --------------------------------------------------------------------------------------------


async def test_the_publication_cap_is_409_only_for_a_new_one(user_a, db_sessions, monkeypatch):
    monkeypatch.setenv("MAX_PUBLICATIONS_PER_USER", "2")
    get_settings.cache_clear()
    try:
        first = await new_room(user_a, name="Uno")
        await published_code(user_a, first)
        await published_code(user_a, await new_room(user_a, name="Dos"))
        third = await new_room(user_a, name="Tres")

        refused = await publish(user_a, third)
        updated = await publish(user_a, first, title="Uno otra vez")
    finally:
        get_settings.cache_clear()

    assert refused.status_code == 409
    assert "2 proyectos publicados" in refused.json()["detail"]
    assert updated.status_code == 200
    assert len(await stored_publications(db_sessions)) == 2


async def test_publication_changes_are_limited_per_hour(user_a, user_b, db_sessions, monkeypatch):
    monkeypatch.setenv("PUBLICATION_CHANGES_PER_HOUR", "2")
    get_settings.cache_clear()
    rate_limit.reset_all()
    try:
        room_id = await new_room(user_a)
        await published_code(user_a, room_id)
        assert (await user_a.delete(publication_url(room_id))).status_code == 204

        refused = await publish(user_a, room_id)
        other_account = await publish(user_b, await new_room(user_b))
    finally:
        get_settings.cache_clear()

    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) > 0
    assert refused.json() == {"detail": rate_limit.PUBLICATION_CHANGES_LIMIT_REACHED}
    assert other_account.status_code == 201


async def test_a_refused_publication_does_not_spend_the_change_quota(user_a, db_sessions, monkeypatch):
    monkeypatch.setenv("PUBLICATION_CHANGES_PER_HOUR", "1")
    get_settings.cache_clear()
    rate_limit.reset_all()
    try:
        room_id = await new_room(user_a)
        assert (await user_a.put(publication_url(room_id), json={"title": ""})).status_code == 422
        response = await publish(user_a, room_id)
    finally:
        get_settings.cache_clear()

    assert response.status_code == 201


async def test_catalog_reads_are_limited_per_minute(user_a, user_b, db_sessions, monkeypatch):
    monkeypatch.setenv("SOCIAL_READS_PER_MINUTE", "3")
    get_settings.cache_clear()
    rate_limit.reset_all()
    try:
        for _ in range(3):
            assert (await user_a.get(CATALOG)).status_code == 200
        refused = await user_a.get(CATALOG)
        other_account = await user_b.get(CATALOG)
    finally:
        get_settings.cache_clear()

    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) > 0
    assert refused.json() == {"detail": rate_limit.SOCIAL_READS_LIMIT_REACHED}
    assert other_account.status_code == 200


async def test_copies_share_the_import_quota(user_a, user_b, db_sessions, undo_store, monkeypatch):
    monkeypatch.setenv("IMPORTS_PER_HOUR", "1")
    get_settings.cache_clear()
    rate_limit.reset_all()
    try:
        code = await published_code(user_a, await new_room(user_a, items=[TABLE]))
        assert (await user_b.post(f"{CATALOG}/{code}/copy")).status_code == 201
        refused = await user_b.post(f"{CATALOG}/{code}/copy")
    finally:
        get_settings.cache_clear()

    assert refused.status_code == 429
    assert refused.json() == {"detail": rate_limit.IMPORT_LIMIT_REACHED}
    (row,) = await stored_publications(db_sessions)
    assert row.copies == 1


async def test_the_account_with_a_real_token_header_is_the_owner(db_sessions):
    """El dueño sale del token, nunca del cuerpo: otra cuenta no puede publicar en nombre de nadie."""
    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=auth_headers("user-c")
    ) as user_c:
        room_id = await new_room(user_c)
        await published_code(user_c, room_id)
    (row,) = await stored_publications(db_sessions)
    assert row.user_id == "user-c"
