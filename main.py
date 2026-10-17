# -*- coding: utf-8 -*-
from kivymd.app import MDApp
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.lang import Builder
from kivy.logger import Logger
from kivy.utils import platform
from kivy.clock import Clock
from kivy.animation import Animation
from kivy.properties import BooleanProperty, ListProperty, NumericProperty, StringProperty
from kivy.storage.jsonstore import JsonStore
import os
import asyncio
import json
import time
from datetime import datetime
from typing import Optional

from kivy.uix.spinner import Spinner, SpinnerOption
from kivy.uix.dropdown import DropDown
from CircularProgressBar import CircularProgressBar

from BLE import (Connection, communication_manager, STATE_CONNECTED,
                 STATE_CONNECTING, STATE_DISCONNECTED, STATE_RECONNECTING)
from gpshelper import GpsHelper
from AccHelper import AccHelper
from trip import TripTracker, format_duration
from ride_log import RideLog, XLSX_MIME, build_workbook, recovered_summary, started_at
from android_utils import (keep_screen_on, open_saved_file, save_to_downloads,
                           sdk_int, share_saved_file)
from kivy.factory import Factory

# Caracteristica unica de lectura/escritura del ESP32 (NO cambiar)
CHAR_UUID = "00002A3D-0000-1000-8000-00805f9b34fb"
GPS_ON = True
STORE_FILE = 'ebike_store.json'
DEVICE_PLACEHOLDER = '...'
EXCEL_FOLDER = 'eBike'          # subcarpeta dentro de Descargas
GPS_STALE_S = 10.0              # sin fijas nuevas en este tiempo = sin senial
PENDING_SUFFIX = '  (sin aplicar)'

STATE_TEXTS = {
    STATE_DISCONNECTED: 'Desconectado',
    STATE_CONNECTING: 'Conectando…',
    STATE_CONNECTED: 'Conectado',
    STATE_RECONNECTING: 'Reconectando…',
}
STATE_COLORS = {
    STATE_DISCONNECTED: [1.0, 0.32, 0.32, 1],
    STATE_CONNECTING: [1.0, 0.70, 0.0, 1],
    STATE_CONNECTED: [0.18, 0.80, 0.44, 1],
    STATE_RECONNECTING: [1.0, 0.70, 0.0, 1],
}


class MainWindow(Screen): pass


class SecondaryWindow(Screen): pass


class WindowManager(ScreenManager): pass


class SpinnerDropdown(DropDown): pass


