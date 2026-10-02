# -*- coding: utf-8 -*-
"""Grafica en vivo de los ultimos 60 s, dibujada con el canvas de Kivy."""
from collections import deque

from kivy.graphics import Color, Line, Rectangle
from kivy.properties import ListProperty, NumericProperty
from kivy.uix.widget import Widget

import theme


class LiveChart(Widget):
    """Tres series normalizadas de 0 a 1: velocidad, set point y manipulacion."""

    window_s = NumericProperty(60)
    speed_color = ListProperty(theme.rgba(theme.UDEM_YELLOW))
    sp_color = ListProperty(theme.rgba(theme.OK))
    manip_color = ListProperty(theme.rgba(theme.TEXT_DIM))
    grid_color = ListProperty(theme.rgba(theme.TRACK))
    bg_color = ListProperty(theme.rgba(theme.SURFACE))
    line_width = NumericProperty(1.4)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        size = int(self.window_s)
        self._series = {'speed': deque(maxlen=size),
                        'sp': deque(maxlen=size),
                        'manip': deque(maxlen=size)}
        self.bind(pos=self._redraw, size=self._redraw)
        self._redraw()

    def push(self, speed, sp, manip) -> None:
        """Agrega una muestra. Los tres valores vienen normalizados de 0 a 1."""
        self._series['speed'].append(self._clamp(speed))
        self._series['sp'].append(self._clamp(sp))
        self._series['manip'].append(self._clamp(manip))
        self._redraw()

    def clear(self) -> None:
        for serie in self._series.values():
            serie.clear()
        self._redraw()

    @property
    def samples(self) -> int:
        return len(self._series['speed'])

    @staticmethod
    def _clamp(value) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    def _points(self, serie) -> list:
        count = len(serie)
        if count < 2:
            return []
        span = max(1, int(self.window_s) - 1)
        step = self.width / span
        start = self.right - (count - 1) * step   # la muestra mas nueva va a la derecha
        points = []
        for i, value in enumerate(serie):
            points.extend((start + i * step, self.y + value * self.height))
        return points

    def _redraw(self, *_) -> None:
        self.canvas.clear()
        with self.canvas:
            Color(*self.bg_color)
            Rectangle(pos=self.pos, size=self.size)
            Color(*self.grid_color)
            for fraction in (0.25, 0.5, 0.75):
                y = self.y + self.height * fraction
                Line(points=[self.x, y, self.right, y], width=1)
            for name, color in (('manip', self.manip_color),
                                ('sp', self.sp_color),
                                ('speed', self.speed_color)):
                points = self._points(self._series[name])
                if points:
                    Color(*color)
                    Line(points=points, width=self.line_width)
