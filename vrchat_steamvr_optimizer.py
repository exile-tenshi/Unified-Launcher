"""VRChat + PCVR optimizer for Windows.

This app gives PC-specific, opt-in Windows tweaks for VRChat, SteamVR,
Vive Hub/Vive Console, Virtual Desktop, Steam Link, OVR tools,
MagicChatbox, and VRCFaceTracking.
It deliberately avoids risky "magic FPS" changes such as BCDEdit timer hacks,
random registry latency tweaks, driver downloads, or deleting shader caches.
"""

from __future__ import annotations

import ctypes
import datetime as _dt
import json
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable, Iterable

try:
    import winreg
except ImportError:  # pragma: no cover - Windows-only app
    winreg = None  # type: ignore


APP_NAME = "VRChat SteamVR Optimizer"
APP_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "VrchatSteamVrOptimizer"
BACKUP_DIR = APP_DIR / "backups"
LOG_DIR = APP_DIR / "logs"
STEAM_VRCHAT_APPID = "438100"
STEAMVR_APPID = "250820"
STEAM_LINK_APPID = "353380"
OVR_ADVANCED_SETTINGS_APPID = "1009850"
OVR_TOOLKIT_APPID = "1068820"
ULTIMATE_POWER_GUID = "e9a42b02-d5df-448d-aa00-03f14749eb61"
PCVR_PROCESS_NAMES = [
    "VRChat",
    "vrserver",
    "vrcompositor",
    "vrdashboard",
    "vrmonitor",
    "steam",
    "streaming_client",
    "steamwebhelper",
    "VirtualDesktop.Streamer",
    "VirtualDesktop.Service",
    "VirtualDesktop",
    "VIVE Hub",
    "ViveHub",
    "VIVE Console",
    "ViveConsole",
    "ViveVRServer",
    "RRServer",
    "AdvancedSettings",
    "OVR Advanced Settings",
    "OVR Toolkit",
    "OVRToolkit",
    "OVR Toolkit-Task",
    "OVRdrop",
    "DesktopPlus",
    "MagicChatbox",
    "VRCFaceTracking",
    "VRCFT",
]
ESSENTIAL_SERVICES = [
    ("Audiosrv", "Automatic", "Windows Audio"),
    ("AudioEndpointBuilder", "Automatic", "Windows Audio Endpoint Builder"),
    ("PlugPlay", "Automatic", "Plug and Play"),
    ("DeviceInstall", "Manual", "Device Install Service"),
    ("DsmSvc", "Manual", "Device Setup Manager"),
    ("hidserv", "Manual", "Human Interface Device Service"),
    ("BthServ", "Manual", "Bluetooth Support Service"),
    ("Dhcp", "Automatic", "DHCP Client"),
    ("Dnscache", "Automatic", "DNS Client"),
    ("NlaSvc", "Automatic", "Network Location Awareness"),
    ("WlanSvc", "Manual", "WLAN AutoConfig"),
    ("Steam Client Service", "Manual", "Steam Client Service"),
]
RUNTIME_SERVICE_PATTERNS = {
    "vive": ["vive", "htc", "viveport"],
    "virtual_desktop": ["virtual desktop", "virtualdesktop"],
    "steam_link": ["steam client", "steam streaming", "steam remote", "remote play"],
    "ovr_tools": ["ovr", "openvr advanced", "desktopplus", "desktop+"],
    "magic_chatbox": ["magicchatbox", "magic chatbox"],
    "vrcft": ["vrcfacetracking", "vrc face tracking", "vrcft"],
}


def now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def ensure_dirs() -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def is_windows() -> bool:
    return os.name == "nt"


def is_admin() -> bool:
    if not is_windows():
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> None:
    if not is_windows():
        messagebox.showerror(APP_NAME, "Administrator relaunch only works on Windows.")
        return
    params = " ".join([f'"{arg}"' for arg in sys.argv])
    try:
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    except Exception as exc:
        messagebox.showerror(APP_NAME, f"Could not relaunch as administrator:\n{exc}")


def run_process(command: list[str], timeout: int = 90) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if is_windows() else 0,
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        return completed.returncode, output.strip()
    except subprocess.TimeoutExpired:
        return 124, "Timed out."
    except FileNotFoundError:
        return 127, f"Command not found: {command[0]}"
    except Exception as exc:
        return 1, str(exc)


def run_powershell(script: str, timeout: int = 90) -> tuple[int, str]:
    return run_process(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        timeout=timeout,
    )


def read_reg_value(root, subkey: str, name: str):
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(root, subkey) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def set_reg_dword(root, subkey: str, name: str, value: int) -> None:
    if winreg is None:
        raise RuntimeError("Windows registry is not available.")
    with winreg.CreateKeyEx(root, subkey, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, int(value))


