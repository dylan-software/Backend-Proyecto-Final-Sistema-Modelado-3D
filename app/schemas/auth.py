from pydantic import BaseModel, Field, field_validator

# Basic shape check (something@domain.tld) without pulling in the email-validator package.
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class UserLogin(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=EMAIL_PATTERN)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        # Runs before the pattern check so " Ana@Mail.com " is accepted and stored as "ana@mail.com".
        return value.strip().lower() if isinstance(value, str) else value


class UserRegister(UserLogin):
    nombre: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("nombre", mode="before")
    @classmethod
    def strip_nombre(cls, value):
        return value.strip() if isinstance(value, str) else value


class UserOut(BaseModel):
    id: int
    nombre: str
    email: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    usuario: UserOut
