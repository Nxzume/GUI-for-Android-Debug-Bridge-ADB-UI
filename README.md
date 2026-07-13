# ADB Tool for Windows

A modern, user-friendly GUI application for Android Debug Bridge (ADB) operations on Windows, built with PyQt6.

## Features

### Device Management
- Auto-detect connected Android devices
- View device information (model, manufacturer, Android version, etc.)
- Auto-refresh device list every 5 seconds (silent mode to reduce log spam)
- Device selection with automatic status updates

### File Operations
- **File Explorer**: Two-pane side-by-side browser for the PC and the Android device — handles all file transfers
  - Navigate folders on both sides with editable path bars, Up / Home / Refresh buttons
  - Quick-jump shortcuts on the device side (Internal Storage, Downloads, DCIM, Pictures, Movies, Music, Documents, /data/local/tmp/, etc.)
  - **Drag and drop both ways**:
    - Drag from the PC pane → drop on the device pane to **push**
    - Drag from the device pane → drop on the PC pane to **pull**
    - Drop a row onto a folder to push/pull straight into that subfolder
    - Drag files in from Windows Explorer → device pane to **push**
    - Drag files out from the PC pane → into Windows Explorer to copy them out
  - Multi-select with Push / Pull buttons in the middle (operate on current selection)
  - Right-click menu: Push/Pull, Rename, Delete, New folder, Refresh
  - Sortable columns: Name, Size, Modified
  - Symlinks shown with their target

### App Management
- Install APK files (supports split APKs)
- Uninstall applications (with fallback to user uninstall for system apps)
- Reinstall apps for current user
- List all installed apps with search functionality
- Enable/disable apps
- View app details and package information
- Open APKs folder

### Device Operations
- Take screenshots (automatically saved to `screenshots/` folder with timestamps)
- Reboot device (normal, recovery, bootloader)

### Shell Commands
- Multi-line textbox for entering complex commands
- Execute ADB shell commands directly on your Android device
- Supports Linux commands (not Windows commands)
- Optional prefixes: You can include "adb shell" prefix, but it's not required (auto-stripped)
- View output in real-time in the log window

### Logcat
- View real-time Android logcat output
- Start/stop logcat streaming with improved error handling
- Automatic process validation and error reporting
- Clear log output

