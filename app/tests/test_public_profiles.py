"""Rutas de perfiles públicos (CONTRATO-HABITA-PERFILES.md, parte A, con el recorte aprobado):
catálogo por autor en /social/authors, página de un perfil y nombre visible en /social/me/profile."""

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models.space import Habitacion, ProjectPublication, PublicProfile
from app.models.user import Usuario
from app.routers.social import NO_SUCH_PROFILE
from app.tests.test_social import (  # noqa: F401  (undo_store es una fixture reutilizada)
    CATALOG,
    SECRET_EMAIL,
    SECRET_NAME,
    new_room,
    publication_url,
    published_code,
    stored_publications,
    undo_store,
)
from app.utils import rate_limit

# Todas usan un almacén de Deshacer nuevo (la fixture de test_social), como las pruebas de la red social.
pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("undo_store")]

AUTHORS = "/api/v1/social/authors"
PROFILES = "/api/v1/social/profiles"
MY_PROFILE = "/api/v1/social/me/profile"
CARD_KEYS = {
    "code",
    "title",
    "description",
    "publishedAt",
    "updatedAt",
    "copies",
    "mine",
    "room",
    "itemCount",
    "author",
    "authorPublicationCount",
}


async def authors(client, page=1):
    response = await client.get(AUTHORS, params={"page": page})
    assert response.status_code == 200, response.text
    return response.json()


async def my_profile(client):
    response = await client.get(MY_PROFILE)
    assert response.status_code == 200, response.text
    return response.json()["profile"]


async def rename(client, display_name):
    return await client.patch(MY_PROFILE, json={"displayName": display_name})


async def publish_many(client, count):
    return [await published_code(client, await new_room(client, name=f"Sala {n}")) for n in range(count)]


async def copy_rows(db_sessions, template_code, user_ids, public=True, code_prefix="c"):
    """Inserta a mano publicaciones con el documento de una ya publicada (una por user_id)."""
    async with db_sessions() as session:
        template = await session.scalar(
            select(ProjectPublication).where(ProjectPublication.code == template_code)
        )
        for number, user_id in enumerate(user_ids):
            # Una sala por publicación (habitacion_id es único).
            room = Habitacion(user_id=user_id, nombre=f"Copia {number}", ancho=4, alto=2.5, profundidad=3)
            session.add(room)
            await session.flush()
            session.add(
                ProjectPublication(
                    code=f"{code_prefix}{number:015d}",
                    user_id=user_id,
                    room_id=room.id,
                    public=public,
                    title=f"Copia {number}",
                    document=template.document,
                )
            )
        await session.commit()


# --- Catálogo por autor --------------------------------------------------------------------------------


async def test_the_author_catalog_has_one_card_per_author_with_the_newest(user_a, user_b, db_sessions):
    a1, a2 = await publish_many(user_a, 2)
    (b1,) = await publish_many(user_b, 1)
    a3 = await published_code(user_a, await new_room(user_a, name="Última"))

    body = await authors(user_b)

    assert (body["page"], body["hasMore"]) == (1, False)
    assert [card["code"] for card in body["items"]] == [a3, b1]
    first, second = body["items"]
    assert set(first) == CARD_KEYS
    assert first["authorPublicationCount"] == 3 and second["authorPublicationCount"] == 1
    assert set(first["author"]) == {"code", "displayName", "mine"}
    assert first["author"]["displayName"] is None
    assert (first["author"]["mine"], second["author"]["mine"]) == (False, True)
    assert (first["mine"], second["mine"]) == (False, True)
    assert first["author"]["code"] != second["author"]["code"]
    assert a1 not in str(body) and a2 not in str(body)


async def test_unpublishing_the_featured_one_promotes_the_next(user_a, db_sessions):
    rooms = [await new_room(user_a, name=f"Sala {n}") for n in range(3)]
    codes = [await published_code(user_a, room_id) for room_id in rooms]

    response = await user_a.delete(publication_url(rooms[2]))
    assert response.status_code == 204

    (card,) = (await authors(user_a))["items"]
    assert card["code"] == codes[1]
    assert card["authorPublicationCount"] == 2


async def test_an_account_without_public_publications_is_not_listed(user_a, db_sessions):
    code = await published_code(user_a, await new_room(user_a))
    await copy_rows(db_sessions, code, ["private-author"], public=False)

    items = (await authors(user_a))["items"]

    assert [card["code"] for card in items] == [code]


async def test_the_author_catalog_has_24_authors_per_page(user_a, user_b, db_sessions):
    code = await published_code(user_a, await new_room(user_a))
    await copy_rows(db_sessions, code, [f"author-{n}" for n in range(24)])

    first = await authors(user_b, 1)
    second = await authors(user_b, 2)

    assert len(first["items"]) == 24 and first["hasMore"] is True
    assert [card["code"] for card in second["items"]] == [code]
    assert second["hasMore"] is False
    # Las filas insertadas a mano no tienen perfil: salen sin autor y con su recuento.
    assert all(card["author"] is None for card in first["items"])
    assert all(card["authorPublicationCount"] == 1 for card in first["items"])


