"""Reusable live CPU and memory monitor for NiceGUI widgets."""

from __future__ import annotations

import os
import time
from collections import deque
from typing import Deque, List

import plotly.graph_objects as go
import psutil
from nicegui import ui


class ResourceMonitor:
    """Render live process/system CPU and memory usage with a rolling chart."""

    def __init__(self, *, sample_interval_s: float = 2.0, history_points: int = 30):
        self.sample_interval_s = sample_interval_s
        self.history_points = history_points

        self._process = psutil.Process(os.getpid())
        self._process.cpu_percent(interval=None)  # Prime for accurate next reading.

        self._start_time = time.monotonic()
        self._elapsed_seconds: Deque[int] = deque(maxlen=history_points)
        self._process_cpu: Deque[float] = deque(maxlen=history_points)
        self._system_cpu: Deque[float] = deque(maxlen=history_points)
        self._process_mem_mb: Deque[float] = deque(maxlen=history_points)

        self._summary_label = None
        self._details_label = None
        self._plot = None
        self._timer = None

        self._fig = go.Figure()

    def create(self):
        """Create monitor UI elements and return the container element."""
        with ui.column().classes("w-full gap-2") as container:
            self._summary_label = ui.label("CPU: -- | RAM: --").classes("text-xs font-medium text-gray-700")
            self._details_label = ui.label("Process and system metrics will appear after first sample.").classes("text-[11px] text-gray-500")

            self._fig = self._build_figure()
            self._plot = ui.plotly(self._fig).classes("w-full rounded")

        self._timer = ui.timer(self.sample_interval_s, self._sample_and_update, active=False)
        return container

    def start(self):
        """Start live sampling if the timer exists."""
        if self._timer is not None:
            self._timer.active = True

    def stop(self):
        """Stop live sampling if the timer exists."""
        if self._timer is not None:
            self._timer.active = False

    def _build_figure(self) -> go.Figure:
        fig = go.Figure()
        fig.add_trace(go.Scatter(name="Process CPU %", x=[], y=[], mode="lines", line=dict(color="#D64545", width=2)))
        fig.add_trace(go.Scatter(name="System CPU %", x=[], y=[], mode="lines", line=dict(color="#F59E0B", width=2, dash="dot")))
        fig.add_trace(go.Scatter(name="Process RAM (MB)", x=[], y=[], mode="lines", yaxis="y2", line=dict(color="#2E7D32", width=2)))

        fig.update_layout(
            height=220,
            margin=dict(l=28, r=28, t=8, b=26),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            xaxis=dict(title="Seconds"),
            yaxis=dict(title="CPU %", rangemode="tozero"),
            yaxis2=dict(title="RAM MB", overlaying="y", side="right", rangemode="tozero"),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        return fig

    def _sample_and_update(self):
        try:
            elapsed = int(time.monotonic() - self._start_time)
            proc_cpu = float(self._process.cpu_percent(interval=None))
            sys_cpu = float(psutil.cpu_percent(interval=None))
            proc_mem_mb = float(self._process.memory_info().rss / (1024 * 1024))
            system_mem_pct = float(psutil.virtual_memory().percent)

            self._elapsed_seconds.append(elapsed)
            self._process_cpu.append(proc_cpu)
            self._system_cpu.append(sys_cpu)
            self._process_mem_mb.append(proc_mem_mb)

            if self._summary_label is not None:
                self._summary_label.text = f"Process CPU: {proc_cpu:.1f}% | Process RAM: {proc_mem_mb:.0f} MB"
            if self._details_label is not None:
                self._details_label.text = f"System CPU: {sys_cpu:.1f}% | System RAM used: {system_mem_pct:.1f}%"

            self._update_plot()
        except RuntimeError:
            # Parent slot deleted (e.g., user navigated away) — stop the timer.
            if self._timer is not None:
                self._timer.active = False
        except Exception:
            # Keep monitor non-intrusive: avoid raising in UI timer callbacks.
            return

    def _update_plot(self):
        if self._plot is None:
            return

        x_values: List[int] = list(self._elapsed_seconds)
        self._fig.data[0].x = x_values
        self._fig.data[0].y = list(self._process_cpu)
        self._fig.data[1].x = x_values
        self._fig.data[1].y = list(self._system_cpu)
        self._fig.data[2].x = x_values
        self._fig.data[2].y = list(self._process_mem_mb)

        self._plot.update()
