#!/bin/zsh
# Double-click this file in Finder to launch Driver Patcher.
# Make sure it is executable first:  chmod +x "Driver Patcher.command"

cd "$(dirname "$0")"

# Check for PySide6; offer to install if missing
if ! python3 -c "import PySide6" &>/dev/null; then
    echo "PySide6 is not installed."
    read "yn?Install it now with pip3? [y/N] "
    if [[ "$yn" =~ ^[Yy]$ ]]; then
        pip3 install PySide6 || { echo "Install failed. Exiting."; exit 1; }
    else
        echo "Cannot launch without PySide6. Exiting."
        exit 1
    fi
fi

python3 main.py
