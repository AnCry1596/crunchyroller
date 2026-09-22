#!/usr/bin/env bash
set -e

echo "=== Building Crunchyroller for Linux ==="

# Check Python environment
PYTHON_BIN="python3"
if [ -d ".venv" ] && [ -f ".venv/bin/python" ]; then
    PYTHON_BIN=".venv/bin/python"
fi

echo "Using Python: $($PYTHON_BIN --version)"

# Check PyInstaller
if ! $PYTHON_BIN -m PyInstaller --version >/dev/null 2>&1; then
    echo "Installing PyInstaller..."
    $PYTHON_BIN -m pip install pyinstaller
fi

# Clean previous build artifacts
rm -rf build dist

echo "Running PyInstaller with crunchyroller.spec..."
$PYTHON_BIN -m PyInstaller crunchyroller.spec

# Package into tar.gz
OUTPUT_DIR="dist/crunchyroller"
if [ -d "$OUTPUT_DIR" ]; then
    VERSION=$(git describe --tags --always 2>/dev/null || echo "v3.2.0")
    ARCH=$(uname -m)
    ARCHIVE_NAME="crunchyroller-${VERSION}-linux-${ARCH}.tar.gz"
    echo "Creating release archive: dist/${ARCHIVE_NAME}..."
    tar -czf "dist/${ARCHIVE_NAME}" -C dist crunchyroller
    echo "=== Build Complete: dist/${ARCHIVE_NAME} ==="
else
    echo "Error: Output directory $OUTPUT_DIR was not created."
    exit 1
fi
