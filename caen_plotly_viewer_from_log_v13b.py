import sys
import os
import re
import json
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from PyQt5.QtWidgets import (
    QApplication, QVBoxLayout, QWidget, QFileDialog,
    QListWidget, QLabel, QHBoxLayout, QPushButton, QScrollArea,
    QAbstractItemView, QListWidgetItem, QSpinBox
)
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtCore import QTimer

def parse_caen_log(filepath):
    pattern = re.compile(
        r"\[(?P<timestamp>[^\]]+)\]: \[[^\]]+\] bd \[(?P<bd>\d+)\] ch \[(?P<ch>\d+)\] "
        r"par \[(?P<par>[^\]]+)\] val \[(?P<val>[\d\.eE+-]+)\];"
    )
    data = []
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            match = pattern.match(line.strip())
            if match:
                entry = match.groupdict()
                try:
                    entry["timestamp"] = pd.to_datetime(entry["timestamp"])
                    entry["bd"] = int(entry["bd"])
                    entry["ch"] = int(entry["ch"])
                    entry["val"] = float(entry["val"])
                    data.append(entry)
                except Exception:
                    continue
    return pd.DataFrame(data)

class PlotlyLiveViewer(QWidget):
    def __init__(self, df, file_path):
        super().__init__()
        self.setWindowTitle("CAEN Log Viewer v14")
        self.setMinimumSize(1200, 800)
        self.df = df
        self.loaded_path = file_path
        self.loaded_filename = os.path.basename(file_path)
        self.viewer = None
        self.viewer_ready = False
        self.trace_map = {}
        self.current_selection = ([], [])
        self.pending_new_data = []

        layout = QVBoxLayout()
        controls = QHBoxLayout()

        self.chan_select = QListWidget()
        self.chan_select.setSelectionMode(QAbstractItemView.MultiSelection)
        self.chan_select.setMaximumHeight(60)
        for ch in sorted(df["ch"].unique()):
            self.chan_select.addItem(QListWidgetItem(str(ch)))

        self.par_select = QListWidget()
        self.par_select.setSelectionMode(QAbstractItemView.MultiSelection)
        self.par_select.setMaximumHeight(60)
        self.par_select.setMinimumWidth(150)
        for par in sorted(df["par"].unique()):
            self.par_select.addItem(QListWidgetItem(par))

        self.plot_button = QPushButton("Plot Selection")
        self.plot_button.setMaximumHeight(40)
        self.plot_button.clicked.connect(self.generate_plots)

        self.interval_input = QSpinBox()
        self.interval_input.setRange(1, 60)
        self.interval_input.setValue(5)
        self.interval_input.setSuffix(" s")
        self.interval_input.setMaximumWidth(100)

        self.toggle_button = QPushButton("Start Live")
        self.toggle_button.setCheckable(True)
        self.toggle_button.clicked.connect(self.toggle_live)

        controls.addWidget(QLabel("Channels:"))
        controls.addWidget(self.chan_select)
        controls.addWidget(QLabel("Parameters:"))
        controls.addWidget(self.par_select)
        controls.addWidget(self.plot_button)
        controls.addWidget(QLabel("Update every:"))
        controls.addWidget(self.interval_input)
        controls.addWidget(self.toggle_button)
        self.export_canvas_button = QPushButton("Export Canvas PDF")
        self.export_canvas_button.clicked.connect(self.export_canvas_pdf)
        controls.addWidget(self.export_canvas_button)
        layout.addLayout(controls)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.plot_container = QWidget()
        self.plot_layout = QVBoxLayout(self.plot_container)
        self.scroll.setWidget(self.plot_container)
        layout.addWidget(self.scroll)
        self.setLayout(layout)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_from_file)
        self.last_position = os.path.getsize(file_path)
        self.live_active = False

    def toggle_live(self):
        interval = self.interval_input.value()
        if self.toggle_button.isChecked():
            self.toggle_button.setText("Stop Live")
            self.timer.start(interval * 1000)
            self.live_active = True
        else:
            self.toggle_button.setText("Start Live")
            self.timer.stop()
            self.live_active = False

    def update_from_file(self):
        try:
            with open(self.loaded_path, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(self.last_position)
                new_lines = f.readlines()
                self.last_position = f.tell()
            if not new_lines:
                return

            pattern = re.compile(
                r"\[(?P<timestamp>[^\]]+)\]: \[[^\]]+\] bd \[(?P<bd>\d+)\] ch \[(?P<ch>\d+)\] "
                r"par \[(?P<par>[^\]]+)\] val \[(?P<val>[\d\.eE+-]+)\];"
            )
            new_data = []
            for line in new_lines:
                match = pattern.match(line.strip())
                if match:
                    entry = match.groupdict()
                    try:
                        entry["timestamp"] = pd.to_datetime(entry["timestamp"])
                        entry["bd"] = int(entry["bd"])
                        entry["ch"] = int(entry["ch"])
                        entry["val"] = float(entry["val"])
                        new_data.append(entry)
                    except:
                        continue

            if new_data:
                new_df = pd.DataFrame(new_data)
                self.df = pd.concat([self.df, new_df], ignore_index=True)
                self.extend_plot(new_df)
        except Exception as e:
            print(f">> Live update error: {e}")

    def generate_plots(self):
        for i in reversed(range(self.plot_layout.count())):
            widget = self.plot_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)
        if self.viewer:
            self.viewer.deleteLater()
            self.viewer = None

        selected_ch = [int(item.text()) for item in self.chan_select.selectedItems()]
        selected_par = [item.text() for item in self.par_select.selectedItems()]
        if not selected_ch or not selected_par:
            return

        df_filtered = self.df[
            self.df["ch"].isin(selected_ch) &
            self.df["par"].isin(selected_par)
        ]
        if df_filtered.empty:
            return

        rows = len(selected_par)
        fig = make_subplots(rows=rows, cols=1, shared_xaxes=True, vertical_spacing=0.03)

        colors = px.colors.qualitative.Set1
        ch_to_color = {ch: colors[i % len(colors)] for i, ch in enumerate(selected_ch)}
        trace_map = {}

        for i, par in enumerate(selected_par, start=1):
            df_par = df_filtered[df_filtered["par"] == par]

            for j, ch in enumerate(selected_ch):
                df_ch = df_par[df_par["ch"] == ch]
                if df_ch.empty:
                    continue
                fig.add_trace(
                    go.Scatter(
                        x=df_ch["timestamp"],
                        y=df_ch["val"],
                        mode="lines+markers",
                        name=f"ch {ch}",
                        marker=dict(color=ch_to_color.get(ch)),
                        showlegend=False
                    ),
                    row=i, col=1
                )
                trace_map[(par, ch)] = len(fig.data) - 1
                fig.add_annotation(
                    text=f"● ch {ch}",
                    xref="paper", yref="paper",
                    x=0.01 + j * 0.1, y=1 - (i - 1 + 0.1) / rows,
                    showarrow=False,
                    font=dict(size=11, color=ch_to_color[ch]),
                    bgcolor="rgba(255,255,255,0.5)",
                    bordercolor="lightgray",
                    borderwidth=1
                )

            fig.update_yaxes(title_text=par, row=i, col=1)

        fig.update_layout(
            height=300 * rows,
            hovermode="x unified",
            title_text=self.loaded_filename
        )

        viewer = QWebEngineView()
        html_content = fig.to_html(
            full_html=True,
            include_plotlyjs="cdn",
            div_id="plotly-live-view"
        )
        js_mapping = json.dumps({f"{par}|{ch}": idx for (par, ch), idx in trace_map.items()})
        html_content += f"""
<script>
window.plotlyLiveViewId = "plotly-live-view";
window.traceNameToIndex = {js_mapping};
</script>
"""
        self.viewer_ready = False
        self.pending_new_data.clear()
        viewer.setHtml(html_content)
        viewer.setMinimumHeight(400)
        viewer.loadFinished.connect(self.on_viewer_load_finished)
        self.plot_layout.addWidget(viewer)

        self.viewer = viewer
        self.trace_map = trace_map
        self.current_selection = (selected_ch, selected_par)

    def export_canvas_pdf(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export current canvas as PDF",
            os.path.splitext(self.loaded_filename)[0] + "_canvas.pdf",
            "PDF Files (*.pdf)"
        )
        if not file_path:
            return
        webview = self.plot_layout.itemAt(self.plot_layout.count() - 1).widget()
        if hasattr(webview, 'page'):
            webview.page().printToPdf(file_path)

    def on_viewer_load_finished(self, ok):
        self.viewer_ready = ok
        if ok and self.pending_new_data:
            combined = pd.concat(self.pending_new_data, ignore_index=True)
            self.pending_new_data.clear()
            self._extend_plot_with_df(combined)

    def extend_plot(self, new_df):
        if new_df.empty:
            return
        if not self.viewer or not self.trace_map:
            return
        if not self.viewer_ready:
            self.pending_new_data.append(new_df)
            return
        self._extend_plot_with_df(new_df)

    def _extend_plot_with_df(self, new_df):
        selected_ch, selected_par = self.current_selection
        if not selected_ch or not selected_par:
            return

        df_filtered = new_df[
            new_df["ch"].isin(selected_ch) &
            new_df["par"].isin(selected_par)
        ]
        if df_filtered.empty:
            return

        df_filtered = df_filtered.sort_values("timestamp")
        for (par, ch), group in df_filtered.groupby(["par", "ch"]):
            trace_idx = self.trace_map.get((par, ch))
            if trace_idx is None:
                continue
            timestamps = group["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S.%f").tolist()
            values = group["val"].tolist()
            payload = json.dumps({
                "trace_index": trace_idx,
                "x": timestamps,
                "y": values
            })
            js_code = f"""
            (function() {{
                if (!window.Plotly || !window.plotlyLiveViewId) return;
                const data = {payload};
                const el = document.getElementById(window.plotlyLiveViewId);
                if (!el) return;
                Plotly.extendTraces(el, {{
                    x: [data.x],
                    y: [data.y]
                }}, [data.trace_index]);
            }})();
            """
            self.viewer.page().runJavaScript(js_code)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    file_path, _ = QFileDialog.getOpenFileName(None, "Open CAEN Log File", "", "Log Files (*.log *.txt)")
    if not file_path:
        sys.exit()
    df = parse_caen_log(file_path)
    if df.empty:
        print("No valid entries found in the log.")
        sys.exit()
    viewer = PlotlyLiveViewer(df, file_path)
    viewer.show()
    sys.exit(app.exec_())
