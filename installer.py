"""Installer for VRChat SteamVR Optimizer.

The setup EXE embeds the already-built optimizer EXE, copies it into the
current user's Programs folder, creates shortcuts, writes a small uninstall
script, and optionally launches the installed app.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk


APP_NAME = "VRChat SteamVR Optimizer"
APP_EXE = "VRChatSteamVROptimizer.exe"
SUPPORT_FILES = ["README.md", "LICENSE"]
INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Programs" / "VRChatSteamVROptimizer"
START_MENU_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / APP_NAME
DESKTOP_DIR = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"


def is_windows() -> bool:
    return os.name == "nt"


def bundle_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent / "dist"


def source_app_exe() -> Path:
    candidates = [
        bundle_dir() / APP_EXE,
        Path(__file__).resolve().parent / "build" / "embedded_app" / APP_EXE,
        Path(__file__).resolve().parent / "dist" / APP_EXE,
        Path(__file__).resolve().parent / APP_EXE,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"Could not find bundled {APP_EXE}.")


def bundled_support_file(name: str) -> Path | None:
    candidates = [
        bundle_dir() / name,
        Path(__file__).resolve().parent / name,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def run_powershell(script: str) -> tuple[int, str]:
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if is_windows() else 0,
    )
    return completed.returncode, ((completed.stdout or "") + (completed.stderr or "")).strip()


def escape_ps(value: Path | str) -> str:
    return str(value).replace("'", "''")


def create_shortcut(shortcut_path: Path, target_path: Path, description: str, arguments: str = "") -> None:
    shortcut_path.parent.mkdir(parents=True, exist_ok=True)
    script = f"""
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut('{escape_ps(shortcut_path)}')
$shortcut.TargetPath = '{escape_ps(target_path)}'
$shortcut.Arguments = '{arguments.replace("'", "''")}'
$shortcut.WorkingDirectory = '{escape_ps(target_path.parent)}'
$shortcut.Description = '{description.replace("'", "''")}'
$shortcut.IconLocation = '{escape_ps(target_path)},0'
$shortcut.Save()
"""
    code, output = run_powershell(script)
    if code != 0:
        raise RuntimeError(output or f"Could not create shortcut: {shortcut_path}")


def write_uninstaller() -> Path:
    uninstall_path = INSTALL_DIR / "Uninstall-VRChatSteamVROptimizer.ps1"
    script = f"""$ErrorActionPreference = "SilentlyContinue"
$installDir = '{escape_ps(INSTALL_DIR)}'
$startMenu = '{escape_ps(START_MENU_DIR)}'
$desktopShortcut = '{escape_ps(DESKTOP_DIR / (APP_NAME + ".lnk"))}'
Remove-Item -LiteralPath $desktopShortcut -Force
Remove-Item -LiteralPath $startMenu -Recurse -Force
Start-Sleep -Milliseconds 300
Remove-Item -LiteralPath $installDir -Recurse -Force
Write-Host "{APP_NAME} removed."
"""
    uninstall_path.write_text(script, encoding="utf-8")
    return uninstall_path


def install_app(create_desktop_shortcut: bool, launch_after: bool, log: callable) -> None:
    if not is_windows():
        raise RuntimeError("This installer is for Windows only.")

    source = source_app_exe()
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    target = INSTALL_DIR / APP_EXE

    log(f"Installing to {INSTALL_DIR}")
    shutil.copy2(source, target)
    log(f"Copied {APP_EXE}")

    for support_file in SUPPORT_FILES:
        source_file = bundled_support_file(support_file)
        if source_file:
            shutil.copy2(source_file, INSTALL_DIR / support_file)
            log(f"Copied {support_file}")

    uninstall_script = write_uninstaller()
    log("Wrote uninstall script")

    create_shortcut(START_MENU_DIR / f"{APP_NAME}.lnk", target, APP_NAME)
    create_shortcut(
        START_MENU_DIR / "Uninstall.lnk",
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
        f"Uninstall {APP_NAME}",
        f'-NoProfile -ExecutionPolicy Bypass -File "{uninstall_script}"',
    )
    log("Created Start Menu shortcuts")

    if create_desktop_shortcut:
        create_shortcut(DESKTOP_DIR / f"{APP_NAME}.lnk", target, APP_NAME)
        log("Created Desktop shortcut")

    if launch_after:
        os.startfile(target)  # type: ignore[attr-defined]
        log("Launched installed app")


class InstallerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} Setup")
        self.geometry("620x420")
        self.minsize(560, 360)
        self.desktop_var = tk.BooleanVar(value=True)
        self.launch_var = tk.BooleanVar(value=True)
        self._build_ui()

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)

        ttk.Label(root, text=f"Install {APP_NAME}", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(
            root,
            text=(
                "This setup installs the self-contained optimizer app. "
                "No separate Python install is needed."
            ),
            wraplength=560,
        ).pack(anchor="w", pady=(8, 14))

        path_frame = ttk.LabelFrame(root, text="Install Location", padding=10)
        path_frame.pack(fill="x")
        ttk.Label(path_frame, text=str(INSTALL_DIR), wraplength=540).pack(anchor="w")

        options = ttk.LabelFrame(root, text="Options", padding=10)
        options.pack(fill="x", pady=12)
        ttk.Checkbutton(options, text="Create Desktop shortcut", variable=self.desktop_var).pack(anchor="w")
        ttk.Checkbutton(options, text="Launch after install", variable=self.launch_var).pack(anchor="w")

        ttk.Label(root, text="Install Log").pack(anchor="w")
        self.log_text = tk.Text(root, height=7, wrap="word", font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True, pady=(4, 10))

        buttons = ttk.Frame(root)
        buttons.pack(fill="x")
        self.install_button = ttk.Button(buttons, text="Install", command=self.on_install)
        self.install_button.pack(side="right")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=(0, 8))

    def log(self, message: str) -> None:
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.update_idletasks()

    def on_install(self) -> None:
        self.install_button.configure(state="disabled")
        try:
            install_app(self.desktop_var.get(), self.launch_var.get(), self.log)
        except Exception as exc:
            messagebox.showerror(f"{APP_NAME} Setup", str(exc))
            self.install_button.configure(state="normal")
            return
        messagebox.showinfo(f"{APP_NAME} Setup", "Install complete.")
        self.destroy()


def main() -> int:
    if not is_windows():
        print("This installer is for Windows only.")
        return 1
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    app = InstallerApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
