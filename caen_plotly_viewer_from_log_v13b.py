import sys
import os
import re
import json
import base64
import traceback
import urllib.parse
import threading
import http.server
import socket
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from PyQt5.QtWidgets import (
    QApplication,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QFileDialog,
    QListWidget,
    QLabel,
    QPushButton,
    QScrollArea,
    QAbstractItemView,
    QListWidgetItem,
    QSpinBox,
    QMessageBox,
    QLineEdit,
    QCheckBox,
    QSlider,
    QTextEdit,
    QTabWidget,
    QTabBar,
)
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtCore import QTimer, Qt, QUrl, QMarginsF
from PyQt5.QtGui import QPageLayout, QPageSize, QPainter, QFont
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtPrintSupport import QPrinter

APP_TITLE = "CAEN Log Viewer v15"

LOG_PATTERN = re.compile(
    r"\[(?P<timestamp>[^\]]+)\]: \[[^\]]+\] bd \[(?P<bd>\d+)\] ch \[(?P<ch>\d+)\] "
    r"par \[(?P<par>[^\]]+)\] val \[(?P<val>[\d\.eE+-]+)\];"
)


# ── Module-level utilities ────────────────────────────────────────────────────

def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _parse_text(text):
    """Vectorised parser: findall runs in C, type conversions applied column-wise."""
    matches = LOG_PATTERN.findall(text)
    if not matches:
        return pd.DataFrame(columns=["timestamp", "bd", "ch", "par", "val"])
    df = pd.DataFrame(matches, columns=["timestamp", "bd", "ch", "par", "val"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df["bd"] = pd.to_numeric(df["bd"], errors="coerce")
    df["ch"] = pd.to_numeric(df["ch"], errors="coerce")
    df["val"] = pd.to_numeric(df["val"], errors="coerce")
    df = df.dropna()
    df["bd"] = df["bd"].astype(int)
    df["ch"] = df["ch"].astype(int)
    return df


def parse_caen_lines(lines):
    return _parse_text("".join(lines))


def parse_caen_log(filepath):
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        return _parse_text(f.read())


# ── Per-tab canvas widget ─────────────────────────────────────────────────────

class PlotTab(QWidget):
    """Self-contained canvas tab.

    Owns its channel/parameter selection, date-range sliders, Log Y checkbox,
    QWebEngineView canvas, and a private HTTP server that serves the plot HTML
    over loopback so Qt WebEngine receives it via http:// (not file://).
    """

    def __init__(self, viewer_app):
        super().__init__()
        # Reference to the owning PlotlyLiveViewer for shared state/utilities
        self.viewer_app = viewer_app

        # ── per-tab canvas state ──────────────────────────────────────────────
        self.viewer = None
        self.viewer_ready = False
        self.trace_map = {}
        self.current_selection = ([], [])
        self.pending_new_data = []
        self.current_fig = None
        self._t_min = None
        self._pending_pdf_path = None
        self._pdf_poll_timer = None

        # ── per-tab HTTP server ───────────────────────────────────────────────
        # Each tab gets its own loopback port.  The handler is defined as a
        # local class so it closes over this tab instance's HTML bytes.
        self._plot_html_lock = threading.Lock()
        self._plot_html_bytes: bytes = b""
        self._plot_port = _find_free_port()

        _self = self

        class _TabHTTPHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                with _self._plot_html_lock:
                    body = _self._plot_html_bytes
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt, *args):  # silence request logging
                pass

        self._http_server = http.server.HTTPServer(
            ("127.0.0.1", self._plot_port), _TabHTTPHandler
        )
        threading.Thread(
            target=self._http_server.serve_forever, daemon=True
        ).start()

        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Row 1: date-range controls
        date_controls = QHBoxLayout()

        date_controls.addWidget(QLabel("From:"))
        self.start_label = QLabel("—")
        self.start_label.setMinimumWidth(155)
        date_controls.addWidget(self.start_label)
        self.start_slider = QSlider(Qt.Horizontal)
        self.start_slider.setRange(0, 0)
        self.start_slider.valueChanged.connect(self._on_range_slider_changed)
        date_controls.addWidget(self.start_slider, stretch=1)

        date_controls.addWidget(QLabel("  To:"))
        self.end_label = QLabel("—")
        self.end_label.setMinimumWidth(155)
        date_controls.addWidget(self.end_label)
        self.end_slider = QSlider(Qt.Horizontal)
        self.end_slider.setRange(0, 0)
        self.end_slider.valueChanged.connect(self._on_range_slider_changed)
        date_controls.addWidget(self.end_slider, stretch=1)

        self.reset_range_button = QPushButton("Reset Range")
        self.reset_range_button.clicked.connect(self._reset_date_range)
        date_controls.addWidget(self.reset_range_button)

        layout.addLayout(date_controls)

        # Row 2: channel/parameter selection + per-tab action buttons
        controls = QHBoxLayout()

        self.chan_select = QListWidget()
        self.chan_select.setSelectionMode(QAbstractItemView.MultiSelection)
        self.chan_select.setMinimumWidth(70)
        self.chan_select.setMaximumWidth(90)
        self.chan_select.setMaximumHeight(120)
        self.chan_select.setSpacing(4)
        self.chan_select.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chan_select.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.chan_select.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)

        self.par_select = QListWidget()
        self.par_select.setSelectionMode(QAbstractItemView.MultiSelection)
        self.par_select.setMinimumWidth(150)

        self.plot_button = QPushButton("Plot Selection")
        self.plot_button.setMaximumHeight(40)
        self.plot_button.clicked.connect(self.generate_plots)

        self.log_scale_checkbox = QCheckBox("Log Y")
        # Use toggled (bool) instead of stateChanged (int) and wrap in a lambda
        # so the signal argument is explicitly discarded — avoids a silent
        # TypeError on some PyQt5 builds when argument count mismatches.
        self.log_scale_checkbox.toggled.connect(
            lambda _checked: self.on_plot_option_changed()
        )

        self.export_canvas_button = QPushButton("Export Canvas PDF")
        self.export_canvas_button.clicked.connect(self.export_canvas_pdf)
        self.export_canvas_button.setEnabled(False)

        controls.addWidget(QLabel("Channels:"))
        controls.addWidget(self.chan_select)
        controls.addWidget(QLabel("Parameters:"))
        controls.addWidget(self.par_select)
        controls.addWidget(self.plot_button)
        controls.addWidget(self.log_scale_checkbox)
        controls.addStretch()
        controls.addWidget(self.export_canvas_button)

        layout.addLayout(controls)

        # Row 3+: canvas scroll area (QWebEngineView is added here on plot)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.plot_container = QWidget()
        self.plot_layout = QVBoxLayout(self.plot_container)
        self.scroll.setWidget(self.plot_container)
        layout.addWidget(self.scroll, stretch=1)

        self.setLayout(layout)

    # ── population & state ────────────────────────────────────────────────────

    def _repopulate_selections(self, channels, parameters):
        """Replace chan_select/par_select with the current file's channels and params."""
        self.chan_select.clear()
        self.par_select.clear()
        for ch in channels:
            self.chan_select.addItem(QListWidgetItem(str(ch)))
        for par in parameters:
            self.par_select.addItem(QListWidgetItem(par))
        _par_row_h = self.par_select.sizeHintForRow(0) if self.par_select.count() else 22
        _par_visible = min(max(len(parameters), 1), 3)
        self.par_select.setFixedHeight(
            _par_visible * _par_row_h + self.par_select.frameWidth() * 2
        )

    def _set_loaded_state(self, loaded):
        self.chan_select.setEnabled(loaded)
        self.par_select.setEnabled(loaded)
        self.plot_button.setEnabled(loaded)
        self.log_scale_checkbox.setEnabled(loaded)
        self.start_slider.setEnabled(loaded)
        self.end_slider.setEnabled(loaded)
        self.reset_range_button.setEnabled(loaded)
        self.export_canvas_button.setEnabled(loaded and self.current_fig is not None)

    def cleanup(self):
        """Release resources — called before this tab is removed from the widget."""
        if self._pdf_poll_timer is not None:
            self._pdf_poll_timer.stop()
            self._pdf_poll_timer = None
        try:
            self._http_server.shutdown()
        except Exception:
            pass
        if self.viewer:
            self.viewer.deleteLater()
            self.viewer = None

    # ── logging (delegates to shared log box) ─────────────────────────────────

    def _log(self, msg: str) -> None:
        self.viewer_app._log(msg)

    # ── selection helpers ─────────────────────────────────────────────────────

    def _selected_channels(self):
        return [int(item.text()) for item in self.chan_select.selectedItems()]

    def _selected_parameters(self):
        return [item.text() for item in self.par_select.selectedItems()]

    # ── date range ────────────────────────────────────────────────────────────

    def _slider_to_dt(self, value):
        return self._t_min + pd.Timedelta(seconds=value)

    def _on_range_slider_changed(self):
        if self._t_min is None:
            return
        # Enforce start <= end
        if self.start_slider.value() > self.end_slider.value():
            sender = self.sender()
            if sender is self.start_slider:
                self.start_slider.blockSignals(True)
                self.start_slider.setValue(self.end_slider.value())
                self.start_slider.blockSignals(False)
            else:
                self.end_slider.blockSignals(True)
                self.end_slider.setValue(self.start_slider.value())
                self.end_slider.blockSignals(False)
        fmt = "%Y-%m-%d %H:%M:%S"
        self.start_label.setText(
            self._slider_to_dt(self.start_slider.value()).strftime(fmt)
        )
        self.end_label.setText(
            self._slider_to_dt(self.end_slider.value()).strftime(fmt)
        )
        self.on_plot_option_changed()

    def _reset_date_range(self):
        df = self.viewer_app.df
        if df.empty:
            return
        self._t_min = df["timestamp"].min()
        total_seconds = int((df["timestamp"].max() - self._t_min).total_seconds())
        for w in (self.start_slider, self.end_slider):
            w.blockSignals(True)
            w.setRange(0, max(total_seconds, 1))
        self.start_slider.setValue(0)
        self.end_slider.setValue(total_seconds)
        for w in (self.start_slider, self.end_slider):
            w.blockSignals(False)
        fmt = "%Y-%m-%d %H:%M:%S"
        self.start_label.setText(self._t_min.strftime(fmt))
        self.end_label.setText(self._slider_to_dt(total_seconds).strftime(fmt))
        # Regenerate the plot so data outside the previous custom window reappears.
        # Guard with current_fig so the call from load_file() (where _clear_plot()
        # has already set current_fig=None) is a no-op.
        if self.current_fig is not None:
            self.on_plot_option_changed()

    # ── plot generation ───────────────────────────────────────────────────────

    def on_plot_option_changed(self):
        if self._selected_channels() and self._selected_parameters():
            self.generate_plots()

    def _filter_group_for_plot(self, group):
        if not self.log_scale_checkbox.isChecked():
            return group
        return group[group["val"] > 0].copy()

    def generate_plots(self):
        try:
            self._clear_plot()

            df = self.viewer_app.df
            if df.empty:
                self.current_selection = ([], [])
                return

            selected_ch = self._selected_channels()
            selected_par = self._selected_parameters()
            self.current_selection = (selected_ch, selected_par)
            if not selected_ch or not selected_par:
                return

            axis_type = "log" if self.log_scale_checkbox.isChecked() else "linear"

            t_start = self._slider_to_dt(self.start_slider.value())
            t_end = self._slider_to_dt(self.end_slider.value())
            df_filtered = df[
                df["ch"].isin(selected_ch)
                & df["par"].isin(selected_par)
                & (df["timestamp"] >= t_start)
                & (df["timestamp"] <= t_end)
            ]
            if axis_type == "log":
                df_filtered = df_filtered[df_filtered["val"] > 0]
            if df_filtered.empty:
                return

            # Group once — avoids one filter pass per (par, ch) combination
            groups = df_filtered.groupby(["par", "ch"])

            rows = len(selected_par)
            fig = make_subplots(
                rows=rows, cols=1, shared_xaxes=True, vertical_spacing=0.03
            )

            colors = px.colors.qualitative.Set1
            ch_to_color = {
                ch: colors[i % len(colors)] for i, ch in enumerate(selected_ch)
            }
            trace_map = {}
            legend_channels_seen = set()

            for i, par in enumerate(selected_par, start=1):
                for ch in selected_ch:
                    try:
                        df_ch = groups.get_group((par, ch))
                    except KeyError:
                        continue

                    show_channel_legend = ch not in legend_channels_seen
                    fig.add_trace(
                        go.Scatter(
                            x=df_ch["timestamp"],
                            y=df_ch["val"],
                            mode="lines+markers",
                            name=self.viewer_app._channel_display_name(ch),
                            legendgroup=f"ch {ch}",
                            marker=dict(color=ch_to_color.get(ch)),
                            showlegend=show_channel_legend,
                            # Snap hover only to actual data markers, not to
                            # positions along line segments between them.
                            hoveron="points",
                        ),
                        row=i,
                        col=1,
                    )
                    legend_channels_seen.add(ch)
                    trace_map[(par, ch)] = len(fig.data) - 1

                fig.update_yaxes(title_text=par, type=axis_type, row=i, col=1)

            fig.update_layout(
                height=300 * rows,
                hovermode="x unified",
                title_text=self.viewer_app.loaded_filename,
                margin=dict(r=220),
                legend=dict(x=1.02, y=1, xanchor="left", yanchor="top"),
                # Larger default font — Plotly's built-in default (12 px) is
                # too small on typical monitor / DPI combinations.  This sets
                # the base size for axis tick labels, axis titles, legend text,
                # and hover labels; the plot title inherits a 1.2× multiplier.
                font=dict(size=15),
            )

            viewer = QWebEngineView()
            plot_ready_script = """
window.plotlyLiveViewId = "{plot_id}";
window.plotlyPendingUpdates = 0;
window.plotlyRenderReady = false;
(function() {
    const el = document.getElementById("{plot_id}");
    if (!el) return;
    const markReady = function() {
        window.requestAnimationFrame(function() {
            window.requestAnimationFrame(function() {
                window.plotlyRenderReady = true;
            });
        });
    };
    if (el.on) {
        el.on("plotly_afterplot", function() {
            markReady();
        });
    }
    markReady();
})();
"""
            html_content = fig.to_html(
                full_html=True,
                include_plotlyjs=True,
                div_id="plotly-live-view",
                post_script=plot_ready_script,
            )
            js_mapping = json.dumps(
                {f"{par}|{ch}": idx for (par, ch), idx in trace_map.items()}
            )
            html_content += f"""
<script>
window.traceNameToIndex = {js_mapping};
</script>
"""
            # Plotly.js is packaged as a UMD bundle.  If Qt WebEngine (or any
            # injected Qt script) has already defined the global `define` or
            # `require` symbols, the UMD wrapper silently routes the module
            # through AMD / CommonJS instead of the browser-global path, so
            # `window.Plotly` is never set and the canvas stays blank.
            # Injecting this preamble into <head> — before Plotly's <script> —
            # neutralises AMD detection and catches any early JS errors.
            preamble = """<script>
(function () {
    // Neutralise AMD/CommonJS so Plotly always assigns window.Plotly
    try { window.define  = undefined; } catch (_) {}
    try { window.require = undefined; } catch (_) {}

    // Patch CSSStyleSheet.insertRule to silently ignore unsupported rules.
    // Plotly.js uses :focus-visible (Chrome 86+) but the Chromium bundled
    // with PyQt5 on some builds is older and throws a SyntaxError.  That
    // uncaught exception aborts the Plotly.js <script> block before
    // window.Plotly is ever assigned, leaving the canvas blank.
    var _origInsertRule = CSSStyleSheet.prototype.insertRule;
    CSSStyleSheet.prototype.insertRule = function (rule, index) {
        try { return _origInsertRule.call(this, rule, index); } catch (_) { return 0; }
    };
})();
</script>"""
            html_content = html_content.replace("<head>", "<head>" + preamble, 1)

            # Serve the HTML over loopback HTTP so Qt WebEngine receives it
            # as a normal http:// response.  file:// URLs trigger Chromium's
            # cross-origin security policy and render a blank page on Windows.
            with self._plot_html_lock:
                self._plot_html_bytes = html_content.encode("utf-8")

            url = f"http://127.0.0.1:{self._plot_port}/plot.html"
            self.viewer_ready = False
            self.pending_new_data.clear()
            viewer.load(QUrl(url))
            viewer.setMinimumHeight(400)
            viewer.loadFinished.connect(self.on_viewer_load_finished)
            self.plot_layout.addWidget(viewer)

            self.viewer = viewer
            self.current_fig = fig
            self.trace_map = trace_map
            self.export_canvas_button.setEnabled(True)
        except Exception:
            err = traceback.format_exc()
            QMessageBox.critical(self, "Plot Error", err)

    # ── PDF export ────────────────────────────────────────────────────────────

    def export_canvas_pdf(self):
        if self.current_fig is None:
            QMessageBox.information(
                self,
                "No Plot Available",
                "Generate a plot before exporting the canvas.",
            )
            return

        timestamp_suffix = datetime.now().strftime("_%Y_%m_%d_%H_%M")
        default_export_name = os.path.join(
            os.path.dirname(self.viewer_app.loaded_path),
            os.path.splitext(self.viewer_app.loaded_filename)[0]
            + timestamp_suffix
            + ".pdf",
        )
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export current canvas as PDF",
            default_export_name,
            "PDF Files (*.pdf)",
        )
        if not file_path:
            return
        if not file_path.lower().endswith(".pdf"):
            file_path += ".pdf"

        self._pending_pdf_path = file_path
        self.export_canvas_button.setEnabled(False)
        self.export_canvas_button.setText("Exporting...")

        # Ask Plotly.js to render the figure as SVG and store in a global.
        # printToPdf() hangs because Plotly's rAF loops never reach idle;
        # this path uses Plotly's own JS API then Qt's vector SVG renderer.
        self.viewer.page().runJavaScript("""
            window._caenPdfSvg = null;
            (function() {
                var el = document.getElementById('plotly-live-view');
                if (!el || !window.Plotly) { window._caenPdfSvg = 'ERROR:no element'; return; }
                Plotly.toImage(el, {format: 'svg', width: el.offsetWidth, height: el.offsetHeight})
                    .then(function(url) { window._caenPdfSvg = url; })
                    .catch(function(e) { window._caenPdfSvg = 'ERROR:' + e; });
            })();
        """)
        self._pdf_poll_timer = QTimer(self)
        self._pdf_poll_timer.setInterval(200)
        self._pdf_poll_timer.timeout.connect(self._poll_svg_export)
        self._pdf_poll_timer.start()

    def _reset_export_button(self):
        self.export_canvas_button.setText("Export Canvas PDF")
        self.export_canvas_button.setEnabled(self.current_fig is not None)

    def _poll_svg_export(self):
        self.viewer.page().runJavaScript("window._caenPdfSvg", self._handle_svg_result)

    def _handle_svg_result(self, result):
        try:
            if result is None:
                return  # Plotly.toImage not finished yet — keep polling

            if self._pdf_poll_timer is not None:
                self._pdf_poll_timer.stop()
                self._pdf_poll_timer = None

            if not isinstance(result, str) or result.startswith("ERROR"):
                self._reset_export_button()
                msg = str(result)
                QTimer.singleShot(
                    0,
                    lambda: QMessageBox.warning(
                        self, "Export Failed", f"Could not render SVG:\n{msg}"
                    ),
                )
                return

            base64_prefix = "data:image/svg+xml;base64,"
            urlenc_prefix = "data:image/svg+xml,"

            if result.startswith(base64_prefix):
                svg_bytes = base64.b64decode(result[len(base64_prefix):])
            elif result.startswith(urlenc_prefix):
                svg_bytes = urllib.parse.unquote(
                    result[len(urlenc_prefix):]
                ).encode("utf-8")
            else:
                self._reset_export_button()
                preview = result[:80]
                QTimer.singleShot(
                    0,
                    lambda: QMessageBox.warning(
                        self,
                        "Export Failed",
                        f"Unexpected SVG data format:\n{preview}",
                    ),
                )
                return

            printer = QPrinter(QPrinter.HighResolution)
            printer.setOutputFileName(self._pending_pdf_path)
            printer.setOutputFormat(QPrinter.PdfFormat)
            printer.setPageLayout(
                QPageLayout(
                    QPageSize(QPageSize.A4),
                    QPageLayout.Landscape,
                    QMarginsF(10, 10, 10, 10),
                    QPageLayout.Millimeter,
                )
            )
            renderer = QSvgRenderer(svg_bytes)
            painter = QPainter(printer)
            renderer.render(painter)
            painter.end()

            saved_path = self._pending_pdf_path
            self._reset_export_button()
            QTimer.singleShot(
                0,
                lambda: QMessageBox.information(
                    self, "Export Complete", f"Saved PDF to:\n{saved_path}"
                ),
            )
        except Exception:
            try:
                self._reset_export_button()
            except Exception:
                pass

    # ── canvas lifecycle ──────────────────────────────────────────────────────

    def _clear_plot(self):
        for i in reversed(range(self.plot_layout.count())):
            widget = self.plot_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)
        if self.viewer:
            self.viewer.deleteLater()
            self.viewer = None
        self.current_fig = None
        self.viewer_ready = False
        self.trace_map = {}
        self.pending_new_data.clear()
        self.export_canvas_button.setEnabled(False)

    def _plot_ready_js(self):
        return """
        (function() {
            var el = document.getElementById("plotly-live-view");
            return Boolean(window.Plotly && el && el._fullData);
        })();
        """

    def on_viewer_load_finished(self, ok):
        self._log(f"loadFinished ok={ok}")
        self.viewer_ready = False
        if ok and self.viewer:
            self._poll_viewer_ready(self.viewer, 0)

    def _poll_viewer_ready(self, viewer, attempt):
        if not viewer or viewer is not self.viewer:
            return
        viewer.page().runJavaScript(
            self._plot_ready_js(),
            lambda ready, v=viewer, a=attempt: self._handle_ready_check(
                v, a, bool(ready)
            ),
        )

    def _handle_ready_check(self, viewer, attempt, ready):
        if viewer is not self.viewer:
            return
        if ready:
            self._log(f"viewer_ready=True after {attempt} poll(s)")
            self.viewer_ready = True
            if self.pending_new_data:
                combined = pd.concat(self.pending_new_data, ignore_index=True)
                self.pending_new_data.clear()
                self._extend_plot_with_df(combined)
            return
        if attempt >= 150:
            # Readiness check timed out (15 s). Mark ready anyway so that
            # live updates are not permanently blocked; the chart may simply
            # not expose _fullData in this Plotly version.
            self._log("viewer_ready timeout (150 polls) — forcing True")
            self.viewer_ready = True
            if self.pending_new_data:
                combined = pd.concat(self.pending_new_data, ignore_index=True)
                self.pending_new_data.clear()
                self._extend_plot_with_df(combined)
            return
        QTimer.singleShot(
            100,
            lambda v=viewer, a=attempt + 1: self._poll_viewer_ready(v, a),
        )

    # ── live update ───────────────────────────────────────────────────────────

    def extend_plot(self, new_df):
        if new_df.empty:
            return
        if not self.viewer or not self.trace_map:
            self._log(
                f"extend_plot: skipped (viewer={bool(self.viewer)}"
                f" trace_map={bool(self.trace_map)})"
            )
            return
        if not self.viewer_ready:
            self.pending_new_data.append(new_df)
            self._log(
                f"extend_plot: viewer_ready=False, queuing"
                f" ({len(self.pending_new_data)} pending)"
            )
            return
        self._log("extend_plot: calling _extend_plot_with_df")
        self._extend_plot_with_df(new_df)

    def _extend_plot_with_df(self, new_df):
        selected_ch, selected_par = self.current_selection
        if not selected_ch or not selected_par:
            return

        df_filtered = new_df[
            new_df["ch"].isin(selected_ch) & new_df["par"].isin(selected_par)
        ]
        if df_filtered.empty:
            return

        df_filtered = df_filtered.sort_values("timestamp")
        for (par, ch), group in df_filtered.groupby(["par", "ch"]):
            group = self._filter_group_for_plot(group)
            if group.empty:
                continue

            trace_idx = self.trace_map.get((par, ch))
            if trace_idx is None:
                self._log(f"extendTraces: no trace for ({par},{ch}) — regenerating")
                self.generate_plots()
                return

            self._log(
                f"extendTraces idx={trace_idx} ({par}|ch{ch}) x={len(group)} pts"
            )
            figure_timestamps = group["timestamp"].tolist()
            values = group["val"].tolist()

            # Update the Python-side figure so current_trace always holds the
            # full accumulated data (initial plot + all live updates so far).
            if self.current_fig is None or trace_idx >= len(self.current_fig.data):
                continue
            current_trace = self.current_fig.data[trace_idx]
            current_trace.x = list(current_trace.x) + figure_timestamps
            current_trace.y = list(current_trace.y) + values

            # Send the FULL x+y from Python — never read el.data[i].y from
            # the DOM. Plotly.js 2.x does not populate el.data[i].y after
            # rendering (internal state lives in _fullData), so DOM reads
            # give an empty y array → wrong x/y pairings in the chart.
            full_x = (
                pd.to_datetime(list(current_trace.x))
                .strftime("%Y-%m-%dT%H:%M:%S.%f")
                .tolist()
            )
            full_y = [float(v) for v in current_trace.y]
            payload = json.dumps(
                {"trace_index": trace_idx, "x": full_x, "y": full_y}
            )
            js_code = f"""
            (function() {{
                if (!window.Plotly) return 'no-plotly';
                const data = {payload};
                const el = document.getElementById('plotly-live-view');
                if (!el) return 'no-element';
                if (!el.data || !el.data[data.trace_index]) return 'no-trace:' + data.trace_index;
                try {{
                    Plotly.restyle(el, {{x: [data.x], y: [data.y]}}, [data.trace_index])
                        .then(function() {{
                            // New timestamps are beyond the original axis window;
                            // force autorange so they become visible.
                            Plotly.relayout(el, {{'xaxis.autorange': true}});
                        }});
                    return 'ok:n=' + data.x.length;
                }} catch(e) {{
                    return 'error:' + e.toString();
                }}
            }})();
            """
            self.viewer.page().runJavaScript(
                js_code,
                lambda r, _par=par, _ch=ch: self._log(
                    f"extendTraces JS ({_par}|ch{_ch}): {r}"
                ),
            )


