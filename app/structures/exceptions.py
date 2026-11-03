"""Errores comunes de las estructuras de app.structures."""


class CapacityError(OverflowError):
    """Se intentó añadir un elemento a una estructura que ya está llena."""


class InvariantError(AssertionError):
    """check_invariants() encontró la estructura en un estado inconsistente (un error de programación)."""


def validate_capacity(capacity):
    """None (sin límite) o un entero positivo; cualquier otra cosa es un error de uso.

    Raises:
        ValueError: capacity no es None ni un entero mayor que 0 (bool no cuenta como entero).
    """
    if capacity is None:
        return None
    if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
        raise ValueError("capacity debe ser None o un entero mayor que 0")
    return capacity
