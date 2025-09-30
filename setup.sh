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

# Ensure script is executable
chmod +x /opt/update-all/upall
print_status $GREEN "✓ Made script executable"

# Create log directory if it doesn't exist
mkdir -p /opt/update-all
print_status $GREEN "✓ Ensured log directory exists"

print_status $BLUE "==========================================="
print_status $GREEN "Setup completed successfully!"
print_status $BLUE "==========================================="
print_status $YELLOW "You can now run: sudo upall"
print_status $BLUE "==========================================="

