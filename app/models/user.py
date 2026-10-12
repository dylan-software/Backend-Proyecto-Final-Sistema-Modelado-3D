from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.space import Base


class Usuario(Base):
    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    # "scrypt$<salt hex>$<hash hex>"; the plain password is never stored.
    password_hash: Mapped[str] = mapped_column(String(255))
