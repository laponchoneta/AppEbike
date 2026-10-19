# -*- coding: utf-8 -*-
"""Phone tilt angle measured with the gyroscope.

Integrating the gyroscope follows every movement instantly but slowly drifts;
gravity read by the accelerometer does not drift but is shaken by bumps,
pedaling and braking. A complementary filter keeps the best of both: the
gyroscope turns the "up" direction and the accelerometer gently pulls it back.
"""
import asyncio
from math import atan2, degrees, sqrt
from typing import Callable, Optional

from kivy.logger import Logger
from kivy.utils import platform

TIME_CONSTANT_S = 2.0   # how slowly the accelerometer corrects the gyroscope drift
MAX_GAP_S = 0.5         # longer gaps between readings (app paused) restart from the accelerometer
MIN_ACCEL = 2.0         # m/s^2: below this the accelerometer cannot tell where "up" is


def wrap_degrees(angle: float) -> float:
    """Angle in degrees within [-180, 180)."""
    return (angle + 180.0) % 360.0 - 180.0


def _unit(x: float, y: float, z: float) -> tuple:
    norm = sqrt(x * x + y * y + z * z)
    return x / norm, y / norm, z / norm


class TiltFilter:
    """Complementary filter of the "up" direction in phone axes (pure Python)."""

    def __init__(self, time_constant: float = TIME_CONSTANT_S):
        self.time_constant = time_constant
        self.reset()

    def reset(self) -> None:
        self.up = None             # unit vector (x, y, z): what the accelerometer reads at rest
        self._gyro_t = None
        self._accel_t = None

    def add_gyro(self, t: float, wx: float, wy: float, wz: float) -> None:
        """Rotation speed in rad/s around the phone axes, at time t in seconds."""
        last, self._gyro_t = self._gyro_t, t
        up = self.up
        if up is None or last is None or not 0.0 < t - last <= MAX_GAP_S:
            return
        dt = t - last
        ux, uy, uz = up
        # Seen from the phone, a fixed direction turns the opposite way: d(up)/dt = up x w
        self.up = _unit(ux + (uy * wz - uz * wy) * dt,
                        uy + (uz * wx - ux * wz) * dt,
                        uz + (ux * wy - uy * wx) * dt)

    def add_accel(self, t: float, ax: float, ay: float, az: float) -> None:
        """Acceleration in m/s^2 along the phone axes, gravity included."""
        if sqrt(ax * ax + ay * ay + az * az) < MIN_ACCEL:
            return
        reading = _unit(ax, ay, az)
        last, self._accel_t = self._accel_t, t
        up = self.up
        if up is None or last is None or not 0.0 <= t - last <= MAX_GAP_S:
            self.up = reading
            return
        k = (t - last) / (self.time_constant + t - last)
        self.up = _unit(*(u + k * (r - u) for u, r in zip(up, reading)))

    @property
    def pitch(self) -> Optional[float]:
        """Degrees the top edge of the phone is raised: 0 lying flat, 90 standing upright."""
        up = self.up
        if up is None:
            return None
        return degrees(atan2(up[1], up[2]))


if platform == 'android':
    from jnius import PythonJavaClass, autoclass, cast, java_method

    class _SensorListener(PythonJavaClass):
        """Receives the readings of one sensor on Android's UI thread."""
        __javainterfaces__ = ['android/hardware/SensorEventListener']

        def __init__(self, on_sample):
            super().__init__()
            self.on_sample = on_sample

        @java_method('(Landroid/hardware/SensorEvent;)V')
        def onSensorChanged(self, event):
            x, y, z = event.values[:3]
            self.on_sample(event.timestamp / 1e9, x, y, z)

        @java_method('(Landroid/hardware/Sensor;I)V')
        def onAccuracyChanged(self, sensor, accuracy):
            pass


class GyroHelper:
    """Tilt for the 'Angle' box: rotation around the phone's horizontal (x) axis."""

    def __init__(self):
        self.filter = TiltFilter()
        self.zero = 0.0              # pitch shown as 0°, chosen by tapping the angle
        self._manager = None
        self._listeners = None       # created once: Android may call them until the very end
        self._registered = []

    @property
    def angle(self) -> Optional[float]:
        """Degrees from the chosen zero; positive when the top edge goes up."""
        pitch = self.filter.pitch
        return None if pitch is None else wrap_degrees(pitch - self.zero)

    def set_zero(self) -> bool:
        """The current tilt becomes 0°. False if there is no reading yet."""
        pitch = self.filter.pitch
        if pitch is None:
            return False
        self.zero = pitch
        return True

    async def run(self, on_angle: Callable[[float], None], dt: float = 0.2) -> None:
        """Turns the sensors on and reports the angle every dt seconds until cancelled."""
        if not self._start():
            return
        try:
            while True:
                angle = self.angle
                if angle is not None:
                    on_angle(angle)
                await asyncio.sleep(dt)
        finally:
            self._stop()

    def _start(self) -> bool:
        if platform != 'android':
            return False
        try:
            Context = autoclass('android.content.Context')
            Sensor = autoclass('android.hardware.Sensor')
            SensorManager = autoclass('android.hardware.SensorManager')
            activity = autoclass('org.kivy.android.PythonActivity').mActivity
            manager = cast('android.hardware.SensorManager',
                           activity.getSystemService(Context.SENSOR_SERVICE))
            accel = manager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)
            gyro = manager.getDefaultSensor(Sensor.TYPE_GYROSCOPE)
            if accel is None:
                Logger.warning('[GYRO] El celular no tiene acelerometro: no hay angulo')
                return False
            if gyro is None:
                Logger.warning('[GYRO] El celular no tiene giroscopio: angulo solo con el acelerometro')
            if self._listeners is None:
                self._listeners = (_SensorListener(self.filter.add_accel),
                                   _SensorListener(self.filter.add_gyro))
            self.filter.reset()
            self._manager = manager
            for listener, sensor in zip(self._listeners, (accel, gyro)):
                if sensor is not None:
                    manager.registerListener(listener, sensor, SensorManager.SENSOR_DELAY_GAME)
                    self._registered.append((listener, sensor))
            Logger.info('[GYRO] Giroscopio y acelerometro activados')
            return True
        except Exception as e:
            Logger.warning(f'[GYRO] No se pudo activar el giroscopio: {e!r}')
            self._stop()
            return False

    def _stop(self) -> None:
        registered, self._registered = self._registered, []
        for listener, sensor in registered:
            try:
                self._manager.unregisterListener(listener, sensor)
            except Exception as e:
                Logger.warning(f'[GYRO] No se pudo apagar un sensor: {e!r}')
        if registered:
            Logger.info('[GYRO] Giroscopio apagado')