class Main(MDApp):
    # Estado de la conexion: alimenta el indicador de la barra superior
    connection_state = StringProperty(STATE_DISCONNECTED)
    connection_text = StringProperty(STATE_TEXTS[STATE_DISCONNECTED])
    connection_detail = StringProperty('')
    connection_color = ListProperty(STATE_COLORS[STATE_DISCONNECTED])
    pulse_opacity = NumericProperty(1.0)
    live = BooleanProperty(False)            # True solo si los datos del ESP32 estan vigentes
    last_device_name = StringProperty('')
    assist_pending = BooleanProperty(False)  # SP visible pero "sin aplicar" tras reconectar
    gps_text = StringProperty('GPS inactivo')

    screen_flag = True

    datajson_queue = asyncio.Queue()
    speed_queue = asyncio.Queue()
    device_queue = asyncio.Queue()
    battery_queue = asyncio.Queue()
    manipulation_queue = asyncio.Queue()

    def build(self):
        """setting design for application widget development specifications on design.kv"""
        self.theme_cls.theme_style = 'Dark'
        self.theme_cls.primary_palette = 'Gray'
        # Una sola instancia del GPS para toda la app
        self.gps = GpsHelper()
        if GPS_ON:
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
        Logger.info("Called start")

        # Conexion (misma logica de reconexion que la app nueva)
        self._tasks = {}
        self._device_map = {}
        self.connection = None
        self.manual_disconnect = False
        self.last_device = {'name': '', 'address': ''}
        self._prev_state = STATE_DISCONNECTED
        self._assist_ever_sent = False
        self.acc_helper = AccHelper()

        # Vuelta en curso: resumen y Excel
        self.trip = TripTracker()
        self.ride_log = RideLog(os.path.join(self.user_data_dir, 'vueltas'))
        self.last_excel = None
        self.angle_value = None
        self._last_battery = None
        self._last_manipulation = None
        self.adapt_on = False

        # Ventana Conexión BLE
        # Menu desplegante oculto mientras no se busquen y encuentren dispositivos
        self._hide_device_list()

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

        self._load_store()
        self._refresh_status()
        self._apply_live_ui()
        self._recover_unsaved_ride()
        Clock.schedule_interval(self._tick, 1.0)

        # El GPS arranca desde aqui para que ya tenga senial al conectar
        self.start_gps()

    def start_gps(self, *args) -> None:
        """Arranca el GPS (una sola vez). Si falla, no debe tumbar la conexion BLE."""
        if not GPS_ON:
            return
        try:
            self.gps.run(self.speed_queue)
        except Exception as e:
            Logger.error(f'[GPS] No se pudo iniciar: {e!r}')

    def get_permissions(self):
        # Request permissions on Android
        if platform == 'android':
            from android.permissions import Permission, request_permissions

            def callback(permissions, results):
                if all(results):
                    print('Got all permissions')
                else:
                    print('Did not get all permissions')
                # Llega en el hilo de Java: el GPS se arranca en el de Kivy
                Clock.schedule_once(self.start_gps)

            wanted = [Permission.ACCESS_COARSE_LOCATION, Permission.ACCESS_FINE_LOCATION,
                      Permission.BLUETOOTH, Permission.BLUETOOTH_ADMIN,
                      'android.permission.BLUETOOTH_SCAN', Permission.BLUETOOTH_CONNECT]
            if sdk_int() < 29:
                # Android 9 o menos: guardar el Excel en Descargas requiere este permiso
                wanted.append('android.permission.WRITE_EXTERNAL_STORAGE')
            try:
                # ACCESS_BACKGROUND_LOCATION no va aqui: en Android 11+ invalida toda la peticion
                request_permissions(wanted, callback)
            except Exception as e:
                print(e)

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
    # Conexion / desconexion (igual que en la app nueva)
    # ------------------------------------------------------------------ #
    def connect_ble(self, *_) -> None:
        """Boton 'Search Devices': escanea y deja elegir en la lista."""
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
        self.set_connection_state(STATE_CONNECTING)
        self.root.get_screen('main_window').ids.spinner.active = True

        self._start_task('ble', self._ble_main(target))
        self._start_task('acc', self.acc_helper.run(self.datajson_queue))
        self._start_task('battery', self.update_battery_value())
        self._start_task('speed', self.update_speed_value())
        self._start_task('manipulation', self.update_manipulation_value())
        self.start_gps()
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
        if connection is not None:
            await connection.close()
        self.set_connection_state(STATE_DISCONNECTED)
        self.log_task_inventory('tras desconectar')

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
    # Estado de la conexion (indicador de la barra superior)
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
        self.connection_color = list(STATE_COLORS.get(state, STATE_COLORS[STATE_DISCONNECTED]))

        # Seguridad: tras una reconexion el SP queda "sin aplicar" hasta que
        # el usuario toque el control.
        if state == STATE_CONNECTED and self._prev_state == STATE_RECONNECTING:
            self.assist_pending = self._assist_ever_sent

        keep_screen_on(state in (STATE_CONNECTED, STATE_RECONNECTING))
        self._refresh_status()
        self._update_pulse()
        self._apply_live_ui()

        if state == STATE_CONNECTED:
            # La vuelta empieza al conectar de verdad (no al escanear) y sigue
            # durante las reconexiones; termina solo al desconectar a mano.
            if not self.trip.running:
                self._start_ride()
            self.root.get_screen('main_window').ids.spinner.active = False
            if self.root.current != 'secondary_window':
                self.root.current = 'secondary_window'
        elif state == STATE_DISCONNECTED:
            self.root.get_screen('main_window').ids.spinner.active = False
            self._hide_device_list()

    def on_connection_detail(self, *_) -> None:
        self._refresh_status()

    def _refresh_status(self) -> None:
        text = STATE_TEXTS.get(self.connection_state, self.connection_state)
        if self.connection_detail:
            text = f'{text} {self.connection_detail}'
        self.connection_text = text

    def _update_pulse(self) -> None:
        """El punto del indicador late suavemente solo cuando esta 'en vivo'."""
        Animation.cancel_all(self, 'pulse_opacity')
        if self.connection_state != STATE_CONNECTED:
            self.pulse_opacity = 1.0
            return
        pulse = Animation(pulse_opacity=0.3, d=0.7) + Animation(pulse_opacity=1.0, d=0.7)
        pulse.repeat = True
        pulse.start(self)

    def _apply_live_ui(self) -> None:
        """Borra los datos del ESP32 cuando ya no estan vigentes."""
        if self.live or not hasattr(self, 'baterry_indicator'):
            return
        self.baterry_indicator.text = '—'
        self.baterry_indicator.set_value = 0
        self.sp_indicator.text = 'SP: —'
        self.manip_indicator.text = 'M: —'
        self.angle_indicator.text = 'Angle : —'

    def on_assist_pending(self, *_) -> None:
        """Marca el SP mostrado mientras no se haya vuelto a enviar."""
        label = getattr(self, 'read_slider_text', None)
        if label is None:
            return
        base = label.text.replace(PENDING_SUFFIX, '')
        label.text = base + (PENDING_SUFFIX if self.assist_pending else '')

    def update_data_age(self, age: float) -> None:
        """La llama el vigilante de BLE una vez por segundo."""
        if self.connection_state == STATE_CONNECTED:
            self.set_connection_state(STATE_CONNECTED,
                                      f'· {int(age)} s' if age >= 3 else '')

    def update_angle(self, value) -> None:
        """Inclinacion en grados que manda el ESP32."""
        try:
            self.angle_value = float(value)
        except (TypeError, ValueError):
            return
        self.angle_indicator.text = f'Angle : {self.angle_value:.0f}°'
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
        # Mismo tamanio que tenia la lista en la version original
        dropdown.size = (self.root.width - 200, 100)
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

    def _send_assist(self, payload: dict) -> None:
        """Comandos de asistencia: SOLO por accion del usuario y con conexion viva."""
        if not self.live or self.manual_disconnect:
            Logger.info(f'[BLE] Sin conexion viva, comando ignorado: {payload}')
            return
        self.datajson_queue.put_nowait(json.dumps(payload))
        self._assist_ever_sent = True
        self.assist_pending = False

    # ------------------------------------------------------------------ #
    # Vuelta: GPS, resumen al desconectarse y Excel
    # ------------------------------------------------------------------ #
    def _tick(self, _dt) -> None:
        """Latido de 1 Hz: distancia de la vuelta, ubicacion y fila del Excel."""
        fix = self.gps.last_fix
        if self.trip.running and fix:
            self.trip.add_fix(fix.get('lat'), fix.get('lon'), fix.get('speed_kmh'),
                              fix.get('accuracy'), fix.get('time'))
        self._refresh_gps_text()
        if self.ride_log.active:
            self.ride_log.add(self._snapshot())

    def _fresh_fix(self) -> dict:
        fix = self.gps.last_fix
        if fix and time.time() - fix.get('time', 0) <= GPS_STALE_S:
            return fix
        return {}

    def _refresh_gps_text(self) -> None:
        fix = self._fresh_fix()
        if fix:
            self.gps_text = (f"GPS ±{fix.get('accuracy', 0):.0f} m   "
                             f"{fix['lat']:.5f}, {fix['lon']:.5f}")
        elif self.gps.running:
            self.gps_text = 'GPS: buscando señal…'
        else:
            self.gps_text = 'GPS inactivo'

    def _snapshot(self) -> dict:
        """Una fila del Excel con lo que se sabe en este segundo."""
        fix = self._fresh_fix()
        live = self.live
        acc_y = getattr(self.acc_helper, 'y', None)
        slider = self.root.get_screen('secondary_window').ids.adapt_slider
        return {
            'fecha': datetime.now().isoformat(timespec='seconds'),
            't': round(self.trip.duration_s, 1),
            'vel': self.gps.velocity if fix else None,
            'vel_cruda': self.gps.raw_velocity if fix else None,
            'lat': fix.get('lat'),
            'lon': fix.get('lon'),
            'precision': round(fix['accuracy'], 1) if fix else None,
            'dist': round(self.trip.distance_km, 3),
            'bateria': self._last_battery if live else None,
            'angulo': self.angle_value if live else None,
            'pendiente': (round(self.trip.slope_percent, 1)
                          if live and self.angle_value is not None else None),
            'm': self._last_manipulation if live else None,
            'sp': slider.value,
            'sp_enviado': self.slider_value,
            'modo': 'Manual' if self.per_button_pressed else 'Automático',
            'deteccion': 'Sí' if self.adapt_on else 'No',
            'acc_y': round(acc_y, 3) if isinstance(acc_y, (int, float)) else None,
            'conexion': STATE_TEXTS.get(self.connection_state, self.connection_state),
        }

    def _start_ride(self) -> None:
        self.trip.start()
        self.ride_log.start()

    def _finish_ride(self, show_summary: bool = True) -> None:
        """Cierra la vuelta: guarda el Excel y muestra el resumen."""
        if not self.trip.running:
            return
        self.trip.stop()
        self.ride_log.close()
        try:
            rows = self.ride_log.load_rows()
            summary = self._summary_rows(self.trip.summary(), len(rows))
            saved = self._save_excel(rows, summary, datetime.fromtimestamp(self.trip.started_at))
            if saved:
                self.ride_log.discard()
            if show_summary:
                self.show_ride_summary(summary, saved)
        except Exception as e:
            # El diario queda en disco y se recupera al volver a abrir la app
            Logger.error(f'[XLS] Error al cerrar la vuelta: {e!r}')

    def _summary_rows(self, summary: dict, samples: int) -> list:
        def value_or_dash(value):
            return value if value is not None else '—'

        return [
            ('Fecha', datetime.fromtimestamp(summary['inicio']).strftime('%d/%m/%Y %H:%M')),
            ('Duración', format_duration(summary['duracion_s'])),
            ('Distancia (km)', round(summary['distancia_km'], 2)),
            ('Velocidad promedio (km/h)', summary['vel_prom']),
            ('Velocidad máxima (km/h)', summary['vel_max']),
            ('Batería al inicio (%)', value_or_dash(self.trip.battery_start)),
            ('Batería al final (%)', value_or_dash(self.trip.battery_last)),
            ('Batería usada (%)', value_or_dash(summary['bateria_usada'])),
            ('Muestras grabadas', samples),
        ]

    def _save_excel(self, rows: list, summary: list, started: datetime,
                    suffix: str = '') -> Optional[dict]:
        name = started.strftime('eBike_%Y-%m-%d_%H-%M-%S') + suffix + '.xlsx'
        try:
            data = build_workbook(rows, summary)
        except Exception as e:
            Logger.error(f'[XLS] No se pudo generar el Excel: {e!r}')
            return None
        saved = save_to_downloads(name, data, XLSX_MIME, EXCEL_FOLDER,
                                  fallback_dir=os.path.join(self.user_data_dir, 'excel'))
        if saved:
            self.last_excel = saved
        return saved

    def _recover_unsaved_ride(self) -> None:
        """Si la app se cerro de golpe a media vuelta, exporta lo que quedo grabado."""
        if not self.ride_log.has_unsaved():
            return
        rows = self.ride_log.load_rows()
        if rows:
            started = started_at(rows) or datetime.now()
            saved = self._save_excel(rows, recovered_summary(rows), started, '_recuperada')
            if not saved:
                return
            Logger.info(f'[XLS] Vuelta recuperada: {saved["location"]}')
        self.ride_log.discard()

    def show_ride_summary(self, summary: list, saved: Optional[dict]) -> None:
        popup = Factory.SummaryPopup()
        body = popup.ids.body
        body.clear_widgets()
        for name, value in summary:
            row = Factory.SummaryRow()
            row.ids.name.text = name
            row.ids.value.text = str(value)
            body.add_widget(row)
        popup.ids.file_info.text = (f'Excel guardado en {saved["location"]}' if saved
                                    else 'No se pudo guardar el Excel')
        popup.ids.open_button.disabled = not saved
        popup.ids.share_button.disabled = not saved
        popup.open()

    def open_last_excel(self, *_) -> None:
        if not open_saved_file(self.last_excel, XLSX_MIME):
            Logger.warning('[XLS] No se pudo abrir el Excel; prueba con Compartir')

    def share_last_excel(self, *_) -> None:
        share_saved_file(self.last_excel, XLSX_MIME)

    def switch_motion_detect(self, _, value: bool) -> None:
        """Switch state for adaptive mode switch. When in adaptive mode, the slider is disabled"""
        self.adapt_on = bool(value)
        self._send_assist({'adapt': 0 if value else 1})
        slider = self.root.get_screen('secondary_window').ids.adapt_slider
        slider.value = slider.min

    def slider_unit_km(self, touch: bool) -> None:
        if touch:
            self.km_button_pressed = True
            self.per_button_pressed = False
            self.read_slider_text.text = f'0 km/h'
            self.root.get_screen('secondary_window').ids.adapt_slider.value = 0
            self.root.get_screen('secondary_window').ids.adapt_slider.max = 40
            self.root.get_screen('secondary_window').ids.adapt_slider.step = 1
            self.root.get_screen('secondary_window').ids.adapt_slider.color = "#A7D0D2"
            self.root.get_screen('secondary_window').ids.adapt_slider.thumb_color_inactive = "#A7D0D2"
            self.root.get_screen('secondary_window').ids.adapt_slider.thumb_color_active = "#A7D0D2"

    def slider_unit_per(self, touch: bool) -> None:
        if touch:
            self.km_button_pressed = False
            self.per_button_pressed = True
            self.read_slider_text.text = f'0 %'
            self.root.get_screen('secondary_window').ids.adapt_slider.value = 0
            self.root.get_screen('secondary_window').ids.adapt_slider.max = 100
            self.root.get_screen('secondary_window').ids.adapt_slider.step = 5
            self.root.get_screen('secondary_window').ids.adapt_slider.color = "#99998F"
            self.root.get_screen('secondary_window').ids.adapt_slider.thumb_color_inactive = "#99998F"
            self.root.get_screen('secondary_window').ids.adapt_slider.thumb_color_active = "#99998F"

    def slider_on_value(self, _, value: int) -> None:
        """Sends percentage of assistance wanted to ESP32
        In automatic mode: Use BATTERY and ACCELEROMETER values to determine percentage of use"""
        
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
            self.root.get_screen('secondary_window').ids.adapt_slider.hint_text_color = "red"
            if not self.slider_flag:
                self._send_assist({self.slider_label: self.slider_value})
                self.slider_flag = True
        elif value == self.root.get_screen('secondary_window').ids.adapt_slider.min and not self.slider_flag:
            self._send_assist({self.slider_label: self.slider_value})
            self.slider_flag = True
        else:
            self.root.get_screen('secondary_window').ids.adapt_slider.hint_text_color = "white"
            self.slider_flag = False

    def slider_touch_up(self, *args) -> None:
        self._send_assist({self.slider_label: self.slider_value})

    async def update_manipulation_value(self) -> None:
        """FOR DEBUGGING PURPOSES"""
        while True:
            try:
                manip = int(float(await self.manipulation_queue.get()))
                self._last_manipulation = manip
                self.manip_indicator.text = f'M: {manip}'  # TODO: convert to percentage
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f'EXCEPTION IN MANIP: {e}')
                await asyncio.sleep(1.0)

    async def update_speed_value(self) -> None:
        """Monitors current speed of bike"""
        while True:
            try:
                speed = float(await self.speed_queue.get())
                print(f"speed-> {speed}")
                await self.datajson_queue.put(json.dumps({'speed': speed}))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f'Exception in speed:: {e}')
                await asyncio.sleep(1.0)
                continue
            if speed <= 2 * self.speedmeter_indicator.end_value / 3.6:
                self.speedmeter_indicator.set_value = speed - 25
                self.speedmeter_indicator.text = f'{int(speed)} km/h'

    async def update_battery_value(self) -> None:
        """Monitors Battery life from bike"""
        while True:
            try:
                battery_life = max(0, min(100, int(float(await self.battery_queue.get()))))
                print(f"battery-> {battery_life}")
                self._last_battery = battery_life
                self.baterry_indicator.set_value = battery_life
                self.baterry_indicator.text = f'{battery_life}%'
                self.trip.set_battery(battery_life)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f'Exception in battery:: {e}')
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
            # Primero se corta la conexion; despues se guarda el Excel y sale el resumen
            self._finish_ride()

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
        self.angle_indicator.text = 'Angle : —'
        self.slider_label = 'slider'
        self.slider_value = 0
        self.slider_flag = False
        self.assist_pending = False
        self.slider_unit_per(True)
        self.root.get_screen('secondary_window').ids.adapt_switch.active = False

    # Popup Exit
    def cancel_exit(self, touch: bool) -> None:
        if touch:  
            self.root.current = "main_window"

    def accept_exit(self, touch: bool) -> None:
        if touch:
            print("Exit app...")
            self.stop()
            os._exit(0)

    def on_stop(self):
        """Si se sale con una vuelta en curso, se guarda su Excel."""
        if getattr(self, 'trip', None) is not None and self.trip.running:
            self._finish_ride(show_summary=False)
        Animation.cancel_all(self)
        keep_screen_on(False)


if __name__ == '__main__':
    async def main_thread():
        """Creating main thread for asynchronous task definition"""
        bike_app = Main()
        await bike_app.start()

    asyncio.run(main_thread())