@pytest.mark.parametrize("page", ["0", "101", "x", "-1", "1.5"])
async def test_an_author_page_out_of_range_is_422(user_a, db_sessions, page):
    response = await user_a.get(AUTHORS, params={"page": page})

    assert response.status_code == 422


async def test_a_publication_without_profile_has_a_null_author(user_a, db_sessions):
    code = await published_code(user_a, await new_room(user_a))
    await copy_rows(db_sessions, code, ["no-profile"])

    items = (await authors(user_a))["items"]

    assert [card["author"] is None for card in items] == [True, False]
    assert items[0]["authorPublicationCount"] == 1


async def test_the_old_catalog_does_not_change(user_a, db_sessions):
    await publish_many(user_a, 2)

    response = await user_a.get(CATALOG)

    assert response.status_code == 200
    assert len(response.json()["items"]) == 2
    assert all("author" not in card for card in response.json()["items"])


# --- Página del perfil ---------------------------------------------------------------------------------


async def test_a_profile_lists_only_its_public_publications(user_a, user_b, db_sessions):
    a1, a2 = await publish_many(user_a, 2)
    (b1,) = await publish_many(user_b, 1)
    await copy_rows(db_sessions, a1, ["user-a"], public=False, code_prefix="p")
    profile_code = (await my_profile(user_a))["code"]

    seen_by_b = await user_b.get(f"{PROFILES}/{profile_code}")
    seen_by_a = await user_a.get(f"{PROFILES}/{profile_code}")

    assert seen_by_b.status_code == 200, seen_by_b.text
    body = seen_by_b.json()
    assert set(body) == {"profile", "items", "page", "hasMore"}
    assert body["profile"] == {
        "code": profile_code,
        "displayName": None,
        "publicationCount": 2,
        "mine": False,
    }
    assert [item["code"] for item in body["items"]] == [a2, a1]
    assert b1 not in seen_by_b.text
    assert (body["page"], body["hasMore"]) == (1, False)
    assert seen_by_a.json()["profile"]["mine"] is True
    assert all(item["mine"] for item in seen_by_a.json()["items"])


async def test_a_profile_has_24_publications_per_page(user_a, user_b, db_sessions):
    code = await published_code(user_a, await new_room(user_a))
    await copy_rows(db_sessions, code, ["user-a"] * 24)
    profile_code = (await my_profile(user_a))["code"]

    first = (await user_b.get(f"{PROFILES}/{profile_code}", params={"page": 1})).json()
    second = (await user_b.get(f"{PROFILES}/{profile_code}", params={"page": 2})).json()

    assert len(first["items"]) == 24 and first["hasMore"] is True
    assert [item["code"] for item in second["items"]] == [code]
    assert first["profile"]["publicationCount"] == 25


async def test_a_profile_without_public_publications_is_200_and_empty(user_a, user_b, db_sessions):
    response = await rename(user_a, "Lola")
    profile_code = response.json()["code"]

    body = (await user_b.get(f"{PROFILES}/{profile_code}")).json()

    assert body["items"] == [] and body["profile"]["publicationCount"] == 0


async def test_an_unknown_profile_is_404(user_a, db_sessions):
    response = await user_a.get(f"{PROFILES}/{'x' * 16}")

    assert response.status_code == 404
    assert response.json()["detail"] == NO_SUCH_PROFILE


@pytest.mark.parametrize("code", ["corto", "x" * 17, "con espacios1234", "%3Cscript%3E12345"])
async def test_a_malformed_profile_code_is_422(user_a, db_sessions, code):
    response = await user_a.get(f"{PROFILES}/{code}")

    assert response.status_code == 422


async def test_the_social_routes_need_a_session(client, db_sessions):
    for response in (
        await client.get(AUTHORS),
        await client.get(f"{PROFILES}/{'x' * 16}"),
        await client.get(MY_PROFILE),
        await client.patch(MY_PROFILE, json={"displayName": "Lola"}),
    ):
        assert response.status_code == 401


async def test_profile_reads_share_the_social_read_quota(user_a, db_sessions, monkeypatch):
    monkeypatch.setenv("SOCIAL_READS_PER_MINUTE", "2")
    get_settings.cache_clear()
    rate_limit.reset_all()
    try:
        profile_code = (await rename(user_a, "Lola")).json()["code"]
        assert (await user_a.get(AUTHORS)).status_code == 200
        assert (await user_a.get(f"{PROFILES}/{profile_code}")).status_code == 200
        blocked = await user_a.get(CATALOG)
    finally:
        get_settings.cache_clear()

    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1


# --- Nombre visible ------------------------------------------------------------------------------------


