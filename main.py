# -*- coding: utf-8 -*-
import os

# Debe fijarse ANTES de que arranque SDL: sin esto Android congela el lazo de
# Kivy al apagar la pantalla y la reconexion BLE se detiene (Etapa 4).
os.environ.setdefault('SDL_ANDROID_BLOCK_ON_PAUSE', '0')

from kivymd.app import MDApp
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.core.window import Window
from kivy.lang import Builder
from kivy.logger import Logger
from kivy.utils import platform
from kivy.metrics import dp
from kivy.animation import Animation
from kivy.clock import Clock
from kivy.properties import (BooleanProperty, ListProperty, NumericProperty,
                             StringProperty)
from kivy.storage.jsonstore import JsonStore
import asyncio
import json
import time
from typing import Optional

from kivy.uix.spinner import Spinner, SpinnerOption
from kivy.uix.dropdown import DropDown
from CircularProgressBar import CircularProgressBar
from chart import LiveChart

import theme
from alerts import AlertCenter
from android_utils import (acquire_wake_lock, external_files_dir, keep_screen_on,
                           release_wake_lock, share_file, start_background_service,
                           stop_background_service, vibrate)
from history import HistoryStore, format_date
from recorder import CsvRecorder
from trip import TripTracker, format_duration
from BLE import (Connection, communication_manager, STATE_CONNECTED,
                 STATE_CONNECTING, STATE_DISCONNECTED, STATE_RECONNECTING)
from gpshelper import GpsHelper
from AccHelper import AccHelper
from kivy.factory import Factory

# Caracteristica unica de lectura/escritura del ESP32 (NO cambiar)
CHAR_UUID = "00002A3D-0000-1000-8000-00805f9b34fb"
APP_VERSION = '0.2'
GPS_ON = True
STORE_FILE = 'ebike_store.json'
HISTORY_FILE = 'ebike_history.json'
DEVICE_PLACEHOLDER = 'Selecciona un dispositivo'
SPEED_MAX_KMH = 50.0     # fondo de escala del medidor de velocidad
SP_MAX_PER = 180.0       # tope que usa el modo Manual al convertir el slider
MANIP_MAX = 180.0        # escala supuesta de 'manipulation' para la grafica
BANNER_SECONDS = 5.0     # cuanto dura visible un aviso

# Texto base del chip de estado (Tarea 1)
STATE_TEXTS = {
    STATE_DISCONNECTED: 'Desconectado',
    STATE_CONNECTING: 'Conectando…',
    STATE_CONNECTED: 'Conectado',
    STATE_RECONNECTING: 'Reconectando…',
}


class MainWindow(Screen): pass


class SecondaryWindow(Screen): pass


class WindowManager(ScreenManager): pass


class SpinnerDropdown(DropDown): pass


