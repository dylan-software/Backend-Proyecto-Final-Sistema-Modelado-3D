"""Validación de los textos que escribe el usuario: nombres de grupos y proyectos, y descripciones.

Los mensajes de error nunca repiten el texto recibido (el manejador de 422 tampoco lo hace).
"""

import unicodedata

from app.models.space import MAX_DESCRIPTION_LENGTH

MAX_GROUP_NAME_LENGTH = 80
MAX_PROJECT_NAME_LENGTH = 120

# Caracteres de control permitidos en una descripción: saltos de línea y tabulador.
DESCRIPTION_ALLOWED_CONTROLS = "\n\r\t"


def _is_control(char):
    return unicodedata.category(char) in ("Cc", "Cf")


def plain_name(value, max_length):
    """Nombre en texto plano, sin espacios en los extremos.

    Raises:
        ValueError: no es texto, queda vacío, pasa de max_length o tiene '<', '>' o caracteres de control.
    """
    if not isinstance(value, str):
        raise ValueError("el nombre debe ser texto")
    text = value.strip()
    if not 1 <= len(text) <= max_length:
        raise ValueError(f"el nombre debe tener entre 1 y {max_length} caracteres")
    if any(char in "<>" or _is_control(char) for char in text):
        raise ValueError("el nombre no puede contener < ni > ni caracteres de control")
    return text


def optional_description(value):
    """Descripción libre sin espacios en los extremos; None si falta o queda vacía.

    Raises:
        ValueError: no es texto, pasa de MAX_DESCRIPTION_LENGTH o tiene caracteres de control que no sean
            saltos de línea ni tabuladores.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("la descripción debe ser texto")
    text = value.strip()
    if not text:
        return None
    if len(text) > MAX_DESCRIPTION_LENGTH:
        raise ValueError(f"la descripción admite como mucho {MAX_DESCRIPTION_LENGTH} caracteres")
    if any(_is_control(char) and char not in DESCRIPTION_ALLOWED_CONTROLS for char in text):
        raise ValueError("la descripción no puede contener caracteres de control")
    return text
