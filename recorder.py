# -*- coding: utf-8 -*-
"""Grabacion del recorrido a CSV para analizar la respuesta del control."""
import csv
import os
import time
from datetime import datetime
from typing import Optional

from kivy.logger import Logger

COLUMNS = ['timestamp', 'hora', 'speed_kmh', 'battery', 'angle',
           'manipulation', 'sp', 'modo', 'acc_y', 'conexion']


class CsvRecorder:
    """Escribe una fila por segundo mientras la grabacion esta activa."""

    def __init__(self, directory: str):
        self.directory = directory
        self.path: Optional[str] = None
        self.rows = 0
        self._file = None
        self._writer = None

    @property
    def recording(self) -> bool:
        return self._file is not None

    def start(self) -> Optional[str]:
        if self.recording:
            return self.path
        try:
            os.makedirs(self.directory, exist_ok=True)
            name = datetime.now().strftime('ebike_%Y%m%d_%H%M%S.csv')
            self.path = os.path.join(self.directory, name)
            self._file = open(self.path, 'w', newline='', encoding='utf-8')
            self._writer = csv.DictWriter(self._file, fieldnames=COLUMNS)
            self._writer.writeheader()
            self.rows = 0
            Logger.info(f'[BLE] Grabando en {self.path}')
            return self.path
        except Exception as e:
            Logger.error(f'[BLE] No se pudo iniciar la grabacion: {e!r}')
            self._file = None
            self._writer = None
            return None

    def write(self, values: dict) -> None:
        if not self.recording:
            return
        row = {column: values.get(column, '') for column in COLUMNS}
        row['timestamp'] = round(time.time(), 2)
        row['hora'] = datetime.now().strftime('%H:%M:%S')
        try:
            self._writer.writerow(row)
            self.rows += 1
            if self.rows % 10 == 0:
                self._file.flush()
        except Exception as e:
            Logger.warning(f'[BLE] Error al escribir el CSV: {e!r}')

    def stop(self) -> Optional[str]:
        path = self.path
        if self._file is not None:
            try:
                self._file.flush()
                self._file.close()
                Logger.info(f'[BLE] Grabacion terminada: {path} ({self.rows} filas)')
            except Exception as e:
                Logger.warning(f'[BLE] Error al cerrar el CSV: {e!r}')
        self._file = None
        self._writer = None
        return path

    def list_files(self) -> list:
        """CSV guardados, del mas reciente al mas viejo."""
        try:
            names = [n for n in os.listdir(self.directory) if n.endswith('.csv')]
        except OSError:
            return []
        paths = [os.path.join(self.directory, n) for n in names]
        return sorted(paths, key=os.path.getmtime, reverse=True)
