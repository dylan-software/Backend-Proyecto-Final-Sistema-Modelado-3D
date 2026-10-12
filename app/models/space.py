from sqlalchemy import Float, ForeignKey, Integer, String
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
    habitacion_id: Mapped[int] = mapped_column(
        ForeignKey("habitaciones.id", ondelete="CASCADE"), index=True
    )
    nombre: Mapped[str] = mapped_column(String(120))
    ancho: Mapped[float] = mapped_column(Float)
    alto: Mapped[float] = mapped_column(Float)
    profundidad: Mapped[float] = mapped_column(Float)
    posicion_x: Mapped[float] = mapped_column(Float)
    posicion_y: Mapped[float] = mapped_column(Float)
    posicion_z: Mapped[float] = mapped_column(Float)

    habitacion: Mapped["Habitacion"] = relationship(back_populates="muebles")
