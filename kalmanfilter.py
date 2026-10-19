# -*- coding: utf-8 -*-
"""Scalar Kalman filter for the GPS speed, in pure Python.

Does not use filterpy: its Q_discrete_white_noise only accepts dim 2 to 4.
"""
from typing import Optional


class SpeedKalman:
    """Random walk: the speed changes through accelerations that are not measured."""

    def __init__(self, accel_std: float = 1.0, meas_var: float = 0.25):
        self.accel_var = accel_std ** 2   # (m/s^2)^2: how fast the speed can change
        self.meas_var = meas_var          # (m/s)^2: default noise of a measurement
        self.reset()

    def reset(self) -> None:
        self.x: Optional[float] = None    # estimated speed in m/s
        self.p = 0.0                      # variance of the estimate

    def predict(self, dt: float = 1.0) -> None:
        if self.x is None:
            return
        dt = min(max(float(dt), 0.0), 5.0)
        self.p += self.accel_var * dt * dt

    def update(self, z: float, r: Optional[float] = None) -> float:
        """Adds a measurement z (m/s) with variance r and returns the estimate."""
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
    """Original name, kept for compatibility with whoever imports it."""
    return SpeedKalman()

