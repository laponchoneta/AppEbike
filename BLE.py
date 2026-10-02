# -*- coding: utf-8 -*-
"""Capa BLE de eBIKE: una sola conexion, con reconexion automatica.

Protocolo (NO se modifica): una sola caracteristica de lectura/escritura
00002A3D-0000-1000-8000-00805f9b34fb que transporta JSON en texto.
    App   -> ESP32: {"speed":x} {"acc_y":x} {"adapt":0|1} {"slider_per":n} {"slider_km":n}
    ESP32 -> App  : {"battery":%} {"angle":grados} {"manipulation":n}
"""
import asyncio
import json
import time
from typing import Any, Optional

from bleak import BleakClient, BleakScanner
from kivy.logger import Logger

from android_utils import is_bluetooth_enabled

# --- Constantes de reconexion (Tarea 1) ---
RECONNECT_INTERVAL = 5.0   # segundos de espera entre intentos
CONNECT_TIMEOUT = 10.0     # timeout de cada intento de conexion
GATT_TIMEOUT = 5.0         # timeout de cada lectura/escritura GATT
DATA_TIMEOUT = 5.0         # watchdog: segundos sin datos del ESP32 = caida
SCAN_TIMEOUT = 8.0         # duracion del escaneo de dispositivos
POLL_INTERVAL = 0.2        # periodo del polling de lectura

# --- Estados de la maquina de conexion ---
STATE_DISCONNECTED = 'disconnected'
STATE_CONNECTING = 'connecting'
STATE_CONNECTED = 'connected'
STATE_RECONNECTING = 'reconnecting'


