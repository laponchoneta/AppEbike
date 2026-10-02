# -*- coding: utf-8 -*-
"""Alertas de la app: bateria baja, GPS y conexion. Banner + vibracion."""
import time
from typing import Callable, Optional

from kivy.logger import Logger

LEVEL_INFO = 'info'
LEVEL_WARN = 'warn'
LEVEL_ERROR = 'error'

COOLDOWN_S = 45.0        # no repetir la misma alerta antes de este tiempo
GPS_STALE_S = 15.0       # sin fijas nuevas en este tiempo = GPS sin senial
GPS_BAD_ACCURACY = 25.0  # metros


class AlertCenter:
    """Decide cuando mostrar una alerta y evita que se repitan sin parar."""

    def __init__(self, show: Callable[[str, str], None],
                 vibrate: Optional[Callable[[float], None]] = None):
        self.show = show
        self.vibrate = vibrate
        self.enabled = True
        self._last_emit = {}
        self._battery_stage = None   # None / 'baja' / 'critica'
        self._gps_bad = False
        self._last_state = None

    def reset(self) -> None:
        self._last_emit.clear()
        self._battery_stage = None
        self._gps_bad = False
        self._last_state = None

    def emit(self, key: str, text: str, level: str = LEVEL_WARN,
             buzz: float = 0.0) -> None:
        if not self.enabled:
            return
        now = time.time()
        if now - self._last_emit.get(key, 0.0) < COOLDOWN_S:
            return
        self._last_emit[key] = now
        Logger.info(f'[BLE] Alerta ({level}): {text}')
        self.show(text, level)
        if buzz and self.vibrate:
            self.vibrate(buzz)

    # ------------------------------------------------------------------ #
    def check_battery(self, level) -> None:
        try:
            level = int(level)
        except (TypeError, ValueError):
            return
        if level <= 10:
            stage = 'critica'
        elif level <= 20:
            stage = 'baja'
        elif level >= 25:   # histeresis para no parpadear en el limite
            stage = None
        else:
            return

        if stage == self._battery_stage:
            return
        self._battery_stage = stage
        if stage == 'critica':
            self.emit('bateria_critica', f'Batería crítica: {level} %',
                      LEVEL_ERROR, buzz=0.6)
        elif stage == 'baja':
            self.emit('bateria_baja', f'Batería baja: {level} %',
                      LEVEL_WARN, buzz=0.3)

    def check_gps(self, accuracy, age_s) -> None:
        bad = (age_s is None or age_s > GPS_STALE_S
               or (accuracy or 0) > GPS_BAD_ACCURACY)
        if bad and not self._gps_bad:
            self._gps_bad = True
            if age_s is None or age_s > GPS_STALE_S:
                self.emit('gps', 'Sin señal de GPS', LEVEL_WARN, buzz=0.2)
            else:
                self.emit('gps', f'GPS impreciso (±{int(accuracy)} m)', LEVEL_WARN)
        elif not bad and self._gps_bad:
            self._gps_bad = False
            self.emit('gps_ok', 'Señal de GPS recuperada', LEVEL_INFO)

    def check_connection(self, state: str) -> None:
        previous, self._last_state = self._last_state, state
        if previous == state:
            return
        if state == 'reconnecting' and previous == 'connected':
            self.emit('conexion_perdida', 'Conexión perdida con la bicicleta',
                      LEVEL_ERROR, buzz=0.5)
        elif state == 'connected' and previous == 'reconnecting':
            self.emit('conexion_ok', 'Conexión recuperada', LEVEL_INFO, buzz=0.2)