def set_reg_sz(root, subkey: str, name: str, value: str) -> None:
    if winreg is None:
        raise RuntimeError("Windows registry is not available.")
    with winreg.CreateKeyEx(root, subkey, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def enum_reg_subkeys(root, subkey: str) -> list[str]:
    if winreg is None:
        return []
    try:
        with winreg.OpenKey(root, subkey) as key:
            names = []
            index = 0
            while True:
                try:
                    names.append(winreg.EnumKey(key, index))
                    index += 1
                except OSError:
                    break
            return names
    except OSError:
        return []


def find_registry_paths(root, base: str, patterns: Iterable[str], max_depth: int = 2) -> list[str]:
    lowered = [pattern.lower() for pattern in patterns]
    found: list[str] = []

    def walk(subkey: str, depth: int) -> None:
        name_blob = subkey.lower()
        if any(pattern in name_blob for pattern in lowered):
            found.append(subkey)
        if depth <= 0:
            return
        for child in enum_reg_subkeys(root, subkey):
            walk(subkey + "\\" + child, depth - 1)

    walk(base, max_depth)
    return sorted(dict.fromkeys(found))


def reg_snapshot(items: Iterable[tuple[str, object, str, str]]) -> dict[str, object]:
    snap: dict[str, object] = {}
    for label, root, subkey, name in items:
        snap[label] = read_reg_value(root, subkey, name)
    return snap


def save_backup(name: str, data: object) -> Path:
    ensure_dirs()
    path = BACKUP_DIR / f"{name}-{now_stamp()}.json"
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return path


def ps_json(script: str) -> object | None:
    code, output = run_powershell(script, timeout=20)
    if code != 0 or not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return None


def detect_specs() -> dict[str, object]:
    specs: dict[str, object] = {
        "os": platform.platform(),
        "admin": is_admin(),
        "python": sys.version.split()[0],
    }
    ps = r"""
$ErrorActionPreference = "SilentlyContinue"
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1 Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed
$gpu = Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM,DriverVersion,VideoProcessor
$mem = Get-CimInstance Win32_ComputerSystem | Select-Object TotalPhysicalMemory,Manufacturer,Model
$os = Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,BuildNumber
$disk = Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | Select-Object DeviceID,FreeSpace,Size
$power = powercfg /getactivescheme
[pscustomobject]@{
  Cpu = $cpu
  Gpu = @($gpu)
  Memory = $mem
  OsInfo = $os
  Disk = @($disk)
  ActivePowerPlan = $power
} | ConvertTo-Json -Depth 5
"""
    data = ps_json(ps)
    if isinstance(data, dict):
        specs.update(data)

    specs["Steam"] = detect_steam()
    specs["PCVRRuntimes"] = detect_pcvr_runtimes(specs["Steam"] if isinstance(specs.get("Steam"), dict) else None)
    specs["Suggestions"] = build_suggestions(specs)
    return specs


def normalize_path(path: str | None) -> Path | None:
    if not path:
        return None
    expanded = os.path.expandvars(path.replace("/", "\\"))
    candidate = Path(expanded)
    return candidate if candidate.exists() else None


def detect_steam() -> dict[str, object]:
    result: dict[str, object] = {
        "InstallPath": None,
        "Libraries": [],
        "VRChatPath": None,
        "SteamVRPath": None,
        "SteamLinkPath": None,
        "OvrAdvancedSettingsPath": None,
        "OvrToolkitPath": None,
        "OvrDesktopPath": None,
        "SteamVRSettings": [],
    }
    if winreg is None:
        return result

    roots = [
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    ]
    steam_path = None
    for root, subkey, name in roots:
        steam_path = normalize_path(read_reg_value(root, subkey, name))
        if steam_path:
            break

    if not steam_path:
        default = normalize_path(r"%ProgramFiles(x86)%\Steam")
        steam_path = default

    if not steam_path:
        return result

    result["InstallPath"] = str(steam_path)
    libraries = [steam_path]
    library_vdf = steam_path / "steamapps" / "libraryfolders.vdf"
    if library_vdf.exists():
        text = library_vdf.read_text(encoding="utf-8", errors="ignore")
        for match in re.finditer(r'"path"\s+"([^"]+)"', text):
            lib = normalize_path(match.group(1))
            if lib and lib not in libraries:
                libraries.append(lib)

    result["Libraries"] = [str(p) for p in libraries]
    result["VRChatPath"] = find_steam_app(libraries, "VRChat")
    result["SteamVRPath"] = find_steam_app(libraries, "SteamVR")
    result["SteamLinkPath"] = find_steam_app(libraries, "Steam Link")
    result["OvrAdvancedSettingsPath"] = find_steam_app_any(
        libraries,
        ["OVR Advanced Settings", "OpenVR Advanced Settings", "Advanced Settings"],
    )
    result["OvrToolkitPath"] = find_steam_app_any(libraries, ["OVR Toolkit", "OVRToolkit"])
    result["OvrDesktopPath"] = find_steam_app_any(libraries, ["OVRdrop", "Desktop+", "Desktop Plus"])

    settings_candidates = [
        steam_path / "config" / "steamvr.vrsettings",
        Path(os.environ.get("LOCALAPPDATA", "")) / "openvr" / "steamvr.vrsettings",
    ]
    result["SteamVRSettings"] = [str(p) for p in settings_candidates if p.exists()]
    return result


def common_program_roots() -> list[Path]:
    roots: list[Path] = []
    for env_name in ["ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA", "APPDATA"]:
        value = os.environ.get(env_name)
        if value:
            path = Path(value)
            if path.exists() and path not in roots:
                roots.append(path)
    return roots


def find_first_existing(paths: Iterable[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def find_named_exes(root_patterns: Iterable[Path], exe_names: Iterable[str], max_hits: int = 12) -> list[Path]:
    hits: list[Path] = []
    seen: set[str] = set()
    for root in root_patterns:
        if not root.exists():
            continue
        if root.is_file() and root.name.lower() in {name.lower() for name in exe_names}:
            key = str(root).lower()
            if key not in seen:
                hits.append(root)
                seen.add(key)
            continue
        if not root.is_dir():
            continue
        for exe_name in exe_names:
            try:
                matches = list(root.rglob(exe_name))
            except (OSError, PermissionError):
                continue
            for match in matches:
                key = str(match).lower()
                if key not in seen and match.exists():
                    hits.append(match)
                    seen.add(key)
                    if len(hits) >= max_hits:
                        return hits
    return hits


def find_named_files(root_patterns: Iterable[Path], file_names: Iterable[str], max_hits: int = 20) -> list[Path]:
    hits: list[Path] = []
    seen: set[str] = set()
    names = {name.lower() for name in file_names}
    for root in root_patterns:
        if not root.exists() or not root.is_dir():
            continue
        for file_name in file_names:
            try:
                matches = list(root.rglob(file_name))
            except (OSError, PermissionError):
                continue
            for match in matches:
                key = str(match).lower()
                if match.is_file() and match.name.lower() in names and key not in seen:
                    hits.append(match)
                    seen.add(key)
                    if len(hits) >= max_hits:
                        return hits
    return hits


def detect_pcvr_runtimes(steam: dict[str, object] | None = None) -> dict[str, object]:
    if steam is None:
        steam = detect_steam()

    roots = common_program_roots()
    vive_roots = []
    vd_roots = []
    ovr_roots = []
    magic_chatbox_roots = []
    vrcft_roots = []
    vive_settings_roots = []
    vd_settings_roots = []
    for root in roots:
        vive_roots.extend(
            [
                root / "VIVE",
                root / "VIVE Hub",
                root / "HTC",
                root / "HTC Vive",
                root / "VIVEPORT",
            ]
        )
        vd_roots.extend(
            [
                root / "Virtual Desktop Streamer",
                root / "Virtual Desktop",
                root / "VirtualDesktop",
                root / "Guy Godin",
            ]
        )
        vd_settings_roots.extend(
            [
                root / "Virtual Desktop",
                root / "VirtualDesktop",
                root / "Virtual Desktop Streamer",
                root / "Guy Godin",
            ]
        )
        vive_settings_roots.extend(
            [
                root / "VIVE",
                root / "VIVE Hub",
                root / "HTC",
                root / "HTC Vive",
                root / "VIVEPORT",
            ]
        )
        ovr_roots.extend(
            [
                root / "OVR Advanced Settings",
                root / "OpenVR Advanced Settings",
                root / "OVR Toolkit",
                root / "OVRToolkit",
                root / "OVRdrop",
                root / "Desktop+",
                root / "Desktop Plus",
            ]
        )
        magic_chatbox_roots.extend(
            [
                root / "MagicChatbox",
                root / "Magic Chatbox",
                root / "Programs" / "MagicChatbox",
                root / "Programs" / "Magic Chatbox",
            ]
        )
        vrcft_roots.extend(
            [
                root / "VRCFaceTracking",
                root / "VRC Face Tracking",
                root / "VRCFT",
                root / "Programs" / "VRCFaceTracking",
                root / "Programs" / "VRC Face Tracking",
            ]
        )

    vive_exes = find_named_exes(
        vive_roots,
        [
            "VIVE Hub.exe",
            "ViveHub.exe",
            "VIVE Console.exe",
            "ViveConsole.exe",
            "ViveVRServer.exe",
            "RRServer.exe",
        ],
    )
    virtual_desktop_exes = find_named_exes(
        vd_roots,
        [
            "VirtualDesktop.Streamer.exe",
            "Virtual Desktop Streamer.exe",
            "VirtualDesktop.Service.exe",
            "VirtualDesktop.exe",
        ],
    )
    ovr_exes = find_named_exes(
        ovr_roots,
        [
            "AdvancedSettings.exe",
            "OVR Advanced Settings.exe",
            "OVR Toolkit.exe",
            "OVRToolkit.exe",
            "OVR Toolkit-Task.exe",
            "OVRdrop.exe",
            "DesktopPlus.exe",
            "Desktop+.exe",
        ],
    )
    magic_chatbox_exes = find_named_exes(
        magic_chatbox_roots,
        ["MagicChatbox.exe", "Magic Chatbox.exe"],
    )
    vrcft_exes = find_named_exes(
        vrcft_roots,
        ["VRCFaceTracking.exe", "VRC Face Tracking.exe", "VRCFT.exe"],
    )
    virtual_desktop_settings = find_named_files(
        vd_settings_roots,
        ["settings.json", "config.json", "streamer.json", "VirtualDesktop.json", "VirtualDesktop.Streamer.json"],
        max_hits=16,
    )
    vive_settings = find_named_files(
        vive_settings_roots,
        ["settings.json", "config.json", "vivehub.json", "ViveHub.json", "ViveConsole.json", "default.vrsettings"],
        max_hits=20,
    )

    steam_link_exes: list[Path] = []
    if isinstance(steam, dict):
        install_path = steam.get("InstallPath")
        if isinstance(install_path, str):
            steam_root = Path(install_path)
            steam_exe = steam_root / "steam.exe"
            if steam_exe.exists():
                steam_link_exes.append(steam_exe)
            steam_link_exes.extend(
                find_named_exes(
                    [
                        steam_root / "streaming_client",
                        steam_root / "bin",
                        steam_root / "steamapps" / "common" / "Steam Link",
                    ],
                    ["streaming_client.exe", "SteamLink.exe", "steamlink.exe"],
                    max_hits=8,
                )
            )
        steam_link_path = steam.get("SteamLinkPath")
        if isinstance(steam_link_path, str):
            steam_link_exes.extend(find_named_exes([Path(steam_link_path)], ["SteamLink.exe", "steamlink.exe"], max_hits=4))
        for key in ["OvrAdvancedSettingsPath", "OvrToolkitPath", "OvrDesktopPath"]:
            ovr_path = steam.get(key)
            if isinstance(ovr_path, str):
                ovr_exes.extend(
                    find_named_exes(
                        [Path(ovr_path)],
                        [
                            "AdvancedSettings.exe",
                            "OVR Advanced Settings.exe",
                            "OVR Toolkit.exe",
                            "OVRToolkit.exe",
                            "OVR Toolkit-Task.exe",
                            "OVRdrop.exe",
                            "DesktopPlus.exe",
                            "Desktop+.exe",
                        ],
                        max_hits=8,
                    )
                )

    return {
        "Vive": [str(path) for path in vive_exes],
        "VirtualDesktop": [str(path) for path in virtual_desktop_exes],
        "SteamLink": [str(path) for path in dict.fromkeys(str(path) for path in steam_link_exes)],
        "OvrTools": [str(path) for path in dict.fromkeys(str(path) for path in ovr_exes)],
        "MagicChatbox": [str(path) for path in magic_chatbox_exes],
        "VRCFaceTracking": [str(path) for path in vrcft_exes],
        "VirtualDesktopSettings": [str(path) for path in virtual_desktop_settings],
        "ViveSettings": [str(path) for path in vive_settings],
    }


def find_steam_app(libraries: list[Path], folder_name: str) -> str | None:
    for library in libraries:
        candidate = library / "steamapps" / "common" / folder_name
        if candidate.exists():
            return str(candidate)
    return None


def find_steam_app_any(libraries: list[Path], folder_names: list[str]) -> str | None:
    for folder_name in folder_names:
        found = find_steam_app(libraries, folder_name)
        if found:
            return found
    return None


def bytes_to_gb(value: object) -> float:
    try:
        return round(float(value) / (1024 ** 3), 1)
    except Exception:
        return 0.0


def get_ram_gb(specs: dict[str, object]) -> float:
    memory = specs.get("Memory")
    if isinstance(memory, dict):
        return bytes_to_gb(memory.get("TotalPhysicalMemory"))
    return 0.0


def get_gpu_names(specs: dict[str, object]) -> list[str]:
    gpu = specs.get("Gpu")
    rows = gpu if isinstance(gpu, list) else [gpu]
    names = []
    for item in rows:
        if isinstance(item, dict) and item.get("Name"):
            names.append(str(item["Name"]))
    return names


def executable_candidates(steam: dict[str, object] | None = None) -> list[tuple[str, Path]]:
    if steam is None:
        steam = detect_steam()
    candidates: list[tuple[str, Path]] = []
    if not isinstance(steam, dict):
        return candidates

    vrchat_path = steam.get("VRChatPath")
    if isinstance(vrchat_path, str):
        candidates.append(("VRChat", Path(vrchat_path) / "VRChat.exe"))

    steamvr_path = steam.get("SteamVRPath")
    if isinstance(steamvr_path, str):
        steamvr_root = Path(steamvr_path)
        candidates.extend(
            [
                ("SteamVR Server", steamvr_root / "bin" / "win64" / "vrserver.exe"),
                ("SteamVR Compositor", steamvr_root / "bin" / "win64" / "vrcompositor.exe"),
                ("SteamVR Dashboard", steamvr_root / "bin" / "win64" / "vrdashboard.exe"),
                ("SteamVR Monitor", steamvr_root / "bin" / "win64" / "vrmonitor.exe"),
            ]
        )

    runtimes = detect_pcvr_runtimes(steam)
    for label, key in [
        ("Vive Hub / Console", "Vive"),
        ("Virtual Desktop Streamer", "VirtualDesktop"),
        ("Steam Link / Remote Play", "SteamLink"),
        ("OVR / OpenVR Overlay Tool", "OvrTools"),
        ("MagicChatbox", "MagicChatbox"),
        ("VRCFaceTracking", "VRCFaceTracking"),
    ]:
        paths = runtimes.get(key, [])
        if isinstance(paths, list):
            for path_text in paths:
                if isinstance(path_text, str):
                    candidates.append((label, Path(path_text)))

    clean: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for label, path in candidates:
        key = str(path).lower()
        if path.exists() and key not in seen:
            clean.append((label, path))
            seen.add(key)
    return clean


def build_suggestions(specs: dict[str, object]) -> list[str]:
    suggestions: list[str] = []
    ram = get_ram_gb(specs)
    gpu_names = " ".join(get_gpu_names(specs)).lower()
    if ram and ram < 16:
        suggestions.append("RAM is under 16 GB. Keep avatar safety limits strict in VRChat and close browsers/recorders before VR.")
    if any(x in gpu_names for x in ["intel(r) uhd", "intel uhd", "radeon graphics"]):
        suggestions.append("Detected an integrated or entry GPU. Use conservative SteamVR render scale and avoid heavy mirrors.")
    if not is_admin():
        suggestions.append("Run as administrator to apply power, graphics, and network stack settings.")
    steam = specs.get("Steam")
    if isinstance(steam, dict):
        if not steam.get("VRChatPath"):
            suggestions.append("VRChat was not found in the detected Steam libraries.")
        if not steam.get("SteamVRPath"):
            suggestions.append("SteamVR was not found in the detected Steam libraries.")
    runtimes = specs.get("PCVRRuntimes")
    if isinstance(runtimes, dict):
        if runtimes.get("VirtualDesktop"):
            suggestions.append("Virtual Desktop Streamer was detected and will be included in GPU/firewall/priority compatibility actions.")
        if runtimes.get("Vive"):
            suggestions.append("Vive Hub or Vive Console components were detected and will be included in compatibility actions.")
        if runtimes.get("SteamLink"):
            suggestions.append("Steam Link or Steam Remote Play components were detected and will be included in compatibility actions.")
        if runtimes.get("OvrTools"):
            suggestions.append("OVR/OpenVR overlay tools were detected and will be included in GPU/firewall/priority optimization actions.")
        if runtimes.get("MagicChatbox"):
            suggestions.append("MagicChatbox was detected and will be included in firewall/priority optimization actions.")
        if runtimes.get("VRCFaceTracking"):
            suggestions.append("VRCFaceTracking was detected and will be included in firewall/priority optimization actions.")
    return suggestions


@dataclass
class Action:
    key: str
    category: str
    title: str
    description: str
    commands: list[str]
    apply: Callable[[Callable[[str], None]], None]
    requires_admin: bool = False
    restart_required: bool = False
    recommended: bool = True


def apply_power_plan(log: Callable[[str], None]) -> None:
    code, before = run_process(["powercfg", "/getactivescheme"])
    save_backup("power-plan", {"active_before": before})
    log("Saved current power plan.")
    run_logged(log, ["powercfg", "-duplicatescheme", ULTIMATE_POWER_GUID], allow_fail=True)
    run_logged(log, ["powercfg", "/setactive", ULTIMATE_POWER_GUID])


def apply_required_services(log: Callable[[str], None]) -> None:
    services_json = json.dumps(
        [{"Name": name, "Startup": startup, "Label": label} for name, startup, label in ESSENTIAL_SERVICES]
    )
    snapshot_script = rf"""
$services = ConvertFrom-Json @'
{services_json}
'@
$snapshot = foreach ($svc in $services) {{
  $found = Get-Service -Name $svc.Name -ErrorAction SilentlyContinue
  if ($found) {{
    $cim = Get-CimInstance Win32_Service -Filter "Name='$($svc.Name.Replace("'","''"))'" -ErrorAction SilentlyContinue
    [pscustomobject]@{{
      Name = $found.Name
      DisplayName = $found.DisplayName
      Status = $found.Status.ToString()
      StartType = if ($cim) {{ $cim.StartMode }} else {{ $null }}
    }}
  }}
}}
$snapshot | ConvertTo-Json -Depth 4
"""
    code, before = run_powershell(snapshot_script)
    save_backup("essential-services", {"before": before})
    log("Saved essential service startup/status snapshot.")

    apply_script = rf"""
$ErrorActionPreference = "Continue"
$services = ConvertFrom-Json @'
{services_json}
'@
foreach ($svc in $services) {{
  $found = Get-Service -Name $svc.Name -ErrorAction SilentlyContinue
  if (-not $found) {{
    Write-Output "Missing: $($svc.Label) [$($svc.Name)]"
    continue
  }}
  try {{
    Set-Service -Name $svc.Name -StartupType $svc.Startup -ErrorAction Stop
    Write-Output "Startup $($svc.Startup): $($svc.Label)"
  }} catch {{
    Write-Output "Startup unchanged for $($svc.Label): $($_.Exception.Message)"
  }}
  try {{
    Start-Service -Name $svc.Name -ErrorAction Stop
    Write-Output "Running: $($svc.Label)"
  }} catch {{
    $current = (Get-Service -Name $svc.Name -ErrorAction SilentlyContinue).Status
    Write-Output "Start skipped for $($svc.Label) [$current]: $($_.Exception.Message)"
  }}
}}
"""
    code, output = run_powershell(apply_script, timeout=120)
    if output:
        log(output)
    if code != 0:
        raise RuntimeError(output or "Service restore command failed.")


def apply_game_mode_on(log: Callable[[str], None]) -> None:
    if winreg is None:
        raise RuntimeError("Registry unavailable.")
    items = [
        ("AllowAutoGameMode", winreg.HKEY_CURRENT_USER, r"Software\Microsoft\GameBar", "AllowAutoGameMode"),
        ("AutoGameModeEnabled", winreg.HKEY_CURRENT_USER, r"Software\Microsoft\GameBar", "AutoGameModeEnabled"),
    ]
    save_backup("game-mode-registry", reg_snapshot(items))
    set_reg_dword(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\GameBar", "AllowAutoGameMode", 1)
    set_reg_dword(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\GameBar", "AutoGameModeEnabled", 1)
    log("Enabled Windows Game Mode registry flags.")


def apply_gpu_preferences(log: Callable[[str], None]) -> None:
    if winreg is None:
        raise RuntimeError("Registry unavailable.")
    exes = executable_candidates()
    if not exes:
        log("No detected VRChat, PCVR runtime, OVR tool, MagicChatbox, or VRCFaceTracking executable paths found for GPU preference.")
        return

    subkey = r"Software\Microsoft\DirectX\UserGpuPreferences"
    snapshot_items = [(label, winreg.HKEY_CURRENT_USER, subkey, str(path)) for label, path in exes]
    save_backup("gpu-preferences", reg_snapshot(snapshot_items))
    for label, path in exes:
        set_reg_sz(winreg.HKEY_CURRENT_USER, subkey, str(path), "GpuPreference=2;")
        log(f"Set high-performance GPU preference: {label} -> {path}")


def apply_firewall_allow_rules(log: Callable[[str], None]) -> None:
    exes = executable_candidates()
    if not exes:
        log("No detected VRChat, PCVR runtime, OVR tool, MagicChatbox, or VRCFaceTracking executable paths found for firewall rules.")
        return

    payload = json.dumps([{"Label": label, "Path": str(path)} for label, path in exes])
    script = rf"""
$apps = ConvertFrom-Json @'
{payload}
'@
$before = foreach ($app in $apps) {{
  Get-NetFirewallRule -ErrorAction SilentlyContinue |
    Where-Object {{ $_.DisplayName -like "VRChat SteamVR Optimizer - $($app.Label) -*" }} |
    Select-Object DisplayName,Enabled,Direction,Action,Profile
}}
"Existing optimizer firewall rules: " + (($before | Measure-Object).Count)
foreach ($app in $apps) {{
  if (-not (Test-Path $app.Path)) {{
    Write-Output "Missing executable: $($app.Path)"
    continue
  }}
  foreach ($dir in "Inbound","Outbound") {{
    $name = "VRChat SteamVR Optimizer - $($app.Label) - $dir"
    $existing = Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue
    if ($existing) {{
      Set-NetFirewallRule -DisplayName $name -Enabled True -Action Allow -Profile Any
      Write-Output "Enabled firewall rule: $name"
    }} else {{
      New-NetFirewallRule -DisplayName $name -Direction $dir -Action Allow -Program $app.Path -Profile Any -Enabled True | Out-Null
      Write-Output "Created firewall rule: $name"
    }}
  }}
}}
"""
    save_backup("firewall-rule-targets", [{"label": label, "path": str(path)} for label, path in exes])
    code, output = run_powershell(script, timeout=120)
    if output:
        log(output)
    if code != 0:
        raise RuntimeError(output or "Firewall rule command failed.")


def apply_multimedia_performance_profile(log: Callable[[str], None]) -> None:
    if winreg is None:
        raise RuntimeError("Registry unavailable.")
    items = [
        ("NetworkThrottlingIndex", winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile", "NetworkThrottlingIndex"),
        ("SystemResponsiveness", winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile", "SystemResponsiveness"),
        ("GamesGPU", winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile\Tasks\Games", "GPU Priority"),
        ("GamesPriority", winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile\Tasks\Games", "Priority"),
        ("GamesScheduling", winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile\Tasks\Games", "Scheduling Category"),
        ("GamesSFIO", winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile\Tasks\Games", "SFIO Priority"),
    ]
    save_backup("multimedia-performance-profile", reg_snapshot(items))
    profile_key = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"
    games_key = profile_key + r"\Tasks\Games"
    set_reg_dword(winreg.HKEY_LOCAL_MACHINE, profile_key, "NetworkThrottlingIndex", 0xFFFFFFFF)
    set_reg_dword(winreg.HKEY_LOCAL_MACHINE, profile_key, "SystemResponsiveness", 0)
    set_reg_dword(winreg.HKEY_LOCAL_MACHINE, games_key, "GPU Priority", 8)
    set_reg_dword(winreg.HKEY_LOCAL_MACHINE, games_key, "Priority", 6)
    set_reg_sz(winreg.HKEY_LOCAL_MACHINE, games_key, "Scheduling Category", "High")
    set_reg_sz(winreg.HKEY_LOCAL_MACHINE, games_key, "SFIO Priority", "High")
    log("Applied low-latency multimedia scheduling profile for games and PCVR streaming.")


def apply_fullscreen_optimization_profile(log: Callable[[str], None]) -> None:
    if winreg is None:
        raise RuntimeError("Registry unavailable.")
    exes = executable_candidates()
    if not exes:
        log("No detected PCVR executable paths found for fullscreen optimization profile.")
        return

    subkey = r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"
    snapshot_items = [(label, winreg.HKEY_CURRENT_USER, subkey, str(path)) for label, path in exes]
    save_backup("fullscreen-optimization-profile", reg_snapshot(snapshot_items))
    for label, path in exes:
        set_reg_sz(winreg.HKEY_CURRENT_USER, subkey, str(path), "~ DISABLEDXMAXIMIZEDWINDOWEDMODE")
        log(f"Disabled fullscreen optimizations for {label}: {path}")


def load_json_file(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8-sig", errors="ignore").strip()
    if not text:
        return {}
    data = json.loads(text)
    if isinstance(data, dict):
        return data
    raise ValueError(f"{path} did not contain a JSON object.")


def backup_file(path: Path, label: str, log: Callable[[str], None]) -> None:
    ensure_dirs()
    if path.exists():
        target = BACKUP_DIR / f"{label}-{path.name}-{now_stamp()}.bak"
        shutil.copy2(path, target)
        log(f"Backed up {path} to {target}")


def set_nested(data: dict[str, object], dotted_key: str, value: object) -> None:
    parts = dotted_key.split(".")
    current = data
    for part in parts[:-1]:
        existing = current.get(part)
        if not isinstance(existing, dict):
            existing = {}
            current[part] = existing
        current = existing
    current[parts[-1]] = value


def apply_json_profile(path: Path, label: str, updates: dict[str, object], log: Callable[[str], None]) -> bool:
    try:
        if path.exists():
            backup_file(path, label, log)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = load_json_file(path)
        for dotted_key, value in updates.items():
            set_nested(data, dotted_key, value)
        data.setdefault("VrchatSteamVrOptimizer", {})
        marker = data["VrchatSteamVrOptimizer"]
        if isinstance(marker, dict):
            marker["profile"] = "Balanced performance with good graphics"
            marker["updatedAt"] = _dt.datetime.now().isoformat(timespec="seconds")
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        log(f"Applied {label} settings profile to {path}")
        return True
    except json.JSONDecodeError as exc:
        log(f"Skipped {path}: could not parse JSON ({exc}).")
    except OSError as exc:
        log(f"Skipped {path}: {exc}")
    return False


def apply_registry_profile(
    root,
    root_name: str,
    subkeys: list[str],
    label: str,
    dword_updates: dict[str, int],
    string_updates: dict[str, str],
    log: Callable[[str], None],
) -> int:
    if winreg is None or not subkeys:
        return 0
    snapshot_items = []
    for subkey in subkeys:
        for name in list(dword_updates) + list(string_updates):
            snapshot_items.append((f"{root_name}\\{subkey}\\{name}", root, subkey, name))
    save_backup(f"{label}-registry", reg_snapshot(snapshot_items))
    applied = 0
    for subkey in subkeys:
        for name, value in dword_updates.items():
            try:
                set_reg_dword(root, subkey, name, value)
                applied += 1
            except OSError as exc:
                log(f"Registry DWORD skipped {root_name}\\{subkey}\\{name}: {exc}")
        for name, value in string_updates.items():
            try:
                set_reg_sz(root, subkey, name, value)
                applied += 1
            except OSError as exc:
                log(f"Registry string skipped {root_name}\\{subkey}\\{name}: {exc}")
        log(f"Applied {label} registry profile to {root_name}\\{subkey}")
    return applied


def apply_steamvr_balanced_quality(log: Callable[[str], None]) -> None:
    steam = detect_steam()
    settings_paths = [Path(path_text) for path_text in steam.get("SteamVRSettings", []) if isinstance(path_text, str)]
    if not settings_paths:
        install_path = steam.get("InstallPath")
        if isinstance(install_path, str):
            candidate = Path(install_path) / "config" / "steamvr.vrsettings"
            settings_paths.append(candidate)

    if not settings_paths:
        log("SteamVR settings path was not detected.")
        return

    for path in settings_paths:
        try:
            if path.exists():
                backup_file(path, "steamvr", log)
            path.parent.mkdir(parents=True, exist_ok=True)
            data = load_json_file(path)
            steamvr_section = data.setdefault("steamvr", {})
            if not isinstance(steamvr_section, dict):
                steamvr_section = {}
                data["steamvr"] = steamvr_section
            steamvr_section["enableHomeApp"] = False
            steamvr_section["supersampleManualOverride"] = False
            steamvr_section["allowSupersampleFiltering"] = True
            steamvr_section["motionSmoothing"] = True
            steamvr_section["showMirrorView"] = False
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            log(f"Applied balanced SteamVR quality/performance preset to {path}")
        except json.JSONDecodeError as exc:
            log(f"Skipped {path}: could not parse SteamVR settings JSON ({exc}).")
        except OSError as exc:
            log(f"Skipped {path}: {exc}")


def apply_virtual_desktop_balanced_settings(log: Callable[[str], None]) -> None:
    runtimes = detect_pcvr_runtimes()
    settings_paths = [Path(path_text) for path_text in runtimes.get("VirtualDesktopSettings", []) if isinstance(path_text, str)]
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    roaming = Path(os.environ.get("APPDATA", ""))
    for candidate in [
        local / "Virtual Desktop Streamer" / "settings.json",
        local / "Virtual Desktop" / "settings.json",
        roaming / "Virtual Desktop Streamer" / "settings.json",
        roaming / "Virtual Desktop" / "settings.json",
    ]:
        if candidate.parent.exists() and candidate not in settings_paths:
            settings_paths.append(candidate)

    updates = {
        "streaming.profile": "Balanced",
        "streaming.preferredCodec": "Auto",
        "streaming.autoBitrate": True,
        "streaming.autoResolution": True,
        "streaming.slicedEncoding": True,
        "streaming.videoBuffering": False,
        "streaming.preferLowLatency": True,
        "graphics.sharpening": "Balanced",
        "graphics.dynamicQuality": True,
    }
    applied_files = 0
    for path in settings_paths:
        applied_files += int(apply_json_profile(path, "virtual-desktop", updates, log))

    registry_applied = 0
    if winreg is not None:
        hkcu_paths = find_registry_paths(
            winreg.HKEY_CURRENT_USER,
            "Software",
            ["virtual desktop", "virtualdesktop", "guy godin"],
            max_depth=3,
        )
        registry_applied += apply_registry_profile(
            winreg.HKEY_CURRENT_USER,
            "HKCU",
            hkcu_paths,
            "virtual-desktop",
            {
                "AutoBitrate": 1,
                "AutoResolution": 1,
                "SlicedEncoding": 1,
                "VideoBuffering": 0,
                "PreferLowLatency": 1,
                "BoostGamePriority": 1,
            },
            {
                "QualityPreset": "Balanced",
                "PreferredCodec": "Auto",
            },
            log,
        )

    if not applied_files and not registry_applied:
        log("Virtual Desktop settings were not detected. Install/open Virtual Desktop Streamer once, then reload specs.")


def apply_vive_balanced_settings(log: Callable[[str], None]) -> None:
    runtimes = detect_pcvr_runtimes()
    settings_paths = [Path(path_text) for path_text in runtimes.get("ViveSettings", []) if isinstance(path_text, str)]
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    roaming = Path(os.environ.get("APPDATA", ""))
    program_data = Path(os.environ.get("PROGRAMDATA", ""))
    for candidate in [
        local / "VIVE" / "ViveHub" / "settings.json",
        local / "HTC" / "ViveHub" / "settings.json",
        roaming / "VIVE" / "ViveHub" / "settings.json",
        program_data / "VIVE" / "ViveHub" / "settings.json",
        local / "VIVE" / "ViveConsole" / "settings.json",
        local / "HTC" / "ViveConsole" / "settings.json",
    ]:
        if candidate.parent.exists() and candidate not in settings_paths:
            settings_paths.append(candidate)

    updates = {
        "profile": "Balanced",
        "graphics.profile": "Balanced",
        "graphics.autoResolution": True,
        "graphics.motionCompensation": True,
        "graphics.lowLatencyMode": True,
        "streaming.profile": "Balanced",
        "streaming.autoBitrate": True,
        "runtime.disableHomeOnLaunch": True,
        "runtime.preferPerformanceMode": True,
    }
    applied_files = 0
    for path in settings_paths:
        applied_files += int(apply_json_profile(path, "vive-hub", updates, log))

    registry_applied = 0
    if winreg is not None:
        hkcu_paths = find_registry_paths(
            winreg.HKEY_CURRENT_USER,
            "Software",
            ["vive", "htc", "viveport"],
            max_depth=3,
        )
        registry_applied += apply_registry_profile(
            winreg.HKEY_CURRENT_USER,
            "HKCU",
            hkcu_paths,
            "vive-hub",
            {
                "AutoResolution": 1,
                "MotionCompensation": 1,
                "LowLatencyMode": 1,
                "AutoBitrate": 1,
                "PreferPerformanceMode": 1,
            },
            {
                "QualityPreset": "Balanced",
                "GraphicsProfile": "Balanced",
            },
            log,
        )

    if not applied_files and not registry_applied:
        log("Vive Hub/Vive Console settings were not detected. Install/open Vive Hub once, then reload specs.")


def apply_no_sleep_ac(log: Callable[[str], None]) -> None:
    code, before = run_process(["powercfg", "/query", "SCHEME_CURRENT"])
    save_backup("power-timeouts", {"active_scheme_query_before": before})
    run_logged(log, ["powercfg", "/change", "monitor-timeout-ac", "0"])
    run_logged(log, ["powercfg", "/change", "standby-timeout-ac", "0"])


def apply_usb_suspend_off(log: Callable[[str], None]) -> None:
    code, before = run_process(["powercfg", "/query", "SCHEME_CURRENT"])
    save_backup("usb-power", {"active_scheme_query_before": before})
    subgroup_usb = "2a737441-1930-4402-8d77-b2bebba308a3"
    setting_suspend = "48e6b7a6-50f5-4782-a5d4-53bb8f07e226"
    run_logged(log, ["powercfg", "/SETACVALUEINDEX", "SCHEME_CURRENT", subgroup_usb, setting_suspend, "0"])
    run_logged(log, ["powercfg", "/SETDCVALUEINDEX", "SCHEME_CURRENT", subgroup_usb, setting_suspend, "0"])
    run_logged(log, ["powercfg", "/S", "SCHEME_CURRENT"])


def apply_game_capture_off(log: Callable[[str], None]) -> None:
    if winreg is None:
        raise RuntimeError("Registry unavailable.")
    items = [
        ("GameDVR_Enabled", winreg.HKEY_CURRENT_USER, r"System\GameConfigStore", "GameDVR_Enabled"),
        ("GameDVR_FSEBehaviorMode", winreg.HKEY_CURRENT_USER, r"System\GameConfigStore", "GameDVR_FSEBehaviorMode"),
        ("AppCaptureEnabled", winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\GameDVR", "AppCaptureEnabled"),
    ]
    save_backup("game-capture-registry", reg_snapshot(items))
    set_reg_dword(winreg.HKEY_CURRENT_USER, r"System\GameConfigStore", "GameDVR_Enabled", 0)
    set_reg_dword(winreg.HKEY_CURRENT_USER, r"System\GameConfigStore", "GameDVR_FSEBehaviorMode", 2)
    set_reg_dword(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\GameDVR", "AppCaptureEnabled", 0)
    log("Disabled Windows background capture/Game DVR registry flags.")


def apply_hags_on(log: Callable[[str], None]) -> None:
    if winreg is None:
        raise RuntimeError("Registry unavailable.")
    items = [
        ("HwSchMode", winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers", "HwSchMode"),
    ]
    save_backup("hags-registry", reg_snapshot(items))
    set_reg_dword(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers", "HwSchMode", 2)
    log("Enabled Hardware-accelerated GPU scheduling. Restart Windows before judging performance.")


def apply_process_priority(log: Callable[[str], None]) -> None:
    process_names = json.dumps(PCVR_PROCESS_NAMES)
    script = rf"""
$names = ConvertFrom-Json @'
{process_names}
'@
$changed = @()
foreach ($name in $names) {{
  Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {{
    try {{
      $_.PriorityClass = "High"
      $changed += "$($_.ProcessName)($($_.Id))"
    }} catch {{
      Write-Output "Could not set priority for $($_.ProcessName): $($_.Exception.Message)"
    }}
  }}
}}
if ($changed.Count -eq 0) {{ "No supported PCVR processes were running." }} else {{ "Set High priority: " + ($changed -join ", ") }}
"""
    code, output = run_powershell(script)
    log(output or "Priority command completed.")
    if code != 0:
        raise RuntimeError(output)


def apply_dns_flush(log: Callable[[str], None]) -> None:
    run_logged(log, ["ipconfig", "/flushdns"])


def apply_tcp_defaults(log: Callable[[str], None]) -> None:
    code, before = run_process(["netsh", "int", "tcp", "show", "global"])
    save_backup("tcp-global", {"before": before})
    run_logged(log, ["netsh", "int", "tcp", "set", "heuristics", "disabled"])
    run_logged(log, ["netsh", "int", "tcp", "set", "global", "autotuninglevel=normal"])
    run_logged(log, ["netsh", "int", "tcp", "set", "global", "rss=enabled"])
    run_logged(log, ["netsh", "int", "tcp", "set", "global", "ecncapability=disabled"])


def apply_network_repair(log: Callable[[str], None]) -> None:
    code, tcp_before = run_process(["netsh", "int", "tcp", "show", "global"])
    save_backup("network-repair", {"tcp_before": tcp_before})
    run_logged(log, ["ipconfig", "/flushdns"], allow_fail=True)
    run_logged(log, ["netsh", "winsock", "reset"])
    run_logged(log, ["netsh", "int", "ip", "reset"])


def backup_steamvr_settings(log: Callable[[str], None]) -> None:
    steam = detect_steam()
    settings = steam.get("SteamVRSettings", [])
    if not settings:
        log("No steamvr.vrsettings file found to back up.")
        return
    ensure_dirs()
    for path_text in settings:
        source = Path(path_text)
        target = BACKUP_DIR / f"{source.name}-{now_stamp()}.bak"
        shutil.copy2(source, target)
        log(f"Backed up {source} to {target}")


def restart_steamvr_processes(log: Callable[[str], None]) -> None:
    script = r"""
$names = "vrmonitor","vrserver","vrcompositor","vrdashboard"
foreach ($name in $names) {
  Get-Process -Name $name -ErrorAction SilentlyContinue | Stop-Process -Force
}
"Stopped running SteamVR background processes. Start SteamVR again from Steam."
"""
    code, output = run_powershell(script)
    log(output or "SteamVR process restart command completed.")
    if code != 0:
        raise RuntimeError(output)


def launch_vrchat(log: Callable[[str], None]) -> None:
    if is_windows():
        os.startfile(f"steam://rungameid/{STEAM_VRCHAT_APPID}")  # type: ignore[attr-defined]
        log("Sent Steam launch request for VRChat.")
    else:
        raise RuntimeError("Steam URI launch is only implemented on Windows.")


def launch_first_runtime_exe(runtime_key: str, friendly_name: str, log: Callable[[str], None]) -> bool:
    runtimes = detect_pcvr_runtimes()
    paths = runtimes.get(runtime_key, [])
    if isinstance(paths, list):
        for path_text in paths:
            if isinstance(path_text, str) and Path(path_text).exists():
                os.startfile(Path(path_text))  # type: ignore[attr-defined]
                log(f"Launched {friendly_name}: {path_text}")
                return True
    return False


def launch_vive_hub(log: Callable[[str], None]) -> None:
    if not launch_first_runtime_exe("Vive", "Vive Hub / Vive Console", log):
        log("Vive Hub or Vive Console was not detected. Install it first, then reload specs.")


def launch_virtual_desktop(log: Callable[[str], None]) -> None:
    if not launch_first_runtime_exe("VirtualDesktop", "Virtual Desktop Streamer", log):
        log("Virtual Desktop Streamer was not detected. Install it first, then reload specs.")


def launch_steam_link(log: Callable[[str], None]) -> None:
    if is_windows():
        os.startfile(f"steam://rungameid/{STEAM_LINK_APPID}")  # type: ignore[attr-defined]
        log("Sent Steam launch request for Steam Link.")
    else:
        raise RuntimeError("Steam URI launch is only implemented on Windows.")


def launch_ovr_advanced_settings(log: Callable[[str], None]) -> None:
    if launch_first_runtime_exe("OvrTools", "OVR / OpenVR overlay tool", log):
        return
    if is_windows():
        os.startfile(f"steam://rungameid/{OVR_ADVANCED_SETTINGS_APPID}")  # type: ignore[attr-defined]
        log("Sent Steam launch request for OVR Advanced Settings.")
    else:
        raise RuntimeError("Steam URI launch is only implemented on Windows.")


def launch_magic_chatbox(log: Callable[[str], None]) -> None:
    if not launch_first_runtime_exe("MagicChatbox", "MagicChatbox", log):
        log("MagicChatbox was not detected. Install it first, then reload specs.")


def launch_vrcft(log: Callable[[str], None]) -> None:
    if not launch_first_runtime_exe("VRCFaceTracking", "VRCFaceTracking", log):
        log("VRCFaceTracking was not detected. Install it first, then reload specs.")


def apply_pcvr_streaming_network(log: Callable[[str], None]) -> None:
    script = r"""
$profiles = Get-NetConnectionProfile -ErrorAction SilentlyContinue | Where-Object { $_.IPv4Connectivity -ne "Disconnected" -or $_.IPv6Connectivity -ne "Disconnected" }
$snapshot = $profiles | Select-Object Name,InterfaceAlias,NetworkCategory,IPv4Connectivity,IPv6Connectivity
$snapshot | ConvertTo-Json -Depth 4
"""
    code, before = run_powershell(script)
    save_backup("pcvr-streaming-network-profile", {"before": before})
    apply_script = r"""
$profiles = Get-NetConnectionProfile -ErrorAction SilentlyContinue | Where-Object { $_.IPv4Connectivity -ne "Disconnected" -or $_.IPv6Connectivity -ne "Disconnected" }
if (-not $profiles) {
  "No active network profiles found."
  return
}
foreach ($profile in $profiles) {
  try {
    Set-NetConnectionProfile -InterfaceIndex $profile.InterfaceIndex -NetworkCategory Private -ErrorAction Stop
    Write-Output "Set Private network profile: $($profile.Name) [$($profile.InterfaceAlias)]"
  } catch {
    Write-Output "Could not change $($profile.Name): $($_.Exception.Message)"
  }
}
foreach ($group in "Network Discovery") {
  try {
    Enable-NetFirewallRule -DisplayGroup $group -ErrorAction Stop
    Write-Output "Enabled firewall group: $group"
  } catch {
    Write-Output "Firewall group skipped: $group ($($_.Exception.Message))"
  }
}
"""
    code, output = run_powershell(apply_script, timeout=90)
    if output:
        log(output)
    if code != 0:
        raise RuntimeError(output or "PCVR streaming network command failed.")


def apply_runtime_services_for(runtime_key: str, friendly_name: str, log: Callable[[str], None]) -> None:
    patterns = RUNTIME_SERVICE_PATTERNS[runtime_key]
    payload = json.dumps(patterns)
    snapshot_script = rf"""
$patterns = ConvertFrom-Json @'
{payload}
'@
$services = Get-CimInstance Win32_Service -ErrorAction SilentlyContinue | Where-Object {{
  $name = ($_.Name + " " + $_.DisplayName).ToLowerInvariant()
  foreach ($pattern in $patterns) {{
    if ($name.Contains($pattern)) {{ return $true }}
  }}
  return $false
}}
$services | Select-Object Name,DisplayName,State,StartMode,PathName | ConvertTo-Json -Depth 4
"""
    code, before = run_powershell(snapshot_script)
    save_backup(f"{runtime_key}-runtime-services", {"before": before})

    apply_script = rf"""
$patterns = ConvertFrom-Json @'
{payload}
'@
$services = Get-CimInstance Win32_Service -ErrorAction SilentlyContinue | Where-Object {{
  $name = ($_.Name + " " + $_.DisplayName).ToLowerInvariant()
  foreach ($pattern in $patterns) {{
    if ($name.Contains($pattern)) {{ return $true }}
  }}
  return $false
}}
if (-not $services) {{
  "No {friendly_name} Windows services were detected."
  return
}}
foreach ($svc in $services) {{
  try {{
    if ($svc.StartMode -eq "Disabled") {{
      Set-Service -Name $svc.Name -StartupType Manual -ErrorAction Stop
      Write-Output "Enabled Manual startup: $($svc.DisplayName)"
    }}
  }} catch {{
    Write-Output "Startup unchanged for $($svc.DisplayName): $($_.Exception.Message)"
  }}
  try {{
    Start-Service -Name $svc.Name -ErrorAction Stop
    Write-Output "Running: $($svc.DisplayName)"
  }} catch {{
    $state = (Get-Service -Name $svc.Name -ErrorAction SilentlyContinue).Status
    Write-Output "Start skipped for $($svc.DisplayName) [$state]: $($_.Exception.Message)"
  }}
}}
"""
    code, output = run_powershell(apply_script, timeout=120)
    if output:
        log(output)
    if code != 0:
        raise RuntimeError(output or f"{friendly_name} service optimization failed.")


def apply_vive_runtime_optimization(log: Callable[[str], None]) -> None:
    apply_runtime_services_for("vive", "Vive Hub / Vive Console", log)


def apply_virtual_desktop_runtime_optimization(log: Callable[[str], None]) -> None:
    apply_runtime_services_for("virtual_desktop", "Virtual Desktop Streamer", log)


def apply_steam_link_runtime_optimization(log: Callable[[str], None]) -> None:
    apply_runtime_services_for("steam_link", "Steam Link / Steam Remote Play", log)


def apply_ovr_tools_runtime_optimization(log: Callable[[str], None]) -> None:
    apply_runtime_services_for("ovr_tools", "OVR / OpenVR overlay tools", log)


def apply_magic_chatbox_runtime_optimization(log: Callable[[str], None]) -> None:
    apply_runtime_services_for("magic_chatbox", "MagicChatbox", log)


def apply_vrcft_runtime_optimization(log: Callable[[str], None]) -> None:
    apply_runtime_services_for("vrcft", "VRCFaceTracking", log)


def apply_network_adapter_streaming_performance(log: Callable[[str], None]) -> None:
    snapshot_script = r"""
$adapters = Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq "Up" }
$advanced = foreach ($adapter in $adapters) {
  Get-NetAdapterAdvancedProperty -Name $adapter.Name -ErrorAction SilentlyContinue |
    Select-Object @{n="Adapter";e={$adapter.Name}},DisplayName,DisplayValue
}
[pscustomobject]@{
  Adapters = @($adapters | Select-Object Name,InterfaceDescription,Status,LinkSpeed,MacAddress)
  Power = @(Get-NetAdapterPowerManagement -Name ($adapters.Name) -ErrorAction SilentlyContinue)
  Advanced = @($advanced)
} | ConvertTo-Json -Depth 5
"""
    code, before = run_powershell(snapshot_script)
    save_backup("network-adapter-streaming-performance", {"before": before})

    apply_script = r"""
$adapters = Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq "Up" }
if (-not $adapters) {
  "No active physical network adapters found."
  return
}
$targets = @(
  @{ Name = "Energy Efficient Ethernet"; Value = "Disabled" },
  @{ Name = "Green Ethernet"; Value = "Disabled" },
  @{ Name = "Power Saving Mode"; Value = "Disabled" },
  @{ Name = "U-APSD support"; Value = "Disabled" },
  @{ Name = "Throughput Booster"; Value = "Enabled" }
)
foreach ($adapter in $adapters) {
  Write-Output "Optimizing network adapter for PCVR streaming: $($adapter.Name)"
  try {
    Disable-NetAdapterPowerManagement -Name $adapter.Name -ErrorAction Stop | Out-Null
    Write-Output "Disabled adapter power management: $($adapter.Name)"
  } catch {
    Write-Output "Power management unchanged for $($adapter.Name): $($_.Exception.Message)"
  }
  foreach ($target in $targets) {
    $prop = Get-NetAdapterAdvancedProperty -Name $adapter.Name -DisplayName $target.Name -ErrorAction SilentlyContinue
    if ($prop) {
      try {
        Set-NetAdapterAdvancedProperty -Name $adapter.Name -DisplayName $target.Name -DisplayValue $target.Value -NoRestart -ErrorAction Stop
        Write-Output "$($target.Name) -> $($target.Value)"
      } catch {
        Write-Output "$($target.Name) unchanged: $($_.Exception.Message)"
      }
    }
  }
}
"Adapter changes may briefly reconnect networking or require reconnecting Wi-Fi."
"""
    code, output = run_powershell(apply_script, timeout=120)
    if output:
        log(output)
    if code != 0:
        raise RuntimeError(output or "Network adapter streaming optimization failed.")


def run_logged(log: Callable[[str], None], command: list[str], allow_fail: bool = False) -> None:
    log("> " + " ".join(command))
    code, output = run_process(command)
    if output:
        log(output)
    if code != 0 and not allow_fail:
        raise RuntimeError(output or f"Command failed with exit code {code}.")


def build_actions(specs: dict[str, object]) -> list[Action]:
    ram = get_ram_gb(specs)
    low_memory = bool(ram and ram < 16)
    return [
        Action(
            key="required_services",
            category="Prerequisites",
            title="Restore required VR and network services",
            description="Turns key audio, USB/HID, Bluetooth, Steam, DNS, DHCP, and Wi-Fi services back on if a tweak tool disabled them.",
            commands=[
                "Set-Service Audiosrv, AudioEndpointBuilder, Dhcp, Dnscache, NlaSvc to Automatic",
                "Set-Service PlugPlay, DeviceInstall, DsmSvc, hidserv, BthServ, WlanSvc, Steam Client Service to Manual/available",
                "Start detected required services where Windows allows it",
            ],
            apply=apply_required_services,
            requires_admin=True,
            recommended=True,
        ),
        Action(
            key="game_mode_on",
            category="Prerequisites",
            title="Turn Windows Game Mode back on",
            description="Restores Game Mode flags in case they were disabled by an old optimizer or debloat script.",
            commands=[
                r'reg add "HKCU\Software\Microsoft\GameBar" /v AllowAutoGameMode /t REG_DWORD /d 1 /f',
                r'reg add "HKCU\Software\Microsoft\GameBar" /v AutoGameModeEnabled /t REG_DWORD /d 1 /f',
            ],
            apply=apply_game_mode_on,
            recommended=True,
        ),
        Action(
            key="gpu_preferences",
            category="Prerequisites",
            title="Force high-performance GPU for detected VR apps",
            description="Adds Windows Graphics Settings entries for detected VRChat, PCVR runtimes, OVR tools, MagicChatbox, and VRCFaceTracking executables.",
            commands=[r'reg add "HKCU\Software\Microsoft\DirectX\UserGpuPreferences" /v "<detected PCVR exe>" /d "GpuPreference=2;" /f'],
            apply=apply_gpu_preferences,
            recommended=True,
        ),
        Action(
            key="firewall_allow",
            category="Prerequisites",
            title="Allow detected VR apps through Windows Firewall",
            description="Creates or re-enables inbound/outbound allow rules for detected VRChat, PCVR runtimes, OVR tools, MagicChatbox, and VRCFaceTracking executables.",
            commands=["New-NetFirewallRule for detected VRChat, PCVR runtimes, OVR tools, MagicChatbox, and VRCFaceTracking executables"],
            apply=apply_firewall_allow_rules,
            requires_admin=True,
            recommended=True,
        ),
        Action(
            key="pcvr_streaming_network",
            category="Compatibility",
            title="Prepare network for PCVR streaming",
            description="Sets active network profiles to Private and enables Network Discovery firewall rules for Virtual Desktop and Steam Link local streaming.",
            commands=[
                "Set-NetConnectionProfile -NetworkCategory Private for active networks",
                "Enable-NetFirewallRule -DisplayGroup \"Network Discovery\"",
            ],
            apply=apply_pcvr_streaming_network,
            requires_admin=True,
            recommended=False,
        ),
        Action(
            key="vive_runtime_optimize",
            category="Vive Hub",
            title="Optimize Vive Hub / Vive Console services",
            description="Finds Vive/HTC/Viveport Windows services, re-enables disabled ones to Manual startup, and starts them when Windows allows it.",
            commands=["Set-Service detected Vive/HTC services -StartupType Manual", "Start-Service detected Vive/HTC services"],
            apply=apply_vive_runtime_optimization,
            requires_admin=True,
            recommended=True,
        ),
        Action(
            key="vive_balanced_settings",
            category="Vive Hub",
            title="Apply balanced Vive Hub / Vive Console settings",
            description="Backs up and edits detected Vive Hub/Vive Console settings files or registry keys for balanced graphics, auto resolution, motion compensation, and low latency.",
            commands=[
                "Backup detected Vive settings files/registry keys",
                "Set balanced graphics/performance profile where detected",
                "Enable auto resolution, motion compensation, low latency, and performance mode where supported",
            ],
            apply=apply_vive_balanced_settings,
            requires_admin=False,
            recommended=True,
        ),
        Action(
            key="virtual_desktop_runtime_optimize",
            category="Virtual Desktop",
            title="Optimize Virtual Desktop Streamer services",
            description="Finds Virtual Desktop Windows services, re-enables disabled ones to Manual startup, and starts them for headset streaming.",
            commands=["Set-Service detected Virtual Desktop services -StartupType Manual", "Start-Service detected Virtual Desktop services"],
            apply=apply_virtual_desktop_runtime_optimization,
            requires_admin=True,
            recommended=True,
        ),
        Action(
            key="virtual_desktop_balanced_settings",
            category="Virtual Desktop",
            title="Apply balanced Virtual Desktop settings",
            description="Backs up and edits detected Virtual Desktop Streamer settings files or registry keys for automatic bitrate/resolution, low latency, and balanced graphics.",
            commands=[
                "Backup detected Virtual Desktop settings files/registry keys",
                "Set preferred codec Auto, auto bitrate, auto resolution, sliced encoding",
                "Set low latency and balanced sharpening/dynamic quality where supported",
            ],
            apply=apply_virtual_desktop_balanced_settings,
            requires_admin=False,
            recommended=True,
        ),
        Action(
            key="steam_link_runtime_optimize",
            category="Steam Link",
            title="Optimize Steam Link / Remote Play services",
            description="Finds Steam streaming/Remote Play support services, re-enables disabled ones to Manual startup, and starts them when present.",
            commands=["Set-Service detected Steam streaming services -StartupType Manual", "Start-Service detected Steam streaming services"],
            apply=apply_steam_link_runtime_optimization,
            requires_admin=True,
            recommended=True,
        ),
        Action(
            key="ovr_tools_runtime_optimize",
            category="OVR Tools",
            title="Optimize OVR Advanced Settings / OVR Desktop tools",
            description="Finds OVR/OpenVR overlay support services, re-enables disabled ones to Manual startup, and includes OVR apps in GPU/firewall/priority optimization.",
            commands=["Set-Service detected OVR/OpenVR services -StartupType Manual", "Start-Service detected OVR/OpenVR services"],
            apply=apply_ovr_tools_runtime_optimization,
            requires_admin=True,
            recommended=True,
        ),
        Action(
            key="magic_chatbox_runtime_optimize",
            category="MagicChatbox",
            title="Optimize MagicChatbox",
            description="Finds MagicChatbox support services if present, re-enables disabled ones, and includes MagicChatbox in firewall/priority optimization for OSC chatbox use.",
            commands=["Set-Service detected MagicChatbox services -StartupType Manual", "Start-Service detected MagicChatbox services"],
            apply=apply_magic_chatbox_runtime_optimization,
            requires_admin=True,
            recommended=True,
        ),
        Action(
            key="vrcft_runtime_optimize",
            category="VRCFaceTracking",
            title="Optimize VRCFaceTracking",
            description="Finds VRCFaceTracking support services if present, re-enables disabled ones, and includes VRCFT in firewall/priority optimization for OSC face tracking.",
            commands=["Set-Service detected VRCFaceTracking services -StartupType Manual", "Start-Service detected VRCFaceTracking services"],
            apply=apply_vrcft_runtime_optimization,
            requires_admin=True,
            recommended=True,
        ),
        Action(
            key="network_adapter_streaming_performance",
            category="Performance + Graphics",
            title="Optimize active network adapters for PCVR streaming",
            description="Disables adapter power saving and common green/energy-saving options on active network adapters for steadier Virtual Desktop and Steam Link streaming.",
            commands=[
                "Disable-NetAdapterPowerManagement for active physical adapters",
                "Disable Energy Efficient Ethernet / Green Ethernet / Power Saving Mode where supported",
                "Enable Throughput Booster where supported",
            ],
            apply=apply_network_adapter_streaming_performance,
            requires_admin=True,
            recommended=False,
        ),
        Action(
            key="multimedia_performance",
            category="Performance + Graphics",
            title="Apply low-latency PCVR scheduling profile",
            description="Tunes Windows multimedia/game scheduling for VRChat, Vive Hub, Virtual Desktop, Steam Link, and SteamVR streaming without lowering graphics.",
            commands=[
                r'reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile" /v NetworkThrottlingIndex /t REG_DWORD /d 0xffffffff /f',
                r'reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile" /v SystemResponsiveness /t REG_DWORD /d 0 /f',
                r'reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile\Tasks\Games" /v "GPU Priority" /t REG_DWORD /d 8 /f',
            ],
            apply=apply_multimedia_performance_profile,
            requires_admin=True,
            recommended=True,
        ),
        Action(
            key="fullscreen_optimizations",
            category="Performance + Graphics",
            title="Disable fullscreen optimizations for detected PCVR apps",
            description="Reduces Windows presentation-layer interference for detected VRChat, PCVR runtime, OVR tool, MagicChatbox, and VRCFaceTracking executables.",
            commands=[r'reg add "HKCU\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers" /v "<detected PCVR exe>" /d "~ DISABLEDXMAXIMIZEDWINDOWEDMODE" /f'],
            apply=apply_fullscreen_optimization_profile,
            recommended=True,
        ),
        Action(
            key="steamvr_balanced_quality",
            category="Performance + Graphics",
            title="Apply balanced SteamVR quality preset",
            description="Backs up SteamVR settings, keeps automatic render resolution, enables smoothing/filtering, hides mirror view, and disables SteamVR Home to save GPU/VRAM.",
            commands=[
                "steamvr.vrsettings: steamvr.enableHomeApp=false",
                "steamvr.vrsettings: steamvr.supersampleManualOverride=false",
                "steamvr.vrsettings: steamvr.allowSupersampleFiltering=true",
                "steamvr.vrsettings: steamvr.motionSmoothing=true",
                "steamvr.vrsettings: steamvr.showMirrorView=false",
            ],
            apply=apply_steamvr_balanced_quality,
            recommended=True,
        ),
        Action(
            key="launch_vive",
            category="Compatibility",
            title="Launch Vive Hub / Vive Console",
            description="Starts the detected Vive Hub or Vive Console app if installed.",
            commands=["start detected Vive Hub / Vive Console executable"],
            apply=launch_vive_hub,
            recommended=False,
        ),
        Action(
            key="launch_virtual_desktop",
            category="Compatibility",
            title="Launch Virtual Desktop Streamer",
            description="Starts the detected Virtual Desktop Streamer so Quest/Pico streaming can connect.",
            commands=["start detected Virtual Desktop Streamer executable"],
            apply=launch_virtual_desktop,
            recommended=False,
        ),
        Action(
            key="launch_steam_link",
            category="Compatibility",
            title="Launch Steam Link",
            description="Sends Steam's Steam Link app launch request for Steam Link / Remote Play setups.",
            commands=[f"start steam://rungameid/{STEAM_LINK_APPID}"],
            apply=launch_steam_link,
            recommended=False,
        ),
        Action(
            key="launch_ovr_tools",
            category="OVR Tools",
            title="Launch detected OVR tool",
            description="Starts detected OVR Advanced Settings, OVR Toolkit, OVRdrop, or Desktop+; falls back to Steam OVR Advanced Settings launch.",
            commands=[f"start detected OVR executable or steam://rungameid/{OVR_ADVANCED_SETTINGS_APPID}"],
            apply=launch_ovr_advanced_settings,
            recommended=False,
        ),
        Action(
            key="launch_magic_chatbox",
            category="MagicChatbox",
            title="Launch MagicChatbox",
            description="Starts the detected MagicChatbox app if installed.",
            commands=["start detected MagicChatbox executable"],
            apply=launch_magic_chatbox,
            recommended=False,
        ),
        Action(
            key="launch_vrcft",
            category="VRCFaceTracking",
            title="Launch VRCFaceTracking",
            description="Starts the detected VRCFaceTracking app if installed.",
            commands=["start detected VRCFaceTracking executable"],
            apply=launch_vrcft,
            recommended=False,
        ),
        Action(
            key="power_ultimate",
            category="Power",
            title="Use Ultimate Performance power plan",
            description="Creates/activates Windows Ultimate Performance to reduce CPU and device power throttling during VR.",
            commands=[
                f"powercfg -duplicatescheme {ULTIMATE_POWER_GUID}",
                f"powercfg /setactive {ULTIMATE_POWER_GUID}",
            ],
            apply=apply_power_plan,
            requires_admin=True,
            recommended=True,
        ),
        Action(
            key="power_no_sleep",
            category="Power",
            title="Prevent AC sleep/display timeout",
            description="Keeps Windows from dimming/sleeping while plugged in during long VR sessions.",
            commands=["powercfg /change monitor-timeout-ac 0", "powercfg /change standby-timeout-ac 0"],
            apply=apply_no_sleep_ac,
            requires_admin=True,
            recommended=True,
        ),
        Action(
            key="usb_suspend",
            category="Power",
            title="Disable USB selective suspend",
            description="Helps avoid headset/controller dropouts caused by aggressive USB power saving.",
            commands=[
                "powercfg /SETACVALUEINDEX SCHEME_CURRENT SUB_USB USBSELECTIVE 0",
                "powercfg /SETDCVALUEINDEX SCHEME_CURRENT SUB_USB USBSELECTIVE 0",
            ],
            apply=apply_usb_suspend_off,
            requires_admin=True,
            recommended=True,
        ),
        Action(
            key="game_capture",
            category="Graphics",
            title="Disable Windows Game DVR capture",
            description="Turns off background capture flags that can steal GPU/CPU time from VRChat.",
            commands=[
                r'reg add "HKCU\System\GameConfigStore" /v GameDVR_Enabled /t REG_DWORD /d 0 /f',
                r'reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\GameDVR" /v AppCaptureEnabled /t REG_DWORD /d 0 /f',
            ],
            apply=apply_game_capture_off,
            recommended=True,
        ),
        Action(
            key="hags",
            category="Graphics",
            title="Enable Hardware-accelerated GPU scheduling",
            description="Enables the Windows HAGS setting. It helps some VR PCs and hurts others, so test it after reboot.",
            commands=[r'reg add "HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers" /v HwSchMode /t REG_DWORD /d 2 /f'],
            apply=apply_hags_on,
            requires_admin=True,
            restart_required=True,
            recommended=not low_memory,
        ),
        Action(
            key="priority",
            category="VR Runtime",
            title="Set PCVR process priority to High",
            description="If VRChat, SteamVR, Vive Hub, Virtual Desktop, or Steam Link processes are running, raises their priority for this session.",
            commands=[r'powershell: Get-Process VRChat,vrserver,vrcompositor,VirtualDesktop.Streamer,ViveHub,steam | set PriorityClass=High'],
            apply=apply_process_priority,
            recommended=True,
        ),
        Action(
            key="steamvr_backup",
            category="VR Runtime",
            title="Back up SteamVR settings file",
            description="Copies detected steamvr.vrsettings files before you manually tune render scale/motion smoothing.",
            commands=["copy detected steamvr.vrsettings to backup folder"],
            apply=backup_steamvr_settings,
            recommended=True,
        ),
        Action(
            key="steamvr_restart",
            category="VR Runtime",
            title="Stop stale SteamVR background processes",
            description="Force-closes SteamVR helper processes so SteamVR can start cleanly. Do this only when not in VR.",
            commands=["Stop-Process vrmonitor, vrserver, vrcompositor, vrdashboard -Force"],
            apply=restart_steamvr_processes,
            recommended=False,
        ),
        Action(
            key="dns_flush",
            category="Network",
            title="Flush DNS resolver cache",
            description="Clears stale DNS cache entries. Safe and quick; useful if worlds or logins are acting weird.",
            commands=["ipconfig /flushdns"],
            apply=apply_dns_flush,
            recommended=True,
        ),
        Action(
            key="tcp_defaults",
            category="Network",
            title="Normalize TCP settings for gaming",
            description="Uses Microsoft netsh settings: disables TCP heuristics, enables RSS, normal receive auto-tuning, disables ECN.",
            commands=[
                "netsh int tcp set heuristics disabled",
                "netsh int tcp set global autotuninglevel=normal",
                "netsh int tcp set global rss=enabled",
                "netsh int tcp set global ecncapability=disabled",
            ],
            apply=apply_tcp_defaults,
            requires_admin=True,
            recommended=True,
        ),
        Action(
            key="network_repair",
            category="Network",
            title="Repair Windows network stack",
            description="Runs Winsock/IP reset. Use for broken/unstable networking; it requires a reboot and may reset adapter tweaks.",
            commands=["netsh winsock reset", "netsh int ip reset"],
            apply=apply_network_repair,
            requires_admin=True,
            restart_required=True,
            recommended=False,
        ),
        Action(
            key="launch_vrchat",
            category="Launch",
            title="Launch VRChat through Steam",
            description="Sends steam://rungameid/438100 after selected optimizations finish.",
            commands=[f"start steam://rungameid/{STEAM_VRCHAT_APPID}"],
            apply=launch_vrchat,
            recommended=False,
        ),
    ]


class OptimizerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1180x820")
        self.minsize(980, 680)
        self.configure(bg="#eef2f7")
        self.specs: dict[str, object] = {}
        self.actions: list[Action] = []
        self.action_vars: dict[str, tk.BooleanVar] = {}
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.running = False
        self.log_path: Path | None = None

        self._style()
        self._build_ui()
        self.refresh_specs()
        self.after(120, self._drain_log_queue)

    def _style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", font=("Segoe UI", 10))
        style.configure("App.TFrame", background="#eef2f7")
        style.configure("Panel.TFrame", background="#ffffff")
        style.configure("Hero.TFrame", background="#111827")
        style.configure("Title.TLabel", font=("Segoe UI", 22, "bold"), foreground="#ffffff", background="#111827")
        style.configure("Subtitle.TLabel", font=("Segoe UI", 10), foreground="#cbd5e1", background="#111827")
        style.configure("HeroChip.TLabel", font=("Segoe UI", 9, "bold"), foreground="#dbeafe", background="#1f2937", padding=(10, 5))
        style.configure("Status.TLabel", font=("Segoe UI", 10, "bold"), foreground="#0f172a", background="#eef2f7")
        style.configure("Section.TLabel", font=("Segoe UI", 12, "bold"), foreground="#0f172a", background="#ffffff")
        style.configure("Muted.TLabel", font=("Segoe UI", 9), foreground="#64748b", background="#ffffff")
        style.configure("ActionTitle.TCheckbutton", font=("Segoe UI", 10, "bold"), background="#ffffff", foreground="#111827")
        style.configure("TNotebook", background="#eef2f7", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(16, 8), font=("Segoe UI", 10, "bold"))
        style.configure("TButton", padding=(12, 7))
        style.configure("Run.TButton", font=("Segoe UI", 11, "bold"), padding=(16, 9), foreground="#ffffff", background="#2563eb")
        style.map("Run.TButton", background=[("active", "#1d4ed8"), ("disabled", "#94a3b8")])

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=16, style="App.TFrame")
        root.pack(fill="both", expand=True)

        header = ttk.Frame(root, padding=18, style="Hero.TFrame")
        header.pack(fill="x")
        title_block = ttk.Frame(header, style="Hero.TFrame")
        title_block.pack(side="left", fill="x", expand=True)
        ttk.Label(title_block, text=APP_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            title_block,
            text="Professional PCVR optimizer for SteamVR, Vive Hub, Virtual Desktop, Steam Link, OSC tools, and VRChat.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(4, 0))
        chip_block = ttk.Frame(header, style="Hero.TFrame")
        chip_block.pack(side="right")
        self.admin_label = ttk.Label(chip_block, text="", style="HeroChip.TLabel")
        self.admin_label.pack(side="top", anchor="e", pady=(0, 8))
        self.admin_button = ttk.Button(chip_block, text="Relaunch as Admin", command=relaunch_as_admin)
        self.admin_button.pack(side="top", anchor="e")

        self.summary_var = tk.StringVar(value="Detecting PC specs...")
        self.stats_var = tk.StringVar(value="")
        status_row = ttk.Frame(root, style="App.TFrame")
        status_row.pack(fill="x", pady=(12, 12))
        ttk.Label(status_row, textvariable=self.summary_var, style="Status.TLabel", wraplength=780).pack(side="left", fill="x", expand=True)
        ttk.Label(status_row, textvariable=self.stats_var, style="Status.TLabel").pack(side="right")

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True)

        self.optimize_tab = ttk.Frame(self.notebook, padding=8)
        self.specs_tab = ttk.Frame(self.notebook, padding=8)
        self.log_tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(self.optimize_tab, text="Optimize")
        self.notebook.add(self.specs_tab, text="Detected Specs")
        self.notebook.add(self.log_tab, text="Run Log")

        self._build_optimize_tab()
        self._build_specs_tab()
        self._build_log_tab()

    def _build_optimize_tab(self) -> None:
        top = ttk.Frame(self.optimize_tab, style="Panel.TFrame")
        top.pack(fill="x", pady=(0, 8))
        ttk.Button(top, text="Reload Specs", command=self.refresh_specs).pack(side="left")
        ttk.Button(top, text="Select Recommended", command=self.select_recommended).pack(side="left", padx=6)
        ttk.Button(top, text="Clear Selection", command=self.clear_selection).pack(side="left")
        ttk.Button(top, text="Preview Commands", command=self.preview_commands).pack(side="left", padx=6)
        self.run_button = ttk.Button(top, text="Run Selected", style="Run.TButton", command=self.run_selected)
        self.run_button.pack(side="right")

        panes = ttk.PanedWindow(self.optimize_tab, orient="horizontal")
        panes.pack(fill="both", expand=True)

        list_frame = ttk.Frame(panes)
        preview_frame = ttk.Frame(panes)
        panes.add(list_frame, weight=3)
        panes.add(preview_frame, weight=2)

        self.actions_canvas = tk.Canvas(list_frame, highlightthickness=0, background="#ffffff")
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.actions_canvas.yview)
        self.actions_inner = ttk.Frame(self.actions_canvas)
        self.actions_inner.bind(
            "<Configure>",
            lambda _event: self.actions_canvas.configure(scrollregion=self.actions_canvas.bbox("all")),
        )
        self.actions_window = self.actions_canvas.create_window((0, 0), window=self.actions_inner, anchor="nw")
        self.actions_canvas.bind(
            "<Configure>",
            lambda event: self.actions_canvas.itemconfigure(self.actions_window, width=event.width),
        )
        self.actions_canvas.configure(yscrollcommand=scrollbar.set)
        self.actions_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        ttk.Label(preview_frame, text="Command Preview / Notes", style="Section.TLabel").pack(anchor="w")
        self.preview_text = tk.Text(
            preview_frame,
            height=20,
            wrap="word",
            font=("Cascadia Mono", 10),
            background="#0f172a",
            foreground="#e5e7eb",
            insertbackground="#ffffff",
            relief="flat",
            padx=12,
            pady=12,
        )
        self.preview_text.pack(fill="both", expand=True, pady=(4, 0))

    def _build_specs_tab(self) -> None:
        self.specs_text = tk.Text(self.specs_tab, wrap="none", font=("Cascadia Mono", 10), relief="flat", padx=12, pady=12)
        y = ttk.Scrollbar(self.specs_tab, orient="vertical", command=self.specs_text.yview)
        x = ttk.Scrollbar(self.specs_tab, orient="horizontal", command=self.specs_text.xview)
        self.specs_text.configure(yscrollcommand=y.set, xscrollcommand=x.set)
        self.specs_text.pack(side="left", fill="both", expand=True)
        y.pack(side="right", fill="y")
        x.pack(side="bottom", fill="x")

    def _build_log_tab(self) -> None:
        self.log_text = tk.Text(self.log_tab, wrap="word", font=("Cascadia Mono", 10), relief="flat", padx=12, pady=12)
        y = ttk.Scrollbar(self.log_tab, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=y.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        y.pack(side="right", fill="y")

    def refresh_specs(self) -> None:
        self.specs = detect_specs()
        self.actions = build_actions(self.specs)
        self._render_specs()
        self._render_actions()
        self.select_recommended()
        admin_text = "Administrator: yes" if is_admin() else "Administrator: no"
        self.admin_label.configure(text=admin_text)
        self.admin_button.configure(state=("disabled" if is_admin() else "normal"))
        ram = get_ram_gb(self.specs)
        gpu_names = ", ".join(get_gpu_names(self.specs)) or "GPU unknown"
        self.summary_var.set(f"{ram or '?'} GB RAM | {gpu_names}")
        recommended_count = sum(1 for action in self.actions if action.recommended)
        self.stats_var.set(f"{len(self.actions)} actions | {recommended_count} recommended")

    def _render_specs(self) -> None:
        self.specs_text.configure(state="normal")
        self.specs_text.delete("1.0", "end")
        self.specs_text.insert("end", json.dumps(self.specs, indent=2, default=str))
        self.specs_text.configure(state="disabled")

    def _render_actions(self) -> None:
        for child in self.actions_inner.winfo_children():
            child.destroy()
        self.action_vars.clear()

        grouped: dict[str, list[Action]] = {}
        for action in self.actions:
            grouped.setdefault(action.category, []).append(action)

        for category, actions in grouped.items():
            frame = ttk.Frame(self.actions_inner, style="Panel.TFrame", padding=12)
            frame.pack(fill="x", expand=True, pady=(0, 10), padx=(0, 8))
            header = ttk.Frame(frame, style="Panel.TFrame")
            header.pack(fill="x", pady=(0, 8))
            ttk.Label(header, text=category, style="Section.TLabel").pack(side="left")
            ttk.Label(header, text=f"{len(actions)} actions", style="Muted.TLabel").pack(side="right")
            for action in actions:
                var = tk.BooleanVar(value=False)
                self.action_vars[action.key] = var
                row = ttk.Frame(frame, style="Panel.TFrame")
                row.pack(fill="x", pady=6)
                flags = []
                if action.requires_admin:
                    flags.append("Admin")
                if action.restart_required:
                    flags.append("Restart")
                if not action.recommended:
                    flags.append("Advanced")
                flag_text = f" [{' / '.join(flags)}]" if flags else ""
                ttk.Checkbutton(row, variable=var, text=f"{action.title}{flag_text}", style="ActionTitle.TCheckbutton").pack(anchor="w")
                ttk.Label(row, text=action.description, wraplength=720, style="Muted.TLabel").pack(anchor="w", padx=(26, 0), pady=(2, 0))

    def selected_actions(self) -> list[Action]:
        return [action for action in self.actions if self.action_vars.get(action.key, tk.BooleanVar()).get()]

    def select_recommended(self) -> None:
        for action in self.actions:
            var = self.action_vars.get(action.key)
            if var:
                var.set(action.recommended)
        self.preview_commands()

    def clear_selection(self) -> None:
        for var in self.action_vars.values():
            var.set(False)
        self.preview_commands()

    def preview_commands(self) -> None:
        selected = self.selected_actions()
        self.preview_text.delete("1.0", "end")
        if not selected:
            self.preview_text.insert("end", "No actions selected.")
            return

        for action in selected:
            self.preview_text.insert("end", f"[{action.category}] {action.title}\n")
            if action.requires_admin:
                self.preview_text.insert("end", "Requires administrator rights.\n")
            if action.restart_required:
                self.preview_text.insert("end", "Restart required before this fully applies.\n")
            for cmd in action.commands:
                self.preview_text.insert("end", f"  {cmd}\n")
            self.preview_text.insert("end", "\n")

    def run_selected(self) -> None:
        if self.running:
            return
        selected = self.selected_actions()
        if not selected:
            messagebox.showinfo(APP_NAME, "Select at least one optimization first.")
            return
        admin_needed = [a.title for a in selected if a.requires_admin]
        if admin_needed and not is_admin():
            messagebox.showwarning(APP_NAME, "Some selected actions need administrator rights. Relaunch as admin first.")
            return
        advanced = [a.title for a in selected if not a.recommended]
        details = "\n".join(f"- {a.title}" for a in selected)
        prompt = f"Run these selected actions?\n\n{details}"
        if advanced:
            prompt += "\n\nAdvanced actions selected. Make sure PCVR apps are closed before stopping runtime processes or changing streaming network settings."
        if not messagebox.askyesno(APP_NAME, prompt):
            return

        ensure_dirs()
        self.log_path = LOG_DIR / f"optimizer-{now_stamp()}.log"
        self.running = True
        self.run_button.configure(state="disabled")
        self.notebook.select(self.log_tab)
        self._log(f"Starting {APP_NAME} at {_dt.datetime.now()}")
        self._log(f"Log file: {self.log_path}")
        thread = threading.Thread(target=self._run_worker, args=(selected,), daemon=True)
        thread.start()

    def _run_worker(self, selected: list[Action]) -> None:
        restart_needed = False
        for action in selected:
            self._log("")
            self._log(f"== {action.title} ==")
            try:
                action.apply(self._log)
                self._log("OK")
                restart_needed = restart_needed or action.restart_required
            except Exception as exc:
                self._log(f"FAILED: {exc}")
        self._log("")
        self._log("Finished.")
        if restart_needed:
            self._log("One or more changes require a Windows restart.")
        self.log_queue.put("__DONE__")

    def _log(self, message: str) -> None:
        self.log_queue.put(message)
        if self.log_path:
            try:
                with self.log_path.open("a", encoding="utf-8") as handle:
                    handle.write(message + "\n")
            except OSError:
                pass

    def _drain_log_queue(self) -> None:
        try:
            while True:
                message = self.log_queue.get_nowait()
                if message == "__DONE__":
                    self.running = False
                    self.run_button.configure(state="normal")
                    continue
                self.log_text.insert("end", message + "\n")
                self.log_text.see("end")
        except queue.Empty:
            pass
        self.after(120, self._drain_log_queue)


def main() -> int:
    if not is_windows():
        print("This optimizer is designed for Windows PCs running PCVR runtimes.")
        return 1
    ensure_dirs()
    app = OptimizerApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