class Connection:
    """Duenio unico del ciclo de vida de la conexion BLE.

    manager() es una tarea larga que nunca termina por una excepcion de BLE:
    conecta, vigila la conexion y reintenta cada RECONNECT_INTERVAL segundos
    hasta que el usuario desconecte a mano.
    """

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
        self.attempt = 0            # intentos consecutivos fallidos
        self.total_attempts = 0     # contador acumulado, solo para diagnostico
        self.packets = 0            # JSON validos recibidos, solo para diagnostico
        self.last_data_ts = 0.0

        self._drop_event = asyncio.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ------------------------------------------------------------------ #
    # Estado
    # ------------------------------------------------------------------ #
    @property
    def manual_disconnect(self) -> bool:
        """Bandera de desconexion manual; vive en la App (fuente unica)."""
        return bool(getattr(self.app, 'manual_disconnect', False))

    def _set_state(self, state: str, detail: str = '') -> None:
        self.app.set_connection_state(state, detail)

    def mark_data(self) -> None:
        """Marca que acaba de llegar algo del ESP32 (alimenta el watchdog)."""
        self.last_data_ts = time.monotonic()

    def notify_failure(self, reason: str) -> None:
        """Lo llama communication_manager cuando una lectura/escritura falla."""
        if not self.connected:
            return
        Logger.warning(f'[BLE] Fallo de E/S: {reason}')
        self.connected = False
        self._signal_drop()

    def _signal_drop(self) -> None:
        """Despierta al vigilante. Puede llamarse desde otro hilo (bleak)."""
        loop = self._loop
        try:
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(self._drop_event.set)
            else:
                self._drop_event.set()
        except RuntimeError:
            self._drop_event.set()

    def _on_disconnect(self, _client: Any) -> None:
        """disconnected_callback de bleak: aqui si nos enteramos de las caidas."""
        Logger.warning('[BLE] disconnected_callback: el dispositivo se desconecto')
        self.connected = False
        self._signal_drop()

    # ------------------------------------------------------------------ #
    # Ciclo de vida
    # ------------------------------------------------------------------ #
    async def manager(self) -> None:
        """Unica tarea que mantiene viva la conexion. Se cancela al desconectar."""
        self._loop = asyncio.get_event_loop()
        Logger.info('[BLE] Gestor de conexion iniciado')
        reconnecting = False
        try:
            while not self.manual_disconnect:
                # 1. Si todavia no sabemos a quien conectarnos, escaneamos.
                if not self.target_address:
                    self._set_state(STATE_CONNECTING)
                    if not await self.select_device():
                        await asyncio.sleep(2.0)
                    continue

                # 2. Sin Bluetooth no tiene caso intentar, pero seguimos vivos.
                if not is_bluetooth_enabled():
                    Logger.warning('[BLE] El Bluetooth del celular esta apagado')
                    self._set_state(STATE_RECONNECTING if reconnecting else STATE_CONNECTING,
                                    'Bluetooth apagado')
                    await asyncio.sleep(RECONNECT_INTERVAL)
                    continue

                # 3. Un intento a la vez, nunca dos en paralelo.
                self.attempt += 1
                self.total_attempts += 1
                if reconnecting:
                    self._set_state(STATE_RECONNECTING, f'({self.attempt})')
                Logger.info(f'[BLE] Intento {self.attempt} -> '
                            f'{self.target_name} ({self.target_address})')

                connected = await self._connect_once()
                if connected:
                    self.attempt = 0
                    reconnecting = False
                    self.app.remember_device(self.target_name, self.target_address)
                    self._set_state(STATE_CONNECTED)
                    await self._watch_connection()   # bloquea hasta que se caiga

                await self._close_client()
                if self.manual_disconnect:
                    break

                reconnecting = True
                if connected:
                    # Se cayo estando conectado: se reintenta de inmediato.
                    Logger.warning('[BLE] Conexion perdida, se reintentara')
                    self._set_state(STATE_RECONNECTING)
                else:
                    self._set_state(STATE_RECONNECTING, f'({self.attempt})')
                    await asyncio.sleep(RECONNECT_INTERVAL)
        except asyncio.CancelledError:
            Logger.info('[BLE] Gestor de conexion cancelado')
            raise
        except Exception as e:
            Logger.error(f'[BLE] Error inesperado en el gestor: {e!r}')
            raise
        finally:
            Logger.info('[BLE] Gestor de conexion terminado')

    async def _connect_once(self) -> bool:
        """Un intento de conexion. Nunca lanza: devuelve True/False."""
        self._drop_event.clear()
        client = BleakClient(self.target_address,
                             timeout=CONNECT_TIMEOUT,
                             disconnected_callback=self._on_disconnect)
        self.client = client
        try:
            await asyncio.wait_for(client.connect(),
                                   timeout=CONNECT_TIMEOUT + 5.0)
            self.connected = bool(client.is_connected)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            Logger.warning(f'[BLE] Intento {self.attempt} fallido: {e!r}')
            self.connected = False
            return False

        if not self.connected:
            Logger.warning(f'[BLE] Intento {self.attempt}: el cliente no quedo conectado')
            return False

        self.mark_data()
        # start_notify es opcional: si el ESP32 no notifica seguimos con polling.
        try:
            await client.start_notify(self.read_char, self.notification_handler)
            Logger.info('[BLE] Notificaciones activadas')
        except Exception as e:
            Logger.warning(f'[BLE] start_notify no disponible: {e!r}')
        Logger.info(f'[BLE] Conectado a {self.target_name} ({self.target_address})')
        return True

    async def _watch_connection(self) -> None:
        """Vigila la conexion: callback de bleak + watchdog de datos."""
        while not self.manual_disconnect:
            try:
                await asyncio.wait_for(self._drop_event.wait(), timeout=1.0)
                Logger.warning('[BLE] Caida detectada (callback o error de E/S)')
                return
            except asyncio.TimeoutError:
                pass

            try:
                still_up = self.client is not None and self.client.is_connected
            except Exception:
                still_up = False
            if not still_up:
                Logger.warning('[BLE] Caida detectada (is_connected = False)')
                return

            age = time.monotonic() - self.last_data_ts
            self.app.update_data_age(age)
            if age > DATA_TIMEOUT:
                Logger.warning(f'[BLE] Watchdog: {age:.1f} s sin datos del ESP32')
                return

    async def _close_client(self) -> None:
        """Cierra el cliente actual sin propagar errores."""
        client, self.client = self.client, None
        self.connected = False
        if client is None:
            return
        try:
            await asyncio.wait_for(client.disconnect(), timeout=GATT_TIMEOUT)
            Logger.info('[BLE] Cliente cerrado')
        except asyncio.CancelledError:
            raise
        except Exception as e:
            Logger.warning(f'[BLE] Error al cerrar el cliente: {e!r}')

    async def close(self) -> None:
        """Cierre ordenado desde la App (desconexion manual)."""
        await self._close_client()

    # ------------------------------------------------------------------ #
    # Seleccion de dispositivo
    # ------------------------------------------------------------------ #
    async def select_device(self) -> bool:
        """Escanea y espera a que el usuario elija un dispositivo en la lista."""
        if not is_bluetooth_enabled():
            Logger.warning('[BLE] No se puede escanear: Bluetooth apagado')
            self._set_state(STATE_CONNECTING, 'Bluetooth apagado')
            await asyncio.sleep(RECONNECT_INTERVAL)
            return False

        Logger.info('[BLE] Escaneando dispositivos...')
        self.app.show_scanning(True)
        try:
            devices = await asyncio.wait_for(
                BleakScanner.discover(timeout=SCAN_TIMEOUT),
                timeout=SCAN_TIMEOUT + 10.0)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            Logger.warning(f'[BLE] Error al escanear: {e!r}')
            devices = []

        found = {d.name: d.address for d in devices if d.name}
        if not found:
            Logger.warning('[BLE] No se encontraron dispositivos')
            self.app.show_scanning(False)
            return False

        for name, address in found.items():
            Logger.info(f'[BLE] Encontrado: {name} ({address})')
        self.app.fill_device_list(found)

        name = await self.device_queue.get()
        address = found.get(name)
        if not address:
            Logger.warning(f'[BLE] Seleccion invalida: {name}')
            return False

        self.target_name, self.target_address = name, address
        self.attempt = 0
        Logger.info(f'[BLE] Dispositivo elegido: {name} ({address})')
        self.app.show_scanning(True)
        return True

    # ------------------------------------------------------------------ #
    # Recepcion de datos
    # ------------------------------------------------------------------ #
    def notification_handler(self, _sender: Any, data: Any) -> None:
        """Notificaciones del ESP32: alimentan la UI igual que el polling."""
        self.mark_data()
        try:
            self.dispatch_payload(bytes(data).decode('utf-8', errors='ignore'))
        except Exception as e:
            Logger.debug(f'[BLE] Notificacion ilegible: {e!r}')

    def dispatch_payload(self, raw: str) -> bool:
        """Parsea el JSON del ESP32 y reparte los valores. Nunca lanza."""
        raw = (raw or '').strip()
        if not raw:
            return False
        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            Logger.debug(f'[BLE] JSON incompleto, se ignora: {raw!r}')
            return False
        if not isinstance(msg, dict):
            return False

        self.mark_data()
        self.packets += 1
        if 'battery' in msg:
            self.battery_queue.put_nowait(msg['battery'])
        if 'manipulation' in msg:
            self.manipulation_queue.put_nowait(msg['manipulation'])
        if 'angle' in msg:
            self.app.update_angle(msg['angle'])
        return True


