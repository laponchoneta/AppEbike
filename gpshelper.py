# -*- coding: utf-8 -*-
"""Velocidad del GPS del celular, leida directo del LocationManager de Android.

No se usa plyer.gps: su LocationListener no implementa onLocationChanged(List),
que es la variante que Android 12+ invoca, y ahi nunca llega ninguna lectura.
"""
import asyncio
import time
from math import asin, cos, pi, sqrt
from typing import Optional

from kivy.clock import Clock, mainthread
from kivy.logger import Logger
from kivy.utils import platform

from kalmanfilter import SpeedKalman

GPS_PROVIDER = 'gps'
MIN_TIME_MS = 0            # 0 = tan rapido como lo entregue el chip GNSS (casi siempre 1 Hz)
MIN_DISTANCE_M = 0.0       # sin minimo: tambien hay lecturas estando detenido
PUBLISH_MIN_S = 0.25       # cada envio BLE tarda ~0.15 s; mas rapido solo acumula atraso
MAX_ACCURACY_M = 25.0      # precision minima para calcular velocidad por posicion
MAX_SPEED_MS = 30.0        # 108 km/h: lectura imposible en bicicleta
MIN_SPEED_STD = 0.2        # m/s, piso para el error que reporta el GNSS
DOPPLER_VAR = 0.5 ** 2     # (m/s)^2 si el celular no reporta la precision de la velocidad
POSITION_VAR = 1.5 ** 2    # (m/s)^2: la velocidad por diferencia de posiciones es mas ruidosa
PERMISSION_RETRY_S = 2.0
EARTH_RADIUS_M = 6371000.0


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia en metros entre dos coordenadas."""
    p = pi / 180
    return 2 * EARTH_RADIUS_M * asin(sqrt(
        0.5 - cos((lat2 - lat1) * p) / 2
        + cos(lat1 * p) * cos(lat2 * p) * (1 - cos((lon2 - lon1) * p)) / 2))


def _has_location_permission() -> bool:
    try:
        from android.permissions import Permission, check_permission
        return bool(check_permission(Permission.ACCESS_FINE_LOCATION))
    except Exception as e:
        Logger.warning(f'[GPS] No se pudo revisar el permiso: {e!r}')
        return True


if platform == 'android':
    from jnius import PythonJavaClass, autoclass, cast, java_method

    SDK_INT = autoclass('android.os.Build$VERSION').SDK_INT

    def _read_location(location) -> dict:
        """Copia los datos a Python en el mismo hilo de Java que los entrega."""
        fix = {
            'lat': location.getLatitude(),
            'lon': location.getLongitude(),
            'accuracy': location.getAccuracy() if location.hasAccuracy() else None,
            'speed': location.getSpeed() if location.hasSpeed() else None,
            'speed_acc': None,
            't': location.getElapsedRealtimeNanos() / 1e9,
        }
        if SDK_INT >= 26 and location.hasSpeedAccuracy():
            fix['speed_acc'] = location.getSpeedAccuracyMetersPerSecond()
        return fix

    class _LocationListener(PythonJavaClass):
        """Recibe las ubicaciones en el hilo de UI de Android y se las pasa a GpsHelper."""
        __javainterfaces__ = ['android/location/LocationListener']

        def __init__(self, on_fix, on_provider):
            self.on_fix = on_fix
            self.on_provider = on_provider
            super().__init__()

        def _deliver(self, location) -> None:
            try:
                self.on_fix(_read_location(location))
            except Exception as e:
                Logger.warning(f'[GPS] Lectura ilegible: {e!r}')

        @java_method('(Landroid/location/Location;)V')
        def onLocationChanged(self, location):
            self._deliver(location)

        # Android 12+ entrega las lecturas por esta variante; sin ella no llega ninguna
        @java_method('(Ljava/util/List;)V', name='onLocationChanged')
        def onLocationChangedBatch(self, locations):
            for i in range(locations.size()):
                self._deliver(cast('android.location.Location', locations.get(i)))

        @java_method('(I)V')
        def onFlushComplete(self, request_code):
            pass

        @java_method('(Ljava/lang/String;)V')
        def onProviderEnabled(self, provider):
            self.on_provider(True)

        @java_method('(Ljava/lang/String;)V')
        def onProviderDisabled(self, provider):
            self.on_provider(False)

        @java_method('(Ljava/lang/String;ILandroid/os/Bundle;)V')
        def onStatusChanged(self, provider, status, extras):
            pass


class GpsHelper:
    """Lee la velocidad del GPS y la publica filtrada, en km/h, en speed_queue."""

    def __init__(self):
        self.speed_queue: Optional[asyncio.Queue] = None
        self.running = False
        self.velocity = 0.0          # km/h filtrada con Kalman: es la que se envia al ESP32
        self.raw_velocity = 0.0      # km/h tal como la reporta el GNSS
        self.readings = 0
        self.last_fix = {}
        self.last_fix_time = 0.0     # time.time() de la ultima fija recibida, buena o mala
        self.last_accuracy = 0.0
        self.kf = SpeedKalman()
        self._prev = None            # ultima fija precisa, para la velocidad por posicion
        self._last_t = None
        self._last_publish = 0.0
        self._manager = None
        self._listener = None        # debe seguir vivo mientras Android pueda llamarlo
        self._retry = None
        self._warned = False
        self._dialog = None

    def run(self, speed_queue: asyncio.Queue) -> None:
        """Arranca el GPS una sola vez; llamarlo de nuevo solo actualiza la cola."""
        self.speed_queue = speed_queue
        if self.running or platform != 'android':
            return
        self._cancel_retry()
        if not _has_location_permission():
            if not self._warned:
                Logger.warning('[GPS] Falta el permiso de ubicacion precisa, se reintentara')
                self._warned = True
            self._retry = Clock.schedule_once(lambda dt: self.run(self.speed_queue),
                                              PERMISSION_RETRY_S)
            return
        try:
            self._start_android()
        except Exception as e:
            Logger.error(f'[GPS] No se pudo iniciar: {e!r}')

    def _start_android(self) -> None:
        Context = autoclass('android.content.Context')
        Looper = autoclass('android.os.Looper')
        activity = autoclass('org.kivy.android.PythonActivity').mActivity
        manager = cast('android.location.LocationManager',
                       activity.getSystemService(Context.LOCATION_SERVICE))
        if self._listener is None:
            self._listener = _LocationListener(self._on_fix, self._on_provider)
        manager.requestLocationUpdates(GPS_PROVIDER, MIN_TIME_MS, MIN_DISTANCE_M,
                                       self._listener, Looper.getMainLooper())
        self._manager = manager
        self.running = True
        self.kf.reset()
        self._prev = self._last_t = None
        Logger.info(f'[GPS] Escuchando el proveedor GPS, intervalo minimo {MIN_TIME_MS} ms')
        if not manager.isProviderEnabled(GPS_PROVIDER):
            self._on_provider(False)

    def stop(self) -> None:
        self._cancel_retry()
        if not self.running:
            return
        self.running = False
        try:
            self._manager.removeUpdates(self._listener)
            Logger.info('[GPS] Detenido')
        except Exception as e:
            Logger.warning(f'[GPS] No se pudo detener: {e!r}')

    def _cancel_retry(self) -> None:
        if self._retry is not None:
            self._retry.cancel()
            self._retry = None

    # Android llama desde su hilo de UI; Kivy y asyncio solo se tocan desde el de Kivy
    @mainthread
    def _on_fix(self, fix: dict) -> None:
        self.handle_fix(fix)

    @mainthread
    def _on_provider(self, enabled: bool) -> None:
        if enabled:
            Logger.info('[GPS] Ubicacion del celular activada')
        else:
            Logger.warning('[GPS] Ubicacion del celular desactivada')
            self.open_gps_access_popup()

    def handle_fix(self, fix: dict) -> None:
        """Procesa una fija: calcula la velocidad, la filtra y la publica."""
        self.last_fix_time = time.time()
        accuracy = fix.get('accuracy')
        self.last_accuracy = accuracy if accuracy is not None else 0.0
        speed_ms, variance = self._measure(fix)
        if accuracy is not None and accuracy <= MAX_ACCURACY_M:
            self._prev = fix
        if speed_ms is None:
            return

        if self._last_t is not None:
            self.kf.predict(fix['t'] - self._last_t)
        self._last_t = fix['t']
        estimate = max(0.0, self.kf.update(speed_ms, variance))

        self.raw_velocity = round(speed_ms * 3.6, 2)
        self.velocity = round(estimate * 3.6, 2)
        self.readings += 1
        self.last_fix = {'lat': fix['lat'], 'lon': fix['lon'], 'speed_kmh': self.velocity,
                         'accuracy': self.last_accuracy, 'time': self.last_fix_time}
        Logger.info(f'[GPS] v={self.velocity:.1f} km/h (crudo {self.raw_velocity:.1f}, '
                    f'precision {self.last_accuracy:.0f} m)')
        self._publish(self.velocity)

    def _measure(self, fix: dict):
        """Velocidad en m/s y su varianza. Prefiere el Doppler que reporta el GNSS."""
        speed = fix.get('speed')
        if speed is not None:
            std = fix.get('speed_acc')
            variance = max(std, MIN_SPEED_STD) ** 2 if std else DOPPLER_VAR
        else:
            prev, accuracy = self._prev, fix.get('accuracy')
            if prev is None or accuracy is None or accuracy > MAX_ACCURACY_M:
                return None, None
            dt = fix['t'] - prev['t']
            if not 0.2 <= dt <= 10.0:
                return None, None
            speed = _haversine_m(prev['lat'], prev['lon'], fix['lat'], fix['lon']) / dt
            variance = POSITION_VAR
        if not 0.0 <= speed <= MAX_SPEED_MS:
            return None, None
        return speed, variance

    def _publish(self, kmh: float) -> None:
        """Deja en la cola solo la lectura mas nueva: al ESP32 no le sirven las viejas."""
        queue = self.speed_queue
        now = time.monotonic()
        if queue is None or now - self._last_publish < PUBLISH_MIN_S:
            return
        self._last_publish = now
        while not queue.empty():
            queue.get_nowait()
        queue.put_nowait(kmh)

    def open_gps_access_popup(self) -> None:
        if self._dialog is not None:
            return
        from kivymd.uix.dialog import MDDialog
        dialog = MDDialog(title='GPS desactivado',
                          text='Activa la ubicación del celular para medir la velocidad.')
        dialog.size_hint = [0.8, 0.8]
        dialog.pos_hint = {'center_x': 0.5, 'center_y': 0.5}
        dialog.bind(on_dismiss=self._on_dialog_dismiss)
        self._dialog = dialog
        dialog.open()

    def _on_dialog_dismiss(self, *_) -> None:
        self._dialog = None