class Main(MDApp):
    # --- Estado de la conexion: alimenta TODA la UI ---
    connection_state = StringProperty(STATE_DISCONNECTED)
    connection_text = StringProperty(STATE_TEXTS[STATE_DISCONNECTED])
    connection_detail = StringProperty('')
    connection_color = ListProperty(theme.rgba(theme.ERROR))
    pulse_opacity = NumericProperty(1.0)
    live = BooleanProperty(False)            # True solo si los datos estan vigentes
    last_device_name = StringProperty('')
    assist_pending = BooleanProperty(False)  # SP visible pero "sin aplicar"
    data_age = NumericProperty(-1.0)

    # --- Etapa 3: recorrido, grabacion, alertas, historial, diagnostico ---
    trip_distance = StringProperty('0.00')
    trip_time = StringProperty('00:00')
    trip_avg = StringProperty('0.0')
    trip_max = StringProperty('0.0')
    trip_slope = StringProperty('0')
    trip_range = StringProperty('—')
    recording = BooleanProperty(False)
    recording_info = StringProperty('Sin grabar')
    banner_text = StringProperty('')
    banner_color = ListProperty(theme.rgba(theme.WARN))
    banner_visible = BooleanProperty(False)
    diag_text = StringProperty('—')

    # Interruptores de Ajustes: cada funcion se puede apagar sin tocar el BLE
    feat_trip = BooleanProperty(True)
    feat_record = BooleanProperty(True)
    feat_alerts = BooleanProperty(True)
    feat_history = BooleanProperty(True)
    feat_diag = BooleanProperty(False)
    feat_chart = BooleanProperty(False)
    feat_background = BooleanProperty(True)
    FEATURES = ('feat_trip', 'feat_record', 'feat_alerts', 'feat_history',
                'feat_diag', 'feat_chart', 'feat_background')

    screen_flag = True

    datajson_queue = asyncio.Queue()
    speed_queue = asyncio.Queue()
    device_queue = asyncio.Queue()
    battery_queue = asyncio.Queue()
    manipulation_queue = asyncio.Queue()

    def build(self):
        """setting design for application widget development specifications on design.kv"""
        self.theme_cls.theme_style = 'Dark'
        self.theme_cls.primary_palette = 'Yellow'
        self.theme_cls.accent_palette = 'Amber'
        Window.clearcolor = theme.rgba(theme.BG)
        self.get_permissions()
        return Builder.load_file(filename='design.kv')

    async def launch_app(self):
        """Asyncronous function for kivy app start"""
        await self.async_run(async_lib='asyncio')

    async def start(self):
        """Asyncronous app making sure start is awaited as coroutine"""
        task = asyncio.create_task(self.launch_app())
        (_, pending) = await asyncio.wait({task}, return_when='FIRST_COMPLETED')

    def on_start(self):
        """On start method for building desired variables for later use"""
        Logger.info('[BLE] Arranque de la app')

        # Estado interno
        self._tasks = {}
        self._device_map = {}
        self.connection = None
        self.manual_disconnect = False
        self.last_device = {'name': '', 'address': ''}
        self.angle_value = 0.0
        self._prev_state = STATE_DISCONNECTED
        self._assist_ever_sent = False
        self._last_battery = ''
        self._last_manipulation = ''
        self._packets_seen = 0
        self._banner_event = None
        self.gps_helper = GpsHelper()
        self.acc_helper = AccHelper()

        # Etapa 3: recorrido, grabacion, alertas e historial
        self.trip = TripTracker()
        self.recorder = CsvRecorder(external_files_dir() or self.user_data_dir)
        self.history = HistoryStore(os.path.join(self.user_data_dir, HISTORY_FILE))
        self.alerts = AlertCenter(show=self.show_banner, vibrate=vibrate)

        # Ventana Status
        self.speedmeter_indicator = self.root.get_screen('secondary_window').ids.speed_indicator
        self.sp_indicator = self.root.get_screen('secondary_window').ids.sp_indicator
        self.manip_indicator = self.root.get_screen('secondary_window').ids.manip_indicator
        self.baterry_indicator = self.root.get_screen('secondary_window').ids.baterry_indicator

        # Ventana Settings
        self.angle_indicator = self.root.get_screen('secondary_window').ids.angle_indicator
        self.read_slider_text = self.root.get_screen('secondary_window').ids.read_slider_text
        self.per_button_pressed = True
        self.km_button_pressed = False
        self.slider_label = 'slider'
        self.slider_value = 0
        self.slider_flag = False

        # Ventana Conexion BLE: lista oculta mientras no se escanee
        self._hide_device_list()
        self._load_store()
        self._refresh_chip()
        self._apply_live_ui()

        # KivyMD 1.0.2 calcula el ancho de las pestanias una sola vez, cuando el
        # navegador todavia mide 100 px, y nunca lo corrige: los textos se enciman.
        nav = self.root.get_screen('secondary_window').ids.nav
        nav.bind(size=self._fix_bottom_nav)
        self._fix_bottom_nav()

        self._load_features()
        Clock.schedule_interval(self._tick, 1.0)

    def _fix_bottom_nav(self, *_) -> None:
        nav = self.root.get_screen('secondary_window').ids.nav
        tabs = nav.ids.tab_manager.screens
        if not tabs or nav.width <= 1:
            return
        for tab in tabs:
            tab.header.width = nav.width / len(tabs)
        nav.ids.tab_bar.width = nav.width

    # ------------------------------------------------------------------ #
    # Interruptores de las funciones nuevas (Etapa 3)
    # ------------------------------------------------------------------ #
    def _load_features(self) -> None:
        try:
            if self.store.exists('features'):
                saved = self.store.get('features')
                for name in self.FEATURES:
                    if name in saved:
                        setattr(self, name, bool(saved[name]))
        except Exception as e:
            Logger.warning(f'[BLE] No se pudieron leer los ajustes: {e!r}')
        for name in self.FEATURES:
            self.bind(**{name: self._save_features})
        self.alerts.enabled = self.feat_alerts

    def _save_features(self, *_) -> None:
        self.alerts.enabled = self.feat_alerts
        try:
            self.store.put('features',
                           **{name: getattr(self, name) for name in self.FEATURES})
        except Exception as e:
            Logger.warning(f'[BLE] No se pudieron guardar los ajustes: {e!r}')

    # ------------------------------------------------------------------ #
    # Latido de 1 Hz: recorrido, alertas, grabacion y diagnostico
    # ------------------------------------------------------------------ #
    def _tick(self, _dt) -> None:
        fix = self.gps_helper.last_fix
        if self.feat_trip and self.trip.running and fix:
            self.trip.add_fix(fix.get('lat'), fix.get('lon'),
                              fix.get('speed_kmh'), fix.get('accuracy'),
                              fix.get('time'))
        self._refresh_trip()

        if self.feat_alerts and self.gps_helper.running:
            last_fix_time = self.gps_helper.last_fix_time
            age = (time.time() - last_fix_time) if last_fix_time else None
            self.alerts.check_gps(self.gps_helper.last_accuracy, age)

        if self.recorder.recording:
            self.recorder.write(self._snapshot())
            self.recording_info = f'{self.recorder.rows} filas'

        if self.feat_chart:
            self._push_chart()

        self._refresh_diag()

    def _push_chart(self) -> None:
        """Series normalizadas 0..1 para comparar velocidad contra el set point."""
        chart = self.root.get_screen('secondary_window').ids.live_chart
        sp_scale = SP_MAX_PER if self.per_button_pressed else SPEED_MAX_KMH
        chart.push(self.gps_helper.velocity / SPEED_MAX_KMH,
                   self.slider_value / sp_scale,
                   float(self._last_manipulation or 0) / MANIP_MAX)

    def _refresh_trip(self) -> None:
        trip = self.trip
        self.trip_distance = f'{trip.distance_km:.2f}'
        self.trip_time = format_duration(trip.duration_s)
        self.trip_avg = f'{trip.avg_speed:.1f}'
        self.trip_max = f'{trip.max_speed:.1f}'
        self.trip_slope = f'{trip.slope_percent:+.0f}'
        remaining = trip.estimated_range_km
        self.trip_range = f'~{remaining:.0f} km (est.)' if remaining else '—'

    def _snapshot(self) -> dict:
        """Fila del CSV con todo lo que se puede medir en este instante."""
        return {
            'speed_kmh': round(self.gps_helper.velocity, 2),
            'battery': self._last_battery,
            'angle': self.angle_value,
            'manipulation': self._last_manipulation,
            'sp': self.slider_value,
            'modo': 'manual' if self.per_button_pressed else 'automatico',
            'acc_y': round(getattr(self.acc_helper, 'y', 0.0) or 0.0, 3),
            'conexion': self.connection_state,
        }

    def _refresh_diag(self) -> None:
        if not self.feat_diag:
            return
        packets = getattr(self.connection, 'packets', 0)
        rate = max(0, packets - self._packets_seen)
        self._packets_seen = packets
        age = f'{int(self.data_age)} s' if self.data_age >= 0 else '—'
        self.diag_text = (
            f'Último dato: {age}  ·  {rate} paq/s  ·  '
            f'reintentos: {getattr(self.connection, "total_attempts", 0)}  ·  '
            f'GPS ±{int(self.gps_helper.last_accuracy)} m  ·  v{APP_VERSION}'
        )

    # ------------------------------------------------------------------ #
    # Avisos (banner + vibracion)
    # ------------------------------------------------------------------ #
    def show_banner(self, text: str, level: str = 'warn') -> None:
        colors = {'info': theme.OK, 'warn': theme.WARN, 'error': theme.ERROR}
        self.banner_text = text
        self.banner_color = theme.rgba(colors.get(level, theme.WARN))
        self.banner_visible = True
        if self._banner_event is not None:
            self._banner_event.cancel()
        self._banner_event = Clock.schedule_once(self.hide_banner, BANNER_SECONDS)

    def hide_banner(self, *_) -> None:
        self.banner_visible = False
        self._banner_event = None

    # ------------------------------------------------------------------ #
    # Grabacion a CSV
    # ------------------------------------------------------------------ #
    def toggle_recording(self, *_) -> None:
        if self.recorder.recording:
            path = self.recorder.stop()
            self.recording = False
            self.recording_info = os.path.basename(path) if path else 'Sin grabar'
            self.show_banner(f'Grabación guardada: {self.recording_info}', 'info')
            return
        if not self.feat_record:
            return
        if self.recorder.start() is None:
            self.show_banner('No se pudo iniciar la grabación', 'error')
            return
        self.recording = True
        self.recording_info = '0 filas'
        self.show_banner('Grabando datos del recorrido', 'info')

    def share_recording(self, *_) -> None:
        files = self.recorder.list_files()
        if not files:
            self.show_banner('No hay grabaciones que compartir', 'warn')
            return
        if not share_file(files[0]):
            self.show_banner(f'Archivo en {files[0]}', 'info')

    # ------------------------------------------------------------------ #
    # Recorrido e historial
    # ------------------------------------------------------------------ #
    def _finish_trip(self) -> None:
        """Cierra el recorrido, lo guarda en el historial y muestra el resumen."""
        if not self.trip.running:
            return
        self.trip.stop()
        self._refresh_trip()
        summary = self.trip.summary()
        Logger.info(f'[BLE] Recorrido terminado: {summary}')
        if self.feat_history and self.history.should_save(summary):
            self.history.add(summary)
            self.show_trip_summary(summary)

    def show_trip_summary(self, summary: dict) -> None:
        popup = Factory.SummaryPopup()
        used = summary.get('bateria_usada')
        rows = [
            ('Fecha', format_date(summary.get('inicio', 0))),
            ('Duración', format_duration(summary.get('duracion_s', 0))),
            ('Distancia', f"{summary.get('distancia_km', 0):.2f} km"),
            ('Vel. promedio', f"{summary.get('vel_prom', 0):.1f} km/h"),
            ('Vel. máxima', f"{summary.get('vel_max', 0):.1f} km/h"),
            ('Batería usada', f'{used} %' if used is not None else '—'),
        ]
        body = popup.ids.body
        body.clear_widgets()
        for name, value in rows:
            row = Factory.SummaryRow()
            row.ids.name.text = name
            row.ids.value.text = value
            body.add_widget(row)
        popup.open()

    def open_history(self, *_) -> None:
        popup = Factory.HistoryPopup()
        self._fill_history(popup)
        popup.open()

    def _fill_history(self, popup) -> None:
        body = popup.ids.body
        body.clear_widgets()
        trips = self.history.list()
        popup.ids.empty.opacity = 0 if trips else 1
        for key, data in trips:
            used = data.get('bateria_usada')
            detail = (f"{data.get('distancia_km', 0):.2f} km  ·  "
                      f"{format_duration(data.get('duracion_s', 0))}")
            if used is not None:
                detail += f"  ·  batería −{used} %"
            row = Factory.TripRow()
            row.ids.title.text = format_date(data.get('inicio', 0))
            row.ids.detail.text = detail
            row.ids.detail2.text = (f"prom {data.get('vel_prom', 0):.1f}  ·  "
                                    f"máx {data.get('vel_max', 0):.1f} km/h")
            row.ids.delete.bind(
                on_release=lambda _w, k=key, p=popup: self._delete_trip(k, p))
            body.add_widget(row)

    def _delete_trip(self, key: str, popup) -> None:
        self.history.delete(key)
        self._fill_history(popup)

    def get_permissions(self):
        """Permisos en runtime. BLUETOOTH_SCAN es obligatorio en Android 12+."""
        if platform != 'android':
            return
        from android.permissions import Permission, request_permissions

        def callback(permissions, results):
            missing = [p for p, ok in zip(permissions, results) if not ok]
            if missing:
                Logger.warning(f'[BLE] Permisos NO concedidos: {missing}')
            else:
                Logger.info('[BLE] Todos los permisos concedidos')

        def perm(name, fallback):
            # python-for-android viejo puede no exponer las constantes nuevas.
            return getattr(Permission, name, fallback)

        wanted = [
            Permission.ACCESS_COARSE_LOCATION,
            Permission.ACCESS_FINE_LOCATION,
            Permission.BLUETOOTH,
            Permission.BLUETOOTH_ADMIN,
            perm('BLUETOOTH_SCAN', 'android.permission.BLUETOOTH_SCAN'),
            perm('BLUETOOTH_CONNECT', 'android.permission.BLUETOOTH_CONNECT'),
        ]
        try:
            # ACCESS_BACKGROUND_LOCATION se pide aparte a proposito: Android 11+
            # rechaza toda la peticion si se mezcla con permisos de primer plano.
            request_permissions(wanted, callback)
        except Exception as e:
            Logger.error(f'[BLE] Error al pedir permisos: {e!r}')

    # ------------------------------------------------------------------ #
    # Tareas asincronas: una sola instancia de cada una
    # ------------------------------------------------------------------ #
    def _start_task(self, name: str, coro) -> asyncio.Future:
        """Crea una tarea unica por nombre; si ya existia, la cancela antes."""
        self._cancel_task(name)
        task = asyncio.ensure_future(coro)
        self._tasks[name] = task
        Logger.info(f'[BLE] Tarea "{name}" iniciada')
        return task

    def _cancel_task(self, name: str) -> None:
        task = self._tasks.pop(name, None)
        if task is not None and not task.done():
            task.cancel()
            Logger.info(f'[BLE] Tarea "{name}" cancelada')

    def _cancel_all_tasks(self, exclude=()) -> None:
        for name in list(self._tasks):
            if name not in exclude:
                self._cancel_task(name)

    def log_task_inventory(self, tag: str = '') -> None:
        """Permite verificar en logcat que no quedan tareas duplicadas."""
        alive = sorted(n for n, t in self._tasks.items() if not t.done())
        try:
            total = len(asyncio.all_tasks())
        except RuntimeError:
            total = -1
        Logger.info(f'[BLE] Inventario {tag}: propias={alive} asyncio_total={total}')

    # ------------------------------------------------------------------ #
    # Conexion / desconexion
    # ------------------------------------------------------------------ #
    def connect_ble(self, *_) -> None:
        """Boton 'Buscar dispositivos': escanea y deja elegir en la lista."""
        self.start_connection(None)

    def reconnect_last(self, *_) -> None:
        """Boton 'Reconectar a <nombre>': un toque, sin escanear."""
        if not self.last_device.get('address'):
            return
        Logger.info('[BLE] Reconexion manual al ultimo dispositivo')
        self.start_connection(dict(self.last_device))

    def start_connection(self, target: Optional[dict] = None) -> None:
        """Arranca la conexion y todas sus tareas desde cero."""
        if self.connection_state != STATE_DISCONNECTED:
            Logger.info('[BLE] Ya hay una conexion en curso, se ignora el toque')
            return
        # Conectar a proposito limpia la bandera de desconexion manual.
        self.manual_disconnect = False
        self.assist_pending = False
        self._assist_ever_sent = False
        self._cancel_all_tasks(exclude=('shutdown',))
        self._drain_queues()
        self.alerts.reset()
        self.trip.start()
        self.root.get_screen('secondary_window').ids.live_chart.clear()
        self._enter_background_mode()
        self.set_connection_state(STATE_CONNECTING)
        self.root.get_screen('main_window').ids.spinner.active = True

        self._start_task('ble', self._ble_main(target))
        self._start_task('acc', self.acc_helper.run(self.datajson_queue))
        self._start_task('battery', self.update_battery_value())
        self._start_task('speed', self.update_speed_value())
        self._start_task('manipulation', self.update_manipulation_value())
        if GPS_ON:
            self.gps_helper.run(self.speed_queue)
        self.log_task_inventory('al conectar')

    async def _ble_main(self, target: Optional[dict] = None) -> None:
        """Crea la conexion y corre su gestor hasta que se cancele la tarea."""
        connection = Connection(app=self,
                                read_char=CHAR_UUID,
                                write_char=CHAR_UUID,
                                device_queue=self.device_queue,
                                battery_queue=self.battery_queue,
                                manipulation_queue=self.manipulation_queue,
                                target=target)
        self.connection = connection
        self._start_task('ble_comm',
                         communication_manager(connection, self.datajson_queue))
        await connection.manager()

    async def _shutdown_connection(self) -> None:
        """Desconexion manual: corta todo y NO vuelve a intentar."""
        connection, self.connection = self.connection, None
        for name in ('ble', 'ble_comm', 'acc', 'battery', 'speed', 'manipulation'):
            self._cancel_task(name)
        self.gps_helper.stop()
        if self.recorder.recording:
            self.toggle_recording()
        self._finish_trip()
        self._exit_background_mode()
        if connection is not None:
            await connection.close()
        self.set_connection_state(STATE_DISCONNECTED)
        self.log_task_inventory('tras desconectar')

    # ------------------------------------------------------------------ #
    # Segundo plano (Etapa 4)
    # ------------------------------------------------------------------ #
    # ------------------------------------------------------------------ #
    # Segundo plano (Etapa 4)
    # ------------------------------------------------------------------ #
    def _enter_background_mode(self) -> None:
        """Mantiene viva la app con la pantalla apagada mientras hay conexion."""
        if not self.feat_background:
            return
        acquire_wake_lock()
        
        # PREVENCIÓN DE CRASHEO API 34: Android matará la app si se inicia el
        # servicio sin que el usuario haya aceptado los popups de Bluetooth.
        if platform == 'android':
            from android.permissions import Permission, check_permission
            try:
                bt_connect = getattr(Permission, 'BLUETOOTH_CONNECT', 'android.permission.BLUETOOTH_CONNECT')
                if not check_permission(bt_connect):
                    Logger.warning('[BLE] Permiso BT_CONNECT denegado. Posponiendo servicio en primer plano.')
                    return
            except Exception as e:
                Logger.warning(f'[BLE] Error validando permisos para servicio: {e!r}')

        if not start_background_service():
            Logger.warning('[BLE] Sin servicio en primer plano: '
                           'Android puede congelar la app en segundo plano')

    def _exit_background_mode(self) -> None:
        stop_background_service()
        release_wake_lock()

    def on_pause(self):
        """Devolver True evita que Kivy termine la app al pasar a segundo plano."""
        Logger.info('[BLE] App en segundo plano')
        return True

    def on_resume(self):
        Logger.info('[BLE] App de vuelta en primer plano')

    def _drain_queues(self) -> None:
        """Tira datos viejos para no mandarselos al ESP32 al reconectar."""
        for queue in (self.datajson_queue, self.speed_queue, self.device_queue,
                      self.battery_queue, self.manipulation_queue):
            while True:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

    # ------------------------------------------------------------------ #
    # Maquina de estados del indicador en vivo
    # ------------------------------------------------------------------ #
    def set_connection_state(self, state: str, detail: str = '') -> None:
        """Unica puerta de entrada para cambiar el estado de la conexion."""
        if state != self.connection_state:
            Logger.info(f'[BLE] Estado: {self.connection_state} -> {state} {detail}'.strip())
            self._prev_state = self.connection_state
        self.connection_detail = detail
        self.connection_state = state

    def on_connection_state(self, *_) -> None:
        state = self.connection_state
        self.live = (state == STATE_CONNECTED)
        self.connection_color = theme.rgba(theme.STATE_COLORS.get(state, theme.ERROR))
        if state != STATE_CONNECTED:
            self.data_age = -1.0

        # Seguridad: tras una reconexion el SP queda "sin aplicar" hasta que
        # el usuario toque el control.
        if state == STATE_CONNECTED and self._prev_state == STATE_RECONNECTING:
            self.assist_pending = self._assist_ever_sent

        keep_screen_on(state in (STATE_CONNECTED, STATE_RECONNECTING))
        if self.feat_alerts:
            self.alerts.check_connection(state)
        self._refresh_chip()
        self._update_pulse()
        self._apply_live_ui()

        if state == STATE_CONNECTED:
            self.root.get_screen('main_window').ids.spinner.active = False
            if self.root.current != 'secondary_window':
                self.root.current = 'secondary_window'
        elif state == STATE_DISCONNECTED:
            self.root.get_screen('main_window').ids.spinner.active = False
            self._hide_device_list()

    def on_connection_detail(self, *_) -> None:
        self._refresh_chip()

    def _refresh_chip(self) -> None:
        text = STATE_TEXTS.get(self.connection_state, self.connection_state)
        if self.connection_detail:
            text = f'{text} {self.connection_detail}'
        self.connection_text = text

    def _update_pulse(self) -> None:
        """Pulso suave del punto del chip: solo late cuando esta 'en vivo'."""
        Animation.cancel_all(self, 'pulse_opacity')
        if self.connection_state != STATE_CONNECTED:
            self.pulse_opacity = 1.0
            return
        pulse = Animation(pulse_opacity=0.3, d=0.7) + Animation(pulse_opacity=1.0, d=0.7)
        pulse.repeat = True
        pulse.start(self)

    def _apply_live_ui(self) -> None:
        """Atenua y borra los datos del ESP32 cuando ya no estan vigentes."""
        if self.live or not hasattr(self, 'baterry_indicator'):
            return
        self.baterry_indicator.text = '—'
        self.baterry_indicator.set_value = 0
        self.sp_indicator.text = 'SP: —'
        self.manip_indicator.text = 'M: —'
        self.angle_indicator.text = '—'

    def update_data_age(self, age: float) -> None:
        """La llama el vigilante de BLE una vez por segundo."""
        self.data_age = age
        if self.connection_state == STATE_CONNECTED:
            self.set_connection_state(STATE_CONNECTED,
                                      f'· {int(age)} s' if age >= 3 else '')

    def update_angle(self, value) -> None:
        """Inclinacion en grados que manda el ESP32."""
        try:
            self.angle_value = float(value)
        except (TypeError, ValueError):
            return
        self.angle_indicator.text = f'{self.angle_value:.0f}°'
        self.trip.set_angle(self.angle_value)

    # ------------------------------------------------------------------ #
    # Ultimo dispositivo (sobrevive al cierre de la app)
    # ------------------------------------------------------------------ #
    def _load_store(self) -> None:
        try:
            self.store = JsonStore(os.path.join(self.user_data_dir, STORE_FILE))
            if self.store.exists('last_device'):
                data = self.store.get('last_device')
                self.last_device = {'name': data.get('name', ''),
                                    'address': data.get('address', '')}
                self.last_device_name = self.last_device['name']
                Logger.info(f'[BLE] Ultimo dispositivo recordado: {self.last_device}')
        except Exception as e:
            Logger.warning(f'[BLE] No se pudo leer el almacenamiento local: {e!r}')

    def remember_device(self, name: str, address: str) -> None:
        """Guarda en disco el ultimo ESP32 al que si se logro conectar."""
        if not address:
            return
        name = name or address
        if self.last_device == {'name': name, 'address': address}:
            return
        self.last_device = {'name': name, 'address': address}
        self.last_device_name = name
        try:
            self.store.put('last_device', name=name, address=address)
            Logger.info(f'[BLE] Ultimo dispositivo guardado: {name} ({address})')
        except Exception as e:
            Logger.warning(f'[BLE] No se pudo guardar el ultimo dispositivo: {e!r}')

    # ------------------------------------------------------------------ #
    # Lista de dispositivos de la pantalla principal
    # ------------------------------------------------------------------ #
    def _hide_device_list(self) -> None:
        dropdown = self.root.get_screen('main_window').ids.device_dropdown
        self._device_map = {}
        dropdown.values = []
        dropdown.text = ''
        dropdown.size_hint = (None, None)
        dropdown.size = (0, 0)
        dropdown.opacity = 0
        dropdown.disabled = True

    def show_scanning(self, active: bool) -> None:
        """La llama BLE.py mientras escanea."""
        self.root.get_screen('main_window').ids.spinner.active = bool(active)
        if active:
            self._hide_device_list()

    def fill_device_list(self, devices: dict) -> None:
        """Llena el Spinner con los dispositivos encontrados {nombre: direccion}."""
        self._device_map = dict(devices)
        dropdown = self.root.get_screen('main_window').ids.device_dropdown
        dropdown.values = list(devices.keys())
        dropdown.text = DEVICE_PLACEHOLDER
        dropdown.size_hint = (None, None)
        dropdown.size = (dp(260), dp(48))
        dropdown.opacity = 1
        dropdown.disabled = False
        self.root.get_screen('main_window').ids.spinner.active = False

    def device_clicked(self, _, value: str) -> None:
        """Acepta cualquier dispositivo de la lista, no solo uno llamado ESP32."""
        if not value or value == DEVICE_PLACEHOLDER or value not in self._device_map:
            return
        Logger.info(f'[BLE] Dispositivo seleccionado: {value}')
        self.device_queue.put_nowait(value)
        self.root.get_screen('main_window').ids.spinner.active = True


    # ------------------------------------------------------------------ #
    # Controles de asistencia
    # ------------------------------------------------------------------ #
    def _send_assist(self, payload: dict) -> None:
        """Comandos de asistencia: SOLO por accion del usuario y con conexion viva."""
        if not self.live or self.manual_disconnect:
            Logger.info(f'[BLE] Sin conexion viva, comando ignorado: {payload}')
            return
        self.datajson_queue.put_nowait(json.dumps(payload))
        self._assist_ever_sent = True
        self.assist_pending = False

    def switch_motion_detect(self, _, value: bool) -> None:
        """Switch state for adaptive mode switch"""
        self._send_assist({'adapt': 0 if value else 1})
        slider = self.root.get_screen('secondary_window').ids.adapt_slider
        slider.value = slider.min

    def _highlight_mode(self, manual: bool) -> None:
        """Resalta en amarillo el modo de asistencia activo."""
        ids = self.root.get_screen('secondary_window').ids
        active = (theme.rgba(theme.UDEM_YELLOW), theme.rgba(theme.UDEM_CHARCOAL))
        idle = (theme.rgba(theme.SURFACE_ALT), theme.rgba(theme.TEXT))
        ids.manual_button.md_bg_color, ids.manual_button.text_color = active if manual else idle
        ids.auto_button.md_bg_color, ids.auto_button.text_color = idle if manual else active

    def slider_unit_km(self, touch: bool) -> None:
        """Modo Automatico: el set point se expresa en km/h."""
        if touch:
            self.km_button_pressed = True
            self.per_button_pressed = False
            self.read_slider_text.text = f'0 km/h'
            slider = self.root.get_screen('secondary_window').ids.adapt_slider
            slider.value = 0
            slider.max = 40
            slider.step = 1
            self._highlight_mode(manual=False)

    def slider_unit_per(self, touch: bool) -> None:
        """Modo Manual: el set point se expresa en porcentaje."""
        if touch:
            self.km_button_pressed = False
            self.per_button_pressed = True
            self.read_slider_text.text = f'0 %'
            slider = self.root.get_screen('secondary_window').ids.adapt_slider
            slider.value = 0
            slider.max = 100
            slider.step = 5
            self._highlight_mode(manual=True)

    def slider_on_value(self, _, value: int) -> None:
        """Sends percentage of assistance wanted to ESP32
        In automatic mode: Use BATTERY and ACCELEROMETER values to determine percentage of use"""
        label = self.slider_label
        if self.per_button_pressed:
            self.read_slider_text.text = f'{value} %'
            value = int(180 * value / 100)
            label = 'slider_per'
        if self.km_button_pressed:
            self.sp_indicator.text = f'SP: {value}'
            self.read_slider_text.text = f'{value} km/h'
            value = value
            label = 'slider_km'

        self.slider_label = label
        self.slider_value = value

        if value == self.root.get_screen('secondary_window').ids.adapt_slider.max:
            self.root.get_screen('secondary_window').ids.adapt_slider.hint_text_color = theme.rgba(theme.ERROR)
            if not self.slider_flag:
                self._send_assist({self.slider_label: self.slider_value})
                self.slider_flag = True
        elif value == self.root.get_screen('secondary_window').ids.adapt_slider.min and not self.slider_flag:
            self._send_assist({self.slider_label: self.slider_value})
            self.slider_flag = True
        else:
            self.root.get_screen('secondary_window').ids.adapt_slider.hint_text_color = theme.rgba(theme.UDEM_CHARCOAL)
            self.slider_flag = False

    def slider_touch_up(self, *args) -> None:
        self._send_assist({self.slider_label: self.slider_value})

    async def update_manipulation_value(self) -> None:
        """Monitorea la manipulacion (M) que manda el ESP32"""
        while True:
            try:
                manip = int(float(await self.manipulation_queue.get()))
                self._last_manipulation = manip
                self.manip_indicator.text = f'M: {manip}'  # TODO: convert to percentage
            except asyncio.CancelledError:
                raise
            except Exception as e:
                Logger.warning(f'[BLE] Error en manipulacion: {e!r}')
                await asyncio.sleep(1.0)

    async def update_speed_value(self) -> None:
        """Monitors current speed of bike"""
        gauge = self.speedmeter_indicator
        while True:
            try:
                speed = float(await self.speed_queue.get())
                await self.datajson_queue.put(json.dumps({'speed': speed}))
                if 0 <= speed <= SPEED_MAX_KMH * 1.6:   # descarta glitches del GPS
                    fraction = min(speed, SPEED_MAX_KMH) / SPEED_MAX_KMH
                    sweep = gauge.start_value + fraction * (gauge.end_value - gauge.start_value)
                    gauge.set_value = sweep / 3.6
                    gauge.text = f'{int(speed)} km/h'
            except asyncio.CancelledError:
                raise
            except Exception as e:
                Logger.warning(f'[BLE] Error en velocidad: {e!r}')
                await asyncio.sleep(1.0)

    async def update_battery_value(self) -> None:
        """Monitors Battery life from bike"""
        while True:
            try:
                battery_life = int(float(await self.battery_queue.get()))
                battery_life = max(0, min(100, battery_life))
                self._last_battery = battery_life
                self.baterry_indicator.set_value = battery_life
                self.baterry_indicator.text = f'{battery_life}%'
                self.baterry_indicator.bar_color = theme.battery_color(battery_life)
                self.trip.set_battery(battery_life)
                if self.feat_alerts:
                    self.alerts.check_battery(battery_life)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                Logger.warning(f'[BLE] Error en bateria: {e!r}')
                await asyncio.sleep(2.0)


    # Popup Disconnect
    def screen_flag_1(self, touch: bool) -> None:
        self.screen_flag = True
        
    def screen_flag_2(self, touch: bool) -> None:
        self.screen_flag = False
       
    def cancel_disconnect(self, touch: bool) -> None:
        if touch:
            if (self.screen_flag == True):
                self.root.get_screen('secondary_window').ids.nav.switch_tab('screen 1')
            elif (self.screen_flag == False):
                self.root.get_screen('secondary_window').ids.nav.switch_tab('screen 2')

    def accept_disconnect(self, touch: bool) -> None:
        if touch:
            # La bandera se pone ANTES de tocar la UI para que ningun handler
            # alcance a encolar comandos de ultimo momento.
            self.manual_disconnect = True
            Logger.info('[BLE] Desconexion manual solicitada: no habra reintentos')
            self.root.current = 'main_window'
            self.root.get_screen('secondary_window').ids.nav.switch_tab('screen 1')
            self.reset_ui_and_variables()
            self._start_task('shutdown', self._shutdown_connection())

    def reset_ui_and_variables(self):
        # Ventana Conexión BLE
        self._hide_device_list()
        self.root.get_screen('main_window').ids.spinner.active = False

        # Ventana Status
        self.baterry_indicator.text = '—'
        self.baterry_indicator.set_value = 0
        self.sp_indicator.text = 'SP: —'
        self.manip_indicator.text = 'M: —'

        # Ventana Settings
        self.angle_indicator.text = '—'
        self.per_button_pressed = True
        self.km_button_pressed = False
        self.slider_label = 'slider'
        self.slider_value = 0
        self.slider_flag = False
        self.assist_pending = False
        self.read_slider_text.text = f'0 %'
        self._highlight_mode(manual=True)
        slider = self.root.get_screen('secondary_window').ids.adapt_slider
        slider.max = 100
        slider.step = 5
        slider.value = slider.min
        self.root.get_screen('secondary_window').ids.adapt_switch.active = False

    # Popup Exit
    def cancel_exit(self, touch: bool) -> None:
        if touch:  
            self.root.current = "main_window"

    def accept_exit(self, touch: bool) -> None:
        if touch:
            Logger.info('[BLE] Saliendo de la app...')
            keep_screen_on(False)
            self.stop()
            os._exit(0)

    def on_stop(self):
        """Cierre ordenado: detiene animaciones y cierra el CSV abierto."""
        Animation.cancel_all(self)
        if getattr(self, 'recorder', None) is not None and self.recorder.recording:
            self.recorder.stop()
        self._exit_background_mode()
        keep_screen_on(False)


if __name__ == '__main__':
    async def main_thread():
        """Creating main thread for asynchronous task definition"""
        bike_app = Main()
        await bike_app.start()

    asyncio.run(main_thread())


