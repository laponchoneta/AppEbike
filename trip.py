# -*- coding: utf-8 -*-
"""Recorrido en vivo: distancia, tiempo, velocidad y pendiente.

La distancia se calcula con Haversine entre fijas consecutivas del GPS
(la formula estaba en gpshelper.py y se movio aqui, ya corregida).
Solo usa datos que ya tenemos: GPS del celular y el campo 'angle' del ESP32.
"""
import time
from math import asin, cos, pi, radians, sqrt, tan

EARTH_RADIUS_KM = 6371.0

# --- Filtros contra el ruido del GPS cuando la bici esta detenida ---
MAX_ACCURACY_M = 25.0    # descarta fijas imprecisas (mismo criterio que gpshelper)
MIN_SEGMENT_M = 3.0      # saltos mas chicos que esto se consideran ruido
MIN_MOVING_KMH = 2.0     # debajo de esta velocidad se considera detenido
MAX_SPEED_MS = 30.0      # 108 km/h: salto imposible en bicicleta
MAX_SLOPE_PERCENT = 40.0  # la pendiente se recorta a un rango creible
MIN_KM_FOR_RANGE = 1.0   # antes de 1 km la autonomia estimada no es creible


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia en km entre dos coordenadas."""
    p = pi / 180
    return 2 * EARTH_RADIUS_KM * asin(sqrt(
        0.5 - cos((lat2 - lat1) * p) / 2
        + cos(lat1 * p) * cos(lat2 * p) * (1 - cos((lon2 - lon1) * p)) / 2))


def slope_from_angle(angle_deg: float) -> float:
    """Pendiente en % a partir de la inclinacion en grados del ESP32."""
    angle_deg = max(-89.0, min(89.0, float(angle_deg)))
    return max(-MAX_SLOPE_PERCENT,
               min(MAX_SLOPE_PERCENT, tan(radians(angle_deg)) * 100.0))


class TripTracker:
    """Acumula las metricas de un recorrido."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.started_at = None
        self.ended_at = None
        self.distance_km = 0.0
        self.max_speed = 0.0
        self.moving_seconds = 0.0
        self.slope_percent = 0.0
        self.battery_start = None
        self.battery_last = None
        self.rejected_fixes = 0
        self.accepted_fixes = 0
        self._last = None

    def start(self) -> None:
        self.reset()
        self.started_at = time.time()

    def stop(self) -> None:
        if self.started_at and self.ended_at is None:
            self.ended_at = time.time()

    @property
    def running(self) -> bool:
        return self.started_at is not None and self.ended_at is None

    @property
    def duration_s(self) -> float:
        if not self.started_at:
            return 0.0
        return (self.ended_at or time.time()) - self.started_at

    @property
    def avg_speed(self) -> float:
        """Promedio sobre el tiempo en movimiento, no sobre el total."""
        if self.moving_seconds < 1.0:
            return 0.0
        return self.distance_km / (self.moving_seconds / 3600.0)

    @property
    def battery_used(self):
        if self.battery_start is None or self.battery_last is None:
            return None
        return max(0, self.battery_start - self.battery_last)

    @property
    def battery_per_km(self):
        """Porcentaje de bateria gastado por kilometro, o None si no alcanza."""
        used = self.battery_used
        if not used or self.distance_km < MIN_KM_FOR_RANGE:
            return None
        return used / self.distance_km

    @property
    def estimated_range_km(self):
        """Autonomia estimada (experimental): solo tiene sentido tras 1 km."""
        per_km = self.battery_per_km
        if not per_km or self.battery_last is None:
            return None
        return self.battery_last / per_km

    def set_angle(self, angle_deg) -> None:
        try:
            self.slope_percent = slope_from_angle(angle_deg)
        except (TypeError, ValueError):
            pass

    def set_battery(self, level) -> None:
        try:
            level = int(level)
        except (TypeError, ValueError):
            return
        if self.battery_start is None:
            self.battery_start = level
        self.battery_last = level

    def add_fix(self, lat, lon, speed_kmh=0.0, accuracy=0.0, timestamp=None) -> bool:
        """Agrega una fija del GPS. Devuelve True si sumo distancia."""
        if not self.running:
            return False
        now = timestamp or time.time()
        try:
            lat, lon = float(lat), float(lon)
            speed_kmh = max(0.0, float(speed_kmh or 0.0))
            accuracy = float(accuracy or 0.0)
        except (TypeError, ValueError):
            return False

        if accuracy > MAX_ACCURACY_M:
            self.rejected_fixes += 1
            return False

        self.accepted_fixes += 1
        self.max_speed = max(self.max_speed, speed_kmh)

        previous, self._last = self._last, (lat, lon, now)
        if previous is None:
            return False

        elapsed = now - previous[2]
        if elapsed <= 0:
            return False
        meters = haversine_km(previous[0], previous[1], lat, lon) * 1000.0

        # Detenido: el GPS "baila" unos metros y acumularia kilometros falsos.
        if speed_kmh < MIN_MOVING_KMH or meters < MIN_SEGMENT_M:
            return False
        if meters / elapsed > MAX_SPEED_MS:
            self.rejected_fixes += 1
            return False

        self.distance_km += meters / 1000.0
        self.moving_seconds += elapsed
        return True

    def summary(self) -> dict:
        """Resumen que se guarda en el historial."""
        return {
            'inicio': self.started_at or time.time(),
            'fin': self.ended_at or time.time(),
            'duracion_s': round(self.duration_s, 1),
            'distancia_km': round(self.distance_km, 3),
            'vel_prom': round(self.avg_speed, 1),
            'vel_max': round(self.max_speed, 1),
            'bateria_usada': self.battery_used,
        }


def format_duration(seconds: float) -> str:
    """Segundos -> 'MM:SS' o 'H:MM:SS'."""
    seconds = int(max(0, seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f'{hours}:{minutes:02d}:{secs:02d}'
    return f'{minutes:02d}:{secs:02d}'
