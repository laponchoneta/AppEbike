# -*- coding: utf-8 -*-
"""Filtro de Kalman escalar para la velocidad del GPS, en Python puro.

No usa filterpy: su Q_discrete_white_noise solo acepta dim de 2 a 4.
"""
from typing import Optional


class SpeedKalman:
    """Caminata aleatoria: la velocidad cambia por aceleraciones que no se miden."""

    def __init__(self, accel_std: float = 1.0, meas_var: float = 0.25):
        self.accel_var = accel_std ** 2   # (m/s^2)^2: que tan rapido puede cambiar la velocidad
        self.meas_var = meas_var          # (m/s)^2: ruido por defecto de una medicion
        self.reset()

    def reset(self) -> None:
        self.x: Optional[float] = None    # velocidad estimada en m/s
        self.p = 0.0                      # varianza de la estimacion

    def predict(self, dt: float = 1.0) -> None:
        if self.x is None:
            return
        dt = min(max(float(dt), 0.0), 5.0)
        self.p += self.accel_var * dt * dt

    def update(self, z: float, r: Optional[float] = None) -> float:
        """Incorpora una medicion z (m/s) con varianza r y regresa la estimacion."""
        r = self.meas_var if r is None else max(float(r), 1e-4)
        z = float(z)
        if self.x is None:
            self.x, self.p = z, r
        else:
            gain = self.p / (self.p + r)
            self.x += gain * (z - self.x)
            self.p *= 1.0 - gain
        return self.x


def KalmanWrapper(*args) -> SpeedKalman:
    """Nombre original, por compatibilidad con quien lo importe."""
    return SpeedKalman()

