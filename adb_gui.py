import sys
import subprocess
import threading
import os
import shlex
import tempfile
import json
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QTextEdit, QLineEdit, QFileDialog,
    QMessageBox, QInputDialog, QFrame, QScrollArea, QGroupBox, QSizePolicy,
    QDialog, QListWidget, QCheckBox, QRadioButton, QButtonGroup, QTabWidget,
    QTreeWidget, QTreeWidgetItem, QHeaderView, QSplitter, QMenu,
    QAbstractItemView, QToolButton
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QMimeData, QUrl
from PyQt6.QtGui import QFont, QColor, QPalette, QDrag


# ============================================================================
# DeGoogle package lists
# ----------------------------------------------------------------------------
# These are deduplicated via dict.fromkeys() (which preserves insertion order).
# Each package belongs to exactly one bucket; the "risky" bucket is the only
# place sync adapters / GSF login services live so they don't show twice in
# the Custom mode dialog.
# ============================================================================

SAFE_GOOGLE_PACKAGES = list(dict.fromkeys([
    # A. Google Apps
    'com.google.android.youtube',
    'com.google.android.apps.youtube.music',
    'com.google.android.videos',
    'com.google.android.music',
    'com.google.android.apps.books',
    'com.google.android.apps.podcasts',
    'com.google.android.apps.tachyon',           # Duo / Meet
    'com.google.android.apps.chromecast.app',    # Google Home
    'com.google.android.apps.maps',
    'com.google.android.apps.docs',              # Google Drive
    'com.google.android.gm',                     # Gmail
    'com.google.android.calendar',
    'com.google.android.contacts',               # Only if using an alternative app
    # B. Google Assistant / Search / AI
    'com.google.android.googlequicksearchbox',   # Google App (search + feed)
    'com.google.android.apps.googleassistant',
    'com.android.hotwordenrollment.okgoogle',
    'com.android.hotwordenrollment.xgoogle',
    'com.google.android.apps.scribe',            # Recorder transcription AI
    'com.google.android.as',                     # Pixel AI suggestions
    'com.google.android.apps.aiwallpapers',
    # C. Google Media Processing & AR
    'com.google.ar.core',
    'com.google.android.apps.photos',
    'com.google.android.apps.lens',
    'com.google.android.apps.photos.scanner',
    # D. Pixel Optional Features
    'com.google.android.apps.pixelmigrate',
    'com.google.android.apps.pixel.setupwizard',
    'com.google.android.apps.pixel.typeapps',
    'com.google.android.apps.pixel.extras',
    'com.google.android.onetimeinitializer',
    # E. Cloud / Backup / Sync (non-essential, but not core sync)
    'com.google.android.apps.restore',
    'com.google.android.backuptransport',
    'com.google.android.partnersetup',
    # F. Vehicle / Cast / Wearable
    'com.google.android.projection.gearhead',    # Android Auto
    'com.google.android.gms.car',
    'com.google.android.apps.wearables',
    # G. Logging / Analytics / Feedback
    'com.google.android.feedback',
    'com.google.mainline.telemetry',
    'com.google.android.gms.advertisingid',
    'com.google.android.gms.location.history',
]))

# Risky: removing these may break sync, login, or some Google integrations,
# but the device should still boot and function. Sync adapters live here
# (and only here) so they don't appear twice in the Custom dialog.
RISKY_GOOGLE_PACKAGES = list(dict.fromkeys([
    'com.google.android.gsf.login',              # Google Login Service
    'com.google.android.providers.gsf',          # Google Services Provider
    'com.google.android.syncadapters.calendar',  # Calendar sync
    'com.google.android.syncadapters.contacts',  # Contacts sync
]))

# Unsafe: WILL break the Pixel (bootloop, no camera, no network, no launcher,
# failed OTAs, broken notifications). Surfaced behind warnings so a user can
# still opt in if they really know what they are doing.
UNSAFE_GOOGLE_PACKAGES = list(dict.fromkeys([
    # A. Pixel Launcher + UI
    'com.google.android.pixel.launcher',
    'com.google.android.apps.wallpaper',
    'com.google.android.systemui',
    'com.android.systemui',
    # B. Camera / Image Pipeline
    'com.google.pixel.camera.services',
    'com.google.android.camera',
    'com.google.android.camera.provider',
    'com.google.android.camera.experimental2018',
    # C. Google Play Core Components
    'com.google.android.gms',                    # Google Play Services
    'com.google.android.gsf',                    # Google Services Framework
    'com.google.android.gms.location',
    'com.google.android.gms.policy_sidecar',
    # D. Phone, Messaging, Carrier
    'com.android.phone',
    'com.android.providers.telephony',
    'com.android.providers.telephony.overlay',
    'com.android.carrierconfig',
    'com.google.android.ims',                    # VoLTE / VoWiFi
    # E. Core Android Infrastructure
    'com.android.providers.downloads',           # Breaks Play Store + OTA updates
    'com.android.providers.downloads.ui',
    'com.android.vending',                       # Play Store
    'com.android.packageinstaller',
    # F. OTA Update Critical
    'com.google.android.gms.update',
    'com.google.android.gms.setup',
    'com.google.android.gms.unstable',
]))

# Combined set used by Undo to filter saved state; covers everything the
# DeGoogle flow can ever touch.
ALL_DEGOOGLE_PACKAGES = list(dict.fromkeys(
    SAFE_GOOGLE_PACKAGES + RISKY_GOOGLE_PACKAGES + UNSAFE_GOOGLE_PACKAGES
))