# ── Main application window ───────────────────────────────────────────────────

class PlotlyLiveViewer(QWidget):
    def __init__(self):
        super().__init__()
        # ── shared / file-level state ─────────────────────────────────────────
        self.df = pd.DataFrame()
        self.loaded_path = ""
        self.loaded_filename = ""
        self.last_position = 0
        self.channel_titles = {}
        self.channel_title_inputs = {}
        self.live_active = False
        self._tab_counter = 0
        self._tabs = []  # ordered list of PlotTab instances

        self.setMinimumSize(1200, 800)
        # Open at ~85 % of the available screen area so the window feels spacious
        # on any monitor size.  setMinimumSize only sets a floor; the actual
        # opening size must be set explicitly with resize().
        _screen = QApplication.primaryScreen().availableGeometry()
        self.resize(int(_screen.width() * 0.85), int(_screen.height() * 0.85))
        self._update_window_title()

        layout = QVBoxLayout()

        # ── Row 1: file controls ──────────────────────────────────────────────
        file_controls = QHBoxLayout()
        file_controls.addWidget(QLabel("Loaded file:"))
        self.file_path_input = QLineEdit()
        self.file_path_input.setReadOnly(True)
        self.file_path_input.setPlaceholderText("No file loaded")
        self.open_file_button = QPushButton("Open File")
        self.open_file_button.clicked.connect(self.open_file_dialog)
        file_controls.addWidget(self.file_path_input)
        file_controls.addWidget(self.open_file_button)
        layout.addLayout(file_controls)

        # ── Row 2: shared channel-title strip (horizontal scroll) ─────────────
        # Displays "ch N: [optional title input]" for every channel in the file.
        # Titles entered here are shared across all tabs.
        titles_row = QHBoxLayout()
        titles_row.addWidget(QLabel("Channel names:"))
        self.channel_titles_scroll = QScrollArea()
        # Do NOT setWidgetResizable(True) — that forces the inner widget to match
        # the viewport width, which prevents horizontal scrolling and causes Qt to
        # ignore the widget's natural sizeHint after we rebuild its contents.
        self.channel_titles_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff
        )
        self.channel_titles_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAsNeeded
        )
        self.channel_titles_scroll.setFixedHeight(52)
        # channel_titles_widget / channel_titles_layout are (re)created fresh in
        # _rebuild_data_controls each time a file is loaded.  Initialise them here
        # so the scroll area has something to show before a file is opened.
        self.channel_titles_widget = QWidget()
        self.channel_titles_layout = QHBoxLayout(self.channel_titles_widget)
        self.channel_titles_layout.setContentsMargins(4, 2, 4, 2)
        self.channel_titles_layout.setSpacing(6)
        self.channel_titles_scroll.setWidget(self.channel_titles_widget)
        titles_row.addWidget(self.channel_titles_scroll, stretch=1)
        layout.addLayout(titles_row)

        # ── Row 3: live-mode controls + "Add Tab" button ──────────────────────
        live_row = QHBoxLayout()
        self.interval_input = QSpinBox()
        self.interval_input.setRange(1, 60)
        self.interval_input.setValue(5)
        self.interval_input.setSuffix(" s")
        self.interval_input.setMaximumWidth(100)
        self.toggle_button = QPushButton("Start Live")
        self.toggle_button.setCheckable(True)
        self.toggle_button.clicked.connect(self.toggle_live)
        self.add_tab_button = QPushButton("+ Add Tab")
        self.add_tab_button.clicked.connect(self._add_tab)
        live_row.addWidget(QLabel("Update every:"))
        live_row.addWidget(self.interval_input)
        live_row.addWidget(self.toggle_button)
        live_row.addSpacing(24)
        live_row.addWidget(self.add_tab_button)
        live_row.addStretch()
        layout.addLayout(live_row)

        # ── Row 4: tab widget (one PlotTab per tab) ───────────────────────────
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.tabCloseRequested.connect(self._close_tab)
        layout.addWidget(self.tab_widget, stretch=1)

        # ── Row 5: debug log box ──────────────────────────────────────────────
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setFixedHeight(120)
        _mono = QFont("Courier New" if sys.platform == "win32" else "Courier")
        _mono.setPointSize(8)
        self.log_box.setFont(_mono)
        layout.addWidget(self.log_box)

        self.setLayout(layout)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_from_file)

        # Create the first tab and disable controls until a file is loaded
        self._add_tab()
        self._set_loaded_state(False)

    # ── logging ───────────────────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        """Append a timestamped line to the shared debug log box (thread-safe)."""
        ts = datetime.now().strftime("%H:%M:%S")
        QTimer.singleShot(0, lambda: self.log_box.append(f"[{ts}] {msg}"))

    # ── tab management ────────────────────────────────────────────────────────

    def _add_tab(self):
        self._tab_counter += 1
        tab = PlotTab(self)
        idx = self.tab_widget.addTab(tab, f"Plot {self._tab_counter}")
        self.tab_widget.setCurrentIndex(idx)
        self._tabs.append(tab)
        # Populate the new tab if a file is already loaded
        if not self.df.empty:
            channels = sorted(self.df["ch"].unique())
            parameters = sorted(self.df["par"].unique())
            tab._repopulate_selections(channels, parameters)
            tab._reset_date_range()
            tab._set_loaded_state(True)
        self._update_close_buttons()

    def _close_tab(self, index):
        if self.tab_widget.count() <= 1:
            return  # always keep at least one tab
        tab = self.tab_widget.widget(index)
        self.tab_widget.removeTab(index)
        if tab in self._tabs:
            self._tabs.remove(tab)
        tab.cleanup()
        tab.deleteLater()
        self._update_close_buttons()

    def _update_close_buttons(self):
        """Hide the close button when there is only one tab left."""
        only_one = self.tab_widget.count() == 1
        bar = self.tab_widget.tabBar()
        for i in range(self.tab_widget.count()):
            for side in (QTabBar.LeftSide, QTabBar.RightSide):
                btn = bar.tabButton(i, side)
                if btn:
                    btn.setVisible(not only_one)

    # ── shared channel-title helpers ──────────────────────────────────────────

    def _channel_display_name(self, channel):
        return self.channel_titles.get(channel, f"ch {channel}")

    def _handle_channel_title_change(self, channel):
        title_input = self.channel_title_inputs.get(channel)
        if title_input is None:
            return
        custom_title = title_input.text().strip()
        if custom_title:
            self.channel_titles[channel] = custom_title
        else:
            self.channel_titles.pop(channel, None)
        # Regenerate any tab whose current selection includes this channel
        for tab in self._tabs:
            if (
                channel in tab._selected_channels()
                and tab._selected_parameters()
            ):
                tab.generate_plots()

    # ── layout utilities ──────────────────────────────────────────────────────

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif child_layout is not None:
                self._clear_layout(child_layout)

    # ── file operations ───────────────────────────────────────────────────────

    def open_file_dialog(self):
        start_dir = os.path.dirname(self.loaded_path) if self.loaded_path else ""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open CAEN Log File",
            start_dir,
            "Log Files (*.log *.txt)",
        )
        if file_path:
            self.load_file(file_path)

    def load_file(self, filepath):
        try:
            df = parse_caen_log(filepath)
        except OSError as exc:
            QMessageBox.warning(
                self,
                "File Load Failed",
                f"Could not open the selected file:\n{exc}",
            )
            return

        if df.empty:
            QMessageBox.warning(
                self,
                "No Data Loaded",
                "The selected file did not contain any valid CAEN log entries.",
            )
            return

        self._stop_live_mode()
        for tab in self._tabs:
            tab._clear_plot()

        self.df = df.sort_values("timestamp").reset_index(drop=True)
        self.loaded_path = filepath
        self.loaded_filename = os.path.basename(filepath)
        self.last_position = os.path.getsize(filepath)
        self.channel_titles = {}
        self.file_path_input.setText(filepath)
        self.file_path_input.setCursorPosition(0)

        self._rebuild_data_controls()
        self._set_loaded_state(True)
        self._update_window_title()

    def _rebuild_data_controls(self):
        """Rebuild the shared channel-title strip and repopulate all tabs."""
        self.channel_title_inputs = {}

        channels = sorted(self.df["ch"].unique())
        parameters = sorted(self.df["par"].unique())

        # Replace the scroll area's inner widget entirely.  Patching the existing
        # layout in-place is unreliable because Qt may not re-evaluate the widget's
        # sizeHint (needed for horizontal scrolling) after we clear and refill it.
        old_widget = self.channel_titles_scroll.takeWidget()
        if old_widget is not None:
            old_widget.deleteLater()

        self.channel_titles_widget = QWidget()
        self.channel_titles_layout = QHBoxLayout(self.channel_titles_widget)
        self.channel_titles_layout.setContentsMargins(4, 2, 4, 2)
        self.channel_titles_layout.setSpacing(6)

        for ch in channels:
            self.channel_titles_layout.addWidget(QLabel(f"ch {ch}:"))
            inp = QLineEdit()
            inp.setPlaceholderText("Optional title")
            inp.setFixedWidth(110)
            inp.editingFinished.connect(
                lambda ch=ch: self._handle_channel_title_change(ch)
            )
            self.channel_title_inputs[ch] = inp
            self.channel_titles_layout.addWidget(inp)

        self.channel_titles_layout.addStretch()  # push items to the left

        # Let the widget compute its natural size from the layout, then hand it
        # to the scroll area.  adjustSize() sets width = sizeHint().width() so
        # the horizontal scrollbar appears automatically when needed.
        self.channel_titles_widget.adjustSize()
        self.channel_titles_scroll.setWidget(self.channel_titles_widget)

        for tab in self._tabs:
            tab._repopulate_selections(channels, parameters)
            tab._reset_date_range()

    # ── loaded / unloaded state ───────────────────────────────────────────────

    def _set_loaded_state(self, loaded):
        self.interval_input.setEnabled(loaded)
        self.toggle_button.setEnabled(loaded)
        self.add_tab_button.setEnabled(loaded)
        for tab in self._tabs:
            tab._set_loaded_state(loaded)

    # ── live mode ─────────────────────────────────────────────────────────────

    def _stop_live_mode(self):
        self.timer.stop()
        self.live_active = False
        self.toggle_button.blockSignals(True)
        self.toggle_button.setChecked(False)
        self.toggle_button.blockSignals(False)
        self.toggle_button.setText("Start Live")

    def toggle_live(self):
        if not self.loaded_path:
            self._stop_live_mode()
            return
        interval = self.interval_input.value()
        if self.toggle_button.isChecked():
            self.toggle_button.setText("Stop Live")
            self.timer.start(interval * 1000)
            self.live_active = True
        else:
            self._stop_live_mode()

    def update_from_file(self):
        if not self.loaded_path:
            return
        self._log("tick")
        try:
            with open(
                self.loaded_path, "r", encoding="utf-8", errors="ignore"
            ) as handle:
                handle.seek(self.last_position)
                new_lines = handle.readlines()
                self.last_position = handle.tell()
            if not new_lines:
                self._log("tick: no new lines")
                return
            new_df = parse_caen_lines(new_lines)
            if new_df.empty:
                self._log(f"tick: {len(new_lines)} lines but 0 parsed rows")
                return
            self._log(f"tick: {len(new_df)} new rows")
            new_df = new_df.sort_values("timestamp").reset_index(drop=True)
            self.df = pd.concat([self.df, new_df], ignore_index=True)
            for tab in self._tabs:
                tab.extend_plot(new_df)
        except Exception:
            err = traceback.format_exc()
            self._log(f"tick EXCEPTION:\n{err}")
            self._stop_live_mode()
            QTimer.singleShot(
                0,
                lambda: QMessageBox.warning(
                    self,
                    "Live Update Error",
                    f"Live update stopped due to an error:\n{err}",
                ),
            )

    # ── window title ──────────────────────────────────────────────────────────

    def _update_window_title(self):
        if self.loaded_filename:
            self.setWindowTitle(f"{APP_TITLE} - {self.loaded_filename}")
        else:
            self.setWindowTitle(APP_TITLE)


if __name__ == "__main__":
    # Required on Windows (and harmless on other platforms): lets Qt WebEngine
    # share one OpenGL context across all web views in the process.
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    viewer = PlotlyLiveViewer()
    viewer.show()
    sys.exit(app.exec_())
