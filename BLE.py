# -*- coding: utf-8 -*-
"""Capa BLE de eBIKE: una sola conexion, con reconexion automatica."""
import asyncio
import json
import time
from typing import Any, Optional

from bleak import BleakClient, BleakScanner
from kivy.logger import Logger
from kivy.clock import Clock

from android_utils import is_bluetooth_enabled

# Parametros de reconexion BLE
RECONNECT_INTERVAL = 5.0
CONNECT_TIMEOUT = 10.0
GATT_TIMEOUT = 5.0
DATA_TIMEOUT = 5.0
SCAN_TIMEOUT = 8.0
POLL_INTERVAL = 0.2

STATE_DISCONNECTED = 'disconnected'
STATE_CONNECTING = 'connecting'
STATE_CONNECTED = 'connected'
STATE_RECONNECTING = 'reconnecting'


class Connection:
    def __init__(self,
                 app,
                 read_char: str,
                 write_char: str,
                 device_queue: asyncio.Queue,
                 battery_queue: asyncio.Queue,
                 manipulation_queue: asyncio.Queue,
                 target: Optional[dict] = None):
        self.app = app
        self.read_char = read_char
        self.write_char = write_char
        self.device_queue = device_queue
        self.battery_queue = battery_queue
        self.manipulation_queue = manipulation_queue

        target = target or {}
        self.target_name = target.get('name') or ''
        self.target_address = target.get('address') or ''

        self.client: Optional[BleakClient] = None
        self.connected = False
        self.attempt = 0
        self.total_attempts = 0
        self.packets = 0
        self.last_data_ts = 0.0

        self._drop_event = asyncio.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    @property
    def manual_disconnect(self) -> bool:
        return bool(getattr(self.app, 'manual_disconnect', False))

    def _set_state(self, state: str, detail: str = '') -> None:
        # Proteger UI: enviar al hilo principal de Kivy
        Clock.schedule_once(lambda dt: self.app.set_connection_state(state, detail))

    def mark_data(self) -> None:
        self.last_data_ts = time.monotonic()

    def notify_failure(self, reason: str) -> None:
        if not self.connected:
            return
        Logger.warning(f'[BLE] Fallo de E/S: {reason}')
        self.connected = False
        self._signal_drop()

    def _signal_drop(self) -> None:
        loop = self._loop
        try:
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(self._drop_event.set)
            else:
                self._drop_event.set()
        except RuntimeError:
            self._drop_event.set()

    def _on_disconnect(self, _client: Any) -> None:
        Logger.warning('[BLE] disconnected_callback: el dispositivo se desconecto')
        self.connected = False
        self._signal_drop()

    def _attach_jni(self) -> None:
        """Evita un Segmentation Fault forzando a que el hilo se reconozca en JNI."""
        try:
            from jnius import autoclass
            autoclass('java.lang.System')
        except Exception:
            pass

    async def manager(self) -> None:
        self._loop = asyncio.get_event_loop()
        Logger.info('[BLE] Gestor de conexion iniciado')
        reconnecting = False
        try:
            while not self.manual_disconnect:
                if not self.target_address:
                    self._set_state(STATE_CONNECTING)
                    if not await self.select_device():
                        await asyncio.sleep(2.0)
                    continue

                self._attach_jni()
                if not is_bluetooth_enabled():
                    Logger.warning('[BLE] El Bluetooth del celular esta apagado')
                    self._set_state(STATE_RECONNECTING if reconnecting else STATE_CONNECTING,
                                    'Bluetooth apagado')
                    await asyncio.sleep(RECONNECT_INTERVAL)
                    continue

                self.attempt += 1
                self.total_attempts += 1
                if reconnecting:
                    self._set_state(STATE_RECONNECTING, f'({self.attempt})')
                
                connected = await self._connect_once()
                if connected:
                    self.attempt = 0
                    reconnecting = False
                    Clock.schedule_once(lambda dt: self.app.remember_device(self.target_name, self.target_address))
                    self._set_state(STATE_CONNECTED)
                    await self._watch_connection()

                await self._close_client()
                if self.manual_disconnect:
                    break

                reconnecting = True
                if connected:
                    Logger.warning('[BLE] Conexion perdida, se reintentara')
                    self._set_state(STATE_RECONNECTING)
                else:
                    self._set_state(STATE_RECONNECTING, f'({self.attempt})')
                    await asyncio.sleep(RECONNECT_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            Logger.error(f'[BLE] Error inesperado en el gestor: {e!r}')
            raise

    async def _connect_once(self) -> bool:
        self._drop_event.clear()
        client = BleakClient(self.target_address, timeout=CONNECT_TIMEOUT, disconnected_callback=self._on_disconnect)
        self.client = client
        try:
            await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT + 5.0)
            self.connected = bool(client.is_connected)
        except Exception as e:
            Logger.warning(f'[BLE] Intento {self.attempt} fallido: {e!r}')
            self.connected = False
            return False

        if not self.connected:
            return False

        self.mark_data()
        try:
            await client.start_notify(self.read_char, self.notification_handler)
        except Exception as e:
            Logger.warning(f'[BLE] start_notify no disponible: {e!r}')
        return True

    async def _watch_connection(self) -> None:
        while not self.manual_disconnect:
            try:
                await asyncio.wait_for(self._drop_event.wait(), timeout=1.0)
                return
            except asyncio.TimeoutError:
                pass

            try:
                still_up = self.client is not None and self.client.is_connected
            except Exception:
                still_up = False
            if not still_up:
                return

            age = time.monotonic() - self.last_data_ts
            Clock.schedule_once(lambda dt, a=age: self.app.update_data_age(a))
            if age > DATA_TIMEOUT:
                return

    async def _close_client(self) -> None:
        client, self.client = self.client, None
        self.connected = False
        if client is None:
            return
        try:
            await asyncio.wait_for(client.disconnect(), timeout=GATT_TIMEOUT)
        except Exception:
            pass

    async def close(self) -> None:
        await self._close_client()

    async def select_device(self) -> bool:
        self._attach_jni()
        if not is_bluetooth_enabled():
            self._set_state(STATE_CONNECTING, 'Bluetooth apagado')
            await asyncio.sleep(RECONNECT_INTERVAL)
            return False

        Clock.schedule_once(lambda dt: self.app.show_scanning(True))
        try:
            devices = await asyncio.wait_for(BleakScanner.discover(timeout=SCAN_TIMEOUT), timeout=SCAN_TIMEOUT + 10.0)
        except Exception:
            devices = []

        found = {d.name: d.address for d in devices if d.name}
        if not found:
            Clock.schedule_once(lambda dt: self.app.show_scanning(False))
            return False

        Clock.schedule_once(lambda dt: self.app.fill_device_list(found))
        name = await self.device_queue.get()
        address = found.get(name)
        if not address:
            return False

        self.target_name, self.target_address = name, address
        self.attempt = 0
        Clock.schedule_once(lambda dt: self.app.show_scanning(True))
        return True

    def notification_handler(self, _sender: Any, data: Any) -> None:
        """¡CRÍTICO! Este handler es llamado por Bleak desde un hilo secundario de Java.
        Cualquier manipulación de UI o asyncio.Queue aquí crashea la app. 
        Debemos derivarlo al hilo principal."""
        try:
            raw = bytes(data).decode('utf-8', errors='ignore')
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._safe_dispatch, raw)
        except Exception:
            pass

    def _safe_dispatch(self, raw: str) -> None:
        """Se ejecuta ya de forma segura en el hilo principal de la app."""
        self.mark_data()
        self.dispatch_payload(raw)

    def dispatch_payload(self, raw: str) -> bool:
        raw = (raw or '').strip()
        if not raw:
            return False
        try:
            msg = json.loads(raw)
        except ValueError:
            return False
        if not isinstance(msg, dict):
            return False

        self.packets += 1
        if 'battery' in msg:
            self.battery_queue.put_nowait(msg['battery'])
        if 'manipulation' in msg:
            self.manipulation_queue.put_nowait(msg['manipulation'])
        if 'angle' in msg:
            Clock.schedule_once(lambda dt, a=msg['angle']: self.app.update_angle(a))
        return True


