"""Validación de los textos que escribe el usuario: nombres de grupos y proyectos, y descripciones.

Los mensajes de error nunca repiten el texto recibido (el manejador de 422 tampoco lo hace).
"""

import unicodedata

from app.models.space import MAX_DESCRIPTION_LENGTH

MAX_GROUP_NAME_LENGTH = 80
MAX_PROJECT_NAME_LENGTH = 120
# Máximo de caracteres al ESCRIBIR la descripción de un proyecto o de su publicación (decisión del
# usuario, 2026-11-17). Solo se valida lo que llega en POST, PATCH y PUT: la base (CHECK de 1000) y las
# respuestas no cambian, así que las descripciones antiguas más largas se siguen guardando y mostrando, y
# solo hay que recortarlas al editarlas. Los grupos siguen con MAX_DESCRIPTION_LENGTH (1000).
MAX_PROJECT_DESCRIPTION_LENGTH = 500

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


def optional_description(value, max_length=MAX_DESCRIPTION_LENGTH):
    """Descripción libre sin espacios en los extremos; None si falta o queda vacía.

    Args:
        max_length: máximo de caracteres (puntos de código) después de quitar los espacios de los
            extremos. Por defecto, el de la base (grupos); los proyectos usan MAX_PROJECT_DESCRIPTION_LENGTH.

    Raises:
        ValueError: no es texto, pasa de max_length o tiene caracteres de control que no sean saltos de
            línea ni tabuladores. El mensaje nunca repite el texto.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("la descripción debe ser texto")
    text = value.strip()
    if not text:
        return None
    if len(text) > max_length:
        raise ValueError(f"la descripción admite como mucho {max_length} caracteres")
    if any(_is_control(char) and char not in DESCRIPTION_ALLOWED_CONTROLS for char in text):
        raise ValueError("la descripción no puede contener caracteres de control")
    return text
