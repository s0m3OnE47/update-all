#!/bin/bash

# Setup script for Ubuntu Update Script
# This script creates the necessary symlink and sets up permissions

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_status() {
    local color=$1
    local message=$2
    echo -e "${color}${message}${NC}"
}

# Check if running as root
if [[ $EUID -ne 0 ]]; then
    print_status $RED "This setup script must be run as root (use sudo)"
    exit 1
fi

print_status $BLUE "==========================================="
print_status $BLUE "Ubuntu Update Script Setup"
print_status $BLUE "==========================================="

# Create symlink in /usr/bin
if [ ! -L /usr/bin/upall ]; then
    ln -s /opt/update-all/upall /usr/bin/upall
    print_status $GREEN "✓ Created symlink: /usr/bin/upall -> /opt/update-all/upall"
else
    print_status $YELLOW "⚠ Symlink already exists: /usr/bin/upall"
fi

# Ensure scripts are executable
chmod +x /opt/update-all/upall
print_status $GREEN "✓ Made upall executable"

chmod +x /opt/update-all/update_appimages.py
print_status $GREEN "✓ Made update_appimages.py executable"

# Create log directory if it doesn't exist
mkdir -p /opt/update-all
print_status $GREEN "✓ Ensured log directory exists"

# Create config.env with the non-root user for git repo updates (cron/systemd runs)
CONFIG_FILE="/opt/update-all/config.env"
if [[ ! -f "$CONFIG_FILE" ]]; then
    TARGET_USER="${SUDO_USER:-$(stat -c '%U' /opt/update-all 2>/dev/null)}"
    if [[ -n "$TARGET_USER" ]] && [[ "$TARGET_USER" != "root" ]]; then
        echo "UPDATE_GIT_REPOS_USER=$TARGET_USER" > "$CONFIG_FILE"
        chmod 644 "$CONFIG_FILE"
        print_status $GREEN "✓ Created config.env with UPDATE_GIT_REPOS_USER=$TARGET_USER"
    fi
fi

# Create symlink for AppImage updater
if [ ! -L /usr/bin/update-appimages ]; then
    ln -s /opt/update-all/update_appimages.py /usr/bin/update-appimages
    print_status $GREEN "✓ Created symlink: /usr/bin/update-appimages -> /opt/update-all/update_appimages.py"
else
    print_status $YELLOW "⚠ Symlink already exists: /usr/bin/update-appimages"
fi

print_status $BLUE "==========================================="
print_status $GREEN "Setup completed successfully!"
print_status $BLUE "==========================================="
print_status $YELLOW "You can now run: sudo upall"
print_status $BLUE "==========================================="

