from pydantic import BaseModel, Field, field_validator

Point = tuple[float, float, float]


class AnalyzeRequest(BaseModel):
    nombre: str = Field(min_length=1, max_length=120)
    # Account the room belongs to; without it the room is stored but no account can list it.
    user_id: str | None = Field(None, min_length=1, max_length=64)
    estilo: str = Field("moderno", min_length=1, max_length=60)
    instrucciones: str | None = Field(None, max_length=1000)
    puntos: list[Point] = Field(min_length=3)
    # Optional room photo, base64 or data URL; only its colors are used.
    imagen: str | None = Field(None, max_length=15_000_000)
    # Optional measured dimensions; any that are missing are taken from the scan.
    ancho: float | None = Field(None, gt=0)
    alto: float | None = Field(None, gt=0)
    profundidad: float | None = Field(None, gt=0)


class FurnitureProposal(BaseModel):
    nombre: str = Field(min_length=1, max_length=120)
    ancho: float = Field(gt=0)
    alto: float = Field(gt=0)
    profundidad: float = Field(gt=0)
    posicion_x: float
    posicion_y: float = 0.0
    posicion_z: float


class FurnitureOut(FurnitureProposal):
    id: int


class DiscardedFurniture(BaseModel):
    nombre: str
    motivo: str


class Surface(BaseModel):
    tipo: str
    normal: list[float]
    offset: float
    puntos: int


class RoomOut(BaseModel):
    id: int
    nombre: str
    ancho: float
    alto: float
    profundidad: float


class ColorShare(BaseModel):
    hex: str
    proporcion: float


class VisualContext(BaseModel):
    brillo: float
    colores: list[ColorShare]


class AnalyzeResponse(BaseModel):
    habitacion: RoomOut
    descripcion: str
    muebles: list[FurnitureOut]
    descartados: list[DiscardedFurniture]
    superficies: list[Surface]
    origen: Point
    ruido: int
    contexto_visual: VisualContext | None = None


class CatalogFurnitureIn(BaseModel):
    nombre: str = Field(min_length=1, max_length=120)
    # Account adding the item; without it the item is stored but no account can list it.
    user_id: str | None = Field(None, min_length=1, max_length=64)
    # One of the fixed categories of the web interface, or "Others" to create a new one.
    categoria: str = Field(min_length=1, max_length=60)
    # Name of the new category; only used when categoria is "Others".
    custom_category: str | None = Field(None, max_length=60)
    ancho: float = Field(gt=0)
    alto: float = Field(gt=0)
    profundidad: float = Field(gt=0)

    @field_validator("nombre", mode="before")
    @classmethod
    def strip_nombre(cls, value):
        # Runs before min_length, so a name made only of spaces is rejected instead of stored empty.
        return value.strip() if isinstance(value, str) else value


class CatalogFurnitureOut(BaseModel):
    id: int
    nombre: str
    categoria_id: int
    categoria: str
    categoria_nueva: bool
    ancho: float
    alto: float
    profundidad: float


class SpaceOut(RoomOut):
    muebles: list[FurnitureOut]


class CatalogItemOut(BaseModel):
    id: int
    nombre: str
    categoria_id: int
    categoria: str
    ancho: float
    alto: float
    profundidad: float


class UserSpacesOut(BaseModel):
    user_id: str
    habitaciones: list[SpaceOut]
    catalogo: list[CatalogItemOut]