def _collapse(items: list) -> list:
    latest = {}
    for raw in items:
        try:
            parsed = json.loads(raw)
            key = next(iter(parsed)) if isinstance(parsed, dict) and parsed else raw
        except ValueError:
            key = raw
        latest[key] = raw
    return list(latest.values())


async def _send_pending(connection: Connection, datajson_queue: asyncio.Queue) -> None:
    pending = []
    while True:
        try:
            pending.append(str(datajson_queue.get_nowait()))
        except asyncio.QueueEmpty:
            break
    if not pending:
        return

    for item in _collapse(pending):
        client = connection.client
        if client is None:
            return
        try:
            await asyncio.wait_for(
                client.write_gatt_char(connection.write_char, bytearray(item.encode('utf-8')), response=True),
                timeout=GATT_TIMEOUT)
        except Exception as e:
            connection.notify_failure(f'write_gatt_char: {e!r}')
            return
        await asyncio.sleep(0.1)


async def _read_once(connection: Connection) -> None:
    client = connection.client
    if client is None:
        return
    try:
        raw = await asyncio.wait_for(client.read_gatt_char(connection.read_char), timeout=GATT_TIMEOUT)
    except Exception as e:
        connection.notify_failure(f'read_gatt_char: {e!r}')
        return

    connection.mark_data()
    try:
        text = bytes(raw).decode('utf-8', errors='ignore')
        connection.dispatch_payload(text)
    except Exception:
        pass


async def communication_manager(connection: Connection, datajson_queue: asyncio.Queue) -> None:
    try:
        while True:
            if not (connection.client and connection.connected):
                await asyncio.sleep(0.5)
                continue
            await _send_pending(connection, datajson_queue)
            await _read_once(connection)
            await asyncio.sleep(POLL_INTERVAL)
    except asyncio.CancelledError:
        raise