class ADBManager:
    """Manages ADB operations"""
    
    def __init__(self, adb_path=None):
        if adb_path:
            self.adb_path = adb_path
        else:
            self.adb_path = self.find_adb()
        
    def find_adb(self):
        """Try to find ADB executable (fallback only - should use saved path from settings)"""
        # Try to find in PATH first (most reliable if installed system-wide)
        try:
            result = subprocess.run(['where', 'adb'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and result.stdout.strip():
                path = result.stdout.strip().split('\n')[0]
                if os.path.exists(path):
                    return path
        except:
            pass
        
        # Check common locations as fallback
        common_paths = [
            os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Android', 'Sdk', 'platform-tools', 'adb.exe'),
            os.path.join(os.environ.get('ProgramFiles', ''), 'Android', 'android-sdk', 'platform-tools', 'adb.exe'),
            os.path.join(os.path.expanduser('~'), 'Downloads', 'platform-tools-latest-windows', 'platform-tools', 'adb.exe'),
        ]
        
        for path in common_paths:
            if os.path.exists(path):
                return path
        
        return 'adb'  # Fallback to assuming it's in PATH
    
    def set_adb_path(self, path):
        """Set custom ADB path"""
        # Accept a direct path to the adb executable
        if os.path.isfile(path):
            self.adb_path = path
            return True
        # Or accept a directory containing adb.exe
        if os.path.isdir(path) and os.path.isfile(os.path.join(path, 'adb.exe')):
            self.adb_path = os.path.join(path, 'adb.exe')
            return True
        return False
    
    def run_command(self, command, timeout=30):
        """Run ADB command and return result"""
        try:
            # Use POSIX-splitting so enclosing double-quotes STRIP cleanly from each
            # token. On Windows, ``shlex.split(..., posix=False)`` leaves the `"`
            # characters INSIDE argv entries (see Python docs — this mode emulates old
            # ``cmd.exe`` rules). Passing those tokens verbatim to adb makes
            # ``push``/``pull``/``install`` try to ``stat`` filenames that literally
            # start/end with ASCII ``"``, which surfaces as baffling "No such file"
            # errors. POSIX mode matches how adb quoting is written throughout this app.
            command_parts = shlex.split(command) if command else []
            full_command = [self.adb_path] + command_parts
            
            result = subprocess.run(
                full_command,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )
            return {
                'success': result.returncode == 0,
                'stdout': result.stdout,
                'stderr': result.stderr,
                'returncode': result.returncode
            }
        except subprocess.TimeoutExpired:
            return {
                'success': False,
                'stdout': '',
                'stderr': 'Command timed out',
                'returncode': -1
            }
        except Exception as e:
            return {
                'success': False,
                'stdout': '',
                'stderr': str(e),
                'returncode': -1
            }
    
    def get_devices(self, silent=False):
        """Get list of connected devices with model information
        
        Args:
            silent: If True, don't log debug output (for auto-refresh)
        """
        result = self.run_command('devices -l')
        
        # Log the raw output for debugging (only if not silent)
        if not silent and hasattr(self, 'log_callback'):
            # Only log stderr if it's not empty
            stderr_part = f"\nstderr: {result['stderr']}" if result.get('stderr', '').strip() else "\nstderr: (empty)"
            self.log_callback(f"ADB devices command output:\nstdout: {result['stdout']}{stderr_part}\nsuccess: {result['success']}", "DEBUG")
        
        if not result['success']:
            if hasattr(self, 'log_callback'):
                self.log_callback(f"ADB command failed: {result['stderr']}", "ERROR")
            return []
        
        devices = []
        output = result['stdout'].strip()
        if not output:
            return []
        
        lines = output.split('\n')
        # Skip header line (usually "List of devices attached")
        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
            
            # Handle both tab and space separated formats
            if '\t' in line:
                parts = line.split('\t', 1)
            elif ' ' in line:
                parts = line.split(' ', 1)
            else:
                # Just device ID, no status
                devices.append({'id': line, 'status': 'unknown', 'model': None, 'product': None})
                continue
            
            device_id = parts[0].strip()
            if device_id:
                rest = parts[1].strip() if len(parts) > 1 else ''
                status = rest.split()[0] if rest else 'unknown'
                
                # Parse model and product from -l output (e.g., "device product:mustang model:Pixel_10_Pro_XL")
                model = None
                product = None
                if 'model:' in rest:
                    try:
                        model_part = rest.split('model:')[1].split()[0]
                        model = model_part.replace('_', ' ')
                    except:
                        pass
                if 'product:' in rest:
                    try:
                        product_part = rest.split('product:')[1].split()[0]
                        product = product_part.replace('_', ' ')
                    except:
                        pass
                
                devices.append({
                    'id': device_id, 
                    'status': status,
                    'model': model,
                    'product': product
                })
        
        # For devices without model info from -l, try to get it via getprop
        for device in devices:
            if not device.get('model') and device['status'] == 'device':
                # Try to get model name
                model_result = self.run_command(f"-s {device['id']} shell getprop ro.product.model")
                if model_result['success'] and model_result['stdout'].strip():
                    device['model'] = model_result['stdout'].strip()
                
                # Also get manufacturer if model is available
                if device.get('model'):
                    mfr_result = self.run_command(f"-s {device['id']} shell getprop ro.product.manufacturer")
                    if mfr_result['success'] and mfr_result['stdout'].strip():
                        device['manufacturer'] = mfr_result['stdout'].strip()
        
        return devices
    
    def get_device_info(self, device_id):
        """Get device information"""
        info = {}
        commands = {
            'Model': 'shell getprop ro.product.model',
            'Manufacturer': 'shell getprop ro.product.manufacturer',
            'Android Version': 'shell getprop ro.build.version.release',
            'SDK Version': 'shell getprop ro.build.version.sdk',
            'Serial': 'shell getprop ro.serialno',
        }
        
        for key, cmd in commands.items():
            result = self.run_command(f'-s {device_id} {cmd}')
            if result['success']:
                info[key] = result['stdout'].strip()
            else:
                info[key] = 'N/A'
        
        return info


class ADBGUI(QMainWindow):
    """Main GUI Application"""
    
    # Signal for showing custom dialog (must be defined at class level)
    custom_dialog_ready = pyqtSignal(dict)
    app_list_ready = pyqtSignal(list)
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ADB Tool for Windows")
        self.setGeometry(100, 100, 1200, 800)
        self.setMinimumSize(1000, 700)
        
        # Color schemes
        self.light_colors = {
            'bg': '#f5f5f5',
            'fg': '#1f1f1f',
            'accent': '#0078d4',
            'accent_hover': '#106ebe',
            'success': '#107c10',
            'warning': '#ff8c00',
            'error': '#d13438',
            'card_bg': '#ffffff',
            'border': '#e1e1e1',
            'text_secondary': '#666666',
            'text_tertiary': '#999999',
        }
        
        self.dark_colors = {
            'bg': '#1e1e1e',
            'fg': '#e0e0e0',
            'accent': '#0078d4',
            'accent_hover': '#106ebe',
            'success': '#4ec9b0',
            'warning': '#ffaa44',
            'error': '#f48771',
            'card_bg': '#252526',
            'border': '#3e3e42',
            'text_secondary': '#cccccc',
            'text_tertiary': '#858585',
        }
        
        # Current color scheme (will be set by apply_theme)
        self.colors = self.light_colors.copy()
        
        # Get project directory - executable's directory if running as exe, script directory if from source
        if getattr(sys, 'frozen', False):
            # Running as compiled executable
            project_dir = os.path.dirname(sys.executable)
        else:
            # Running as script
            project_dir = os.path.dirname(os.path.abspath(__file__))
        
        # DeGoogle state storage
        self.degoogle_state_file = os.path.join(project_dir, 'degoogle_state.json')
        self.degoogle_state = self.load_degoogle_state()
        
        # Settings storage
        self.settings_file = os.path.join(project_dir, 'settings.json')
        self.settings = self.load_settings()
        
        # Load dark mode preference
        self.dark_mode = self.settings.get('dark_mode', False)
        
        # Apply theme based on preference
        self.apply_theme()
        
        # Check for saved ADB path in settings
        saved_adb_path = self.settings.get('adb_path', None)
        
        # If no saved path, prompt user to select it before creating ADBManager
        if not saved_adb_path or not os.path.exists(saved_adb_path):
            # Show dialog to select ADB path on first boot
            QMessageBox.information(
                self,
                "ADB Path Required",
                "Please select the ADB executable (adb.exe) to continue.\n\n"
                "This is typically located in the 'platform-tools' folder of your Android SDK."
            )
            
            # Prompt user to select ADB folder or executable
            adb_path = self.prompt_for_adb_path()
            if not adb_path:
                # User cancelled - use fallback
                QMessageBox.warning(
                    self,
                    "ADB Path Required",
                    "ADB path is required. The application will use 'adb' from PATH as fallback.\n\n"
                    "You can set the ADB path later using the 'ADB Path' button."
                )
                saved_adb_path = 'adb'  # Fallback
            else:
                # Save the selected path
                self.settings['adb_path'] = adb_path
                self.save_settings()
                saved_adb_path = adb_path
        
        # Create ADBManager with saved path
        self.adb = ADBManager(adb_path=saved_adb_path)
        # Set up logging callback for ADB manager
        self.adb.log_callback = self.log
        self.current_device = None
        self.log_thread = None
        self.log_running = False
        
        self.setup_ui()
        self.update_adb_path_display()
        self.refresh_devices()
        
        # Auto-refresh devices every 5 seconds (silent mode to avoid log spam)
        self.auto_refresh_timer = QTimer()
        self.auto_refresh_timer.timeout.connect(lambda: self.refresh_devices(silent=True))
        self.auto_refresh_timer.start(5000)
        
        # Connect signal for custom dialog
        self.custom_dialog_ready.connect(self._show_custom_dialog)
        # Connect signal for app list dialog
        self.app_list_ready.connect(self.show_app_list_window)
    
    def setup_ui(self):
        """Setup the modern user interface"""
        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Main layout
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(15)
        
        # Header with title
        header_layout = QHBoxLayout()
        self.title_label = QLabel("ADB Tool")
        self.title_label.setFont(QFont('Segoe UI', 20, QFont.Weight.Bold))
        self.title_label.setStyleSheet(f"color: {self.colors['fg']};")
        header_layout.addWidget(self.title_label)
        
        self.subtitle_label = QLabel("Android Device Manager")
        self.subtitle_label.setFont(QFont('Segoe UI', 10))
        self.subtitle_label.setStyleSheet(f"color: {self.colors['text_secondary']};")
        header_layout.addWidget(self.subtitle_label)
        header_layout.addStretch()
        
        # Dark mode toggle button
        self.dark_mode_btn = QPushButton("🌙 Dark Mode" if not self.dark_mode else "☀️ Light Mode")
        self.dark_mode_btn.setMaximumWidth(120)
        self.dark_mode_btn.clicked.connect(self.toggle_dark_mode)
        header_layout.addWidget(self.dark_mode_btn)
        
        main_layout.addLayout(header_layout)
        
        # Device selection card
        device_group = QGroupBox("📱 Device Management")
        # Styles are applied globally via apply_theme
        device_layout = QVBoxLayout(device_group)
        device_layout.setSpacing(10)
        
        # Device selection row
        device_row = QHBoxLayout()
        device_row.addWidget(QLabel("Connected Devices:"))
        
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(400)
        self.device_combo.currentTextChanged.connect(self.on_device_selected)
        device_row.addWidget(self.device_combo)
        
        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self.refresh_devices)
        device_row.addWidget(refresh_btn)
        
        info_btn = QPushButton("ℹ️ Info")
        info_btn.clicked.connect(self.show_device_info)
        device_row.addWidget(info_btn)
        
        path_btn = QPushButton("📂 ADB Path")
        path_btn.clicked.connect(self.set_adb_path_dialog)
        device_row.addWidget(path_btn)
        
        test_btn = QPushButton("✓ Test")
        test_btn.clicked.connect(self.test_adb)
        device_row.addWidget(test_btn)
        device_layout.addLayout(device_row)
        
        # Device status row
        status_row = QHBoxLayout()
        self.device_info_label = QLabel("No device selected")
        self.device_info_label.setStyleSheet(f"color: {self.colors['text_secondary']};")
        status_row.addWidget(self.device_info_label)
        
        self.adb_path_label = QLabel("ADB: Checking...")
        self.adb_path_label.setStyleSheet(f"color: {self.colors['text_tertiary']};")
        status_row.addWidget(self.adb_path_label)
        status_row.addStretch()
        device_layout.addLayout(status_row)
        
        main_layout.addWidget(device_group)
        
        # Main content area (operations + logs side by side)
        content_layout = QHBoxLayout()
        content_layout.setSpacing(7)
        
        # Left side - Operations (scrollable)
        ops_scroll = QScrollArea()
        ops_scroll.setWidgetResizable(True)
        ops_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        ops_widget = QWidget()
        ops_layout = QVBoxLayout(ops_widget)
        ops_layout.setSpacing(12)
        
        # File operations
        file_group = self.create_card("📁 File Transfer")
        self.create_button(file_group, "🗂️ Open File Explorer", self.open_file_explorer, accent=True)
        explorer_help = QLabel(
            "Browse PC ↔ device side-by-side, navigate folders, and drag files "
            "either direction (or in/out of Windows Explorer) to push or pull."
        )
        explorer_help.setWordWrap(True)
        explorer_help.setStyleSheet(f"color: {self.colors['text_secondary']}; font-size: 8pt;")
        file_group.layout().addWidget(explorer_help)
        ops_layout.addWidget(file_group)
        
        # App operations
        app_group = self.create_card("📱 App Management")
        self.create_button(app_group, "📦 Install APK", self.install_apk)
        self.create_button(app_group, "🗑️ Uninstall App", self.uninstall_app)
        self.create_button(app_group, "♻️ Reinstall for User", self.reinstall_for_user)
        self.create_button(app_group, "📋 List Installed Apps", self.list_apps)
        self.create_button(app_group, "📂 Open APKs Folder", self.open_apks_folder)
        
        # Separator
        self.separator = QFrame()
        self.separator.setFrameShape(QFrame.Shape.HLine)
        self.separator.setStyleSheet(f"color: {self.colors['border']};")
        app_group.layout().addWidget(self.separator)
        
        self.create_button(app_group, "🚫 DeGoogle Device", self.degoogle_device, accent=True)
        self.create_button(app_group, "↩️ Undo DeGoogle", self.undo_degoogle)
        ops_layout.addWidget(app_group)
        
        # Device operations
        device_ops_group = self.create_card("⚡ Device Operations")
        self.create_button(device_ops_group, "📸 Take Screenshot", self.take_screenshot)
        self.create_button(device_ops_group, "🔄 Reboot Device", self.reboot_device)
        self.create_button(device_ops_group, "🔧 Reboot to Recovery", self.reboot_recovery)
        self.create_button(device_ops_group, "⚙️ Reboot to Bootloader", self.reboot_bootloader)
        ops_layout.addWidget(device_ops_group)
        
        # Shell operations
        shell_group = self.create_card("💻 Shell Commands")
        shell_group.layout().addWidget(QLabel("Run commands ON YOUR ANDROID DEVICE (not Windows):"))
        help_text = ("⚠️ These commands run on your Android device (Linux), not on Windows!\n\n"
                    "Examples: 'ls /sdcard', 'pm list packages', 'dumpsys battery | grep level'\n"
                    "Use Linux commands: 'grep' (not 'findstr'), 'ls' (not 'dir'), 'cat' (not 'type')\n\n"
                    "Note: You can include 'adb shell' prefix, but it's not required (auto-stripped)")
        self.shell_help_label = QLabel(help_text)
        self.shell_help_label.setStyleSheet(f"color: {self.colors['text_secondary']}; font-size: 8pt;")
        self.shell_help_label.setWordWrap(True)
        shell_group.layout().addWidget(self.shell_help_label)
        self.shell_entry = QTextEdit()
        self.shell_entry.setMaximumHeight(100)
        self.shell_entry.setMinimumHeight(80)
        self.shell_entry.setStyleSheet("padding: 6px; font-size: 10pt;")
        self.shell_entry.setPlaceholderText("Enter Android shell command (e.g., 'ls /sdcard' or 'adb shell pm list packages')\nYou can enter multi-line commands here...")
        # QTextEdit doesn't have returnPressed, so we'll use Ctrl+Enter or just the button
        shell_group.layout().addWidget(self.shell_entry)
        self.create_button(shell_group, "▶️ Run Command", self.run_shell_command, accent=True)
        ops_layout.addWidget(shell_group)
        
        ops_layout.addStretch()
        ops_scroll.setWidget(ops_widget)
        content_layout.addWidget(ops_scroll, 1)
        
        # Right side - Logs
        self.logs_group = QGroupBox("📊 Logs & Output")
        # Styles are applied globally via apply_theme
        logs_layout = QVBoxLayout(self.logs_group)
        
        # Logcat controls
        log_controls = QHBoxLayout()
        self.log_button = QPushButton("▶️ Start Logcat")
        self.log_button.clicked.connect(self.toggle_logcat)
        log_controls.addWidget(self.log_button)
        
        clear_btn = QPushButton("🗑️ Clear")
        clear_btn.clicked.connect(self.clear_output)
        log_controls.addWidget(clear_btn)
        log_controls.addStretch()
        logs_layout.addLayout(log_controls)
        
        # Output text area
        self.output_text = QTextEdit()
        self.output_text.setReadOnly(True)
        self.output_text.setFont(QFont('Consolas', 9))
        logs_layout.addWidget(self.output_text)
        
        content_layout.addWidget(self.logs_group, 2)
        main_layout.addLayout(content_layout, 1)
        
        # Status bar
        self.status_bar = QLabel("Ready")
        self.status_bar.setStyleSheet(f"""
            background-color: {self.colors['card_bg']};
            border: 1px solid {self.colors['border']};
            padding: 8px 15px;
            color: {self.colors['text_secondary']};
        """)
        main_layout.addWidget(self.status_bar)
    
    def create_card(self, title):
        """Create a modern card container"""
        group = QGroupBox(title)
        # Styles are applied globally via apply_theme, no need for individual stylesheet
        layout = QVBoxLayout(group)
        layout.setContentsMargins(15, 20, 15, 15)
        layout.setSpacing(4)
        return group
    
    def create_button(self, parent, text, command, accent=False):
        """Create a modern button"""
        btn = QPushButton(text)
        btn.clicked.connect(command)
        if accent:
            btn.setProperty("accent", "true")
        parent.layout().addWidget(btn)
        return btn
    
    def log(self, message, level="INFO"):
        """Add message to output"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.output_text.append(f"[{timestamp}] [{level}] {message}")
        # Auto-scroll to bottom
        scrollbar = self.output_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
    
    def clear_output(self):
        """Clear output text"""
        self.output_text.clear()
    
    def update_status(self, message):
        """Update status bar"""
        self.status_bar.setText(message)
    
    def update_adb_path_display(self):
        """Update ADB path display label"""
        if os.path.exists(self.adb.adb_path):
            self.adb_path_label.setText(f"✓ ADB: {self.adb.adb_path}")
            self.adb_path_label.setStyleSheet(f"color: {self.colors['success']};")
        else:
            self.adb_path_label.setText("✗ ADB: Not found - Click 'ADB Path' to configure")
            self.adb_path_label.setStyleSheet(f"color: {self.colors['error']};")
    
    def refresh_devices(self, silent=False):
        """Refresh list of connected devices
        
        Args:
            silent: If True, don't log routine refresh messages (for auto-refresh)
        """
        if not silent:
            self.update_status("Refreshing devices...")
        
        # Test ADB connection first
        test_result = self.adb.run_command('version')
        if not test_result['success']:
            error_msg = test_result['stderr'] if test_result['stderr'] else "Unknown error"
            self.log(f"ADB test failed: {error_msg}", "ERROR")
            self.log(f"ADB path: {self.adb.adb_path}", "ERROR")
            self.update_status(f"ADB error: {error_msg[:50]}")
            self.device_info_label.setText(f"ADB Error: {error_msg[:100]}")
            self.device_info_label.setStyleSheet(f"color: {self.colors['error']};")
            return
        
        devices = self.adb.get_devices(silent=silent)
        
        # Get current device list for comparison
        current_device_ids = set()
        if hasattr(self, 'device_display_map'):
            current_device_ids = set(self.device_display_map.values())
        
        if devices:
            # Create display strings with device name/model
            device_list = []
            device_display_map = {}  # Map display string to device ID
            new_device_ids = set()
            
            for d in devices:
                device_id = d['id']
                new_device_ids.add(device_id)
                model = d.get('model')
                manufacturer = d.get('manufacturer', '')
                product = d.get('product')
                
                # Build display name
                if model:
                    if manufacturer:
                        display_name = f"{manufacturer} {model}"
                    else:
                        display_name = model
                elif product:
                    display_name = product.replace('_', ' ').title()
                else:
                    display_name = "Unknown Device"
                
                # Format: "Device Name (ID)"
                display_str = f"{display_name} ({device_id})"
                device_list.append(display_str)
                device_display_map[display_str] = device_id
            
            # Only log if device list changed
            devices_changed = current_device_ids != new_device_ids
            
            # Disconnect signal before modifying combo box to prevent unwanted triggers
            self.device_combo.currentTextChanged.disconnect()
            
            self.device_combo.clear()
            self.device_combo.addItems(device_list)
            self.device_display_map = device_display_map  # Store mapping for selection
            
            # Only auto-select if no device is currently selected
            was_no_device = not self.current_device
            if was_no_device and device_list:
                self.device_combo.setCurrentIndex(0)
                # Call on_device_selected directly with silent parameter (signal is disconnected so won't trigger)
                self.on_device_selected(silent=silent)  # Use silent parameter from refresh_devices
            elif self.current_device and device_list:
                # Device is already selected - just update the combo box index if needed
                # Find the current device in the new list
                current_display = None
                for display_str, device_id in device_display_map.items():
                    if device_id == self.current_device:
                        current_display = display_str
                        break
                
                if current_display:
                    index = self.device_combo.findText(current_display)
                    if index >= 0:
                        self.device_combo.setCurrentIndex(index)
                # Don't call on_device_selected when device is already selected (avoids redundant get_devices call)
            
            # Reconnect signal after all combo box operations are complete
            self.device_combo.currentTextChanged.connect(self.on_device_selected)
            
            if not silent or devices_changed:
                self.update_status(f"Found {len(devices)} device(s)")
                if devices_changed:
                    # Log with device names only when list changes
                    device_names = [f"{d.get('model', d.get('product', 'Unknown'))} ({d['id']})" for d in devices]
                    self.log(f"Found {len(devices)} device(s): {', '.join(device_names)}")
        else:
            had_devices = hasattr(self, 'device_display_map') and len(self.device_display_map) > 0
            self.device_combo.clear()
            self.current_device = None
            self.device_info_label.setText("No devices connected - Check USB connection and USB debugging")
            self.device_info_label.setStyleSheet(f"color: {self.colors['warning']};")
            if not silent or had_devices:
                self.update_status("No devices found")
                if had_devices:
                    self.log("No devices found. Make sure USB debugging is enabled and device is connected.", "WARNING")
    
    def on_device_selected(self, selection=None, silent=False):
        """Handle device selection
        
        Args:
            selection: Device selection string (if None, uses current combo selection)
            silent: If True, don't log the selection (for auto-refresh)
        """
        if selection is None:
            selection = self.device_combo.currentText()
        
        if selection:
            # Extract device ID from display string using the mapping
            if hasattr(self, 'device_display_map') and selection in self.device_display_map:
                self.current_device = self.device_display_map[selection]
            else:
                # Fallback: try to extract from parentheses
                if '(' in selection and ')' in selection:
                    self.current_device = selection.split('(')[1].split(')')[0].strip()
                else:
                    self.current_device = selection.split()[0]
            
            # Get device info for display
            # In silent mode, skip get_devices call to avoid redundant logging
            if silent:
                # In silent mode, just use the device ID we already have
                # Don't call get_devices to avoid logging
                device_info = None
                # Set a simple display text without calling get_devices
                display_text = f"Selected: {self.current_device}"
            else:
                # Not in silent mode, get full device info
                devices = self.adb.get_devices(silent=silent)
                device_info = next((d for d in devices if d['id'] == self.current_device), None)
            
            if device_info:
                model = device_info.get('model', 'Unknown')
                manufacturer = device_info.get('manufacturer', '')
                if manufacturer:
                    display_text = f"Selected: {manufacturer} {model} ({self.current_device})"
                else:
                    display_text = f"Selected: {model} ({self.current_device})"
            else:
                display_text = f"Selected: {self.current_device}"
            
            # Only update UI and log if not in silent mode (for auto-refresh)
            if not silent:
                self.device_info_label.setText(display_text)
                self.device_info_label.setStyleSheet(f"color: {self.colors['success']};")
                self.log(f"Selected device: {display_text}")
            # In silent mode, only update the label if it's not already set correctly
            elif not hasattr(self, 'device_info_label') or self.device_info_label.text() != display_text:
                self.device_info_label.setText(display_text)
                self.device_info_label.setStyleSheet(f"color: {self.colors['success']};")
        else:
            self.current_device = None
    
    def show_device_info(self):
        """Show detailed device information"""
        if not self.current_device:
            QMessageBox.warning(self, "No Device", "Please select a device first")
            return
        
        info = self.adb.get_device_info(self.current_device)
        info_text = "\n".join([f"{k}: {v}" for k, v in info.items()])
        QMessageBox.information(self, "Device Information", info_text)
    
    def test_adb(self):
        """Test ADB connection and show detailed output"""
        self.log("Testing ADB connection...", "INFO")
        self.update_status("Testing ADB...")
        
        # Test version
        version_result = self.adb.run_command('version')
        self.log(f"ADB Version Command:\nSuccess: {version_result['success']}\nReturn Code: {version_result['returncode']}", "DEBUG")
        if version_result['stdout']:
            self.log(f"Version Output:\n{version_result['stdout']}", "INFO")
        if version_result['stderr'] and version_result['stderr'].strip():
            self.log(f"Version Error:\n{version_result['stderr']}", "ERROR")
        
        # Test devices
        devices_result = self.adb.run_command('devices -l')
        self.log(f"ADB Devices Command:\nSuccess: {devices_result['success']}\nReturn Code: {devices_result['returncode']}", "DEBUG")
        if devices_result['stdout']:
            self.log(f"Devices Output:\n{devices_result['stdout']}", "INFO")
        if devices_result['stderr'] and devices_result['stderr'].strip():
            self.log(f"Devices Error:\n{devices_result['stderr']}", "ERROR")
        
        # Show summary
        if version_result['success']:
            self.update_status("ADB is working correctly")
            QMessageBox.information(
                self,
                "ADB Test",
                f"ADB Path: {self.adb.adb_path}\n\n"
                f"Version: {'✓ Working' if version_result['success'] else '✗ Failed'}\n"
                f"Devices: {'✓ Working' if devices_result['success'] else '✗ Failed'}\n\n"
                f"Check the output log for details."
            )
        else:
            self.update_status("ADB test failed - check output log")
            QMessageBox.critical(
                self,
                "ADB Test Failed",
                f"ADB Path: {self.adb.adb_path}\n\n"
                f"Error: {version_result['stderr'] or 'Unknown error'}\n\n"
                f"Please check:\n"
                f"1. ADB path is correct\n"
                f"2. ADB executable exists\n"
                f"3. Check output log for details"
            )
    
    def prompt_for_adb_path(self):
        """Prompt user to select ADB folder or executable (used on first boot)"""
        initial_dir = os.path.expanduser('~')
        
        # First, try folder selection (most common use case)
        folder_path = QFileDialog.getExistingDirectory(
            self,
            "Select platform-tools folder (contains adb.exe)",
            initial_dir
        )
        
        if folder_path:
            adb_exe = os.path.join(folder_path, 'adb.exe')
            if os.path.exists(adb_exe):
                return adb_exe
            else:
                QMessageBox.warning(self, "Error", f"adb.exe not found in:\n{folder_path}\n\nPlease select the folder that contains adb.exe")
                return None
        
        # Allow file selection as alternative
        adb_path, _ = QFileDialog.getOpenFileName(
            self,
            "Or select ADB executable (adb.exe) directly",
            initial_dir,
            "Executable files (*.exe);;All files (*.*)"
        )
        
        if adb_path:
            if os.path.basename(adb_path).lower() == 'adb.exe':
                return adb_path
            else:
                QMessageBox.warning(self, "Warning", "Please select adb.exe file")
                return None
        
        return None
    
    def set_adb_path_dialog(self):
        """Open dialog to set ADB path"""
        # Get initial directory from saved path or use home directory
        saved_path = self.settings.get('adb_path', '')
        if saved_path and os.path.exists(saved_path):
            if os.path.isfile(saved_path):
                initial_dir = os.path.dirname(saved_path)
            else:
                initial_dir = saved_path
        else:
            initial_dir = os.path.expanduser('~')
        
        # First, try folder selection (most common use case)
        folder_path = QFileDialog.getExistingDirectory(
            self,
            "Select platform-tools folder (contains adb.exe)",
            initial_dir
        )
        
        if folder_path:
            adb_exe = os.path.join(folder_path, 'adb.exe')
            if os.path.exists(adb_exe):
                if self.adb.set_adb_path(adb_exe):
                    # Save to settings
                    self.settings['adb_path'] = adb_exe
                    self.save_settings()
                    
                    self.adb_path_label.setText(f"✓ ADB: {adb_exe}")
                    self.adb_path_label.setStyleSheet(f"color: {self.colors['success']};")
                    self.log(f"ADB path set to: {adb_exe}")
                    self.update_status("ADB path updated successfully")
                    QMessageBox.information(self, "Success", f"ADB path set to:\n{adb_exe}")
                    # Refresh devices to test the new path
                    self.refresh_devices()
                else:
                    QMessageBox.critical(self, "Error", "Failed to set ADB path")
            else:
                QMessageBox.warning(self, "Error", f"adb.exe not found in:\n{folder_path}\n\nPlease select the folder that contains adb.exe")
        else:
            # Allow file selection as alternative
            adb_path, _ = QFileDialog.getOpenFileName(
                self,
                "Or select ADB executable (adb.exe) directly",
                initial_dir,
                "Executable files (*.exe);;All files (*.*)"
            )
            
            if adb_path:
                if os.path.basename(adb_path).lower() == 'adb.exe':
                    if self.adb.set_adb_path(adb_path):
                        # Save to settings
                        self.settings['adb_path'] = adb_path
                        self.save_settings()
                        
                        self.adb_path_label.setText(f"✓ ADB: {adb_path}")
                        self.adb_path_label.setStyleSheet(f"color: {self.colors['success']};")
                        self.log(f"ADB path set to: {adb_path}")
                        self.update_status("ADB path updated successfully")
                        QMessageBox.information(self, "Success", f"ADB path set to:\n{adb_path}")
                        # Refresh devices to test the new path
                        self.refresh_devices()
                    else:
                        QMessageBox.critical(self, "Error", "Failed to set ADB path")
                else:
                    QMessageBox.warning(self, "Warning", "Please select adb.exe file")
    
    def get_device_flag(self):
        """Get device flag for ADB commands"""
        return f"-s {self.current_device}" if self.current_device else ""
    
    def open_file_explorer(self):
        """Open the side-by-side PC ↔ Android file explorer."""
        if not self.current_device:
            QMessageBox.warning(self, "No Device", "Please select a device first")
            return
        # Reuse a single non-modal explorer instance per main window so the
        # user can leave it open while running other ADB operations.
        existing = getattr(self, '_file_explorer', None)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            existing.refresh_remote()
            return
        explorer = FileExplorerDialog(self)
        self._file_explorer = explorer
        explorer.show()

    def install_apk(self):
        """Install APK file"""
        if not self.current_device:
            QMessageBox.warning(self, "No Device", "Please select a device first")
            return
        
        apk_path, _ = QFileDialog.getOpenFileName(self, "Select APK file", "", "APK files (*.apk);;All files (*.*)")
        if not apk_path:
            return
        
        self.log(f"Installing {apk_path}...")
        self.update_status("Installing APK...")
        
        def do_install():
            result = self.adb.run_command(f'{self.get_device_flag()} install "{apk_path}"', timeout=120)
            if result['success']:
                self.log("APK installed successfully")
                self.update_status("APK installed successfully")
                QMessageBox.information(self, "Success", "APK installed successfully")
            else:
                self.log(f"Error: {result['stderr']}", "ERROR")
                self.update_status("Failed to install APK")
                QMessageBox.critical(self, "Error", f"Failed to install APK:\n{result['stderr']}")
        
        threading.Thread(target=do_install, daemon=True).start()
    
    def uninstall_app(self):
        """Uninstall app"""
        if not self.current_device:
            QMessageBox.warning(self, "No Device", "Please select a device first")
            return
        
        package_name, ok = QInputDialog.getText(self, "Uninstall App", "Enter package name (e.g., com.example.app):")
        if not ok or not package_name:
            return
        
        reply = QMessageBox.question(self, "Confirm", f"Uninstall {package_name}?", 
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        self.log(f"Uninstalling {package_name}...")
        self.update_status("Uninstalling app...")
        
        def do_uninstall():
            result = self.adb.run_command(f"{self.get_device_flag()} uninstall {package_name}")
            if result['success']:
                # Check if stdout contains success message
                output = result['stdout'].strip() if result['stdout'] else ''
                if 'Success' in output or 'success' in output.lower():
                    self.log("App uninstalled successfully")
                    self.update_status("App uninstalled successfully")
                    # Thread-safe messagebox - use QTimer to call from main thread
                    QTimer.singleShot(0, lambda: QMessageBox.information(self, "Success", "App uninstalled successfully"))
                else:
                    # Sometimes ADB returns success but stdout has info
                    self.log(f"Uninstall result: {output}")
                    self.update_status("Uninstall completed")
                    # Thread-safe messagebox - use QTimer to call from main thread
                    QTimer.singleShot(0, lambda: QMessageBox.information(self, "Success", f"Uninstall completed:\n{output}"))
            else:
                # Get error from stderr or stdout
                error_msg = result['stderr'] if result['stderr'] else result['stdout']
                if not error_msg or error_msg.strip() == '':
                    error_msg = "Unknown error"
                
                self.log(f"Regular uninstall failed: {error_msg}", "WARNING")
                
                # Try uninstalling for current user (works for system apps without root)
                self.log("Attempting to uninstall for current user (--user 0)...")
                result_user = self.adb.run_command(f"{self.get_device_flag()} shell pm uninstall --user 0 {package_name}")
                
                if result_user['success']:
                    output = result_user['stdout'].strip() if result_user['stdout'] else ''
                    if 'Success' in output or 'success' in output.lower() or output == '':
                        self.log("App uninstalled for current user successfully")
                        self.update_status("App uninstalled for current user")
                        # Thread-safe messagebox - use QTimer to call from main thread
                        QTimer.singleShot(0, lambda: QMessageBox.information(self, "Success", f"App uninstalled for current user successfully!\n\nNote: System apps are only removed for your user account, not from the device."))
                    else:
                        self.log(f"Uninstall result: {output}")
                        self.update_status("Uninstall completed")
                        # Thread-safe messagebox - use QTimer to call from main thread
                        QTimer.singleShot(0, lambda: QMessageBox.information(self, "Success", f"Uninstall completed:\n{output}"))
                else:
                    # Both methods failed
                    error_msg_user = result_user['stderr'] if result_user['stderr'] else result_user['stdout']
                    self.log(f"Error: {error_msg}", "ERROR")
                    self.log(f"User uninstall also failed: {error_msg_user}", "ERROR")
                    self.log(f"Return code: {result['returncode']}", "ERROR")
                    self.log(f"Full stdout: {result['stdout']}", "DEBUG")
                    self.log(f"Full stderr: {result['stderr']}", "DEBUG")
                    self.update_status("Failed to uninstall app")
                    
                    # Provide helpful message
                    if 'DELETE_FAILED_INTERNAL_ERROR' in error_msg or 'system app' in error_msg.lower() or 'package is a system package' in error_msg.lower():
                        help_text = f"Failed to uninstall {package_name}:\n\n{error_msg}\n\nTried both regular and user uninstall methods.\nYou can try disabling it instead (use 'Disable Selected')."
                    else:
                        help_text = f"Failed to uninstall {package_name}:\n\n{error_msg}"
                    
                    # Thread-safe messagebox - use QTimer to call from main thread
                    QTimer.singleShot(0, lambda: QMessageBox.critical(self, "Error", help_text))
        
        threading.Thread(target=do_uninstall, daemon=True).start()
    
    def get_app_label(self, package_name):
        """Get app label/name for a package"""
        # Method 1: Try using pm dump (faster and cleaner output)
        result = self.adb.run_command(f"{self.get_device_flag()} shell pm dump {package_name}")
        if result['success'] and result['stdout']:
            output = result['stdout']
            # Look for applicationLabel in pm dump output
            for line in output.split('\n'):
                line_lower = line.lower().strip()
                if 'applicationlabel=' in line_lower:
                    # Extract label - format is usually "applicationLabel=Label Name"
                    parts = line.split('=', 1)
                    if len(parts) == 2:
                        label = parts[1].strip()
                        # Clean up label - remove any trailing info
                        if label and label.lower() != 'null' and label != package_name:
                            # Remove resource IDs if present
                            if not label.startswith('res/') and not label.startswith('0x'):
                                return label
        
        # Method 2: Use dumpsys package (more detailed but slower)
        result = self.adb.run_command(f"{self.get_device_flag()} shell dumpsys package {package_name}")
        if result['success'] and result['stdout']:
            output = result['stdout']
            in_application_section = False
            
            # Try multiple patterns
            for line in output.split('\n'):
                line_stripped = line.strip()
                line_lower = line_stripped.lower()
                
                # Track if we're in the Application section
                if 'application {' in line_lower or 'application:' in line_lower:
                    in_application_section = True
                elif line_stripped.startswith('}') and in_application_section:
                    in_application_section = False
                
                # Pattern 1: applicationLabel=Label (most common)
                if 'applicationlabel=' in line_lower:
                    # Handle both "applicationLabel=Label" and "applicationLabel Label"
                    if '=' in line:
                        parts = line.split('=', 1)
                        if len(parts) == 2:
                            label = parts[1].strip()
                            # Remove resource references
                            if label.startswith('res/') or label.startswith('0x'):
                                continue
                            # Remove any trailing comments or extra info
                            if ' ' in label:
                                # Take first word if it looks like a resource ID
                                first_word = label.split()[0]
                                if not first_word.startswith('res/') and not first_word.startswith('0x'):
                                    label = first_word
                            if label and label.lower() != 'null' and label != package_name:
                                return label
                    elif 'applicationlabel' in line_lower:
                        # Format: "applicationLabel Label Name"
                        parts = line.split(None, 1)
                        if len(parts) == 2:
                            label = parts[1].strip()
                            if label and label.lower() != 'null' and label != package_name:
                                return label
                
                # Pattern 2: Look for labelRes or label in ApplicationInfo
                if in_application_section:
                    if 'label=' in line_lower and 'labelres=' not in line_lower:
                        parts = line.split('=', 1)
                        if len(parts) == 2:
                            label = parts[1].strip()
                            # Remove resource references like "res/0x7f0a0001"
                            if label.startswith('res/') or label.startswith('0x'):
                                continue
                            if label and label.lower() != 'null' and label != package_name:
                                return label
        
        # Last resort - return None to use package name as fallback
        # Note: If labels aren't showing, check the log output to see what dumpsys/pm dump returns
        return None
    
    def reinstall_for_user(self):
        """Reinstall app for current user (for apps uninstalled with --user 0)"""
        if not self.current_device:
            QMessageBox.warning(self, "No Device", "Please select a device first")
            return
        
        self.log("Searching for apps...")
        self.update_status("Loading apps...")
        
        def load_apps():
            # Get all packages (including uninstalled for user)
            # Try to get uninstalled packages first, then fall back to all packages
            result = self.adb.run_command(f"{self.get_device_flag()} shell pm list packages -u")
            if not result['success']:
                # Fall back to all packages
                result = self.adb.run_command(f"{self.get_device_flag()} shell pm list packages")
            
            if not result['success']:
                self.log(f"Error: {result['stderr']}", "ERROR")
                # Thread-safe messagebox - use QTimer to call from main thread
                QTimer.singleShot(0, lambda: QMessageBox.critical(self, "Error", f"Failed to list packages:\n{result['stderr']}"))
                return
            
            packages = result['stdout'].strip().split('\n')
            packages = [p.replace('package:', '').strip() for p in packages if p.strip()]
            
            self.log(f"Found {len(packages)} packages. Getting app names...")
            
            # Get app labels (cache them)
            app_data = {}  # {package_name: (label, package_name)}
            
            # Get labels in batches to avoid too many calls
            for i, package in enumerate(packages):
                if i % 10 == 0:
                    self.log(f"Processing packages {i}/{len(packages)}...")
                
                label = self.get_app_label(package)
                if label:
                    app_data[package] = (label, package)
                else:
                    # Use package name as fallback
                    app_data[package] = (package, package)
            
            self.log(f"Loaded {len(app_data)} apps")
            QTimer.singleShot(0, lambda: self.show_app_search_dialog(app_data))
        
        threading.Thread(target=load_apps, daemon=True).start()
    
    def show_app_search_dialog(self, app_data):
        """Show searchable dialog to select app by name"""
        search_window = QDialog(self)
        search_window.setWindowTitle("Search App to Reinstall")
        search_window.setMinimumSize(600, 500)
        
        layout = QVBoxLayout(search_window)
        layout.setSpacing(10)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # Search label and entry
        search_label = QLabel("Search by app name (e.g., 'youtube' or 'YouTube'):")
        layout.addWidget(search_label)
        
        search_entry = QLineEdit()
        search_entry.setPlaceholderText("Type to search...")
        layout.addWidget(search_entry)
        
        # List widget
        listbox = QListWidget()
        layout.addWidget(listbox)
        
        # Store app data
        search_window.app_data = app_data
        search_window.filtered_data = []
        
        def update_list():
            """Update listbox based on search"""
            search_term = search_entry.text().lower()
            listbox.clear()
            search_window.filtered_data = []
            
            if not search_term:
                # Show all apps
                for package, (label, pkg) in sorted(app_data.items(), key=lambda x: x[1][0].lower()):
                    display_text = f"{label} ({pkg})"
                    listbox.addItem(display_text)
                    search_window.filtered_data.append((label, pkg))
            else:
                # Filter by search term
                for package, (label, pkg) in sorted(app_data.items(), key=lambda x: x[1][0].lower()):
                    if search_term in label.lower() or search_term in pkg.lower():
                        display_text = f"{label} ({pkg})"
                        listbox.addItem(display_text)
                        search_window.filtered_data.append((label, pkg))
        
        def select_app():
            """Select app and reinstall"""
            current_item = listbox.currentItem()
            if not current_item:
                QMessageBox.warning(self, "No Selection", "Please select an app from the list")
                return
            
            idx = listbox.row(current_item)
            if idx < len(search_window.filtered_data):
                label, package_name = search_window.filtered_data[idx]
                
                reply = QMessageBox.question(self, "Confirm Reinstall", 
                                            f"Reinstall {label} ({package_name}) for current user?\n\nThis will restore apps that were uninstalled for your user account.",
                                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if reply != QMessageBox.StandardButton.Yes:
                    return
                
                search_window.accept()
                self._do_reinstall_for_user(package_name, label)
        
        search_entry.textChanged.connect(update_list)
        search_entry.returnPressed.connect(select_app)
        listbox.itemDoubleClicked.connect(lambda: select_app())
        
        # Buttons
        button_layout = QHBoxLayout()
        reinstall_btn = QPushButton("Reinstall Selected")
        reinstall_btn.clicked.connect(select_app)
        button_layout.addWidget(reinstall_btn)
        button_layout.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(search_window.reject)
        button_layout.addWidget(cancel_btn)
        layout.addLayout(button_layout)
        
        # Initial population
        update_list()
        search_entry.setFocus()
        search_window.exec()
    
    def _do_reinstall_for_user(self, package_name, app_label=None):
        """Internal function to perform reinstall"""
        display_name = app_label or package_name
        self.log(f"Reinstalling {display_name} ({package_name}) for current user...")
        self.update_status("Reinstalling app for user...")
        
        def do_reinstall():
            # Use pm install-existing to reinstall apps uninstalled for the user
            result = self.adb.run_command(f"{self.get_device_flag()} shell pm install-existing {package_name}")
            if result['success']:
                output = result['stdout'].strip() if result['stdout'] else ''
                if 'Success' in output or 'success' in output.lower() or 'Package' in output:
                    self.log("App reinstalled for current user successfully")
                    self.update_status("App reinstalled for current user")
                    # Thread-safe messagebox - use QTimer to call from main thread
                    QTimer.singleShot(0, lambda: QMessageBox.information(self, "Success", f"{display_name} reinstalled for current user successfully!\n\n{package_name} is now available again."))
                else:
                    self.log(f"Reinstall result: {output}")
                    self.update_status("Reinstall completed")
                    # Thread-safe messagebox - use QTimer to call from main thread
                    QTimer.singleShot(0, lambda: QMessageBox.information(self, "Success", f"Reinstall completed:\n{output}"))
            else:
                error_msg = result['stderr'] if result['stderr'] else result['stdout']
                if not error_msg or error_msg.strip() == '':
                    error_msg = "Unknown error"
                self.log(f"Error: {error_msg}", "ERROR")
                self.update_status("Failed to reinstall app")
                # Thread-safe messagebox - use QTimer to call from main thread
                QTimer.singleShot(0, lambda: QMessageBox.critical(self, "Error", f"Failed to reinstall {display_name}:\n\n{error_msg}\n\nNote: This only works for apps that were previously installed but uninstalled for your user account."))
        
        threading.Thread(target=do_reinstall, daemon=True).start()
    
    def open_apks_folder(self):
        """Open the APKs folder in file explorer"""
        # Get project directory - executable's directory if running as exe, script directory if from source
        if getattr(sys, 'frozen', False):
            # Running as compiled executable
            project_dir = os.path.dirname(sys.executable)
        else:
            # Running as script
            project_dir = os.path.dirname(os.path.abspath(__file__))
        apks_dir = os.path.join(project_dir, 'apks')
        os.makedirs(apks_dir, exist_ok=True)
        
        # Open folder in file explorer
        if sys.platform == 'win32':
            os.startfile(apks_dir)
        elif sys.platform == 'darwin':
            subprocess.run(['open', apks_dir])
        else:
            subprocess.run(['xdg-open', apks_dir])
        
        self.log(f"Opened APKs folder: {apks_dir}")
    
    def list_apps(self):
        """List installed apps with uninstall/reinstall options"""
        if not self.current_device:
            QMessageBox.warning(self, "No Device", "Please select a device first")
            return
        
        self.log("Fetching installed apps...")
        self.update_status("Fetching apps...")
        
        def do_list():
            result = self.adb.run_command(f"{self.get_device_flag()} shell pm list packages")
            if result['success']:
                apps = result['stdout'].strip().split('\n')
                apps = [app.replace('package:', '') for app in apps if app.strip()]
                self.log(f"Found {len(apps)} installed apps")
                self.update_status(f"Found {len(apps)} apps")
                
                # Show in an interactive window (thread-safe via signal)
                self.app_list_ready.emit(sorted(apps))
            else:
                error_msg = result.get('stderr', 'Unknown error')
                # Only log stderr if it's not empty and contains actual error info
                if error_msg and error_msg.strip() and error_msg.strip() != '':
                    self.log(f"Error listing apps: {error_msg}", "ERROR")
                self.update_status("Failed to list apps")
                QTimer.singleShot(0, lambda: QMessageBox.warning(self, "Error", f"Failed to list installed apps:\n{error_msg}"))
        
        threading.Thread(target=do_list, daemon=True).start()
    
    def show_app_list_window(self, apps):
        """Show interactive app list window with uninstall/reinstall buttons"""
        app_window = QDialog(self)
        app_window.setWindowTitle("Installed Apps")
        app_window.setMinimumSize(700, 500)
        app_window.setModal(True)
        
        layout = QVBoxLayout(app_window)
        layout.setSpacing(5)
        layout.setContentsMargins(5, 5, 5, 5)
        
        # Search frame
        search_layout = QHBoxLayout()
        search_label = QLabel("Search (by app name or package):")
        search_layout.addWidget(search_label)
        
        search_entry = QLineEdit()
        search_entry.setPlaceholderText("Type to search...")
        search_layout.addWidget(search_entry)
        
        # Filter checkbox
        filter_checkbox = QCheckBox("Show only disabled apps")
        search_layout.addWidget(filter_checkbox)
        layout.addLayout(search_layout)
        
        # List widget
        listbox = QListWidget()
        layout.addWidget(listbox)
        
        # Store original apps list in window attribute so refresh can access it
        app_window.original_apps = apps.copy()
        
        # Store app labels (package_name -> app_label)
        app_window.app_labels = {}
        
        # Store app status (enabled/disabled) - will be populated when checking status
        app_window.app_status = {}
        
        def check_app_status(package_name):
            """Check if app is disabled"""
            result = self.adb.run_command(f"{self.get_device_flag()} shell pm list packages -d {package_name}")
            return result['success'] and package_name in result['stdout']
        
        def update_list():
            """Update listbox based on search and filter"""
            search_term = search_entry.text().lower()
            filter_disabled = filter_checkbox.isChecked()
            listbox.clear()
            
            for app in app_window.original_apps:
                # Get app label (use package name as fallback)
                app_label = app_window.app_labels.get(app, app)
                
                # If label is same as package, just show package name (avoid "package (package)")
                if app_label == app:
                    display_label = app
                else:
                    display_label = f"{app_label} ({app})"
                
                # Check if app is disabled
                is_disabled = app_window.app_status.get(app, False)
                
                # Apply disabled filter
                if filter_disabled and not is_disabled:
                    continue
                
                # Check if search term matches app name or package name
                matches = False
                if not search_term:
                    matches = True
                elif search_term in app_label.lower() or search_term in app.lower():
                    matches = True
                
                if matches:
                    display_name = display_label
                    if is_disabled:
                        display_name += " [DISABLED]"
                    listbox.addItem(display_name)
        
        # Load app labels in background
        def load_app_labels():
            """Load app labels for all apps"""
            self.log("Loading app names...")
            labels_found = 0
            for i, package in enumerate(apps):
                if i % 20 == 0:
                    self.log(f"Loading app names {i}/{len(apps)}...")
                label = self.get_app_label(package)
                if label and label != package:
                    app_window.app_labels[package] = label
                    labels_found += 1
                    # Log first few successful extractions for debugging
                    if labels_found <= 3:
                        self.log(f"Found label for {package}: {label}", "DEBUG")
                else:
                    # Use package name as fallback
                    app_window.app_labels[package] = package
                    # Log first few failures for debugging
                    if i < 3:
                        self.log(f"Could not find label for {package}, using package name", "DEBUG")
            self.log(f"Loaded {len(app_window.app_labels)} app names ({labels_found} with custom labels)")
            if labels_found == 0:
                self.log("Warning: No app labels found. Labels may be stored as resource IDs.", "WARNING")
            QTimer.singleShot(0, lambda: update_list())
        
        search_entry.textChanged.connect(update_list)
        filter_checkbox.stateChanged.connect(lambda: update_list())
        
        # Start loading labels in background
        threading.Thread(target=load_app_labels, daemon=True).start()
        
        # Initial list (will show package names until labels load)
        update_list()
        
        # Buttons frame
        button_layout = QHBoxLayout()
        
        def get_selected_package():
            """Extract package name from listbox selection (handles app name and [DISABLED] marker)"""
            current_item = listbox.currentItem()
            if not current_item:
                return None
            display_text = current_item.text()
            # Remove [DISABLED] marker if present
            display_text = display_text.replace(' [DISABLED]', '').strip()
            # Extract package name from format "App Name (package.name)"
            if '(' in display_text and ')' in display_text:
                package_name = display_text.split('(')[-1].rstrip(')').strip()
                return package_name
            # Fallback: if no parentheses, assume it's just the package name
            return display_text
        
        def uninstall_selected():
            """Uninstall selected app"""
            package_name = get_selected_package()
            if not package_name:
                QMessageBox.warning(self, "No Selection", "Please select an app to uninstall")
                return
            app_label = app_window.app_labels.get(package_name, package_name)
            display_name = f"{app_label} ({package_name})" if app_label != package_name else package_name
            reply = QMessageBox.question(self, "Confirm Uninstall", f"Uninstall {display_name}?",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
            
            self.log(f"Uninstalling {package_name}...")
            self.update_status("Uninstalling app...")
            
            def do_uninstall():
                result = self.adb.run_command(f"{self.get_device_flag()} uninstall {package_name}")
                if result['success']:
                    # Check if stdout contains success message
                    output = result['stdout'].strip() if result['stdout'] else ''
                    if 'Success' in output or 'success' in output.lower() or output == '':
                        self.log("App uninstalled successfully")
                        self.update_status("App uninstalled successfully")
                        # Remove from the stored apps list
                        if package_name in app_window.original_apps:
                            app_window.original_apps.remove(package_name)
                        # Refresh the list
                        QTimer.singleShot(0, lambda: update_list())
                        # Thread-safe messagebox - use QTimer to call from main thread
                        QTimer.singleShot(0, lambda: QMessageBox.information(self, "Success", "App uninstalled successfully"))
                    else:
                        # Sometimes ADB returns success but stdout has info
                        self.log(f"Uninstall result: {output}")
                        self.update_status("Uninstall completed")
                        if package_name in app_window.original_apps:
                            app_window.original_apps.remove(package_name)
                        QTimer.singleShot(0, lambda: update_list())
                        # Thread-safe messagebox - use QTimer to call from main thread
                        QTimer.singleShot(0, lambda: QMessageBox.information(self, "Success", f"Uninstall completed:\n{output}"))
                else:
                    # Get error from stderr or stdout
                    error_msg = result['stderr'] if result['stderr'] else result['stdout']
                    if not error_msg or error_msg.strip() == '':
                        error_msg = "Unknown error"
                    
                    self.log(f"Regular uninstall failed: {error_msg}", "WARNING")
                    
                    # Try uninstalling for current user (works for system apps without root)
                    self.log("Attempting to uninstall for current user (--user 0)...")
                    result_user = self.adb.run_command(f"{self.get_device_flag()} shell pm uninstall --user 0 {package_name}")
                    
                    if result_user['success']:
                        output = result_user['stdout'].strip() if result_user['stdout'] else ''
                        if 'Success' in output or 'success' in output.lower() or output == '':
                            self.log("App uninstalled for current user successfully")
                            self.update_status("App uninstalled for current user")
                            # Thread-safe messagebox - use QTimer to call from main thread
                            QTimer.singleShot(0, lambda: QMessageBox.information(self, "Success", f"App uninstalled for current user successfully!\n\nNote: System apps are only removed for your user account, not from the device."))
                        else:
                            self.log(f"Uninstall result: {output}")
                            self.update_status("Uninstall completed")
                            # Thread-safe messagebox - use QTimer to call from main thread
                            QTimer.singleShot(0, lambda: QMessageBox.information(self, "Success", f"Uninstall completed:\n{output}"))
                    else:
                        # Both methods failed
                        error_msg_user = result_user['stderr'] if result_user['stderr'] else result_user['stdout']
                        self.log(f"Error: {error_msg}", "ERROR")
                        self.log(f"User uninstall also failed: {error_msg_user}", "ERROR")
                        self.log(f"Return code: {result['returncode']}", "ERROR")
                        self.log(f"Full stdout: {result['stdout']}", "DEBUG")
                        self.log(f"Full stderr: {result['stderr']}", "DEBUG")
                        self.update_status("Failed to uninstall app")
                        
                        # Provide helpful message
                        if 'DELETE_FAILED_INTERNAL_ERROR' in error_msg or 'system app' in error_msg.lower() or 'package is a system package' in error_msg.lower():
                            help_text = f"Failed to uninstall {package_name}:\n\n{error_msg}\n\nTried both regular and user uninstall methods.\nYou can try disabling it instead (use 'Disable Selected')."
                        else:
                            help_text = f"Failed to uninstall {package_name}:\n\n{error_msg}"
                        
                        # Thread-safe messagebox - use QTimer to call from main thread
                        QTimer.singleShot(0, lambda: QMessageBox.critical(self, "Error", help_text))
            
            threading.Thread(target=do_uninstall, daemon=True).start()
        
        def reinstall_selected():
            """Reinstall selected app"""
            package_name = get_selected_package()
            if not package_name:
                QMessageBox.warning(self, "No Selection", "Please select an app to reinstall")
                return
            app_label = app_window.app_labels.get(package_name, package_name)
            display_name = f"{app_label} ({package_name})" if app_label != package_name else package_name
            reply = QMessageBox.question(self, "Confirm Reinstall", f"Reinstall {display_name}?\n\nThis will uninstall and then reinstall the app.",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
            
            self.log(f"Reinstalling {package_name}...")
            self.update_status("Reinstalling app...")
            
            def do_reinstall():
                # Step 1: Get APK path
                self.log(f"Getting APK path for {package_name}...")
                result = self.adb.run_command(f"{self.get_device_flag()} shell pm path {package_name}")
                if not result['success']:
                    error_msg = result['stderr'] or "Unknown error"
                    self.log(f"Error getting APK path: {error_msg}", "ERROR")
                    # Thread-safe messagebox - use QTimer to call from main thread
                    QTimer.singleShot(0, lambda: QMessageBox.critical(self, "Error", f"Failed to get APK path:\n{error_msg}"))
                    return
                
                # Parse APK path (format: package:/data/app/.../base.apk)
                # Handle multiple APK paths (split APKs)
                apk_paths = result['stdout'].strip().split('\n')
                apk_paths = [p.replace('package:', '').strip() for p in apk_paths if p.strip()]
                
                if not apk_paths:
                    self.log("Could not find APK path", "ERROR")
                    # Thread-safe messagebox - use QTimer to call from main thread
                    QTimer.singleShot(0, lambda: QMessageBox.critical(self, "Error", "Could not find APK path on device"))
                    return
                
                self.log(f"Found {len(apk_paths)} APK file(s)")
                if len(apk_paths) > 1:
                    self.log(f"Note: App uses split APKs. Will pull and install all {len(apk_paths)} APK files.", "INFO")
                
                # Step 2: Pull all APKs to local folder
                # Create apks folder in executable's directory (or script directory if running from source)
                # When running as PyInstaller executable, use the executable's directory
                if getattr(sys, 'frozen', False):
                    # Running as compiled executable
                    project_dir = os.path.dirname(sys.executable)
                else:
                    # Running as script
                    project_dir = os.path.dirname(os.path.abspath(__file__))
                apks_dir = os.path.join(project_dir, 'apks')
                os.makedirs(apks_dir, exist_ok=True)
                local_apks = []
                
                for i, apk_path in enumerate(apk_paths):
                    # Determine filename - base.apk for first, split_*.apk for others
                    if i == 0:
                        filename = f"{package_name}.apk"
                    else:
                        # Extract the split name from path (e.g., split_config.arm64_v8a.apk)
                        split_name = os.path.basename(apk_path)
                        filename = f"{package_name}_{split_name}"
                    
                    local_apk = os.path.join(apks_dir, filename)
                    local_apks.append(local_apk)
                    
                    self.log(f"Pulling APK {i+1}/{len(apk_paths)}: {os.path.basename(apk_path)}...")
                    result = self.adb.run_command(f'{self.get_device_flag()} pull "{apk_path}" "{local_apk}"')
                    if not result['success']:
                        error_msg = result['stderr'] or "Unknown error"
                        self.log(f"Error pulling APK {i+1}: {error_msg}", "ERROR")
                        # Thread-safe messagebox - use QTimer to call from main thread
                        QTimer.singleShot(0, lambda: QMessageBox.critical(self, "Error", f"Failed to pull APK {i+1}:\n{error_msg}"))
                        # Clean up already pulled APKs
                        for apk in local_apks:
                            try:
                                if os.path.exists(apk):
                                    os.remove(apk)
                            except:
                                pass
                        return
                
                self.log(f"Successfully pulled {len(local_apks)} APK file(s)")
                
                # Step 3: Uninstall app
                self.log(f"Uninstalling {package_name}...")
                result = self.adb.run_command(f"{self.get_device_flag()} uninstall {package_name}")
                if not result['success']:
                    error_msg = result['stderr'] or "Unknown error"
                    self.log(f"Error uninstalling: {error_msg}", "ERROR")
                    # Try to install anyway
                    self.log("Continuing with installation despite uninstall error...", "WARNING")
                else:
                    self.log("App uninstalled successfully")
                
                # Step 4: Install APK(s)
                self.log(f"Installing {package_name}...")
                
                # Use install-multiple for split APKs, regular install for single APK
                if len(local_apks) > 1:
                    # Install multiple APKs using install-multiple - quote each path to handle spaces
                    apk_list = ' '.join(f'"{apk}"' for apk in local_apks)
                    result = self.adb.run_command(f"{self.get_device_flag()} install-multiple {apk_list}", timeout=180)
                else:
                    # Single APK - use regular install
                    result = self.adb.run_command(f'{self.get_device_flag()} install "{local_apks[0]}"', timeout=120)
                
                if result['success']:
                    self.log("App reinstalled successfully")
                    self.update_status("App reinstalled successfully")
                    apk_locations = '\n'.join(local_apks)
                    # Thread-safe messagebox - use QTimer to call from main thread
                    QTimer.singleShot(0, lambda: QMessageBox.information(self, "Success", f"App reinstalled successfully!\n\nAPK(s) saved at:\n{apk_locations}"))
                    # Keep APKs in the folder for easy access - don't delete them
                else:
                    error_msg = result['stderr'] or "Unknown error"
                    self.log(f"Error installing: {error_msg}", "ERROR")
                    self.update_status("Failed to reinstall app")
                    apk_locations = '\n'.join(local_apks)
                    # Thread-safe messagebox - use QTimer to call from main thread
                    QTimer.singleShot(0, lambda: QMessageBox.critical(self, "Error", f"Failed to install app:\n{error_msg}\n\nAPK(s) saved at:\n{apk_locations}"))
            
            threading.Thread(target=do_reinstall, daemon=True).start()
        
        def disable_selected():
            """Disable selected app for current user"""
            package_name = get_selected_package()
            if not package_name:
                QMessageBox.warning(self, "No Selection", "Please select an app to disable")
                return
            
            # Validate package name
            if not package_name or package_name.strip() == '':
                self.log(f"Invalid package name extracted: '{package_name}'", "ERROR")
                QMessageBox.critical(self, "Error", "Could not extract package name from selection. Please try refreshing the list.")
                return
            
            app_label = app_window.app_labels.get(package_name, package_name)
            display_name = f"{app_label} ({package_name})" if app_label != package_name else package_name
            reply = QMessageBox.question(self, "Confirm Disable", f"Disable {display_name} for current user?\n\nThis will hide the app from the app drawer.",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
            
            self.log(f"Disabling {package_name}...")
            self.update_status("Disabling app...")
            
            def do_disable():
                # First verify the package exists
                result_check = self.adb.run_command(f"{self.get_device_flag()} shell pm path {package_name}")
                if not result_check['success'] or not result_check['stdout'] or result_check['stdout'].strip() == '':
                    error_msg = "Package not found. The app may have been uninstalled or the package name is invalid."
                    self.log(f"Package check failed: {result_check.get('stderr', 'No output')}", "ERROR")
                    self.log(f"Package name used: '{package_name}'", "DEBUG")
                    self.update_status("Failed to disable app")
                    # Thread-safe messagebox - use QTimer to call from main thread
                    QTimer.singleShot(0, lambda: QMessageBox.critical(self, "Error", f"Failed to disable {display_name}:\n\n{error_msg}\n\nPackage: {package_name}"))
                    return
                
                result = self.adb.run_command(f"{self.get_device_flag()} shell pm disable-user {package_name}")
                if result['success']:
                    self.log("App disabled successfully")
                    self.update_status("App disabled successfully")
                    # Update status
                    app_window.app_status[package_name] = True
                    # Refresh the list
                    QTimer.singleShot(0, lambda: update_list())
                    # Thread-safe messagebox - use QTimer to call from main thread
                    QTimer.singleShot(0, lambda: QMessageBox.information(self, "Success", "App disabled successfully"))
                else:
                    error_msg = result['stderr'] if result['stderr'] else result['stdout']
                    if not error_msg or error_msg.strip() == '':
                        error_msg = "Unknown error"
                    self.log(f"Error: {error_msg}", "ERROR")
                    self.log(f"Package name used: '{package_name}'", "DEBUG")
                    self.update_status("Failed to disable app")
                    # Thread-safe messagebox - use QTimer to call from main thread
                    QTimer.singleShot(0, lambda: QMessageBox.critical(self, "Error", f"Failed to disable app:\n{error_msg}"))
            
            threading.Thread(target=do_disable, daemon=True).start()
        
        def enable_selected():
            """Enable selected app"""
            package_name = get_selected_package()
            if not package_name:
                QMessageBox.warning(self, "No Selection", "Please select an app to enable")
                return
            
            # Validate package name
            if not package_name or package_name.strip() == '':
                self.log(f"Invalid package name extracted: '{package_name}'", "ERROR")
                QMessageBox.critical(self, "Error", "Could not extract package name from selection. Please try refreshing the list.")
                return
            
            app_label = app_window.app_labels.get(package_name, package_name)
            display_name = f"{app_label} ({package_name})" if app_label != package_name else package_name
            reply = QMessageBox.question(self, "Confirm Enable", f"Enable {display_name}?",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
            
            self.log(f"Enabling {package_name}...")
            self.update_status("Enabling app...")
            
            def do_enable():
                # First verify the package exists
                result_check = self.adb.run_command(f"{self.get_device_flag()} shell pm path {package_name}")
                if not result_check['success'] or not result_check['stdout'] or result_check['stdout'].strip() == '':
                    error_msg = "Package not found. The app may have been uninstalled or the package name is invalid."
                    self.log(f"Package check failed: {result_check.get('stderr', 'No output')}", "ERROR")
                    self.log(f"Package name used: '{package_name}'", "DEBUG")
                    self.update_status("Failed to enable app")
                    # Thread-safe messagebox - use QTimer to call from main thread
                    QTimer.singleShot(0, lambda: QMessageBox.critical(self, "Error", f"Failed to enable {display_name}:\n\n{error_msg}\n\nPackage: {package_name}"))
                    return
                
                # Try to enable the app
                result = self.adb.run_command(f"{self.get_device_flag()} shell pm enable {package_name}")
                if result['success']:
                    output = result['stdout'].strip() if result['stdout'] else ''
                    # Check if the output indicates success
                    if 'Package' in output or 'enabled' in output.lower() or output == '':
                        self.log("App enabled successfully")
                        self.update_status("App enabled successfully")
                        # Update status
                        app_window.app_status[package_name] = False
                        # Refresh the list
                        QTimer.singleShot(0, lambda: update_list())
                        # Thread-safe messagebox - use QTimer to call from main thread
                        QTimer.singleShot(0, lambda: QMessageBox.information(self, "Success", "App enabled successfully"))
                    else:
                        # Sometimes ADB returns success but with info message
                        self.log(f"Enable result: {output}")
                        self.update_status("Enable completed")
                        app_window.app_status[package_name] = False
                        QTimer.singleShot(0, lambda: update_list())
                        # Thread-safe messagebox - use QTimer to call from main thread
                        QTimer.singleShot(0, lambda: QMessageBox.information(self, "Success", f"Enable completed:\n{output}"))
                else:
                    error_msg = result['stderr'] if result['stderr'] else result['stdout']
                    if not error_msg or error_msg.strip() == '':
                        error_msg = "Unknown error - The app may not exist or may require special permissions to enable."
                    
                    self.log(f"Error enabling {package_name}: {error_msg}", "ERROR")
                    self.log(f"Package name used: '{package_name}'", "DEBUG")
                    self.log(f"Return code: {result['returncode']}", "ERROR")
                    self.update_status("Failed to enable app")
                    
                    # Provide helpful message for common errors
                    if 'SecurityException' in error_msg or 'Shell cannot change component state' in error_msg:
                        help_text = f"Failed to enable {display_name}:\n\n{error_msg}\n\nThis error usually means:\n1. The app doesn't exist or was uninstalled\n2. The app requires root access to enable\n3. The package name is invalid\n\nTry refreshing the app list."
                    elif 'null' in error_msg.lower():
                        help_text = f"Failed to enable {display_name}:\n\n{error_msg}\n\nThe package name appears to be invalid. Try refreshing the app list."
                    else:
                        help_text = f"Failed to enable {display_name}:\n\n{error_msg}"
                    
                    # Thread-safe messagebox - use QTimer to call from main thread
                    QTimer.singleShot(0, lambda: QMessageBox.critical(self, "Error", help_text))
            
            threading.Thread(target=do_enable, daemon=True).start()
        
        def refresh_list():
            """Refresh the app list"""
            self.log("Refreshing app list...")
            self.update_status("Refreshing apps...")
            
            def do_refresh():
                # Get all packages
                result = self.adb.run_command(f"{self.get_device_flag()} shell pm list packages")
                if result['success']:
                    apps = result['stdout'].strip().split('\n')
                    apps = [app.replace('package:', '') for app in apps if app.strip()]
                    
                    # Get disabled packages
                    result_disabled = self.adb.run_command(f"{self.get_device_flag()} shell pm list packages -d")
                    disabled_apps = set()
                    if result_disabled['success']:
                        disabled_lines = result_disabled['stdout'].strip().split('\n')
                        disabled_apps = {line.replace('package:', '').strip() for line in disabled_lines if line.strip()}
                    
                    # Update status dictionary
                    for app in apps:
                        app_window.app_status[app] = app in disabled_apps
                    
                    self.log(f"Found {len(apps)} installed apps ({len(disabled_apps)} disabled)")
                    self.update_status(f"Found {len(apps)} apps")
                    QTimer.singleShot(0, lambda: self.refresh_app_list_window(app_window, sorted(apps), search_entry, listbox))
                else:
                    self.log(f"Error: {result['stderr']}", "ERROR")
                    self.update_status("Failed to refresh apps")
            
            threading.Thread(target=do_refresh, daemon=True).start()
        
        # Initial status check
        def check_initial_status():
            """Check status of all apps initially"""
            result_disabled = self.adb.run_command(f"{self.get_device_flag()} shell pm list packages -d")
            if result_disabled['success']:
                disabled_lines = result_disabled['stdout'].strip().split('\n')
                for line in disabled_lines:
                    if line.strip():
                        pkg = line.replace('package:', '').strip()
                        app_window.app_status[pkg] = True
            # Mark all others as enabled
            for app in apps:
                if app not in app_window.app_status:
                    app_window.app_status[app] = False
            update_list()
        
        # Check status in background
        threading.Thread(target=check_initial_status, daemon=True).start()
        
        def reinstall_for_user():
            """Reinstall app for current user (for apps uninstalled with --user 0)"""
            package_name = get_selected_package()
            if not package_name:
                QMessageBox.warning(self, "No Selection", "Please select an app to reinstall")
                return
            app_label = app_window.app_labels.get(package_name, package_name)
            display_name = f"{app_label} ({package_name})" if app_label != package_name else package_name
            reply = QMessageBox.question(self, "Confirm Reinstall", f"Reinstall {display_name} for current user?\n\nThis will restore apps that were uninstalled for your user account.",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
            
            self.log(f"Reinstalling {package_name} for current user...")
            self.update_status("Reinstalling app for user...")
            
            def do_reinstall():
                # Use pm install-existing to reinstall apps uninstalled for the user
                result = self.adb.run_command(f"{self.get_device_flag()} shell pm install-existing {package_name}")
                if result['success']:
                    output = result['stdout'].strip() if result['stdout'] else ''
                    if 'Success' in output or 'success' in output.lower() or 'Package' in output:
                        self.log("App reinstalled for current user successfully")
                        self.update_status("App reinstalled for current user")
                        # Add back to the list if it was removed
                        if package_name not in app_window.original_apps:
                            app_window.original_apps.append(package_name)
                        # Refresh the list
                        QTimer.singleShot(0, lambda: update_list())
                        # Thread-safe messagebox - use QTimer to call from main thread
                        QTimer.singleShot(0, lambda: QMessageBox.information(self, "Success", f"App reinstalled for current user successfully!\n\n{package_name} is now available again."))
                    else:
                        self.log(f"Reinstall result: {output}")
                        self.update_status("Reinstall completed")
                        if package_name not in app_window.original_apps:
                            app_window.original_apps.append(package_name)
                        QTimer.singleShot(0, lambda: update_list())
                        # Thread-safe messagebox - use QTimer to call from main thread
                        QTimer.singleShot(0, lambda: QMessageBox.information(self, "Success", f"Reinstall completed:\n{output}"))
                else:
                    error_msg = result['stderr'] if result['stderr'] else result['stdout']
                    if not error_msg or error_msg.strip() == '':
                        error_msg = "Unknown error"
                    self.log(f"Error: {error_msg}", "ERROR")
                    self.update_status("Failed to reinstall app")
                    # Thread-safe messagebox - use QTimer to call from main thread
                    QTimer.singleShot(0, lambda: QMessageBox.critical(self, "Error", f"Failed to reinstall {package_name}:\n\n{error_msg}\n\nNote: This only works for apps that were previously installed but uninstalled for your user account."))
            
            threading.Thread(target=do_reinstall, daemon=True).start()
        
        uninstall_btn = QPushButton("Uninstall Selected")
        uninstall_btn.clicked.connect(uninstall_selected)
        button_layout.addWidget(uninstall_btn)
        
        reinstall_btn = QPushButton("Reinstall Selected")
        reinstall_btn.clicked.connect(reinstall_selected)
        button_layout.addWidget(reinstall_btn)
        
        reinstall_user_btn = QPushButton("Reinstall for User")
        reinstall_user_btn.clicked.connect(reinstall_for_user)
        button_layout.addWidget(reinstall_user_btn)
        
        disable_btn = QPushButton("Disable Selected")
        disable_btn.clicked.connect(disable_selected)
        button_layout.addWidget(disable_btn)
        
        enable_btn = QPushButton("Enable Selected")
        enable_btn.clicked.connect(enable_selected)
        button_layout.addWidget(enable_btn)
        
        refresh_btn = QPushButton("Refresh List")
        refresh_btn.clicked.connect(refresh_list)
        button_layout.addWidget(refresh_btn)
        
        button_layout.addStretch()
        
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(app_window.accept)
        button_layout.addWidget(close_btn)
        
        layout.addLayout(button_layout)
        
        # Double-click to show app info
        listbox.itemDoubleClicked.connect(lambda item: self.show_app_details(get_selected_package()) if get_selected_package() else None)
        
        # Ensure dialog appears on top and is visible
        app_window.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowCloseButtonHint)
        app_window.raise_()
        app_window.activateWindow()
        app_window.exec()
    
    def refresh_app_list_window(self, app_window, apps, search_entry, listbox):
        """Refresh the app list in the existing window"""
        # Update the stored apps list
        app_window.original_apps = apps.copy()
        
        # Load app labels for new apps if needed
        def load_missing_labels():
            for app in apps:
                if app not in app_window.app_labels:
                    label = self.get_app_label(app)
                    app_window.app_labels[app] = label if label else app
            QTimer.singleShot(0, lambda: update_list())
        
        def update_list():
            """Update listbox with filtered apps"""
            search_term = search_entry.text().lower()
            # Get filter checkbox from the window
            filter_checkbox = app_window.findChild(QCheckBox)
            filter_disabled_value = filter_checkbox.isChecked() if filter_checkbox else False
            listbox.clear()
            
            for app in apps:
                # Get app label (use package name as fallback)
                app_label = app_window.app_labels.get(app, app)
                
                # If label is same as package, just show package name (avoid "package (package)")
                if app_label == app:
                    display_label = app
                else:
                    display_label = f"{app_label} ({app})"
                
                # Check if app is disabled
                is_disabled = app_window.app_status.get(app, False)
                
                # Apply disabled filter
                if filter_disabled_value and not is_disabled:
                    continue
                
                # Check if search term matches app name or package name
                matches = False
                if not search_term:
                    matches = True
                elif search_term in app_label.lower() or search_term in app.lower():
                    matches = True
                
                if matches:
                    display_name = display_label
                    if is_disabled:
                        display_name += " [DISABLED]"
                    listbox.addItem(display_name)
        
        # Load missing labels in background
        threading.Thread(target=load_missing_labels, daemon=True).start()
        # Update immediately with existing labels
        update_list()
    
    def show_app_details(self, package_name):
        """Show detailed information about an app"""
        if not self.current_device:
            return
        
        self.log(f"Getting details for {package_name}...")
        
        def get_details():
            # Get APK path (most reliable)
            result = self.adb.run_command(f"{self.get_device_flag()} shell pm path {package_name}")
            apk_path = "Unknown"
            if result['success'] and result['stdout']:
                apk_path = result['stdout'].strip().replace('package:', '').strip()
                # Handle multiple APK paths (split APKs)
                if '\n' in apk_path:
                    apk_path = apk_path.split('\n')[0]
            
            # Get package info using dumpsys
            result = self.adb.run_command(f"{self.get_device_flag()} shell dumpsys package {package_name}")
            version = "Unknown"
            app_label = "Unknown"
            enabled_state = "Unknown"
            
            if result['success'] and result['stdout']:
                output = result['stdout']
                # Extract version
                for line in output.split('\n'):
                    if 'versionName=' in line:
                        version = line.split('versionName=')[1].split()[0].strip()
                        break
                
                # Extract app label
                for line in output.split('\n'):
                    if 'applicationLabel=' in line.lower() or 'label=' in line.lower():
                        if 'applicationLabel' in line.lower():
                            app_label = line.split('=')[-1].strip()
                            break
                
                # Check if enabled/disabled
                if 'enabled=true' in output.lower():
                    enabled_state = "Enabled"
                elif 'enabled=false' in output.lower():
                    enabled_state = "Disabled"
            
            details = f"Package: {package_name}\n"
            details += f"Label: {app_label}\n"
            details += f"Version: {version}\n"
            details += f"Status: {enabled_state}\n"
            details += f"APK Path: {apk_path}"
            
            # Thread-safe messagebox - use QTimer to call from main thread
            QTimer.singleShot(0, lambda: QMessageBox.information(self, "App Details", details))
        
        threading.Thread(target=get_details, daemon=True).start()
    
    def take_screenshot(self):
        """Take screenshot"""
        if not self.current_device:
            QMessageBox.warning(self, "No Device", "Please select a device first")
            return
        
        # Create screenshots folder in executable's directory (or script directory if running from source)
        # When running as PyInstaller executable, use the executable's directory
        if getattr(sys, 'frozen', False):
            # Running as compiled executable
            project_dir = os.path.dirname(sys.executable)
        else:
            # Running as script
            project_dir = os.path.dirname(os.path.abspath(__file__))
        
        screenshots_dir = os.path.join(project_dir, 'screenshots')
        os.makedirs(screenshots_dir, exist_ok=True)
        
        # Generate filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"screenshot_{timestamp}.png"
        dest_path = os.path.join(screenshots_dir, filename)
        
        self.log("Taking screenshot...")
        self.update_status("Taking screenshot...")
        
        def do_screenshot():
            try:
                # Take screenshot on device
                result = self.adb.run_command(f"{self.get_device_flag()} shell screencap -p /sdcard/screenshot.png")
                if result['success']:
                    # Pull screenshot
                    result = self.adb.run_command(f'{self.get_device_flag()} pull /sdcard/screenshot.png "{dest_path}"')
                    if result['success']:
                        self.log(f"Screenshot saved successfully: {dest_path}")
                        self.update_status("Screenshot saved")
                        # Use QTimer.singleShot to safely call QMessageBox from main thread
                        QTimer.singleShot(0, lambda: QMessageBox.information(self, "Success", f"Screenshot saved to:\n{dest_path}"))
                    else:
                        error_msg = result.get('stderr', 'Unknown error')
                        self.log(f"Error pulling screenshot: {error_msg}", "ERROR")
                        self.update_status("Failed to save screenshot")
                        QTimer.singleShot(0, lambda: QMessageBox.warning(self, "Error", f"Failed to save screenshot:\n{error_msg}"))
                else:
                    error_msg = result.get('stderr', 'Unknown error')
                    self.log(f"Error taking screenshot: {error_msg}", "ERROR")
                    self.update_status("Failed to take screenshot")
                    QTimer.singleShot(0, lambda: QMessageBox.warning(self, "Error", f"Failed to take screenshot:\n{error_msg}"))
            except Exception as e:
                error_msg = str(e)
                self.log(f"Exception in screenshot: {error_msg}", "ERROR")
                self.update_status("Screenshot failed")
                QTimer.singleShot(0, lambda: QMessageBox.critical(self, "Error", f"An error occurred:\n{error_msg}"))
        
        threading.Thread(target=do_screenshot, daemon=True).start()
    
    def reboot_device(self):
        """Reboot device"""
        if not self.current_device:
            QMessageBox.warning(self, "No Device", "Please select a device first")
            return
        
        reply = QMessageBox.question(self, "Confirm", "Reboot device?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        self.log("Rebooting device...")
        self.update_status("Rebooting device...")
        
        def do_reboot():
            result = self.adb.run_command(f"{self.get_device_flag()} reboot")
            if result['success']:
                self.log("Device rebooting...")
                self.update_status("Device rebooting...")
            else:
                self.log(f"Error: {result['stderr']}", "ERROR")
                self.update_status("Failed to reboot")
        
        threading.Thread(target=do_reboot, daemon=True).start()
    
    def reboot_recovery(self):
        """Reboot to recovery"""
        if not self.current_device:
            QMessageBox.warning(self, "No Device", "Please select a device first")
            return
        
        reply = QMessageBox.question(self, "Confirm", "Reboot to recovery mode?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        self.log("Rebooting to recovery...")
        self.update_status("Rebooting to recovery...")
        
        def do_reboot():
            result = self.adb.run_command(f"{self.get_device_flag()} reboot recovery")
            if result['success']:
                self.log("Device rebooting to recovery...")
                self.update_status("Device rebooting to recovery...")
            else:
                self.log(f"Error: {result['stderr']}", "ERROR")
                self.update_status("Failed to reboot")
        
        threading.Thread(target=do_reboot, daemon=True).start()
    
    def reboot_bootloader(self):
        """Reboot to bootloader"""
        if not self.current_device:
            QMessageBox.warning(self, "No Device", "Please select a device first")
            return
        
        reply = QMessageBox.question(self, "Confirm", "Reboot to bootloader?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        self.log("Rebooting to bootloader...")
        self.update_status("Rebooting to bootloader...")
        
        def do_reboot():
            result = self.adb.run_command(f"{self.get_device_flag()} reboot bootloader")
            if result['success']:
                self.log("Device rebooting to bootloader...")
                self.update_status("Device rebooting to bootloader...")
            else:
                self.log(f"Error: {result['stderr']}", "ERROR")
                self.update_status("Failed to reboot")
        
        threading.Thread(target=do_reboot, daemon=True).start()
    
    def run_shell_command(self):
        """Run shell command"""
        if not self.current_device:
            QMessageBox.warning(self, "No Device", "Please select a device first")
            return
        
        command = self.shell_entry.toPlainText().strip()
        if not command:
            return
        
        # Strip "adb" and "shell" prefixes if user included them
        # This allows users to paste full adb commands or just shell commands
        command = command.strip()
        if command.startswith('adb '):
            command = command[4:].strip()
        if command.startswith('shell '):
            command = command[6:].strip()
        
        if not command:
            QMessageBox.warning(self, "Invalid Command", "Please enter a shell command to run on the device.")
            return
        
        # Warn if user tries to use Windows commands
        # Note: These commands run ON THE ANDROID DEVICE (Linux), not on Windows
        windows_commands = {
            'findstr': 'grep',
            'dir': 'ls',
            'type': 'cat',
            'copy': 'cp',
            'del': 'rm',
            'move': 'mv',
            'cd': 'cd',  # Same on both, but included for completeness
        }
        command_lower = command.lower()
        for win_cmd, linux_cmd in windows_commands.items():
            # Check if Windows command is used (as a separate word)
            if (f' {win_cmd} ' in command_lower or 
                command_lower.startswith(win_cmd + ' ') or 
                command_lower.endswith(' ' + win_cmd) or
                command_lower == win_cmd):
                if win_cmd != linux_cmd:  # Only warn if they're different
                    QMessageBox.warning(
                        self,
                        "Windows Command Detected",
                        f"⚠️ '{win_cmd}' is a Windows command and won't work on your Android device.\n\n"
                        f"These commands run ON YOUR ANDROID DEVICE (which uses Linux), not on Windows.\n\n"
                        f"Use '{linux_cmd}' instead of '{win_cmd}'.\n\n"
                        f"Example: Replace '{win_cmd}' with '{linux_cmd}' in your command."
                    )
                    return
        
        self.log(f"Running shell command: {command}")
        self.update_status("Running command...")
        
        def do_command():
            result = self.adb.run_command(f"{self.get_device_flag()} shell {command}")
            if result['success']:
                output = result['stdout'] if result['stdout'] else result['stderr']
                if output:
                    self.log(f"Output:\n{output}")
                else:
                    self.log("Command completed (no output)")
                self.update_status("Command completed")
            else:
                error_msg = result.get('stderr', 'Unknown error')
                self.log(f"Error: {error_msg}", "ERROR")
                self.update_status("Command failed")
        
        threading.Thread(target=do_command, daemon=True).start()
    
    def toggle_logcat(self):
        """Start/stop logcat"""
        if not self.current_device:
            QMessageBox.warning(self, "No Device", "Please select a device first")
            return
        
        if self.log_running:
            self.log_running = False
            self.log_button.setText("▶️ Start Logcat")
            self.log("Logcat stopped")
            self.update_status("Logcat stopped")
        else:
            self.log_running = True
            self.log_button.setText("⏹️ Stop Logcat")
            self.log("Starting logcat...")
            self.update_status("Logcat running...")
            
            def run_logcat():
                try:
                    # Store device ID for thread safety
                    device_id = self.current_device
                    
                    process = subprocess.Popen(
                        [self.adb.adb_path, '-s', device_id, 'logcat'],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding='utf-8',
                        errors='replace',
                        bufsize=1,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
                    )
                    
                    # Check if process started successfully
                    if process.poll() is not None:
                        # Process already terminated
                        stderr_output = process.stderr.read()
                        error_msg = f"Logcat process failed to start: {stderr_output}"
                        QTimer.singleShot(0, lambda: self.log(error_msg, "ERROR"))
                        QTimer.singleShot(0, lambda: QMessageBox.warning(self, "Logcat Error", error_msg))
                        self.log_running = False
                        QTimer.singleShot(0, lambda: self.log_button.setText("▶️ Start Logcat"))
                        return
                    
                    # Log that logcat started successfully
                    QTimer.singleShot(0, lambda: self.log("Logcat process started, waiting for output...", "INFO"))
                    
                    # Read output line by line
                    while self.log_running:
                        line = process.stdout.readline()
                        if line:
                            # Use a closure to capture the line value properly
                            line_text = line.strip()
                            if line_text:  # Only log non-empty lines
                                QTimer.singleShot(0, lambda l=line_text: self.log(l, "LOGCAT"))
                        elif process.poll() is not None:
                            # Process ended
                            break
                    
                    # Clean up
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill()
                    
                    if self.log_running:
                        # Process ended unexpectedly
                        stderr_output = process.stderr.read()
                        if stderr_output:
                            QTimer.singleShot(0, lambda: self.log(f"Logcat process ended: {stderr_output}", "ERROR"))
                        else:
                            QTimer.singleShot(0, lambda: self.log("Logcat process ended unexpectedly", "WARNING"))
                        self.log_running = False
                        QTimer.singleShot(0, lambda: self.log_button.setText("▶️ Start Logcat"))
                        
                except Exception as e:
                    error_msg = f"Logcat error: {str(e)}"
                    QTimer.singleShot(0, lambda: self.log(error_msg, "ERROR"))
                    QTimer.singleShot(0, lambda: QMessageBox.critical(self, "Logcat Error", error_msg))
                    self.log_running = False
                    QTimer.singleShot(0, lambda: self.log_button.setText("▶️ Start Logcat"))
                    import traceback
                    QTimer.singleShot(0, lambda: self.log(f"Traceback: {traceback.format_exc()}", "ERROR"))
            
            self.current_device = self.current_device  # Store for logcat thread
            threading.Thread(target=run_logcat, daemon=True).start()
    
    def load_settings(self):
        """Load settings from file"""
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, 'r') as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    def save_settings(self):
        """Save settings to file"""
        try:
            with open(self.settings_file, 'w') as f:
                json.dump(self.settings, f, indent=2)
        except Exception as e:
            self.log(f"Error saving settings: {e}", "ERROR")
    
    def load_degoogle_state(self):
        """Load DeGoogle state from file"""
        if os.path.exists(self.degoogle_state_file):
            try:
                with open(self.degoogle_state_file, 'r') as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    def save_degoogle_state(self):
        """Save DeGoogle state to file"""
        try:
            with open(self.degoogle_state_file, 'w') as f:
                json.dump(self.degoogle_state, f, indent=2)
        except Exception as e:
            self.log(f"Error saving DeGoogle state: {e}", "ERROR")
    
    def apply_theme(self):
        """Apply light or dark theme"""
        if self.dark_mode:
            self.colors = self.dark_colors.copy()
        else:
            self.colors = self.light_colors.copy()
        
        # Apply stylesheet
        self.setStyleSheet(f"""
            QMainWindow {{
                background-color: {self.colors['bg']};
                color: {self.colors['fg']};
            }}
            QWidget {{
                background-color: {self.colors['bg']};
                color: {self.colors['fg']};
            }}
            QPushButton {{
                background-color: {self.colors['card_bg']};
                color: {self.colors['fg']};
                border: 1px solid {self.colors['border']};
                border-radius: 4px;
                padding: 8px;
                font-family: 'Segoe UI';
                font-size: 9pt;
            }}
            QPushButton:hover {{
                background-color: {'#3e3e42' if self.dark_mode else '#f0f0f0'};
            }}
            QPushButton:pressed {{
                background-color: {'#2d2d30' if self.dark_mode else '#e0e0e0'};
            }}
            QPushButton[accent="true"] {{
                background-color: {self.colors['accent']};
                color: white;
            }}
            QPushButton[accent="true"]:hover {{
                background-color: {self.colors['accent_hover']};
            }}
            QGroupBox {{
                border: 1px solid {self.colors['border']};
                border-radius: 4px;
                margin-top: 10px;
                padding-top: 10px;
                background-color: {self.colors['card_bg']};
                color: {self.colors['fg']};
                font-weight: bold;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: {self.colors['fg']};
            }}
            QLineEdit, QComboBox {{
                border: 1px solid {self.colors['border']};
                border-radius: 4px;
                padding: 5px;
                background-color: {self.colors['card_bg']};
                color: {self.colors['fg']};
            }}
            QTextEdit {{
                border: 1px solid {self.colors['border']};
                border-radius: 4px;
                background-color: {'#1e1e1e' if self.dark_mode else '#1e1e1e'};
                color: {'#d4d4d4' if self.dark_mode else '#d4d4d4'};
                font-family: 'Consolas';
                font-size: 9pt;
            }}
            QLabel {{
                color: {self.colors['fg']};
            }}
            QListWidget {{
                background-color: {self.colors['card_bg']};
                color: {self.colors['fg']};
                border: 1px solid {self.colors['border']};
            }}
            QTreeWidget {{
                background-color: {self.colors['card_bg']};
                color: {self.colors['fg']};
                border: 1px solid {self.colors['border']};
                alternate-background-color: {'#262626' if self.dark_mode else '#fafafa'};
                show-decoration-selected: 1;
            }}
            QTreeWidget::item {{
                padding: 4px 2px;
            }}
            QTreeWidget::item:selected {{
                background-color: {self.colors['accent']};
                color: white;
            }}
            QHeaderView::section {{
                background-color: {self.colors['bg']};
                color: {self.colors['fg']};
                border: none;
                border-right: 1px solid {self.colors['border']};
                border-bottom: 1px solid {self.colors['border']};
                padding: 6px 8px;
                font-weight: bold;
            }}
            QToolButton {{
                background-color: {self.colors['card_bg']};
                color: {self.colors['fg']};
                border: 1px solid {self.colors['border']};
                border-radius: 4px;
                padding: 4px 8px;
            }}
            QToolButton:hover {{
                background-color: {'#3e3e42' if self.dark_mode else '#f0f0f0'};
            }}
            QSplitter::handle {{
                background-color: {self.colors['border']};
            }}
            QSplitter::handle:horizontal {{
                width: 3px;
            }}
            QSplitter::handle:vertical {{
                height: 3px;
            }}
            QMenu {{
                background-color: {self.colors['card_bg']};
                color: {self.colors['fg']};
                border: 1px solid {self.colors['border']};
            }}
            QMenu::item:selected {{
                background-color: {self.colors['accent']};
                color: white;
            }}
            QCheckBox {{
                color: {self.colors['fg']};
            }}
            QRadioButton {{
                color: {self.colors['fg']};
            }}
            QTabWidget::pane {{
                border: 1px solid {self.colors['border']};
                background-color: {self.colors['card_bg']};
            }}
            QTabBar::tab {{
                background-color: {self.colors['bg']};
                color: {self.colors['fg']};
                border: 1px solid {self.colors['border']};
                padding: 8px;
            }}
            QTabBar::tab:selected {{
                background-color: {self.colors['card_bg']};
            }}
            QScrollArea {{
                background-color: {self.colors['card_bg']};
                border: 1px solid {self.colors['border']};
            }}
            QDialog {{
                background-color: {self.colors['bg']};
                color: {self.colors['fg']};
            }}
        """)
        
        # Update existing UI elements if they exist
        if hasattr(self, 'device_info_label'):
            self.device_info_label.setStyleSheet(f"color: {self.colors['text_secondary']};")
        if hasattr(self, 'adb_path_label'):
            self.adb_path_label.setStyleSheet(f"color: {self.colors['text_tertiary']};")
    
    def toggle_dark_mode(self):
        """Toggle dark mode on/off"""
        self.dark_mode = not self.dark_mode
        self.settings['dark_mode'] = self.dark_mode
        self.save_settings()
        self.apply_theme()
        
        # Update all UI elements that have custom styles
        self.update_widget_styles()
        
        # Update dark mode button text
        if hasattr(self, 'dark_mode_btn'):
            self.dark_mode_btn.setText("🌙 Dark Mode" if not self.dark_mode else "☀️ Light Mode")
    
    def update_widget_styles(self):
        """Update all widgets with custom stylesheets when theme changes"""
        # Header labels
        if hasattr(self, 'title_label'):
            self.title_label.setStyleSheet(f"color: {self.colors['fg']};")
        if hasattr(self, 'subtitle_label'):
            self.subtitle_label.setStyleSheet(f"color: {self.colors['text_secondary']};")
        
        # Device info labels (only update if not in special state)
        if hasattr(self, 'device_info_label'):
            current_style = self.device_info_label.styleSheet()
            if 'error' not in current_style.lower() and 'warning' not in current_style.lower() and 'success' not in current_style.lower():
                self.device_info_label.setStyleSheet(f"color: {self.colors['text_secondary']};")
        if hasattr(self, 'adb_path_label'):
            current_style = self.adb_path_label.styleSheet()
            if 'error' not in current_style.lower() and 'success' not in current_style.lower():
                self.adb_path_label.setStyleSheet(f"color: {self.colors['text_tertiary']};")
        
        # Separator
        if hasattr(self, 'separator'):
            self.separator.setStyleSheet(f"color: {self.colors['border']};")
        
        # Status bar
        if hasattr(self, 'status_bar'):
            self.status_bar.setStyleSheet(f"""
                background-color: {self.colors['card_bg']};
                border: 1px solid {self.colors['border']};
                padding: 8px 15px;
                color: {self.colors['text_secondary']};
            """)
        
        # Force refresh of all widgets to apply new stylesheet
        # This ensures the global stylesheet is reapplied to all widgets
        self.style().unpolish(self)
        self.style().polish(self)
        
        # Update all child widgets
        for widget in self.findChildren(QWidget):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        
        # Update shell help label if it exists
        if hasattr(self, 'shell_help_label'):
            self.shell_help_label.setStyleSheet(f"color: {self.colors['text_secondary']}; font-size: 8pt;")
    
    # ----- DeGoogle helpers ---------------------------------------------------
    
    def _get_installed_packages(self, timeout=60):
        """Return a set of installed package names on the current device."""
        result = self.adb.run_command(
            f"{self.get_device_flag()} shell pm list packages", timeout=timeout
        )
        if not result['success']:
            return set()
        return {
            line.replace('package:', '').strip()
            for line in (result['stdout'] or '').splitlines()
            if line.strip()
        }
    
    def _process_packages(self, packages, action):
        """Disable or uninstall a list of packages.
        
        Returns:
            (disabled, uninstalled, failed) where failed is a list of
            (package, error_msg) tuples.
        """
        disabled, uninstalled, failed = [], [], []
        total = len(packages)
        flag = self.get_device_flag()
        
        for i, pkg in enumerate(packages, start=1):
            self.log(f"Processing {i}/{total}: {pkg}")
            
            if action == "disable":
                result = self.adb.run_command(f"{flag} shell pm disable-user {pkg}")
                if result['success']:
                    disabled.append(pkg)
                    self.log(f"Disabled: {pkg}")
                else:
                    err = result.get('stderr') or 'Unknown error'
                    failed.append((pkg, err))
                    self.log(f"Failed to disable {pkg}: {err}", "ERROR")
            else:  # uninstall
                result = self.adb.run_command(f"{flag} shell pm uninstall --user 0 {pkg}")
                output = (result.get('stdout') or '').strip()
                if result['success'] and (output == '' or 'success' in output.lower()):
                    uninstalled.append(pkg)
                    self.log(f"Uninstalled for user: {pkg}")
                else:
                    err = output or result.get('stderr') or 'Unknown error'
                    failed.append((pkg, err))
                    self.log(f"Failed to uninstall {pkg}: {err}", "ERROR")
        
        return disabled, uninstalled, failed
    
    def _save_degoogle_action(self, action, disabled, uninstalled, include_risky=None):
        """Merge a degoogle action's results into the persistent state file."""
        device_id = self.current_device
        if not device_id:
            return
        state = self.degoogle_state.setdefault(device_id, {})
        
        if disabled:
            existing = set(state.get('disabled', []))
            existing.update(disabled)
            state['disabled'] = list(existing)
        if uninstalled:
            existing = set(state.get('uninstalled', []))
            existing.update(uninstalled)
            state['uninstalled'] = list(existing)
        if include_risky is not None:
            state[f'{action}d_risky'] = include_risky
        state['action'] = action
        state['timestamp'] = datetime.now().isoformat()
        self.save_degoogle_state()
    
    def _build_results_message(self, action, disabled, uninstalled, failed, processed_unsafe=None):
        """Build the result message text for the completion dialog."""
        lines = ["DeGoogle completed!", ""]
        if action == "disable":
            lines.append(f"Disabled: {len(disabled)} packages")
        else:
            lines.append(f"Uninstalled: {len(uninstalled)} packages")
        
        if processed_unsafe:
            lines.extend([
                "",
                f"⚠️ WARNING: {len(processed_unsafe)} unsafe package(s) were processed!",
                "Monitor your device for issues. If problems occur, use 'Undo DeGoogle' to restore.",
            ])
        
        if failed:
            lines.extend(["", f"Failed: {len(failed)} packages", "", "Failed packages:"])
            for pkg, _ in failed[:5]:
                lines.append(f"• {pkg}")
            if len(failed) > 5:
                lines.append(f"... and {len(failed) - 5} more")
        
        return "\n".join(lines)
    
    @staticmethod
    def _make_checkbox_tab(packages, default_checked=True, header_text=None,
                           checkbox_style=None):
        """Build a tab page containing a scrollable list of package checkboxes.
        
        Returns:
            (widget, dict[package -> QCheckBox])
        """
        widget = QWidget()
        outer = QVBoxLayout(widget)
        outer.setContentsMargins(5, 5, 5, 5)
        
        if header_text:
            header = QLabel(header_text)
            header.setStyleSheet("color: red; font-weight: bold;")
            header.setWordWrap(True)
            outer.addWidget(header)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        
        checkboxes = {}
        for pkg in sorted(packages):
            cb = QCheckBox(pkg)
            cb.setChecked(default_checked)
            if checkbox_style:
                cb.setStyleSheet(checkbox_style)
            checkboxes[pkg] = cb
            inner_layout.addWidget(cb)
        
        inner_layout.addStretch()
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        return widget, checkboxes
    
    def degoogle_device(self):
        """DeGoogle the device - disable/uninstall Google apps."""
        if not self.current_device:
            QMessageBox.warning(self, "No Device", "Please select a device first")
            return
        
        # --- Mode selection dialog ---
        mode_dialog = QDialog(self)
        mode_dialog.setWindowTitle("DeGoogle Device - Choose Mode")
        mode_dialog.setMinimumSize(500, 400)
        mode_dialog.setModal(True)
        
        mode_layout = QVBoxLayout(mode_dialog)
        mode_layout.setSpacing(15)
        mode_layout.setContentsMargins(20, 20, 20, 20)
        
        warning_label = QLabel(
            "⚠️ IMPORTANT WARNING ⚠️\n\n"
            "This will disable or uninstall Google apps and services.\n"
            "Some apps you rely on may stop working until you restore them.\n\n"
            "Tip: Install alternatives (e.g. Brave/Firefox for browsing,\n"
            "an open-source mail/calendar client) before continuing."
        )
        warning_label.setStyleSheet("color: red; font-weight: bold;")
        warning_label.setWordWrap(True)
        warning_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mode_layout.addWidget(warning_label)
        
        mode_label = QLabel("Choose mode:")
        mode_label.setFont(QFont('Segoe UI', 10, QFont.Weight.Bold))
        mode_layout.addWidget(mode_label)
        
        mode_group = QButtonGroup(mode_dialog)
        simple_radio = QRadioButton("Simple Mode - Remove all safe apps")
        simple_radio.setChecked(True)
        mode_group.addButton(simple_radio, 0)
        mode_layout.addWidget(simple_radio)
        
        custom_radio = QRadioButton("Custom Mode - Select individual apps")
        mode_group.addButton(custom_radio, 1)
        mode_layout.addWidget(custom_radio)
        
        mode_layout.addStretch()
        
        mode_button_frame = QHBoxLayout()
        mode_button_frame.addStretch()
        cancel_mode_btn = QPushButton("Cancel")
        cancel_mode_btn.clicked.connect(mode_dialog.reject)
        mode_button_frame.addWidget(cancel_mode_btn)
        continue_btn = QPushButton("Continue")
        continue_btn.clicked.connect(mode_dialog.accept)
        mode_button_frame.addWidget(continue_btn)
        mode_layout.addLayout(mode_button_frame)
        
        if mode_dialog.exec() != QDialog.DialogCode.Accepted:
            return
        
        if simple_radio.isChecked():
            self.show_simple_degoogle_dialog()
            return
        
        # --- Custom Mode: scan installed packages off the UI thread ---
        self.log("Checking installed packages for Custom Mode...")
        self.update_status("Checking installed packages...")
        
        def check_installed_and_show():
            packages_data = {
                'safe': SAFE_GOOGLE_PACKAGES,
                'risky': RISKY_GOOGLE_PACKAGES,
                'unsafe': UNSAFE_GOOGLE_PACKAGES,
                'ready': False,
            }
            try:
                installed = self._get_installed_packages()
                packages_data['installed_safe'] = [p for p in SAFE_GOOGLE_PACKAGES if p in installed]
                packages_data['installed_risky'] = [p for p in RISKY_GOOGLE_PACKAGES if p in installed]
                packages_data['installed_unsafe'] = [p for p in UNSAFE_GOOGLE_PACKAGES if p in installed]
                self.log(
                    f"Found {len(packages_data['installed_safe'])} safe, "
                    f"{len(packages_data['installed_risky'])} risky, "
                    f"{len(packages_data['installed_unsafe'])} unsafe packages"
                )
                self.update_status("Ready")
            except Exception as e:
                import traceback
                self.log(f"Error checking installed packages: {e}", "ERROR")
                self.log(f"Traceback: {traceback.format_exc()}", "ERROR")
                self.update_status("Error checking packages")
                packages_data['error'] = str(e)
            finally:
                packages_data['ready'] = True
                self.custom_dialog_ready.emit(packages_data)
        
        threading.Thread(target=check_installed_and_show, daemon=True).start()
    
    def show_simple_degoogle_dialog(self):
        """Simple DeGoogle dialog with a checkbox to also include risky services."""
        dialog = QDialog(self)
        dialog.setWindowTitle("DeGoogle Device - Simple Mode")
        dialog.setMinimumSize(500, 600)
        dialog.setModal(True)
        
        layout = QVBoxLayout(dialog)
        layout.setSpacing(15)
        layout.setContentsMargins(15, 15, 15, 15)
        
        title_label = QLabel("DeGoogle Device - Simple Mode")
        title_font = QFont()
        title_font.setPointSize(12)
        title_font.setBold(True)
        title_label.setFont(title_font)
        layout.addWidget(title_label)
        
        unsafe_warning_label = QLabel(
            "🚨 CRITICAL: Unsafe packages are PROTECTED and will NOT be removed!\n"
            "These include: Pixel Launcher, Camera, System UI, Phone, Play Services, etc.\n"
            "Removing them WILL break your device (bootloop, no camera, no network, etc.)"
        )
        unsafe_warning_label.setStyleSheet("color: red; font-weight: bold;")
        unsafe_warning_label.setWordWrap(True)
        unsafe_warning_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(unsafe_warning_label)
        
        info_label = QLabel(
            "This will disable/uninstall Google apps and services.\n\n"
            "Safe apps (won't break functionality):\n"
            "• YouTube, Google Photos, Maps, Gmail, Drive, etc.\n\n"
            "Risky services (may break functionality):\n"
            "• Google Login Service\n"
            "• Google Services Provider\n"
            "• Calendar/Contacts sync adapters\n\n"
            "Warning: Disabling risky services may cause:\n"
            "• Apps to crash\n"
            "• Loss of sync functionality\n"
            "• Inability to use Google services"
        )
        info_label.setWordWrap(True)
        info_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(info_label)
        
        risky_checkbox = QCheckBox(
            "Also disable/uninstall risky Google services (may break functionality)"
        )
        layout.addWidget(risky_checkbox)
        
        action_label = QLabel("Action:")
        layout.addWidget(action_label)
        
        action_group = QButtonGroup(dialog)
        action_frame = QHBoxLayout()
        disable_radio = QRadioButton("Disable (can be re-enabled)")
        disable_radio.setChecked(True)
        action_group.addButton(disable_radio, 0)
        action_frame.addWidget(disable_radio)
        uninstall_radio = QRadioButton("Uninstall for user (can be restored)")
        action_group.addButton(uninstall_radio, 1)
        action_frame.addWidget(uninstall_radio)
        action_frame.addStretch()
        layout.addLayout(action_frame)
        
        layout.addStretch()
        
        def do_degoogle():
            action = "disable" if disable_radio.isChecked() else "uninstall"
            include_risky = risky_checkbox.isChecked()
            dialog.accept()
            
            # Build the candidate set: safe + (optionally) risky, minus unsafe.
            unsafe_set = set(UNSAFE_GOOGLE_PACKAGES)
            candidates = list(SAFE_GOOGLE_PACKAGES)
            if include_risky:
                candidates.extend(p for p in RISKY_GOOGLE_PACKAGES if p not in candidates)
            candidates = [p for p in candidates if p not in unsafe_set]
            
            installed = self._get_installed_packages()
            packages_to_process = [p for p in candidates if p in installed]
            
            # Confirm with the user
            preview_lines = [f"This will {action} {len(packages_to_process)} Google package(s):", ""]
            if packages_to_process:
                preview_lines.append("Packages to be removed:")
                for pkg in sorted(packages_to_process):
                    preview_lines.append(f"• {pkg}")
            preview_lines.extend([
                "",
                f"Include risky services: {include_risky}",
                f"Action: {action}",
                "",
                "Continue?",
            ])
            reply = QMessageBox.question(
                self,
                "Preview - Confirm DeGoogle",
                "\n".join(preview_lines),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            
            self.log(f"Starting DeGoogle process for {len(packages_to_process)} package(s)...")
            self.update_status("DeGoogling device...")
            
            def process_degoogle():
                disabled, uninstalled, failed = self._process_packages(packages_to_process, action)
                self._save_degoogle_action(action, disabled, uninstalled, include_risky=include_risky)
                
                msg = self._build_results_message(action, disabled, uninstalled, failed)
                self.update_status("DeGoogle completed")
                QTimer.singleShot(0, lambda: QMessageBox.information(self, "DeGoogle Complete", msg))
            
            threading.Thread(target=process_degoogle, daemon=True).start()
        
        button_frame = QHBoxLayout()
        button_frame.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dialog.reject)
        button_frame.addWidget(cancel_btn)
        degoogle_btn = QPushButton("DeGoogle")
        degoogle_btn.clicked.connect(do_degoogle)
        button_frame.addWidget(degoogle_btn)
        layout.addLayout(button_frame)
        
        dialog.exec()
    
    def _show_custom_dialog(self, packages_data):
        """Show the Custom-Mode selection dialog from the main thread (signal callback)."""
        try:
            if 'error' in packages_data:
                QMessageBox.critical(
                    self, "Error",
                    f"Failed to check installed packages: {packages_data['error']}",
                )
                return
            if not packages_data.get('ready', False):
                QMessageBox.warning(self, "Error", "Package data not ready yet. Please try again.")
                return
            self.show_degoogle_selection_dialog(
                packages_data['installed_safe'],
                packages_data['installed_risky'],
                packages_data['installed_unsafe'],
            )
        except Exception as e:
            import traceback
            self.log(f"Error in _show_custom_dialog: {e}", "ERROR")
            self.log(f"Traceback: {traceback.format_exc()}", "ERROR")
            QMessageBox.critical(self, "Error", f"Failed to show selection dialog: {e}")
    
    def show_degoogle_selection_dialog(self, installed_safe, installed_risky, installed_unsafe):
        """Show the Custom-Mode dialog with checkboxes for the user to pick packages."""
        try:
            self.log(
                f"show_degoogle_selection_dialog called: {len(installed_safe)} safe, "
                f"{len(installed_risky)} risky, {len(installed_unsafe)} unsafe"
            )
            self.update_status("Opening custom selection dialog...")
            
            dialog = QDialog(self)
            dialog.setWindowTitle("DeGoogle Device - Select Apps")
            dialog.setMinimumSize(600, 800)
            dialog.setModal(True)
            
            layout = QVBoxLayout(dialog)
            layout.setSpacing(10)
            layout.setContentsMargins(15, 15, 15, 15)
            
            unsafe_warning_label = QLabel(
                "🚨 CRITICAL WARNING 🚨\n"
                "Unsafe packages CAN break your device!\n"
                "Removing them may cause: bootloop, no camera, no network, "
                "no launcher, failed OTA, broken notifications, etc.\n"
                "Only select unsafe packages if you know what you're doing!"
            )
            unsafe_warning_label.setStyleSheet("color: red; font-weight: bold;")
            unsafe_warning_label.setWordWrap(True)
            unsafe_warning_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(unsafe_warning_label)
            
            tab_widget = QTabWidget()
            layout.addWidget(tab_widget)
            
            safe_checkboxes = {}
            risky_checkboxes = {}
            unsafe_checkboxes = {}
            
            if installed_safe:
                widget, safe_checkboxes = self._make_checkbox_tab(
                    installed_safe, default_checked=True,
                )
                tab_widget.addTab(widget, f"Safe Packages ({len(installed_safe)})")
            
            if installed_risky:
                widget, risky_checkboxes = self._make_checkbox_tab(
                    installed_risky, default_checked=False,
                )
                tab_widget.addTab(widget, f"Risky Packages ({len(installed_risky)})")
            
            if installed_unsafe:
                widget, unsafe_checkboxes = self._make_checkbox_tab(
                    installed_unsafe,
                    default_checked=False,
                    header_text=(
                        "⚠️ WARNING: These packages are UNSAFE to remove!\n"
                        "Removing them WILL break your device "
                        "(bootloop, no camera, no network, etc.)\n"
                        "Only select if you understand the risks and have a backup/recovery plan."
                    ),
                    checkbox_style="QCheckBox { color: #cc0000; font-weight: bold; }",
                )
                for pkg, cb in unsafe_checkboxes.items():
                    cb.setText(f"🔒 {pkg} [UNSAFE]")
                tab_widget.addTab(widget, f"Unsafe Packages ({len(installed_unsafe)})")
            
            action_label = QLabel("Action:")
            layout.addWidget(action_label)
            
            action_group = QButtonGroup(dialog)
            action_frame = QHBoxLayout()
            disable_radio = QRadioButton("Disable (can be re-enabled)")
            disable_radio.setChecked(True)
            action_group.addButton(disable_radio, 0)
            action_frame.addWidget(disable_radio)
            uninstall_radio = QRadioButton("Uninstall for user (can be restored)")
            action_group.addButton(uninstall_radio, 1)
            action_frame.addWidget(uninstall_radio)
            action_frame.addStretch()
            layout.addLayout(action_frame)
            
            def do_degoogle():
                action = "disable" if disable_radio.isChecked() else "uninstall"
                selected_safe = [p for p, cb in safe_checkboxes.items() if cb.isChecked()]
                selected_risky = [p for p, cb in risky_checkboxes.items() if cb.isChecked()]
                selected_unsafe = [p for p, cb in unsafe_checkboxes.items() if cb.isChecked()]
                selected_packages = selected_safe + selected_risky + selected_unsafe
                
                if not selected_packages:
                    QMessageBox.warning(
                        dialog, "No Selection",
                        "Please select at least one package to remove.",
                    )
                    return
                
                if selected_unsafe:
                    preview = [
                        "⚠️ CRITICAL WARNING ⚠️", "",
                        f"You have selected {len(selected_unsafe)} UNSAFE package(s):", "",
                    ]
                    preview.extend(f"• {p}" for p in selected_unsafe[:5])
                    if len(selected_unsafe) > 5:
                        preview.append(f"... and {len(selected_unsafe) - 5} more")
                    preview.extend([
                        "",
                        "Removing these WILL break your device!",
                        "Possible consequences:",
                        "• Bootloop (device won't start)",
                        "• No camera functionality",
                        "• No network/mobile data",
                        "• No launcher (black screen)",
                        "• Failed OTA updates",
                        "• Broken notifications",
                        "",
                        "Are you absolutely sure you want to proceed?",
                    ])
                    reply = QMessageBox.critical(
                        dialog,
                        "⚠️ DANGER - Unsafe Packages Selected",
                        "\n".join(preview),
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    if reply != QMessageBox.StandardButton.Yes:
                        return
                
                dialog.accept()
                
                self.log(f"Starting DeGoogle process for {len(selected_packages)} packages...")
                self.update_status("DeGoogling device...")
                
                def process_degoogle():
                    disabled, uninstalled, failed = self._process_packages(
                        selected_packages, action,
                    )
                    self._save_degoogle_action(action, disabled, uninstalled)
                    
                    processed_unsafe = [
                        p for p in (disabled + uninstalled) if p in selected_unsafe
                    ]
                    msg = self._build_results_message(
                        action, disabled, uninstalled, failed,
                        processed_unsafe=processed_unsafe,
                    )
                    self.update_status("DeGoogle completed")
                    QTimer.singleShot(
                        0, lambda: QMessageBox.information(self, "DeGoogle Complete", msg),
                    )
                
                threading.Thread(target=process_degoogle, daemon=True).start()
            
            button_frame = QHBoxLayout()
            button_frame.addStretch()
            cancel_btn = QPushButton("Cancel")
            cancel_btn.clicked.connect(dialog.reject)
            button_frame.addWidget(cancel_btn)
            degoogle_btn = QPushButton("DeGoogle")
            degoogle_btn.clicked.connect(do_degoogle)
            button_frame.addWidget(degoogle_btn)
            layout.addLayout(button_frame)
            
            if not installed_safe and not installed_risky and not installed_unsafe:
                no_packages_label = QLabel(
                    "No Google packages found on your device.\n\n"
                    "Either they are already removed, or your device "
                    "doesn't have them installed."
                )
                no_packages_label.setWordWrap(True)
                no_packages_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                no_packages_label.setStyleSheet(
                    "color: #666666; font-size: 12px; padding: 20px;"
                )
                layout.insertWidget(1, no_packages_label)
                degoogle_btn.setEnabled(False)
            
            self.log("About to show custom selection dialog...")
            dialog.setWindowFlags(
                Qt.WindowType.Dialog
                | Qt.WindowType.WindowTitleHint
                | Qt.WindowType.WindowCloseButtonHint
            )
            result = dialog.exec()
            self.log(f"Custom selection dialog closed with result: {result}")
        except Exception as e:
            import traceback
            self.log(f"Error showing degoogle selection dialog: {e}", "ERROR")
            self.log(f"Traceback: {traceback.format_exc()}", "ERROR")
            QMessageBox.critical(self, "Error", f"Failed to show dialog: {e}")
    
    def undo_degoogle(self):
        """Undo DeGoogle - restore disabled/uninstalled Google apps with selection."""
        if not self.current_device:
            QMessageBox.warning(self, "No Device", "Please select a device first")
            return
        
        device_id = self.current_device
        known = set(ALL_DEGOOGLE_PACKAGES)
        
        state = self.degoogle_state.get(device_id, {})
        saved_disabled = [pkg for pkg in state.get('disabled', []) if pkg in known]
        saved_uninstalled = [pkg for pkg in state.get('uninstalled', []) if pkg in known]
        
        if saved_disabled or saved_uninstalled:
            self.show_restore_dialog(device_id, saved_disabled, saved_uninstalled)
        else:
            QMessageBox.information(
                self, "Nothing to Restore",
                "No disabled or uninstalled Google packages found in saved state.",
            )
    
    def show_restore_dialog(self, device_id, disabled_packages, uninstalled_packages):
        """Show the restore selection dialog."""
        if not disabled_packages and not uninstalled_packages:
            QMessageBox.information(
                self, "Nothing to Restore",
                "No disabled or uninstalled Google packages found on device or in saved state.",
            )
            return
        
        dialog = QDialog(self)
        dialog.setWindowTitle("Restore DeGoogled Packages")
        dialog.setMinimumSize(600, 700)
        dialog.setModal(True)
        
        layout = QVBoxLayout(dialog)
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)
        
        title_label = QLabel("Select packages to restore")
        title_font = QFont()
        title_font.setPointSize(12)
        title_font.setBold(True)
        title_label.setFont(title_font)
        layout.addWidget(title_label)
        
        info_label = QLabel(
            "Select which packages you want to restore.\n"
            "Disabled packages can be re-enabled.\n"
            "Uninstalled packages will be reinstalled for your user account."
        )
        info_label.setWordWrap(True)
        info_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(info_label)
        
        tab_widget = QTabWidget()
        layout.addWidget(tab_widget)
        
        disabled_checkboxes = {}
        uninstalled_checkboxes = {}
        
        if disabled_packages:
            widget, disabled_checkboxes = self._make_checkbox_tab(
                disabled_packages, default_checked=True,
            )
            tab_widget.addTab(widget, f"Disabled ({len(disabled_packages)})")
        
        if uninstalled_packages:
            widget, uninstalled_checkboxes = self._make_checkbox_tab(
                uninstalled_packages, default_checked=True,
            )
            tab_widget.addTab(widget, f"Uninstalled ({len(uninstalled_packages)})")
        
        # Select all / Deselect all buttons
        button_frame_top = QHBoxLayout()
        
        def set_all(checkboxes, value):
            for cb in checkboxes.values():
                cb.setChecked(value)
        
        if disabled_packages:
            select_all_disabled_btn = QPushButton("Select All Disabled")
            select_all_disabled_btn.clicked.connect(lambda: set_all(disabled_checkboxes, True))
            button_frame_top.addWidget(select_all_disabled_btn)
            deselect_all_disabled_btn = QPushButton("Deselect All Disabled")
            deselect_all_disabled_btn.clicked.connect(lambda: set_all(disabled_checkboxes, False))
            button_frame_top.addWidget(deselect_all_disabled_btn)
        
        if uninstalled_packages:
            select_all_uninstalled_btn = QPushButton("Select All Uninstalled")
            select_all_uninstalled_btn.clicked.connect(lambda: set_all(uninstalled_checkboxes, True))
            button_frame_top.addWidget(select_all_uninstalled_btn)
            deselect_all_uninstalled_btn = QPushButton("Deselect All Uninstalled")
            deselect_all_uninstalled_btn.clicked.connect(lambda: set_all(uninstalled_checkboxes, False))
            button_frame_top.addWidget(deselect_all_uninstalled_btn)
        
        button_frame_top.addStretch()
        layout.addLayout(button_frame_top)
        
        def do_restore():
            selected_disabled = [p for p, cb in disabled_checkboxes.items() if cb.isChecked()]
            selected_uninstalled = [p for p, cb in uninstalled_checkboxes.items() if cb.isChecked()]
            
            if not selected_disabled and not selected_uninstalled:
                QMessageBox.warning(
                    dialog, "No Selection",
                    "Please select at least one package to restore.",
                )
                return
            
            dialog.accept()
            total = len(selected_disabled) + len(selected_uninstalled)
            reply = QMessageBox.question(
                self, "Confirm Restore",
                f"Restore {total} package(s)?\n\n"
                f"Disabled: {len(selected_disabled)}\n"
                f"Uninstalled: {len(selected_uninstalled)}\n\n"
                f"Uninstalled packages will be reinstalled for your user account.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            
            self.log(f"Restoring {total} packages...")
            self.update_status("Restoring packages...")
            
            def do_restore_work():
                restored = []
                failed = []
                flag = self.get_device_flag()
                all_packages = selected_disabled + selected_uninstalled
                
                for i, package in enumerate(all_packages, start=1):
                    self.log(f"Restoring {i}/{len(all_packages)}: {package}")
                    errors = []
                    
                    # install-existing handles uninstalled packages.
                    r1 = self.adb.run_command(f"{flag} shell pm install-existing {package}")
                    if r1['success']:
                        output = (r1.get('stdout') or '').strip()
                        self.log(f"Reinstalled: {package} (output: {output})")
                        restored.append(package)
                        continue
                    errors.append(f"install-existing: {r1.get('stderr') or r1.get('stdout') or 'Unknown error'}")
                    
                    # Fall back to pm enable for disabled packages.
                    r2 = self.adb.run_command(f"{flag} shell pm enable {package}")
                    if r2['success']:
                        self.log(f"Enabled: {package}")
                        restored.append(package)
                        continue
                    errors.append(f"enable: {r2.get('stderr') or r2.get('stdout') or 'Unknown error'}")
                    
                    error_msg = " | ".join(errors)
                    failed.append((package, error_msg))
                    self.log(f"Failed to restore {package}: {error_msg}", "ERROR")
                
                # Update saved state - drop restored packages from each list.
                state = self.degoogle_state.get(device_id, {})
                if restored:
                    restored_set = set(restored)
                    for key in ('disabled', 'uninstalled'):
                        remaining = [p for p in state.get(key, []) if p not in restored_set]
                        if remaining:
                            state[key] = remaining
                        else:
                            state.pop(key, None)
                
                # If nothing meaningful remains, drop the device entry entirely.
                if not state.get('disabled') and not state.get('uninstalled'):
                    self.degoogle_state.pop(device_id, None)
                
                self.save_degoogle_state()
                
                result_msg = f"Restore completed!\n\nRestored: {len(restored)} packages"
                if failed:
                    result_msg += f"\nFailed: {len(failed)} packages"
                
                self.update_status("Restore completed")
                QTimer.singleShot(
                    0, lambda: QMessageBox.information(self, "Restore Complete", result_msg),
                )
            
            threading.Thread(target=do_restore_work, daemon=True).start()
        
        # Buttons
        button_frame = QHBoxLayout()
        button_frame.addStretch()
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dialog.reject)
        button_frame.addWidget(cancel_btn)
        
        restore_btn = QPushButton("Restore Selected")
        restore_btn.clicked.connect(do_restore)
        button_frame.addWidget(restore_btn)
        
        layout.addLayout(button_frame)
        
        # Show dialog
        dialog.exec()


# Custom MIME types used by the file explorer for intra-app drag-and-drop.
# Newline-separated UTF-8 paths.
_MIME_LOCAL_PATHS = "application/x-adb-local-paths"
_MIME_REMOTE_PATHS = "application/x-adb-remote-paths"


class _ExplorerTree(QTreeWidget):
    """QTreeWidget subclass with file-explorer drag-and-drop wiring.

    `side` is either "local" or "remote" and determines which mime types this
    tree produces (when dragging out) and accepts (when dropped on).
    The owning ``FileExplorerDialog`` is consulted at drop time to figure out
    the destination directory (the current pane path, or the folder under the
    cursor when the user dropped onto a folder item).
    """

    def __init__(self, side, dialog):
        super().__init__()
        self._side = side
        self._dialog = dialog
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

    # ---- drag out --------------------------------------------------------

    def startDrag(self, supported_actions):
        items = self.selectedItems()
        if not items:
            return
        mime = QMimeData()
        if self._side == "local":
            base = self._dialog.local_path
            urls, paths = [], []
            for it in items:
                data = it.data(0, Qt.ItemDataRole.UserRole)
                if not data:
                    continue
                full = os.path.join(base, data["name"])
                urls.append(QUrl.fromLocalFile(full))
                paths.append(full)
            if not paths:
                return
            mime.setUrls(urls)  # lets Windows Explorer accept the drop
            mime.setData(
                _MIME_LOCAL_PATHS, "\n".join(paths).encode("utf-8")
            )
            mime.setText("\n".join(paths))
        else:
            base = self._dialog.remote_path
            paths = []
            for it in items:
                data = it.data(0, Qt.ItemDataRole.UserRole)
                if not data:
                    continue
                paths.append(self._dialog._posix_join(base, data["name"]))
            if not paths:
                return
            mime.setData(
                _MIME_REMOTE_PATHS, "\n".join(paths).encode("utf-8")
            )
            mime.setText("\n".join(paths))

        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)

    # ---- drag in ---------------------------------------------------------

    def _can_accept(self, mime):
        # Don't accept drops while a transfer is already running.
        if self._dialog._transfer_busy:
            return False
        if self._side == "local":
            # Pulling from device → local.
            return mime.hasFormat(_MIME_REMOTE_PATHS)
        # Remote side accepts internal local paths or external file URLs
        # from Windows Explorer. We explicitly reject our own remote mime to
        # avoid a no-op intra-pane "move".
        if mime.hasFormat(_MIME_REMOTE_PATHS):
            return False
        return mime.hasFormat(_MIME_LOCAL_PATHS) or mime.hasUrls()

    def dragEnterEvent(self, event):
        if self._can_accept(event.mimeData()):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._can_accept(event.mimeData()):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.acceptProposedAction()
        else:
            event.ignore()

    def _target_item_data(self, event):
        """Return the data dict of the item under the drop cursor (or None)."""
        try:
            pos = event.position().toPoint()
        except AttributeError:
            pos = event.pos()
        item = self.itemAt(pos)
        if item is None:
            return None
        return item.data(0, Qt.ItemDataRole.UserRole)

    def dropEvent(self, event):
        mime = event.mimeData()
        if not self._can_accept(mime):
            event.ignore()
            return

        target = self._target_item_data(event)

        if self._side == "local":
            raw = bytes(mime.data(_MIME_REMOTE_PATHS)).decode("utf-8", errors="replace")
            sources = [p for p in raw.splitlines() if p]
            if target and target.get("is_dir"):
                dest = os.path.join(self._dialog.local_path, target["name"])
            else:
                dest = self._dialog.local_path
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            self._dialog._pull_paths_to_local(sources, dest)
            return

        # Remote pane: collect local paths from either intra-app mime or
        # a Windows Explorer drag (text/uri-list).
        sources = []
        if mime.hasFormat(_MIME_LOCAL_PATHS):
            raw = bytes(mime.data(_MIME_LOCAL_PATHS)).decode("utf-8", errors="replace")
            sources = [p for p in raw.splitlines() if p]
        elif mime.hasUrls():
            for url in mime.urls():
                local = url.toLocalFile()
                if local:
                    sources.append(local)
        if not sources:
            event.ignore()
            return
        if target and target.get("is_dir"):
            dest = self._dialog._posix_join(self._dialog.remote_path, target["name"])
        else:
            dest = self._dialog.remote_path
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        self._dialog._push_local_paths_to_remote(sources, dest)


