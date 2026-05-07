#!/bin/bash
set -e

echo "=== CAEN Log Viewer — macOS build ==="

# Install / upgrade build deps
pip install -r requirements.txt

# Clean previous build artifacts
rm -rf build dist

# Run PyInstaller
pyinstaller caen_viewer.spec --noconfirm

echo ""
echo "Build complete."
echo "Output: dist/CAEN Log Viewer.app"
echo ""
echo "To distribute: zip the .app and send it."
echo ""
echo "First-run note for unsigned apps on other Macs:"
echo "  Right-click → Open, then click Open in the dialog."
echo "  Or run: xattr -cr 'dist/CAEN Log Viewer.app'"
