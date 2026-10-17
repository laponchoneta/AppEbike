# -*- coding: utf-8 -*-
"""Registro de cada vuelta: diario en disco y exportacion a Excel.

Cada segundo se agrega una linea JSON al diario y se hace flush, asi que si la
app se cierra de golpe no se pierde lo grabado; al terminar se convierte a .xlsx.
"""
import json
import os
from datetime import datetime
from typing import Optional

from kivy.logger import Logger

from excel import build_xlsx

XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

# (llave en el diario, encabezado en Excel, ancho de columna)
COLUMNS = [
    ('fecha', 'Fecha y hora', 20),
    ('t', 'Tiempo (s)', 11),
    ('vel', 'Velocidad (km/h)', 16),
    ('vel_cruda', 'Velocidad GPS sin filtrar (km/h)', 29),
    ('lat', 'Latitud', 12),
    ('lon', 'Longitud', 12),
    ('precision', 'Precisión GPS (m)', 17),
    ('dist', 'Distancia (km)', 14),
    ('bateria', 'Batería (%)', 12),
    ('angulo', 'Ángulo (°)', 11),
    ('pendiente', 'Pendiente (%)', 14),
    ('m', 'M', 8),
    ('sp', 'SP en pantalla', 14),
    ('sp_enviado', 'SP enviado al ESP32', 19),
    ('modo', 'Modo', 12),
    ('deteccion', 'Detección de movimiento', 23),
    ('acc_y', 'acc_y', 10),
    ('conexion', 'Conexión', 15),
]


class RideLog:
    """Diario de la vuelta en curso (JSON Lines)."""

    def __init__(self, folder: str):
        self.folder = folder
        self.path = os.path.join(folder, 'vuelta_en_curso.jsonl')
        self.rows = 0
        self._file = None

    @property
    def active(self) -> bool:
        return self._file is not None

    def start(self) -> None:
        self.close()
        try:
            os.makedirs(self.folder, exist_ok=True)
            self._file = open(self.path, 'w', encoding='utf-8')
            self.rows = 0
            Logger.info(f'[XLS] Grabando la vuelta en {self.path}')
        except Exception as e:
            Logger.error(f'[XLS] No se pudo iniciar la grabacion: {e!r}')
            self._file = None

    def add(self, row: dict) -> None:
        if self._file is None:
            return
        try:
            self._file.write(json.dumps(row, ensure_ascii=False) + '\n')
            self._file.flush()
            self.rows += 1
        except Exception as e:
            Logger.warning(f'[XLS] Error al escribir el diario: {e!r}')

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except Exception as e:
                Logger.warning(f'[XLS] Error al cerrar el diario: {e!r}')
        self._file = None

    def has_unsaved(self) -> bool:
        """Quedo un diario de una vuelta que no se exporto (la app se cerro de golpe)."""
        return not self.active and os.path.exists(self.path) and os.path.getsize(self.path) > 0

    def load_rows(self) -> list:
        rows = []
        try:
            with open(self.path, encoding='utf-8') as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        pass   # ultima linea a medias si la app se cerro de golpe
        except OSError as e:
            Logger.warning(f'[XLS] No se pudo leer el diario: {e!r}')
        return rows

    def discard(self) -> None:
        try:
            os.remove(self.path)
        except OSError:
            pass


def _parse_date(value):
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return value
    return value


def build_workbook(rows: list, summary: list) -> bytes:
    """Excel con dos hojas: 'Datos' (una fila por segundo) y 'Resumen'."""
    data = [[title for _key, title, _width in COLUMNS]]
    for row in rows:
        data.append([_parse_date(row.get(key)) if key == 'fecha' else row.get(key)
                     for key, _title, _width in COLUMNS])
    widths = [width for _key, _title, width in COLUMNS]
    resumen = [['Dato', 'Valor']] + [[name, value] for name, value in summary]
    return build_xlsx([('Datos', data, widths), ('Resumen', resumen, [28, 30])])


def recovered_summary(rows: list) -> list:
    """Resumen aproximado de una vuelta recuperada del diario."""
    speeds = [r['vel'] for r in rows if isinstance(r.get('vel'), (int, float))]
    last = rows[-1] if rows else {}
    first = rows[0] if rows else {}
    return [
        ('Nota', 'Vuelta recuperada: la app se cerró antes de desconectar'),
        ('Inicio', first.get('fecha', '—')),
        ('Duración (s)', last.get('t', '—')),
        ('Distancia (km)', last.get('dist', '—')),
        ('Velocidad máxima (km/h)', max(speeds) if speeds else '—'),
        ('Muestras', len(rows)),
    ]


def started_at(rows: list) -> Optional[datetime]:
    if rows:
        value = _parse_date(rows[0].get('fecha'))
        if isinstance(value, datetime):
            return value
    return None
