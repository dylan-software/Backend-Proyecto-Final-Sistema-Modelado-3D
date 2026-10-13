from sqlalchemy import Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Habitacion(Base):
    __tablename__ = "habitaciones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120))
    ancho: Mapped[float] = mapped_column(Float)
    alto: Mapped[float] = mapped_column(Float)
    profundidad: Mapped[float] = mapped_column(Float)

    muebles: Mapped[list["MuebleSugerido"]] = relationship(
        back_populates="habitacion", cascade="all, delete-orphan"
    )


class MuebleSugerido(Base):
    __tablename__ = "muebles_sugeridos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    habitacion_id: Mapped[int] = mapped_column(ForeignKey("habitaciones.id", ondelete="CASCADE"), index=True)
    nombre: Mapped[str] = mapped_column(String(120))
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
    categoria_id: Mapped[int] = mapped_column(ForeignKey("categorias.id"), index=True)
    nombre: Mapped[str] = mapped_column(String(120))
    ancho: Mapped[float] = mapped_column(Float)
    alto: Mapped[float] = mapped_column(Float)
    profundidad: Mapped[float] = mapped_column(Float)

    categoria: Mapped["Categoria"] = relationship(back_populates="muebles")