def _collapse(items: list) -> list:
    """Deja solo el ultimo valor de cada clave JSON.

    Evita inundar al ESP32 con decenas de {"speed":...} acumulados mientras
    la conexion estuvo caida.
    """
    latest = {}
    for raw in items:
        try:
            parsed = json.loads(raw)
            key = next(iter(parsed)) if isinstance(parsed, dict) and parsed else raw
        except (json.JSONDecodeError, ValueError, StopIteration):
            key = raw
        latest[key] = raw
    return list(latest.values())


async def _send_pending(connection: Connection, datajson_queue: asyncio.Queue) -> None:
    """Vacia la cola de salida hacia el ESP32."""
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
                client.write_gatt_char(connection.write_char,
                                       bytearray(item.encode('utf-8')),
                                       response=True),
                timeout=GATT_TIMEOUT)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            connection.notify_failure(f'write_gatt_char: {e!r}')
            return
        Logger.debug(f'[BLE] Enviado: {item}')
        await asyncio.sleep(0.1)


async def _read_once(connection: Connection) -> None:
    """Lee la caracteristica del ESP32 (polling) y reparte el JSON."""
    client = connection.client
    if client is None:
        return
    try:
        raw = await asyncio.wait_for(
            client.read_gatt_char(connection.read_char),
            timeout=GATT_TIMEOUT)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        connection.notify_failure(f'read_gatt_char: {e!r}')
        return

    # Aunque el JSON venga incompleto, el ESP32 respondio: el enlace esta vivo.
    connection.mark_data()
    try:
        text = bytes(raw).decode('utf-8', errors='ignore')
    except Exception as e:
        Logger.debug(f'[BLE] Respuesta ilegible: {e!r}')
        return
    Logger.debug(f'[BLE] Recibido: {text}')
    connection.dispatch_payload(text)


async def communication_manager(connection: Connection,
                                datajson_queue: asyncio.Queue) -> None:
    """Unico lazo de envio/recepcion. Disenado para NO morir nunca por un error.

    Solo se detiene cuando la App cancela la tarea (desconexion manual).
    """
    Logger.info('[BLE] communication_manager iniciado')
    try:
        while True:
            try:
                if not (connection.client and connection.connected):
                    await asyncio.sleep(0.5)
                    continue
                await _send_pending(connection, datajson_queue)
                await _read_once(connection)
                await asyncio.sleep(POLL_INTERVAL)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # Red de seguridad: cualquier error se trata como caida.
                Logger.warning(f'[BLE] Error no esperado en comunicacion: {e!r}')
                connection.notify_failure(repr(e))
                await asyncio.sleep(1.0)
    except asyncio.CancelledError:
        Logger.info('[BLE] communication_manager cancelado')
        raise