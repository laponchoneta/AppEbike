# -*- coding: utf-8 -*-
"""Paleta y constantes visuales de la app eBIKE (identidad UDEM).

UNICO lugar donde se definen los colores. Si el manual de identidad indica
otros valores, cambialos aqui y se propagan a toda la app.

Los dos colores de marca se tomaron pixel a pixel del archivo 'logo udem.png'
que esta en la raiz del proyecto, asi que son los oficiales del logo.
"""
from kivy.utils import get_color_from_hex
from typing import Optional


# --- Colores de marca (extraidos del logo UDEM) ---
UDEM_YELLOW = '#FFF500'      # amarillo institucional
UDEM_CHARCOAL = '#333333'    # negro/carbon institucional

# --- Fondos y texto (tema oscuro) ---
BG = '#121212'               # fondo principal
SURFACE = '#1E1E1E'          # tarjetas
SURFACE_ALT = '#2A2A2A'      # barras y controles
BAR = '#000000'              # barra superior
TEXT = '#FFFFFF'
TEXT_DIM = '#B0B0B0'
TRACK = '#3A3A3A'            # fondo de los medidores circulares

# --- Colores de estado ---
OK = '#2ECC71'               # conectado / bateria alta
WARN = '#FFB300'             # reconectando / bateria media
ERROR = '#FF5252'            # desconectado / bateria baja

# Opacidad de los widgets cuyos datos ya no estan vigentes.
STALE_OPACITY = 0.35

# --- Tipografia (del proyecto; el manual UDEM no se proporciono) ---
FONT_TITLE = 'BalooBhaijaan2-Bold.ttf'
FONT_BODY = 'Roboto-Bold.ttf'
FONT_NUMBER = 'NotoSans-Medium.ttf'

# --- Rutas de marca ---
LOGO = 'assets/udem_logo.png'


def rgba(value: str, alpha: Optional[float] = None) -> list:
    """Convierte '#RRGGBB' a la lista [r, g, b, a] que usa Kivy."""
    color = get_color_from_hex(value)
    if alpha is not None:
        color[3] = alpha
    return color


# Colores del chip de estado de conexion (Tarea 1).
STATE_COLORS = {
    'disconnected': ERROR,
    'connecting': WARN,
    'connected': OK,
    'reconnecting': WARN,
}


def battery_color(level: float) -> list:
    """Verde / amarillo / rojo segun el nivel de bateria en porcentaje."""
    if level <= 20:
        return rgba(ERROR)
    if level <= 50:
        return rgba(WARN)
    return rgba(OK)