async def test_publishing_without_profile_creates_an_anonymous_one(user_a, db_sessions):
    assert await my_profile(user_a) is None

    await published_code(user_a, await new_room(user_a))
    await published_code(user_a, await new_room(user_a, name="Otra"))

    profile = await my_profile(user_a)
    assert profile["displayName"] is None
    assert profile["publicationCount"] == 2 and profile["mine"] is True
    async with db_sessions() as session:
        assert len(list(await session.scalars(select(PublicProfile)))) == 1


async def test_republishing_keeps_the_profile_and_its_name(user_a, db_sessions):
    room_id = await new_room(user_a)
    await published_code(user_a, room_id)
    code = (await rename(user_a, "Lola")).json()["code"]

    again = await user_a.put(publication_url(room_id), json={"title": "Nuevo título"})

    assert again.status_code == 200
    assert await my_profile(user_a) == {
        "code": code,
        "displayName": "Lola",
        "publicationCount": 1,
        "mine": True,
    }


async def test_a_failed_publication_does_not_create_a_profile(user_a, db_sessions):
    room_id = await new_room(user_a)

    response = await user_a.put(publication_url(room_id), json={"title": "<b>"})

    assert response.status_code == 422
    assert await my_profile(user_a) is None


@pytest.mark.parametrize("name", ["L", "x" * 40, "  Lola Pérez  ", "Ñandú 3"])
async def test_a_valid_display_name_is_saved_trimmed(user_a, db_sessions, name):
    response = await rename(user_a, name)

    assert response.status_code == 200, response.text
    assert response.json()["displayName"] == name.strip()
    assert (await my_profile(user_a))["displayName"] == name.strip()


@pytest.mark.parametrize("name", [None, "", "    "])
async def test_an_empty_display_name_is_anonymous(user_a, db_sessions, name):
    await rename(user_a, "Lola")

    response = await rename(user_a, name)

    assert response.status_code == 200
    assert response.json()["displayName"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"displayName": "x" * 41},
        {"displayName": "Lola\u0007"},
        {"displayName": "<b>Lola</b>"},
        {"displayName": 7},
        {},
        {"displayName": "Lola", "email": "otra@example.com"},
    ],
)
async def test_an_invalid_display_name_is_422_without_echo(user_a, db_sessions, body):
    response = await user_a.patch(MY_PROFILE, json=body)

    assert response.status_code == 422
    assert "Lola" not in response.text and "xxxxxxxxxx" not in response.text
    assert await my_profile(user_a) is None


async def test_renaming_never_changes_the_code(user_a, db_sessions):
    first = (await rename(user_a, "Lola")).json()
    second = (await rename(user_a, "Dolores")).json()
    third = (await rename(user_a, None)).json()

    assert first["code"] == second["code"] == third["code"]
    assert len(first["code"]) == 16


async def test_profile_changes_have_an_hourly_quota(user_a, db_sessions, monkeypatch):
    monkeypatch.setenv("PROFILE_CHANGES_PER_HOUR", "2")
    get_settings.cache_clear()
    rate_limit.reset_all()
    try:
        assert (await rename(user_a, "Lola")).status_code == 200
        assert (await user_a.patch(MY_PROFILE, json={"displayName": "x" * 41})).status_code == 422
        assert (await rename(user_a, "Dolores")).status_code == 200
        blocked = await rename(user_a, "Otra")
    finally:
        get_settings.cache_clear()

    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1
    assert (await my_profile(user_a))["displayName"] == "Dolores"


async def test_the_name_shows_in_the_author_catalog(user_a, user_b, db_sessions):
    await published_code(user_a, await new_room(user_a))
    await rename(user_a, "Lola")

    (card,) = (await authors(user_b))["items"]

    assert card["author"]["displayName"] == "Lola"


# --- Privacidad ----------------------------------------------------------------------------------------


async def test_profiles_never_expose_personal_data(user_a, user_b, db_sessions):
    async with db_sessions() as session:
        session.add(Usuario(nombre=SECRET_NAME, email=SECRET_EMAIL, password_hash="scrypt$00$00"))
        await session.commit()
    await published_code(user_a, await new_room(user_a))
    await rename(user_a, "Lola")
    (row,) = await stored_publications(db_sessions)
    async with db_sessions() as session:
        profile = await session.scalar(select(PublicProfile))
    profile_code = profile.code

    texts = [
        (await user_b.get(AUTHORS)).text,
        (await user_b.get(f"{PROFILES}/{profile_code}")).text,
        (await user_a.get(MY_PROFILE)).text,
        (await rename(user_a, "Lola")).text,
    ]

    for text in texts:
        assert row.user_id not in text
        assert SECRET_EMAIL not in text and SECRET_NAME not in text
        # Ni ids internos (de publicación, de perfil, de usuario o de sala) ni sus claves.
        for key in ('"id"', "userId", "user_id", "email", "roomId", "createdAt"):
            assert key not in text
