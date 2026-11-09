from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Máximo de caracteres de las descripciones de grupos y proyectos (migración 005).
MAX_DESCRIPTION_LENGTH = 1000


class Base(DeclarativeBase):
    pass


class ProjectGroup(Base):
    """Carpeta de proyectos (salas) de una cuenta, con una descripción libre (migración 005)."""

    __tablename__ = "grupos_proyecto"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column("nombre", String(80))
    description: Mapped[str | None] = mapped_column("descripcion", Text, nullable=True)
    # Se rellena también en Python para poder devolverlo justo después de crear el grupo.
    created_at: Mapped[datetime] = mapped_column(
        "creado_en",
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    # Mismo nombre que en schema.sql y la migración 005. length() en vez de char_length() porque SQLite
    # (las pruebas) no tiene char_length; en PostgreSQL ambas cuentan caracteres.
    __table_args__ = (
        CheckConstraint(
            f"length(descripcion) <= {MAX_DESCRIPTION_LENGTH}", name="ck_grupos_proyecto_descripcion_len"
        ),
    )


class Habitacion(Base):
    __tablename__ = "habitaciones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Account that owns the room, as sent by the frontend; rooms are only listed to that account.
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    nombre: Mapped[str] = mapped_column(String(120))
    # Meters. With vertices, ancho/profundidad are the polygon's bounding box along x/z; alto is the
    # ceiling height. Rooms without vertices (from /analyze) are the rectangle [0, ancho] x [0, profundidad].
    ancho: Mapped[float] = mapped_column(Float)
    alto: Mapped[float] = mapped_column(Float)
    profundidad: Mapped[float] = mapped_column(Float)
    # Floor polygon as [{"x": float, "y": float}, ...] in meters; the polygon's y is the room's z axis.
    vertices: Mapped[list | None] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=True)
    # Grupo del proyecto (NULL = sin grupo) y su descripción (migración 005).
    group_id: Mapped[int | None] = mapped_column(
        "grupo_id", ForeignKey("grupos_proyecto.id", ondelete="SET NULL"), nullable=True, index=True
    )
    description: Mapped[str | None] = mapped_column("descripcion", Text, nullable=True)
    # RESERVADO para la versión derivada: ningún endpoint lo lee ni lo escribe todavía.
    derived_from: Mapped[int | None] = mapped_column(
        "derivado_de", ForeignKey("habitaciones.id", ondelete="SET NULL"), nullable=True
    )

    muebles: Mapped[list["MuebleSugerido"]] = relationship(
        back_populates="habitacion", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            f"length(descripcion) <= {MAX_DESCRIPTION_LENGTH}", name="ck_habitaciones_descripcion_len"
        ),
    )


class MuebleSugerido(Base):
    __tablename__ = "muebles_sugeridos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    habitacion_id: Mapped[int] = mapped_column(ForeignKey("habitaciones.id", ondelete="CASCADE"), index=True)
    nombre: Mapped[str] = mapped_column(String(120))
    # Meters. posicion_x/posicion_z are the center of the footprint, posicion_y the height of its base.
    ancho: Mapped[float] = mapped_column(Float)
    alto: Mapped[float] = mapped_column(Float)
    profundidad: Mapped[float] = mapped_column(Float)
    posicion_x: Mapped[float] = mapped_column(Float)
    posicion_y: Mapped[float] = mapped_column(Float)
    posicion_z: Mapped[float] = mapped_column(Float)

    habitacion: Mapped["Habitacion"] = relationship(back_populates="muebles")


# Máximo de caracteres del documento congelado de una publicación (como texto), igual que la migración 006.
MAX_PUBLICATION_DOCUMENT_CHARS = 262144


class ProjectPublication(Base):
    """Proyecto publicado en la red social (migración 006).

    Privado por defecto: un proyecto no tiene fila hasta que su dueño lo publica, y una fila sin
    `public` explícito queda en False (el catálogo solo lee las públicas). `document` es la copia
    congelada del proyecto en el formato del archivo exportado ("habita-project" v1), sin datos
    personales. `user_id`, `room_id` e `id` nunca salen en la API: el único id público es `code`.
    """

    __tablename__ = "publicaciones_proyecto"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column("codigo", String(24))
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    # Borrar el proyecto borra su publicación (en PostgreSQL; SQLite sin PRAGMA no aplica la FK).
    room_id: Mapped[int] = mapped_column("habitacion_id", ForeignKey("habitaciones.id", ondelete="CASCADE"))
    public: Mapped[bool] = mapped_column("publica", Boolean, default=False, server_default=false())
    title: Mapped[str] = mapped_column("titulo", String(120))
    description: Mapped[str | None] = mapped_column("descripcion", Text, nullable=True)
    document: Mapped[dict] = mapped_column("documento", JSON().with_variant(JSONB(), "postgresql"))
    copies: Mapped[int] = mapped_column("copias", Integer, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        "creada_en",
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        "actualizada_en",
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    # Mismos nombres que la migración 006. length() en vez de char_length() por SQLite, como en la 005.
    __table_args__ = (
        UniqueConstraint("codigo", name="uq_publicaciones_proyecto_codigo"),
        UniqueConstraint("habitacion_id", name="uq_publicaciones_proyecto_habitacion_id"),
        CheckConstraint(
            f"length(descripcion) <= {MAX_DESCRIPTION_LENGTH}",
            name="ck_publicaciones_proyecto_descripcion_len",
        ),
        CheckConstraint(
            f"length(CAST(documento AS TEXT)) <= {MAX_PUBLICATION_DOCUMENT_CHARS}",
            name="ck_publicaciones_proyecto_documento_len",
        ),
        CheckConstraint("copias >= 0", name="ck_publicaciones_proyecto_copias"),
    )


# El catálogo lista las públicas de la más nueva a la más vieja (índice parcial en PostgreSQL).
Index(
    "ix_publicaciones_proyecto_catalogo",
    ProjectPublication.id.desc(),
    postgresql_where=ProjectPublication.public,
)


class Categoria(Base):
    __tablename__ = "categorias"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(60))

    muebles: Mapped[list["MuebleCatalogo"]] = relationship(back_populates="categoria")

    # "Asientos" and "asientos" are the same category.
    __table_args__ = (Index("uq_categorias_nombre_lower", func.lower(nombre), unique=True),)


class MuebleCatalogo(Base):
    __tablename__ = "muebles_catalogo"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Account that added the item; each account sees only its own catalog. Categories stay shared.
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    categoria_id: Mapped[int] = mapped_column(ForeignKey("categorias.id"), index=True)
    nombre: Mapped[str] = mapped_column(String(120))
    # CENTIMETERS, unlike every other table: the web form sends the sizes in cm and they are stored
    # as typed. Convert with app.geometry.units before mixing them with room coordinates (meters).
    ancho: Mapped[float] = mapped_column(Float)
    alto: Mapped[float] = mapped_column(Float)
    profundidad: Mapped[float] = mapped_column(Float)

    categoria: Mapped["Categoria"] = relationship(back_populates="muebles")
