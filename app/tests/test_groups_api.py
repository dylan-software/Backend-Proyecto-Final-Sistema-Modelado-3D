"""Endpoints de grupos de proyectos (CONTRATO-GRUPOS.md), con SQLite en memoria."""

import pytest

from app.config import Settings, get_settings
from app.models.space import Habitacion

pytestmark = pytest.mark.anyio

GROUPS = "/api/v1/spaces/groups"
SECRET_TEXT = "TEXTO-PRIVADO-DEL-USUARIO"


async def create_group(client, name="Casa de la abuela", **extra):
    return await client.post(GROUPS, json={"name": name, **extra})


async def group_id(client, **kwargs):
    response = await create_group(client, **kwargs)
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def add_room(db_sessions, user_id="user-a", group=None):
    async with db_sessions() as session:
        row = Habitacion(user_id=user_id, nombre="Sala", ancho=4, alto=2.5, profundidad=3, group_id=group)
        session.add(row)
        await session.commit()
        return row.id


# --- Crear y listar ------------------------------------------------------------------------------


async def test_create_a_group_with_its_description(user_a, db_sessions):
    response = await create_group(user_a, description="  Sala y comedor\nsofá en otra pared  ")

    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"id", "name", "description", "createdAt", "projectCount"}
    assert body["name"] == "Casa de la abuela"
    assert body["description"] == "Sala y comedor\nsofá en otra pared"
    assert body["projectCount"] == 0
    assert body["createdAt"]


async def test_list_groups_in_creation_order_with_their_project_count(user_a, db_sessions):
    first = await group_id(user_a, name="Casa")
    second = await group_id(user_a, name="Apartamento")
    await add_room(db_sessions, group=first)
    await add_room(db_sessions, group=first)
    await add_room(db_sessions)  # sin grupo: no cuenta

    response = await user_a.get(GROUPS)

    assert response.status_code == 200
    groups = response.json()["groups"]
    listed = [(g["id"], g["name"], g["projectCount"]) for g in groups]
    assert listed == [(first, "Casa", 2), (second, "Apartamento", 0)]


async def test_an_account_only_sees_its_own_groups(user_a, user_b, db_sessions):
    await group_id(user_a, name="De A")
    await group_id(user_b, name="De B")

    names_a = [g["name"] for g in (await user_a.get(GROUPS)).json()["groups"]]
    names_b = [g["name"] for g in (await user_b.get(GROUPS)).json()["groups"]]

    assert (names_a, names_b) == (["De A"], ["De B"])


async def test_an_empty_or_blank_description_is_stored_as_null(user_a, db_sessions):
    assert (await create_group(user_a, description="   ")).json()["description"] is None
    assert (await create_group(user_a, description=None)).json()["description"] is None


async def test_groups_need_a_session(client, db_sessions):
    assert (await client.get(GROUPS)).status_code == 401
    assert (await client.post(GROUPS, json={"name": "Casa"})).status_code == 401


# --- Editar ----------------------------------------------------------------------------------------


async def test_rename_and_describe_a_group(user_a, db_sessions):
    gid = await group_id(user_a)
    await add_room(db_sessions, group=gid)

    renamed = await user_a.patch(f"{GROUPS}/{gid}", json={"name": "  Casa nueva "})
    described = await user_a.patch(f"{GROUPS}/{gid}", json={"description": "Probamos la sala"})

    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Casa nueva"
    assert described.json() | {"createdAt": None} == {
        "id": gid,
        "name": "Casa nueva",
        "description": "Probamos la sala",
        "createdAt": None,
        "projectCount": 1,
    }


async def test_null_or_empty_description_clears_it_and_keeps_the_name(user_a, db_sessions):
    gid = await group_id(user_a, description="Algo")

    cleared = await user_a.patch(f"{GROUPS}/{gid}", json={"description": None})
    await user_a.patch(f"{GROUPS}/{gid}", json={"description": "Otra vez"})
    emptied = await user_a.patch(f"{GROUPS}/{gid}", json={"description": ""})

    assert cleared.json()["description"] is None
    assert emptied.json()["description"] is None
    assert emptied.json()["name"] == "Casa de la abuela"


async def test_patch_needs_at_least_one_field_and_a_non_null_name(user_a, db_sessions):
    gid = await group_id(user_a)

    assert (await user_a.patch(f"{GROUPS}/{gid}", json={})).status_code == 422
    assert (await user_a.patch(f"{GROUPS}/{gid}", json={"name": None})).status_code == 422


async def test_a_group_of_another_account_or_missing_is_404(user_a, user_b, db_sessions):
    foreign = await group_id(user_b)

    assert (await user_a.patch(f"{GROUPS}/{foreign}", json={"name": "Mía"})).status_code == 404
    assert (await user_a.patch(f"{GROUPS}/99999", json={"name": "Mía"})).status_code == 404
    names_b = [g["name"] for g in (await user_b.get(GROUPS)).json()["groups"]]
    assert names_b == ["Casa de la abuela"]


# --- Validación ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["", "   ", "x" * 81, "<b>Casa</b>", "Casa > Sala", "Casa\x07", "Casa​", 12],
)
async def test_invalid_group_names_are_422_without_echo(user_a, db_sessions, name):
    response = await create_group(user_a, name=name)

    assert response.status_code == 422
    assert "Casa" not in response.text


async def test_group_name_limit_is_80_characters(user_a, db_sessions):
    assert (await create_group(user_a, name="x" * 80)).status_code == 201


async def test_description_limit_is_1000_characters_with_line_breaks_and_tabs(user_a, db_sessions):
    assert (await create_group(user_a, description="a\tb\r\n" + "x" * 995)).status_code == 201
    too_long = await create_group(user_a, description=SECRET_TEXT + "x" * 1000)
    control = await create_group(user_a, description=SECRET_TEXT + "\x00")

    assert (too_long.status_code, control.status_code) == (422, 422)
    assert SECRET_TEXT not in too_long.text + control.text


# --- Tope y cuota ----------------------------------------------------------------------------------


async def test_the_group_cap_answers_409(user_a, user_b, db_sessions, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_groups_per_user", 2)
    await group_id(user_a, name="Uno")
    await group_id(user_a, name="Dos")

    response = await create_group(user_a, name="Tres")

    assert response.status_code == 409
    assert response.json()["detail"] == "Ya tienes 2 grupos; usa uno de los que tienes."
    # El tope es por cuenta.
    assert (await create_group(user_b, name="Uno")).status_code == 201


def test_the_default_group_cap_is_50():
    assert Settings.model_fields["max_groups_per_user"].default == 50


async def test_groups_do_not_spend_ai_quota(user_a, db_sessions, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_requests_per_minute", 1)

    statuses = [(await create_group(user_a, name=f"Grupo {i}")).status_code for i in range(3)]
    statuses.append((await user_a.get(GROUPS)).status_code)

    assert statuses == [201, 201, 201, 200]
