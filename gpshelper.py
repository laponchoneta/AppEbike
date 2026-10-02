from kivy.logger import Logger
from kivy.utils import platform
from kivymd.uix.dialog import MDDialog
import asyncio
import time
from kalmanfilter import KalmanWrapper

MAX_ACCURACY_M = 25.0   # fijas con peor precision se descartan


class GpsHelper:
    gps_time: float = 500
    gps_min_distance: float = 3.0
    velocity: float = 0.0
    running: bool = False
    last_fix: dict = {}         # ultima posicion valida, la consume trip.py
    last_fix_time: float = 0.0  # incluye fijas descartadas, para detectar "sin senial"
    last_accuracy: float = 0.0

    def run(self, speed_queue: asyncio.Queue) -> None:
        """Arranca el GPS una sola vez (no duplica callbacks al reconectar)."""
        self.speed_queue = speed_queue
        if self.running:
            Logger.info('[BLE] El GPS ya estaba corriendo')
            return

        # configure GPS
        if platform == 'android' or platform == 'ios':
            from plyer import gps
            gps.configure(on_location=self.on_location,
                          on_status=self.on_auth_status)
            self.kf = KalmanWrapper()
            gps.start(minTime=self.gps_time, minDistance=self.gps_min_distance)
            self.running = True
            Logger.info('[BLE] GPS iniciado')

    def stop(self) -> None:
        """Detiene el GPS al desconectar a mano."""
        if not self.running:
            return
        self.running = False
        if platform == 'android' or platform == 'ios':
            try:
                from plyer import gps
                gps.stop()
                Logger.info('[BLE] GPS detenido')
            except Exception as e:
                Logger.warning(f'[BLE] No se pudo detener el GPS: {e!r}')

    def on_location(self, *args, **kwargs):
        """callback used to gather relevant information"""
        now = time.time()
        speed_ms = kwargs.get('speed') or 0.0
        accuracy = kwargs.get('accuracy') or 0.0
        try:
            self.kf.predict()
            self.kf.update(speed_ms)
        except Exception as e:
            Logger.warning(f'[BLE] Filtro de Kalman: {e!r}')

        self.last_fix_time = now
        self.last_accuracy = accuracy
        if accuracy > MAX_ACCURACY_M:
            Logger.debug(f'[BLE] Fija del GPS descartada: precision {accuracy} m')
            return

        self.velocity = speed_ms * 3.6
        self.last_fix = {
            'lat': kwargs.get('lat'),
            'lon': kwargs.get('lon'),
            'speed_kmh': self.velocity,
            'accuracy': accuracy,
            'time': now,
        }
        self.speed_queue.put_nowait(self.velocity)

    def on_auth_status(self, general_status: str, status_message: str) -> None:
        if general_status == 'provider-enabled':
            pass
        else:
            self.open_gps_access_popup()

    def open_gps_access_popup(self) -> None:
        dialog = MDDialog(title='GPS desactivado',
                          text='Activa el GPS para que la app funcione correctamente')
        dialog.size_hint = [0.8, 0.8]
        dialog.pos_hint = {'center_x': 0.5, 'center_y': 0.5}
        dialog.open()