class FileExplorerDialog(QDialog):
    """Two-pane file explorer for transferring files between PC and Android device.

    Left pane shows the local filesystem, right pane shows the remote (device)
    filesystem. Files / folders can be navigated, transferred, created, renamed
    and deleted. Items can be dragged either direction (or into / out of
    Windows Explorer). All ADB operations run on background threads so the UI
    stays responsive.
    """

    # Signals for thread-safe UI updates from worker threads.
    remote_listing_ready = pyqtSignal(str, list, str)  # path, entries, error_message
    transfer_finished = pyqtSignal(str, bool, str)     # operation, success, message
    remote_action_finished = pyqtSignal(bool, str, str)  # success, message, refresh_path

    REMOTE_QUICK_PATHS = [
        ("Internal Storage", "/sdcard/"),
        ("Downloads", "/sdcard/Download/"),
        ("DCIM (Camera)", "/sdcard/DCIM/"),
        ("Pictures", "/sdcard/Pictures/"),
        ("Movies", "/sdcard/Movies/"),
        ("Music", "/sdcard/Music/"),
        ("Documents", "/sdcard/Documents/"),
        ("Android data", "/sdcard/Android/"),
        ("Tmp", "/data/local/tmp/"),
        ("Root", "/"),
    ]

    def __init__(self, parent_gui):
        super().__init__(parent_gui)
        self.gui = parent_gui
        self.adb = parent_gui.adb

        self.local_path = self._initial_local_path()
        self.remote_path = "/sdcard/"
        self._remote_busy = False
        self._transfer_busy = False

        self.setWindowTitle("File Explorer — PC ↔ Android")
        self.setMinimumSize(1000, 600)
        self.resize(1300, 760)

        self._build_ui()
        self._wire_signals()

        self.refresh_local()
        self.refresh_remote()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    @staticmethod
    def _initial_local_path():
        for path in (os.path.expanduser("~"), os.getcwd()):
            if path and os.path.isdir(path):
                return path
        return os.path.abspath(os.sep)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        header = QLabel(
            "Browse PC and device side-by-side. Double-click folders to "
            "navigate. Drag items between panes — or in/out of Windows "
            "Explorer — to transfer them. Drop onto a folder row to push or "
            "pull directly into that folder. The Push / Pull buttons act on "
            "the current selection."
        )
        header.setWordWrap(True)
        colors = self.gui.colors
        header.setStyleSheet(f"color: {colors['text_secondary']}; padding: 2px 4px;")
        outer.addWidget(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(splitter, 1)

        splitter.addWidget(self._build_local_pane())
        splitter.addWidget(self._build_actions_pane())
        splitter.addWidget(self._build_remote_pane())

        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 0)
        splitter.setStretchFactor(2, 5)
        splitter.setSizes([550, 140, 550])

        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet(f"color: {colors['text_secondary']}; padding: 4px 2px;")
        outer.addWidget(self.status_label)

    def _build_local_pane(self):
        group = QGroupBox("💻 This PC")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(8, 14, 8, 8)
        layout.setSpacing(5)

        # Path bar
        path_row = QHBoxLayout()
        path_row.setSpacing(4)

        up_btn = QToolButton()
        up_btn.setText("⬆")
        up_btn.setToolTip("Go to parent folder")
        up_btn.clicked.connect(self.go_up_local)
        path_row.addWidget(up_btn)

        home_btn = QToolButton()
        home_btn.setText("🏠")
        home_btn.setToolTip("Home folder")
        home_btn.clicked.connect(lambda: self._navigate_local(os.path.expanduser("~")))
        path_row.addWidget(home_btn)

        refresh_btn = QToolButton()
        refresh_btn.setText("⟳")
        refresh_btn.setToolTip("Refresh")
        refresh_btn.clicked.connect(self.refresh_local)
        path_row.addWidget(refresh_btn)

        browse_btn = QToolButton()
        browse_btn.setText("📁")
        browse_btn.setToolTip("Browse for folder…")
        browse_btn.clicked.connect(self._browse_local_folder)
        path_row.addWidget(browse_btn)

        self.local_path_edit = QLineEdit(self.local_path)
        self.local_path_edit.returnPressed.connect(
            lambda: self._navigate_local(self.local_path_edit.text().strip())
        )
        path_row.addWidget(self.local_path_edit, 1)

        layout.addLayout(path_row)

        # Tree
        self.local_tree = _ExplorerTree("local", self)
        self.local_tree.setColumnCount(3)
        self.local_tree.setHeaderLabels(["Name", "Size", "Modified"])
        self.local_tree.setRootIsDecorated(False)
        self.local_tree.setUniformRowHeights(True)
        self.local_tree.setSortingEnabled(True)
        self.local_tree.setAlternatingRowColors(True)
        self.local_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.local_tree.itemDoubleClicked.connect(self._on_local_double_clicked)
        self.local_tree.customContextMenuRequested.connect(self._show_local_context_menu)
        header = self.local_tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.local_tree, 1)

        # Footer summary
        self.local_summary = QLabel("")
        self.local_summary.setStyleSheet(
            f"color: {self.gui.colors['text_tertiary']}; font-size: 8pt;"
        )
        layout.addWidget(self.local_summary)

        return group

    def _build_remote_pane(self):
        group = QGroupBox("📱 Device")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(8, 14, 8, 8)
        layout.setSpacing(5)

        # Path bar
        path_row = QHBoxLayout()
        path_row.setSpacing(4)

        up_btn = QToolButton()
        up_btn.setText("⬆")
        up_btn.setToolTip("Go to parent folder")
        up_btn.clicked.connect(self.go_up_remote)
        path_row.addWidget(up_btn)

        home_btn = QToolButton()
        home_btn.setText("🏠")
        home_btn.setToolTip("Internal storage (/sdcard/)")
        home_btn.clicked.connect(lambda: self._navigate_remote("/sdcard/"))
        path_row.addWidget(home_btn)

        refresh_btn = QToolButton()
        refresh_btn.setText("⟳")
        refresh_btn.setToolTip("Refresh")
        refresh_btn.clicked.connect(self.refresh_remote)
        path_row.addWidget(refresh_btn)

        self.remote_quick = QComboBox()
        self.remote_quick.addItem("Quick paths…", "")
        for label, path in self.REMOTE_QUICK_PATHS:
            self.remote_quick.addItem(f"{label}  ({path})", path)
        self.remote_quick.activated.connect(self._on_remote_quick_chosen)
        self.remote_quick.setMinimumContentsLength(14)
        path_row.addWidget(self.remote_quick)

        self.remote_path_edit = QLineEdit(self.remote_path)
        self.remote_path_edit.returnPressed.connect(
            lambda: self._navigate_remote(self.remote_path_edit.text().strip())
        )
        path_row.addWidget(self.remote_path_edit, 1)

        layout.addLayout(path_row)

        # Tree
        self.remote_tree = _ExplorerTree("remote", self)
        self.remote_tree.setColumnCount(3)
        self.remote_tree.setHeaderLabels(["Name", "Size", "Modified"])
        self.remote_tree.setRootIsDecorated(False)
        self.remote_tree.setUniformRowHeights(True)
        self.remote_tree.setSortingEnabled(True)
        self.remote_tree.setAlternatingRowColors(True)
        self.remote_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.remote_tree.itemDoubleClicked.connect(self._on_remote_double_clicked)
        self.remote_tree.customContextMenuRequested.connect(self._show_remote_context_menu)
        header = self.remote_tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.remote_tree, 1)

        self.remote_summary = QLabel("")
        self.remote_summary.setStyleSheet(
            f"color: {self.gui.colors['text_tertiary']}; font-size: 8pt;"
        )
        layout.addWidget(self.remote_summary)

        return group

    def _build_actions_pane(self):
        wrapper = QWidget()
        wrapper.setMinimumWidth(120)
        wrapper.setMaximumWidth(170)
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(4, 30, 4, 8)
        layout.setSpacing(8)

        push_btn = QPushButton("Push  ➡")
        push_btn.setToolTip("Send selected PC items to the current folder on the device")
        push_btn.setProperty("accent", "true")
        push_btn.clicked.connect(self.push_selected)
        layout.addWidget(push_btn)

        pull_btn = QPushButton("⬅  Pull")
        pull_btn.setToolTip("Copy selected device items to the current folder on the PC")
        pull_btn.setProperty("accent", "true")
        pull_btn.clicked.connect(self.pull_selected)
        layout.addWidget(pull_btn)

        layout.addSpacing(12)

        new_local = QPushButton("📁+ PC Folder")
        new_local.setToolTip("Create a new folder on the PC side")
        new_local.clicked.connect(self._create_local_folder)
        layout.addWidget(new_local)

        new_remote = QPushButton("📁+ Device Folder")
        new_remote.setToolTip("Create a new folder on the device side")
        new_remote.clicked.connect(self._create_remote_folder)
        layout.addWidget(new_remote)

        layout.addStretch(1)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

        self.push_btn = push_btn
        self.pull_btn = pull_btn
        return wrapper

    def _wire_signals(self):
        self.remote_listing_ready.connect(self._on_remote_listing_ready)
        self.transfer_finished.connect(self._on_transfer_finished)
        self.remote_action_finished.connect(self._on_remote_action_finished)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_status(self, message, level="info"):
        self.status_label.setText(message)
        if level == "error":
            color = self.gui.colors["error"]
        elif level == "success":
            color = self.gui.colors["success"]
        elif level == "warning":
            color = self.gui.colors["warning"]
        else:
            color = self.gui.colors["text_secondary"]
        self.status_label.setStyleSheet(f"color: {color}; padding: 4px 2px;")

    def _set_busy(self, busy):
        """Disable transfer buttons while a long-running ADB op is happening."""
        self._transfer_busy = busy
        self.push_btn.setEnabled(not busy)
        self.pull_btn.setEnabled(not busy)

    def _ensure_device(self):
        if not self.gui.current_device:
            QMessageBox.warning(self, "No Device", "Please select a device first.")
            return False
        return True

    @staticmethod
    def _format_size(size):
        if size is None:
            return ""
        try:
            size = int(size)
        except (TypeError, ValueError):
            return str(size)
        if size < 1024:
            return f"{size} B"
        for unit in ("KB", "MB", "GB", "TB"):
            size /= 1024.0
            if size < 1024:
                return f"{size:.1f} {unit}"
        return f"{size:.1f} PB"

    @staticmethod
    def _posix_join(base, name):
        if not base.endswith("/"):
            base += "/"
        return base + name

    @staticmethod
    def _posix_parent(path):
        path = path.rstrip("/") or "/"
        if path == "/":
            return "/"
        idx = path.rfind("/")
        if idx <= 0:
            return "/"
        return path[:idx] + "/"

    @staticmethod
    def _shell_quote(path):
        """Quote ``path`` for use inside an `adb shell` command.

        Uses POSIX double-quote escaping (only \\, ", $, ` are special inside
        "..."). Double quotes are chosen over single quotes because Python's
        ``shlex.split(posix=False)`` (used by ``ADBManager.run_command``)
        understands ``"..."`` as a single token but does not collapse the POSIX
        ``'\\''`` apostrophe-escape sequence — which would split paths
        containing apostrophes into multiple tokens. With the double-quoted
        form, common Android paths (including those with spaces or
        apostrophes) round-trip safely through shlex → Windows command line →
        adb → device shell.
        """
        escaped = (
            path.replace("\\", "\\\\")
                .replace('"', '\\"')
                .replace("$", "\\$")
                .replace("`", "\\`")
        )
        return '"' + escaped + '"'

    # ------------------------------------------------------------------
    # LOCAL side
    # ------------------------------------------------------------------

    def refresh_local(self):
        path = self.local_path
        try:
            entries = []
            with os.scandir(path) as it:
                for entry in it:
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                        stat = entry.stat(follow_symlinks=False)
                        size = None if is_dir else stat.st_size
                        modified = datetime.fromtimestamp(stat.st_mtime).strftime(
                            "%Y-%m-%d %H:%M"
                        )
                    except OSError:
                        is_dir = False
                        size = None
                        modified = ""
                    entries.append(
                        {
                            "name": entry.name,
                            "is_dir": is_dir,
                            "size": size,
                            "modified": modified,
                            "is_link": entry.is_symlink(),
                        }
                    )
        except PermissionError as e:
            QMessageBox.warning(self, "Permission Denied", f"Cannot list folder:\n{e}")
            return
        except OSError as e:
            QMessageBox.warning(self, "Error", f"Cannot list folder:\n{e}")
            return

        self.local_path_edit.setText(self.local_path)
        self.local_tree.setSortingEnabled(False)
        self.local_tree.clear()

        # Folders first, then files; alphabetical case-insensitive.
        entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))

        dir_count = file_count = 0
        for e in entries:
            item = QTreeWidgetItem()
            icon = "📁" if e["is_dir"] else ("🔗" if e["is_link"] else "📄")
            item.setText(0, f"{icon}  {e['name']}")
            item.setText(1, "" if e["is_dir"] else self._format_size(e["size"]))
            item.setText(2, e["modified"])
            # Sort key for size column
            sort_size = -1 if e["is_dir"] else (e["size"] or 0)
            item.setData(1, Qt.ItemDataRole.UserRole, sort_size)
            item.setData(0, Qt.ItemDataRole.UserRole, e)
            self.local_tree.addTopLevelItem(item)
            if e["is_dir"]:
                dir_count += 1
            else:
                file_count += 1

        self.local_tree.setSortingEnabled(True)
        self.local_summary.setText(
            f"{dir_count} folder(s), {file_count} file(s)"
        )

    def _navigate_local(self, path):
        path = os.path.expanduser(path)
        if not path:
            return
        try:
            path = os.path.abspath(path)
        except OSError:
            QMessageBox.warning(self, "Invalid Path", f"Could not resolve:\n{path}")
            return
        if not os.path.isdir(path):
            QMessageBox.warning(self, "Not a Folder", f"Not a folder:\n{path}")
            self.local_path_edit.setText(self.local_path)
            return
        self.local_path = path
        self.refresh_local()

    def go_up_local(self):
        parent = os.path.dirname(self.local_path.rstrip(os.sep)) or self.local_path
        if parent and parent != self.local_path:
            self._navigate_local(parent)

    def _browse_local_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Select folder", self.local_path)
        if path:
            self._navigate_local(path)

    def _on_local_double_clicked(self, item, column):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        full = os.path.join(self.local_path, data["name"])
        if data["is_dir"]:
            self._navigate_local(full)

    def _selected_local_paths(self):
        items = self.local_tree.selectedItems()
        paths = []
        for it in items:
            data = it.data(0, Qt.ItemDataRole.UserRole)
            if data:
                paths.append(os.path.join(self.local_path, data["name"]))
        return paths

    def _show_local_context_menu(self, pos):
        menu = QMenu(self)
        items = self.local_tree.selectedItems()
        push_action = menu.addAction("Push to device →")
        push_action.setEnabled(bool(items))
        push_action.triggered.connect(self.push_selected)

        rename_action = menu.addAction("Rename…")
        rename_action.setEnabled(len(items) == 1)
        rename_action.triggered.connect(self._rename_local)

        delete_action = menu.addAction("Delete")
        delete_action.setEnabled(bool(items))
        delete_action.triggered.connect(self._delete_local)

        menu.addSeparator()
        new_folder = menu.addAction("New folder…")
        new_folder.triggered.connect(self._create_local_folder)
        refresh_action = menu.addAction("Refresh")
        refresh_action.triggered.connect(self.refresh_local)

        menu.exec(self.local_tree.viewport().mapToGlobal(pos))

    def _create_local_folder(self):
        name, ok = QInputDialog.getText(self, "New Folder (PC)", "Folder name:")
        if not ok or not name.strip():
            return
        target = os.path.join(self.local_path, name.strip())
        try:
            os.makedirs(target, exist_ok=False)
        except FileExistsError:
            QMessageBox.warning(self, "Already Exists", f"{target}\nalready exists.")
            return
        except OSError as e:
            QMessageBox.warning(self, "Error", f"Failed to create folder:\n{e}")
            return
        self.refresh_local()

    def _rename_local(self):
        items = self.local_tree.selectedItems()
        if len(items) != 1:
            return
        data = items[0].data(0, Qt.ItemDataRole.UserRole)
        old_name = data["name"]
        new_name, ok = QInputDialog.getText(
            self, "Rename (PC)", "New name:", text=old_name
        )
        if not ok or not new_name.strip() or new_name == old_name:
            return
        old_path = os.path.join(self.local_path, old_name)
        new_path = os.path.join(self.local_path, new_name.strip())
        try:
            os.rename(old_path, new_path)
        except OSError as e:
            QMessageBox.warning(self, "Error", f"Failed to rename:\n{e}")
            return
        self.refresh_local()

    def _delete_local(self):
        items = self.local_tree.selectedItems()
        if not items:
            return
        names = [it.data(0, Qt.ItemDataRole.UserRole)["name"] for it in items]
        msg = "Delete the following items from the PC?\n\n" + "\n".join(
            f"• {n}" for n in names[:10]
        )
        if len(names) > 10:
            msg += f"\n…and {len(names) - 10} more"
        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        import shutil
        errors = []
        for name in names:
            target = os.path.join(self.local_path, name)
            try:
                if os.path.isdir(target) and not os.path.islink(target):
                    shutil.rmtree(target)
                else:
                    os.remove(target)
            except OSError as e:
                errors.append(f"{name}: {e}")
        self.refresh_local()
        if errors:
            QMessageBox.warning(self, "Some items failed", "\n".join(errors))

    # ------------------------------------------------------------------
    # REMOTE side
    # ------------------------------------------------------------------

    def refresh_remote(self):
        if not self.gui.current_device:
            self.remote_tree.clear()
            self.remote_summary.setText("(no device selected)")
            return
        if self._remote_busy:
            return
        self._remote_busy = True
        self._set_status(f"Listing {self.remote_path}…")
        path = self.remote_path
        threading.Thread(
            target=self._remote_listing_worker, args=(path,), daemon=True
        ).start()

    def _remote_listing_worker(self, path):
        # Normalise path with trailing slash for clarity in display, but
        # ls itself works either way.
        flag = self.gui.get_device_flag()
        quoted = self._shell_quote(path)
        # `-A` excludes . and ..; `-l` long format. `--color=never` is
        # tolerated/ignored on toybox. The shell args are passed as
        # individual tokens (NOT wrapped in an outer "...") so that
        # `adb shell` joins them with spaces and the device shell parses
        # them; wrapping them would send the literal quotes to the device.
        cmd = f"{flag} shell ls -lA --color=never {quoted}"
        result = self.adb.run_command(cmd, timeout=30)
        if not result["success"]:
            err = (result.get("stderr") or result.get("stdout") or "Unknown error").strip()
            self.remote_listing_ready.emit(path, [], err)
            return
        entries, parse_err = self._parse_ls_output(result["stdout"])
        self.remote_listing_ready.emit(path, entries, parse_err or "")

    @staticmethod
    def _parse_ls_output(text):
        """Parse `ls -lA` output from Android (toybox) into a list of dicts.

        Returns (entries, error_message). error_message is non-empty only if
        nothing could be parsed but text was non-empty.
        """
        entries = []
        if not text:
            return entries, ""
        for raw in text.splitlines():
            line = raw.rstrip()
            if not line or line.startswith("total "):
                continue
            # Skip `ls: <path>: Permission denied` style errors but bubble up.
            if line.startswith("ls:"):
                continue
            # Expected: perms links owner group size date time name [-> target]
            parts = line.split(None, 7)
            if len(parts) < 8:
                # Some block/char devices have an extra "size, minor" column;
                # try splitting with one more field.
                parts2 = line.split(None, 8)
                if len(parts2) >= 9 and parts[0][:1] in ("b", "c"):
                    perms = parts2[0]
                    name_field = parts2[8]
                    is_dir = perms.startswith("d")
                    is_link = perms.startswith("l")
                    name = name_field.split(" -> ", 1)[0]
                    if name in (".", ".."):
                        continue
                    entries.append(
                        {
                            "name": name,
                            "is_dir": is_dir,
                            "is_link": is_link,
                            "size": None,
                            "modified": "",
                            "perms": perms,
                            "link_target": None,
                        }
                    )
                continue

            perms, _links, _owner, _group, size, date, time_str, name_field = parts
            is_dir = perms.startswith("d")
            is_link = perms.startswith("l")
            link_target = None
            if is_link and " -> " in name_field:
                name, link_target = name_field.split(" -> ", 1)
            else:
                name = name_field
            if name in (".", ".."):
                continue
            try:
                size_val = int(size)
            except ValueError:
                size_val = None
            entries.append(
                {
                    "name": name,
                    "is_dir": is_dir,
                    "is_link": is_link,
                    "size": None if is_dir else size_val,
                    "modified": f"{date} {time_str}",
                    "perms": perms,
                    "link_target": link_target,
                }
            )
        return entries, ""

    def _on_remote_listing_ready(self, path, entries, error):
        self._remote_busy = False
        # If the user moved on to a different path while we were listing,
        # ignore stale results.
        if path != self.remote_path:
            return

        self.remote_path_edit.setText(self.remote_path)
        self.remote_tree.setSortingEnabled(False)
        self.remote_tree.clear()

        if error and not entries:
            self._set_status(f"Error: {error}", "error")
            self.remote_summary.setText("(error)")
            self.remote_tree.setSortingEnabled(True)
            return

        entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
        dir_count = file_count = 0
        for e in entries:
            item = QTreeWidgetItem()
            if e["is_dir"]:
                icon = "📁"
            elif e["is_link"]:
                icon = "🔗"
            else:
                icon = "📄"
            display_name = f"{icon}  {e['name']}"
            if e["is_link"] and e["link_target"]:
                display_name += f"  →  {e['link_target']}"
            item.setText(0, display_name)
            item.setText(1, "" if e["is_dir"] else self._format_size(e["size"]))
            item.setText(2, e["modified"])
            sort_size = -1 if e["is_dir"] else (e["size"] or 0)
            item.setData(1, Qt.ItemDataRole.UserRole, sort_size)
            item.setData(0, Qt.ItemDataRole.UserRole, e)
            self.remote_tree.addTopLevelItem(item)
            if e["is_dir"]:
                dir_count += 1
            else:
                file_count += 1

        self.remote_tree.setSortingEnabled(True)
        self.remote_summary.setText(
            f"{dir_count} folder(s), {file_count} file(s)"
        )
        self._set_status(f"Listed {self.remote_path}", "success")

    def _navigate_remote(self, path):
        path = (path or "").strip()
        if not path:
            return
        if not path.startswith("/"):
            path = self._posix_join(self.remote_path, path)
        if not path.endswith("/"):
            path += "/"
        self.remote_path = path
        self.refresh_remote()

    def go_up_remote(self):
        self._navigate_remote(self._posix_parent(self.remote_path))

    def _on_remote_quick_chosen(self, idx):
        path = self.remote_quick.itemData(idx) or ""
        if path:
            self._navigate_remote(path)
        # Reset to placeholder
        self.remote_quick.setCurrentIndex(0)

    def _on_remote_double_clicked(self, item, column):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        if data["is_dir"] or data["is_link"]:
            target = self._posix_join(self.remote_path, data["name"])
            self._navigate_remote(target)

    def _selected_remote_entries(self):
        items = self.remote_tree.selectedItems()
        out = []
        for it in items:
            data = it.data(0, Qt.ItemDataRole.UserRole)
            if data:
                out.append(data)
        return out

    def _show_remote_context_menu(self, pos):
        menu = QMenu(self)
        items = self.remote_tree.selectedItems()
        pull_action = menu.addAction("← Pull to PC")
        pull_action.setEnabled(bool(items))
        pull_action.triggered.connect(self.pull_selected)

        rename_action = menu.addAction("Rename…")
        rename_action.setEnabled(len(items) == 1)
        rename_action.triggered.connect(self._rename_remote)

        delete_action = menu.addAction("Delete")
        delete_action.setEnabled(bool(items))
        delete_action.triggered.connect(self._delete_remote)

        menu.addSeparator()
        new_folder = menu.addAction("New folder…")
        new_folder.triggered.connect(self._create_remote_folder)
        refresh_action = menu.addAction("Refresh")
        refresh_action.triggered.connect(self.refresh_remote)

        menu.exec(self.remote_tree.viewport().mapToGlobal(pos))

    def _create_remote_folder(self):
        if not self._ensure_device():
            return
        name, ok = QInputDialog.getText(self, "New Folder (Device)", "Folder name:")
        if not ok or not name.strip():
            return
        target = self._posix_join(self.remote_path, name.strip())
        flag = self.gui.get_device_flag()
        cmd = f"{flag} shell mkdir -p {self._shell_quote(target)}"
        self._set_status(f"Creating {target}…")

        def worker():
            r = self.adb.run_command(cmd, timeout=30)
            if r["success"] and not (r.get("stderr") or "").strip():
                self.remote_action_finished.emit(True, f"Created {target}", self.remote_path)
            else:
                err = (r.get("stderr") or r.get("stdout") or "Unknown error").strip()
                self.remote_action_finished.emit(False, f"mkdir failed: {err}", self.remote_path)

        threading.Thread(target=worker, daemon=True).start()

    def _rename_remote(self):
        if not self._ensure_device():
            return
        items = self.remote_tree.selectedItems()
        if len(items) != 1:
            return
        data = items[0].data(0, Qt.ItemDataRole.UserRole)
        old_name = data["name"]
        new_name, ok = QInputDialog.getText(
            self, "Rename (Device)", "New name:", text=old_name
        )
        if not ok or not new_name.strip() or new_name == old_name:
            return
        old_path = self._posix_join(self.remote_path, old_name)
        new_path = self._posix_join(self.remote_path, new_name.strip())
        flag = self.gui.get_device_flag()
        cmd = (
            f"{flag} shell mv {self._shell_quote(old_path)} "
            f"{self._shell_quote(new_path)}"
        )
        self._set_status(f"Renaming {old_name} → {new_name}…")

        def worker():
            r = self.adb.run_command(cmd, timeout=30)
            if r["success"] and not (r.get("stderr") or "").strip():
                self.remote_action_finished.emit(True, "Renamed", self.remote_path)
            else:
                err = (r.get("stderr") or r.get("stdout") or "Unknown error").strip()
                self.remote_action_finished.emit(False, f"Rename failed: {err}", self.remote_path)

        threading.Thread(target=worker, daemon=True).start()

    def _delete_remote(self):
        if not self._ensure_device():
            return
        entries = self._selected_remote_entries()
        if not entries:
            return
        names = [e["name"] for e in entries]
        msg = "Delete the following items from the device?\n\n" + "\n".join(
            f"• {n}" for n in names[:10]
        )
        if len(names) > 10:
            msg += f"\n…and {len(names) - 10} more"
        msg += "\n\nThis cannot be undone."
        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        flag = self.gui.get_device_flag()
        paths = [self._posix_join(self.remote_path, e["name"]) for e in entries]
        self._set_status(f"Deleting {len(paths)} item(s) on device…")

        def worker():
            errors = []
            for p in paths:
                cmd = f"{flag} shell rm -rf {self._shell_quote(p)}"
                r = self.adb.run_command(cmd, timeout=60)
                err = (r.get("stderr") or "").strip()
                if not r["success"] or err:
                    errors.append(f"{p}: {err or 'failed'}")
            if errors:
                self.remote_action_finished.emit(
                    False,
                    f"{len(errors)} item(s) failed:\n" + "\n".join(errors),
                    self.remote_path,
                )
            else:
                self.remote_action_finished.emit(
                    True, f"Deleted {len(paths)} item(s)", self.remote_path
                )

        threading.Thread(target=worker, daemon=True).start()

    def _on_remote_action_finished(self, success, message, refresh_path):
        if success:
            self._set_status(message, "success")
            self.gui.log(f"[FileExplorer] {message}")
        else:
            self._set_status(message.split("\n")[0], "error")
            self.gui.log(f"[FileExplorer] {message}", "ERROR")
            QMessageBox.warning(self, "Operation Failed", message)
        if refresh_path == self.remote_path:
            self.refresh_remote()

    # ------------------------------------------------------------------
    # PUSH / PULL
    # ------------------------------------------------------------------

    def push_selected(self):
        if not self._ensure_device():
            return
        sources = self._selected_local_paths()
        if not sources:
            QMessageBox.information(
                self, "Nothing selected",
                "Select one or more items on the PC side to push.",
            )
            return
        self._push_local_paths_to_remote(sources, self.remote_path)

    def pull_selected(self):
        if not self._ensure_device():
            return
        entries = self._selected_remote_entries()
        if not entries:
            QMessageBox.information(
                self, "Nothing selected",
                "Select one or more items on the device side to pull.",
            )
            return
        sources = [
            self._posix_join(self.remote_path, e["name"]) for e in entries
        ]
        self._pull_paths_to_local(sources, self.local_path)

    def _push_local_paths_to_remote(self, sources, dest_dir):
        """Push a list of absolute local paths to ``dest_dir`` on the device."""
        if self._transfer_busy:
            self._set_status("Another transfer is already running.", "warning")
            return
        if not self._ensure_device():
            return
        sources = [s for s in (sources or []) if s]
        if not sources:
            return
        if not dest_dir:
            dest_dir = self.remote_path
        if not dest_dir.endswith("/"):
            dest_dir += "/"

        flag = self.gui.get_device_flag()
        self._set_busy(True)
        self._set_status(f"Pushing {len(sources)} item(s) → {dest_dir}…")
        self.gui.log(
            f"[FileExplorer] Push {len(sources)} item(s) to {dest_dir}"
        )

        def worker():
            failed = []
            succeeded = 0
            for src in sources:
                cmd = f'{flag} push "{src}" "{dest_dir}"'
                r = self.adb.run_command(cmd, timeout=600)
                if r["success"]:
                    succeeded += 1
                else:
                    err = (r.get("stderr") or r.get("stdout") or "Unknown error").strip()
                    failed.append(f"{os.path.basename(src.rstrip(os.sep))}: {err}")
            if failed:
                msg = (
                    f"Pushed {succeeded}/{len(sources)} item(s). "
                    f"{len(failed)} failed:\n" + "\n".join(failed)
                )
                self.transfer_finished.emit("push", False, msg)
            else:
                self.transfer_finished.emit(
                    "push", True, f"Pushed {succeeded} item(s) to {dest_dir}"
                )

        threading.Thread(target=worker, daemon=True).start()

    def _pull_paths_to_local(self, sources, dest_dir):
        """Pull a list of absolute remote paths into ``dest_dir`` on the PC."""
        if self._transfer_busy:
            self._set_status("Another transfer is already running.", "warning")
            return
        if not self._ensure_device():
            return
        sources = [s for s in (sources or []) if s]
        if not sources:
            return
        if not dest_dir:
            dest_dir = self.local_path
        if not os.path.isdir(dest_dir):
            QMessageBox.warning(
                self, "Invalid PC folder", f"Not a folder:\n{dest_dir}"
            )
            return

        flag = self.gui.get_device_flag()
        self._set_busy(True)
        self._set_status(f"Pulling {len(sources)} item(s) → {dest_dir}…")
        self.gui.log(
            f"[FileExplorer] Pull {len(sources)} item(s) to {dest_dir}"
        )

        def worker():
            failed = []
            succeeded = 0
            for src in sources:
                cmd = f'{flag} pull "{src}" "{dest_dir}"'
                r = self.adb.run_command(cmd, timeout=600)
                if r["success"]:
                    succeeded += 1
                else:
                    err = (r.get("stderr") or r.get("stdout") or "Unknown error").strip()
                    failed.append(f"{os.path.basename(src.rstrip('/'))}: {err}")
            if failed:
                msg = (
                    f"Pulled {succeeded}/{len(sources)} item(s). "
                    f"{len(failed)} failed:\n" + "\n".join(failed)
                )
                self.transfer_finished.emit("pull", False, msg)
            else:
                self.transfer_finished.emit(
                    "pull", True, f"Pulled {succeeded} item(s) to {dest_dir}"
                )

        threading.Thread(target=worker, daemon=True).start()

    def _on_transfer_finished(self, operation, success, message):
        self._set_busy(False)
        if success:
            self._set_status(message, "success")
            self.gui.log(f"[FileExplorer] {message}")
        else:
            self._set_status(message.split("\n")[0], "error")
            self.gui.log(f"[FileExplorer] {message}", "ERROR")
            QMessageBox.warning(self, "Transfer issue", message)
        # Refresh both sides regardless — partial successes still need a redraw.
        self.refresh_local()
        self.refresh_remote()


def main():
    app = QApplication(sys.argv)
    window = ADBGUI()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()