# Building the Executable

This guide explains how to build a standalone executable from the source code.

## Prerequisites

- Python 3.8 or higher
- PyInstaller installed: `pip install pyinstaller`

## Building

1. Navigate to the main project directory (where you cloned the repo):
   ```bash
   cd adb
   ```

2. Run PyInstaller with the spec file:
   ```bash
   python -m PyInstaller build_exe.spec --clean --noconfirm
   ```

3. The executable will be created in `dist/ADB_GUI.exe`

## Spec File Configuration

The `build_exe.spec` file is configured with:
- **One-file mode**: Single executable (no separate DLLs)
- **No console window**: GUI-only application
- **UPX compression**: Reduces file size (if UPX is available)

## File Size

The resulting executable is typically 50-100MB, as it includes:
- Python interpreter
- PyQt6 and all dependencies
- All required DLLs and libraries

## Distribution

The `ADB_GUI.exe` file is standalone and can be distributed without:
- Python installation
- Additional dependencies
- Source code

Users only need:
- The `ADB_GUI.exe` file
- Android SDK Platform Tools (prompted on first launch)

## Troubleshooting

**Build fails:**
- Ensure PyInstaller is up to date: `pip install --upgrade pyinstaller`
- Check that all dependencies are installed: `pip install -r requirements.txt`
- Try building without UPX: Edit `build_exe.spec` and set `upx=False`

**Executable doesn't run:**
- Check Windows Defender/Antivirus (may flag new executables)
- Try running from command line to see error messages
- Ensure all required DLLs are included (check PyInstaller warnings)

