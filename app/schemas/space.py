from pydantic import BaseModel, Field

Point = tuple[float, float, float]


class AnalyzeRequest(BaseModel):
    nombre: str = Field(min_length=1, max_length=120)
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
    # One of the fixed categories of the web interface, or "Others" to create a new one.
    categoria: str = Field(min_length=1, max_length=60)
    # Name of the new category; only used when categoria is "Others".
    custom_category: str | None = Field(None, max_length=60)
    ancho: float = Field(gt=0)
    alto: float = Field(gt=0)
    profundidad: float = Field(gt=0)


class CatalogFurnitureOut(BaseModel):
    id: int
    nombre: str
    categoria_id: int
    categoria: str
    categoria_nueva: bool
    ancho: float
    alto: float
    profundidad: float
