# CAEN Log Viewer

A lightweight GUI application for visualising and analysing log files from CAEN high-voltage power supplies. Built with PyQt5 and Plotly, it is designed to help detector physicists and engineers monitor and debug HV systems. Developed by Luca Moleri.

---

## Features

### File loading
- **Open File** button loads any CAEN `.log` or `.txt` file without restarting the application
- Switch between files at any time; all controls and plots reset automatically across every open tab

### Tabbed canvas
- The plot area is divided into **independent tabs**, each with its own channel/parameter selection, time-range sliders, and Log Y setting
- A default **Plot 1** tab is created on launch; click **+ Add Tab** to open additional tabs
- Tabs can be closed individually (a minimum of one tab is always kept open)
- **Live mode** and **channel name labels** are shared across all tabs

### Channel and parameter selection (per tab)
- **Channel list** — multi-select one or more channels
- **Parameter list** — multi-select one or more parameters (e.g. VMon, IMon); one subplot is created per parameter, with all selected channels overlaid on the same axes
- **Channel name labels** — an optional custom display title for each channel can be set in the shared strip at the top of the window; labels are shared across all tabs and update all affected plots immediately

### Plot controls (per tab)
- **Plot Selection** — renders the current selection on demand
- **Log Y** — toggles logarithmic scale on all Y axes (values ≤ 0 are automatically excluded)
- **Date/time range sliders** — two sliders (From / To) let you narrow the plotted time window by dragging; the selected timestamps are shown as labels in real time
- **Reset Range** — snaps both sliders back to the full extent of the loaded data, including any points added during live mode

### Y-axis units
Parameter names are automatically annotated with their physical unit:

| Parameter prefix | Unit |
|-----------------|------|
| IMon, ImonH, ImonL, … | µA |
| VMon, VmonH, VmonL, … | V |
| ISet, ISet2, … | µA |
| VSet, VSet2, … | V |
| RUp | V/s |
| RDwn | V/s |
| Trip | s |
| SVMax | V |

Matching is prefix-based and case-insensitive, so any CAEN variant (`ImonH`, `VmonL`, etc.) is recognised. Unknown parameters are displayed without a unit.

### Live update mode
- **Start Live / Stop Live** — polls the loaded file for new data at a configurable interval (1–60 s)
- New data points are appended to the existing traces in-place on **all open tabs** simultaneously, without regenerating the full plot
- The X axis expands automatically to show the latest timestamps

### Export
- **Export Canvas PDF** button in each tab saves that tab's current plot as a PDF
- File name is generated automatically with a timestamp and placed next to the source log file
- The SVG is rendered at the correct aspect ratio so text and labels are not distorted

### Hover
- Hover tooltips snap to actual data markers only (not to positions along line segments), so the displayed value always corresponds to the nearest recorded data point

---

## Running from source

```bash
pip install -r requirements.txt
python caen_plotly_viewer_from_log_v13b.py
```

---

## Standalone executables

Pre-built executables require no Python installation.

### macOS — single `.app` bundle

```bash
bash build_mac.sh
```

Produces `dist/CAEN Log Viewer.app`.  
On macOS, unsigned builds from another machine may trigger Gatekeeper:

```bash
xattr -cr "dist/CAEN Log Viewer.app"
```

or right-click → Open the first time.

### Windows — folder bundle

```bat
build_windows.bat
```

Produces `dist\CAEN_Log_Viewer\` containing `CAEN_Log_Viewer.exe` plus support files.  
**Distribute the entire `dist\CAEN_Log_Viewer\` folder** (e.g. as a zip archive) — the exe requires `QtWebEngineProcess.exe` and resource files alongside it and cannot be packed into a single file due to Qt WebEngine constraints.

> **Note:** Windows Defender / SmartScreen may show a warning on first run. This is a known false-positive for unsigned PyInstaller executables. Click *More info → Run anyway* to proceed.

---

## Technical notes

- Plot HTML is served over a loopback HTTP server (`http://127.0.0.1`) rather than loaded from `file://` URLs. This bypasses Chromium's cross-origin security policy that causes blank canvases in packaged builds on Windows.
- Each canvas tab runs its own HTTP server on a randomly assigned free port, so tabs are fully isolated.
- Plotly's AMD/CommonJS detection is neutralised before the Plotly.js script runs, ensuring `window.Plotly` is always assigned correctly in the Qt WebEngine environment.
- Live updates send the full accumulated trace data from Python (`current_trace.x/y`) to `Plotly.restyle()` each tick, rather than reading back data from the DOM. This avoids a Plotly.js 2.x behaviour where `el.data[i].y` is not populated after rendering.
