from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


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

    muebles: Mapped[list["MuebleSugerido"]] = relationship(
        back_populates="habitacion", cascade="all, delete-orphan"
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
