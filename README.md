# CAEN Log Viewer

A lightweight GUI application for visualising and analysing log files from CAEN high-voltage power supplies. Built with PyQt5 and Plotly, it is designed to help detector physicists and engineers monitor and debug HV systems. Developed by Luca Moleri.

---

## Features

### File loading
- **Open File** button loads any CAEN `.log` or `.txt` file without restarting the application
- Switch between files at any time; all controls and plots reset automatically

### Channel and parameter selection
- **Channel list** — multi-select one or more channels; each channel can be given an optional custom display title
- **Parameter list** — multi-select one or more parameters (e.g. VMon, IMon); one subplot is created per parameter, with all selected channels overlaid on the same axes

### Plot controls
- **Plot Selection** — renders the current selection on demand
- **Log Y** — toggles logarithmic scale on all Y axes (values ≤ 0 are automatically excluded)
- **Date/time range sliders** — two sliders (From / To) let you narrow the plotted time window by dragging; the selected timestamps are shown as labels in real time
- **Reset Range** — snaps both sliders back to the full extent of the loaded data

### Live update mode
- **Start Live / Stop Live** — polls the loaded file for new data at a configurable interval (1–60 s) and extends the existing traces in-place without regenerating the full plot

### Export
- **Export Canvas PDF** — saves the current plot as a PDF file, named automatically with a timestamp and placed next to the source log file

### Performance
- WebGL-based rendering (`Scattergl`) handles large datasets smoothly
- Plot HTML is written to a temporary file before display, removing any content-size restriction

---

## Running from source

```bash
pip install -r requirements.txt
python caen_plotly_viewer_from_log_v13b.py
```

---

## Standalone executables

Pre-built executables require no Python installation.

**macOS** — produces `dist/CAEN Log Viewer.app`:
```bash
bash build_mac.sh
```

**Windows** — produces `dist\CAEN_Log_Viewer.exe`:
```bat
build_windows.bat
```

On macOS, unsigned builds from other machines may need:
```bash
xattr -cr "dist/CAEN Log Viewer.app"
```
or right-click → Open the first time.
