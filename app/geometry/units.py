"""The single place where the backend converts between the catalog's centimeters and room meters.

muebles_catalogo stores ancho/alto/profundidad in centimeters, as the web form sends them. Rooms
(habitaciones) and placed furniture (muebles_sugeridos) are in meters.
"""

CM_PER_M = 100.0


def cm_to_m(value_cm):
    return value_cm / CM_PER_M


def m_to_cm(value_m):
    return value_m * CM_PER_M