### DeGoogle Functionality
- **Simple Mode**: Remove all safe Google apps automatically
- **Custom Mode**: Select individual apps to remove (safe, risky, and unsafe packages)
- Categorized package lists:
  - Safe packages (won't break functionality)
  - Risky packages (might break some features)
  - Unsafe packages (can break device - use with caution)
- Undo DeGoogle: Restore previously disabled/uninstalled packages
- State persistence: All changes are saved to `degoogle_state.json`

### UI Features
- **Dark Mode**: Toggle between light and dark themes (persistent)
- Modern, clean interface
- Real-time status updates
- Comprehensive logging with timestamps
- Settings persistence (saved to `settings.json`)

## Requirements

1. **Python 3.8+**
2. **PyQt6** (install via `pip install PyQt6`)
3. **ADB (Android Debug Bridge)**
   - Download from: https://developer.android.com/studio/releases/platform-tools
   - Extract the `platform-tools` folder
4. **Android Device** with USB debugging enabled

## Installation

1. Clone or download this repository
2. Install Python 3.8 or higher
3. Install PyQt6:
   ```bash
   pip install -r requirements.txt
   ```
   Or directly:
   ```bash
   pip install PyQt6
   ```
4. Download Android SDK Platform Tools:
   - Visit: https://developer.android.com/studio/releases/platform-tools
   - Extract the ZIP file to a location of your choice
   - You'll need the `platform-tools` folder

## Usage

1. **Enable USB debugging on your Android device:**
   - Go to Settings → About Phone
   - Tap "Build Number" 7 times to enable Developer Options
   - Go to Settings → Developer Options
   - Enable "USB Debugging"

2. **Connect your Android device via USB**

3. **Run the application:**
   ```bash
   python adb_gui.py
   ```

4. **First Launch - Select ADB Path:**
   - On first launch, you'll be prompted to select the `platform-tools` folder
   - Navigate to where you extracted the Android SDK Platform Tools
   - Select the `platform-tools` folder (the one containing `adb.exe`)
   - The path will be saved automatically for future use

5. **Select your device** from the dropdown (devices auto-refresh every 5 seconds)

6. **Use the various buttons** to perform ADB operations

## How It Works

### ADB Path Selection
- On first launch, the application prompts you to select the `platform-tools` folder
- The selected path is saved to `settings.json` for persistence
- You can change the ADB path later using the "ADB Path" button in the UI
- The application will also check system PATH as a fallback

### Settings Persistence
- **Settings** are saved to `settings.json`:
  - ADB path
  - Dark mode preference
- **DeGoogle state** is saved to `degoogle_state.json`:
  - Disabled packages
  - Uninstalled packages
  - Timestamps and actions

### Screenshots
- Screenshots are automatically saved to the `screenshots/` folder in the project directory
- Filenames include timestamps: `screenshot_YYYYMMDD_HHMMSS.png`

### APKs Folder
- Pulled APKs are saved to the `apks/` folder in the project directory
- Useful for backing up apps before uninstalling

## Features in Detail

### Device Selection
- Devices are automatically detected and listed
- Device status (device, offline, unauthorized) is shown
- Click "Info" to see detailed device information
- Auto-refresh runs silently in the background (only logs when devices change)

### File Transfer
- All transfers go through the **File Explorer** — click "🗂️ Open File Explorer"
  in the File Transfer card to open a side-by-side browser (PC on the left,
  device on the right).
  - Double-click folders to navigate into them
  - Edit either path bar and press Enter to jump anywhere
  - Use the device "Quick paths…" dropdown for common locations (Downloads, DCIM, etc.)
  - **Drag-and-drop** items between the two panes to transfer them, or into a
    folder row to drop directly into that folder
  - **Drag from Windows Explorer** onto the device pane to push files from
    anywhere on your PC; drag from the PC pane out to a Windows Explorer
    window to copy files out
  - Or select one or more items and click **Push  ➡** / **⬅  Pull**
  - Right-click a tree to Rename, Delete, or create a New folder on either side
  - All ADB operations run in the background so the UI stays responsive

### App Management
- **Install APK**: Browse and install APK files (supports split APKs)
- **Uninstall**: Select from installed apps list or enter package name
- **List Apps**: View all installed packages with:
  - Search functionality (by app name or package)
  - Filter to show only disabled apps
  - Uninstall, reinstall, enable, disable actions
  - Double-click to view app details
- **Reinstall for User**: Restore apps that were uninstalled for your user account
- **Open APKs Folder**: Quickly access pulled APK files

### Screenshots
- Takes a screenshot and automatically saves to `screenshots/` folder
- Filename includes timestamp for easy organization

### Shell Commands
- Enter any ADB shell command in the multi-line textbox
- Commands run on your Android device (Linux), not on Windows
- Use Linux commands: `grep` (not `findstr`), `ls` (not `dir`), `cat` (not `type`)
- You can include "adb shell" prefix, but it's optional (will be auto-stripped)
- Click "Run Command" to execute
- Output appears in real-time in the log window
- Examples: `ls /sdcard`, `pm list packages`, `dumpsys battery | grep level`

### Logcat
- Click "Start Logcat" to begin streaming Android logs
- Improved error handling: Shows clear error messages if logcat fails to start
- Process validation: Automatically checks if logcat process started successfully
- Click "Stop Logcat" to stop streaming
- Click "Clear" to clear the log output
- Logs appear in real-time in the output window with "LOGCAT" tag

### DeGoogle
- **Simple Mode**: Quickly remove all safe Google apps
- **Custom Mode**: Select individual apps from categorized lists:
  - Safe packages: Apps that can be safely removed
  - Risky packages: Apps that might break some functionality
  - Unsafe packages: Critical system components (use with extreme caution)
- Choose to disable (can be re-enabled) or uninstall (can be restored)
- **Undo DeGoogle**: Restore previously removed packages from saved state

### Dark Mode
- Toggle between light and dark themes using the button in the header
- Preference is saved automatically
- All UI elements update in real-time when toggled

## Known Issues

- **Logcat not working**: The logcat feature may not display output correctly. This is a known issue that may be related to device compatibility or ADB version. Workaround: Use shell commands to view logs manually (e.g., `logcat -d`).

## Troubleshooting

**No devices found:**
- Ensure USB debugging is enabled on your device
- Check USB cable connection
- Try different USB port
- On device, check for "Allow USB debugging?" prompt and accept it
- Run `adb devices` in command prompt to verify ADB can see your device

**ADB not found:**
- On first launch, you'll be prompted to select the ADB folder
- If you skipped it, use the "ADB Path" button to set it
- Make sure you select the `platform-tools` folder (contains `adb.exe`)
- The application will also check system PATH as a fallback

**Permission denied errors:**
- Some operations require root access on the device
- Try enabling "Root access" in Developer Options (if available)
- Some system apps can only be disabled/uninstalled for your user account

**App list not showing:**
- Make sure a device is selected
- Check the log output for any error messages
- Try refreshing the device list

**Dark mode not applying to all elements:**
- Try toggling dark mode off and on again
- Restart the application if issues persist

**Shell command errors (e.g., "findstr: inaccessible or not found"):**
- Remember: Commands run on your Android device (Linux), not Windows
- Use Linux commands: `grep` instead of `findstr`, `ls` instead of `dir`, `cat` instead of `type`
- You can include "adb shell" prefix, but it's optional (auto-stripped)
- Check the help text in the UI for examples

**Logcat not showing output:**
- Make sure a device is selected
- Check the log output for error messages
- Verify ADB connection: Try running a simple shell command first
- Some devices may require root access for full logcat output
- If logcat fails to start, check the error message in the log window

## File Structure

```
adb/
├── adb_gui.py          # Main application file
├── requirements.txt    # Python dependencies
├── settings.json       # User settings (ADB path, dark mode)
├── degoogle_state.json # DeGoogle operation history
├── screenshots/        # Screenshot storage (auto-created)
└── apks/              # Pulled APK storage (auto-created)
```

## License

This project is open source and available for personal and commercial use.

## Contributing

Feel free to submit issues, fork the repository, and create pull requests for any improvements.
