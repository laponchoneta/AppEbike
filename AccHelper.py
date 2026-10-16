# -*- coding: utf-8 -*-
from kivy.logger import Logger
from kivy.utils import platform
import asyncio
import json


class AccHelper:
    """Lee el acelerometro del celular y publica acc_y para el ESP32.

    run() es una corrutina: main.py la registra como tarea unica y la cancela
    al desconectar, asi no quedan lecturas huerfanas.
    """
    sensor_enabled: bool = False
    x = 0
    y = 0
    z = 0

    async def run(self, acc_q: asyncio.Queue, dt: float = 1.0) -> None:
        if platform not in ('android', 'ios'):
            return
        from plyer import accelerometer

        self.acc_q = acc_q
        try:
            accelerometer.enable()
        except NotImplementedError:
            Logger.warning('[BLE] El acelerometro no esta disponible en este dispositivo')
            return
        except Exception as e:
            Logger.warning(f'[BLE] No se pudo activar el acelerometro: {e!r}')
            return

        self.sensor_enabled = True
        Logger.info('[BLE] Acelerometro activado')
        try:
            while True:
                try:
                    val = accelerometer.acceleration[:3]
                    if val != (None, None, None):
                        self.x, self.y, self.z = val
                        self.acc_q.put_nowait(json.dumps({'acc_y': self.y}))
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    Logger.warning(f'[BLE] Error leyendo el acelerometro: {e!r}')
                await asyncio.sleep(dt)
        except asyncio.CancelledError:
            raise
        finally:
            self.sensor_enabled = False
            try:
                accelerometer.disable()
                Logger.info('[BLE] Acelerometro apagado')
            except Exception as e:
                Logger.warning(f'[BLE] No se pudo apagar el acelerometro: {e!r}')
