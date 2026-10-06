from sqlalchemy import Float, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Habitacion(Base):
    __tablename__ = "habitaciones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120))
    ancho: Mapped[float] = mapped_column(Float)
    alto: Mapped[float] = mapped_column(Float)
    profundidad: Mapped[float] = mapped_column(Float)


class MuebleSugerido(Base):
    __tablename__ = "muebles_sugeridos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120))
    posicion_x: Mapped[float] = mapped_column(Float)
    posicion_y: Mapped[float] = mapped_column(Float)
    posicion_z: Mapped[float] = mapped_column(Float)
