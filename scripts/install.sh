#!/bin/bash
# Warden — installation script for Debian
#
# Usage: ./scripts/install.sh [--user]
#   --user   install to ~/.local (default)
#   --system install to /usr/local (requires sudo)

set -e

# --- determine install mode
MODE="user"
PREFIX="$HOME/.local"
BIN_DIR="$PREFIX/bin"
APP_DIR="$PREFIX/share/warden"
DESKTOP_DIR="$HOME/.local/share/applications"

if [[ "$1" == "--system" ]]; then
    MODE="system"
    PREFIX="/usr/local"
    BIN_DIR="$PREFIX/bin"
    APP_DIR="$PREFIX/share/warden"
    DESKTOP_DIR="$PREFIX/share/applications"
    if [[ $EUID -ne 0 ]]; then
        echo "Error: --system mode requires root. Use sudo."
        exit 1
    fi
fi

# --- check dependencies
echo "Checking dependencies..."
MISSING=()

if ! command -v rsync &> /dev/null; then
    MISSING+=("rsync")
fi
if ! command -v rclone &> /dev/null; then
    MISSING+=("rclone")
fi
if ! command -v tar &> /dev/null; then
    MISSING+=("tar")
fi
if ! python3 -c "import gi" 2>/dev/null; then
    MISSING+=("python3-gi gir1.2-gtk-3.0")
fi

if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo "Missing dependencies: ${MISSING[*]}"
    echo "Install with:"
    echo "  sudo apt install ${MISSING[*]}"
    exit 1
fi

echo "All dependencies present."

# --- build & install
echo "Installing Warden ($MODE mode)..."

# Install the package via pip (user or system)
if [[ "$MODE" == "user" ]]; then
    pip install --user .
else
    pip install .
fi

# --- desktop file
mkdir -p "$DESKTOP_DIR"
cp data/warden-gui.desktop "$DESKTOP_DIR/"

# --- ensure binary directory is in PATH (user mode)
if [[ "$MODE" == "user" ]]; then
    if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
        echo ""
        echo "⚠ Note: $HOME/.local/bin is not in your PATH."
        echo "  Add this to your ~/.bashrc or ~/.profile:"
        echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
    fi
fi

echo ""
echo "✓ Warden installed successfully."
echo ""
echo "Next steps:"
echo "  1. Run 'warden-gui' to open the graphical interface."
echo "  2. Or use 'warden --help' for the command-line interface."
echo "  3. Configure Google Drive: see README.md → 'Google Drive setup'."
echo ""
echo "To uninstall: pip uninstall warden"
