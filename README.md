# Ubuntu System Update Script

A comprehensive bash script that automates Ubuntu system updates using multiple package managers and provides detailed logging and error handling.

## 📋 Overview

This script performs a complete system update on Ubuntu by running various update commands in sequence, handling errors gracefully, and providing detailed logging of all operations.

## 🚀 Quick Start

1. **Setup the script:**
   ```bash
   sudo /opt/update-all/setup.sh
   ```

2. **Run the update:**
   ```bash
   sudo upall
   ```

   **With verbose output:**
   ```bash
   sudo upall -v
   # or
   sudo upall --verbose
   ```

## 📁 File Structure

```
/opt/update-all/
├── upall              # Main update script
├── setup.sh           # Setup script for symlink creation
├── update_log.txt     # Log file (created when script runs)
└── README.md          # This documentation
```

## 🔧 What the Script Does

### Core Update Commands
- **`apt update -y`** - Updates the package lists from repositories
- **`apt upgrade -y`** - Upgrades all installed packages to their latest versions
- **`apt autoremove -y`** - Removes packages that are no longer needed
- **`snap refresh`** - Updates all installed snap packages
- **`/opt/update-cursor/bin/update-cursor`** - Updates the Cursor editor (if available)

### Additional Update Commands
- **`apt full-upgrade -y`** - Performs a more comprehensive upgrade that handles changing dependencies
- **`apt autoclean`** - Cleans the package cache to free up disk space
- **`flatpak update -y`** - Updates Flatpak packages (if Flatpak is installed)

### System Checks
- **Reboot Detection** - Checks if a system reboot is required after updates
- **Error Tracking** - Monitors and reports any failed commands
- **Progress Reporting** - Shows real-time status of each operation

## ✨ Features

- **🎨 Colored Output**:
  - 🟢 Green for successful operations
  - 🔴 Red for errors and failures
  - 🟡 Yellow for warnings and information
  - 🔵 Blue for headers and status messages

- **📝 Comprehensive Logging**:
  - All command output saved to `/opt/update-all/update_log.txt`
  - Log file is replaced on each run
  - Includes timestamps and command status
  - **Verbose mode**: Display output on terminal AND save to log file

- **⚠️ Error Handling**:
  - Tracks failed commands
  - Continues execution even if some commands fail
  - Provides detailed error summary at the end

- **🛡️ Security**:
  - Requires root privileges (sudo)
  - Validates permissions before execution

- **📊 Progress Tracking**:
  - Shows which commands succeeded/failed
  - Displays total command count and error count
  - Provides execution time information

## 📖 Usage

### Basic Usage
```bash
sudo upall
```

### Verbose Mode
```bash
# Show detailed output on terminal while also logging to file
sudo upall -v
# or
sudo upall --verbose
```

### Help
```bash
# Show usage information and available options
sudo upall -h
# or
sudo upall --help
```

### Manual Setup (Alternative to setup.sh)
```bash
# Create symlink
sudo ln -s /opt/update-all/upall /usr/bin/upall

# Make executable
sudo chmod +x /opt/update-all/upall
```

### Checking Logs
```bash
# View the latest update log
cat /opt/update-all/update_log.txt

# Follow the log in real-time (if script is running)
tail -f /opt/update-all/update_log.txt
```

## 📋 Sample Output

```
===========================================
Ubuntu System Update Script
===========================================
Starting system update at Mon Jan 15 10:30:00 UTC 2024
===========================================

Running: Update package lists
✓ Success: Update package lists

Running: Upgrade installed packages
✓ Success: Upgrade installed packages

Running: Remove unnecessary packages
✓ Success: Remove unnecessary packages

Running: Update snap packages
✓ Success: Update snap packages

Running: Update Cursor editor
✓ Success: Update Cursor editor

Running: Full system upgrade (handles dependencies)
✓ Success: Full system upgrade (handles dependencies)

Running: Clean package cache
✓ Success: Clean package cache

✓ No reboot required

===========================================
UPDATE SUMMARY
===========================================
✓ All updates completed successfully!
✓ 7/7 commands executed without errors
Verbose output saved to: /opt/update-all/update_log.txt
Completed: Mon Jan 15 10:35:00 UTC 2024
===========================================
```

## 🔍 Troubleshooting

### Common Issues

1. **Permission Denied**
   - Ensure you're running with `sudo`
   - Check that the script has execute permissions

2. **Command Not Found**
   - Some commands like `/opt/update-cursor/bin/update-cursor` or `flatpak` may not be installed
   - The script will skip unavailable commands and continue

3. **Network Issues**
   - Check your internet connection
   - Verify repository sources are accessible

4. **Disk Space**
   - Ensure sufficient disk space for updates
   - The script includes `apt autoclean` to help free space

### Log Analysis

The log file contains detailed information about each command:
- Command executed
- Timestamp
- Full output (stdout and stderr)
- Success/failure status

## 🔧 Customization

### Adding More Commands

To add additional update commands, edit the `upall` script and add new `log_command` calls:

```bash
# Example: Add Docker cleanup
log_command "docker system prune -a -f" "Clean Docker system"
```

### Modifying Log Location

Change the `LOG_FILE` variable in the script:
```bash
LOG_FILE="/var/log/upall.log"  # System log location
# or
LOG_FILE="$HOME/update_log.txt"  # User home directory
```

## 🛡️ Security Considerations

- **Root Access**: This script requires root privileges to update system packages
- **Log Files**: Log files may contain sensitive system information
- **Network**: Script downloads packages from official repositories
- **Backup**: Consider backing up important data before major updates

## 📚 Additional Ubuntu Update Commands

For a more comprehensive update, you might also consider:

- **Firmware Updates**: `sudo fwupdmgr update`
- **Security Updates**: `sudo unattended-upgrade`
- **Docker Updates**: `sudo docker system prune -a`
- **Node.js Updates**: `sudo npm update -g`
- **Python Updates**: `pip list --outdated`

## 🤝 Contributing

To improve this script:
1. Test thoroughly on your system
2. Add error handling for edge cases
3. Consider additional package managers
4. Improve logging and output formatting

## 📄 License

This script is provided as-is for educational and practical purposes. Use at your own risk and always backup important data before running system updates.

---

**Note**: Always review what updates will be installed before running any system update script. Some updates may require manual intervention or system reboot.
