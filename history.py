# -*- coding: utf-8 -*-
"""Historial de recorridos guardado en disco (JsonStore)."""
from datetime import datetime

from kivy.logger import Logger
from kivy.storage.jsonstore import JsonStore

MAX_TRIPS = 50
MIN_DISTANCE_KM = 0.05   # recorridos mas cortos no se guardan


class HistoryStore:
    """Lista de resumenes de recorrido, del mas reciente al mas viejo."""

    def __init__(self, path: str):
        self.store = JsonStore(path)

    def should_save(self, summary: dict) -> bool:
        return summary.get('distancia_km', 0) >= MIN_DISTANCE_KM

    def add(self, summary: dict):
        key = f"trip_{int(summary.get('inicio', 0))}"
        try:
            self.store.put(key, **summary)
            Logger.info(f'[BLE] Recorrido guardado: {key} {summary}')
            self._prune()
            return key
        except Exception as e:
            Logger.warning(f'[BLE] No se pudo guardar el recorrido: {e!r}')
            return None

    def list(self) -> list:
        """[(clave, datos)] ordenado por fecha descendente."""
        try:
            items = [(key, self.store.get(key)) for key in self.store.keys()]
        except Exception as e:
            Logger.warning(f'[BLE] No se pudo leer el historial: {e!r}')
            return []
        return sorted(items, key=lambda item: item[1].get('inicio', 0), reverse=True)

    def delete(self, key: str) -> None:
        try:
            if self.store.exists(key):
                self.store.delete(key)
                Logger.info(f'[BLE] Recorrido borrado: {key}')
        except Exception as e:
            Logger.warning(f'[BLE] No se pudo borrar el recorrido: {e!r}')

    def clear(self) -> None:
        for key, _ in self.list():
            self.delete(key)

    def _prune(self) -> None:
        extra = self.list()[MAX_TRIPS:]
        for key, _ in extra:
            self.delete(key)


def format_date(timestamp: float) -> str:
    try:
        return datetime.fromtimestamp(timestamp).strftime('%d/%m/%Y %H:%M')
    except (TypeError, ValueError, OSError):
        return '—'
