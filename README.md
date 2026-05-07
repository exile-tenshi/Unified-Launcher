<div align="center">

<img src="app/src-tauri/icons/128x128.png" alt="Unified Game Library icon" width="96" height="96">

# Unified Game Library

### Your games. One library. One click to launch.

[![Latest release](https://img.shields.io/github/v/release/exile-tenshi/Unified-Launcher?style=for-the-badge&logo=github&label=Download&color=3b82f6)](https://github.com/exile-tenshi/Unified-Launcher/releases/latest)
[![Windows CI](https://img.shields.io/github/actions/workflow/status/exile-tenshi/Unified-Launcher/windows-build.yml?branch=master&style=for-the-badge&label=Windows%20build&logo=githubactions)](https://github.com/exile-tenshi/Unified-Launcher/actions/workflows/windows-build.yml)
[![Platform](https://img.shields.io/badge/platform-Windows%20x64-2ea043?style=for-the-badge&logo=windows)](https://github.com/exile-tenshi/Unified-Launcher/releases/latest)

<br />

### Download (pick one)

|  | File on the [Latest release](https://github.com/exile-tenshi/Unified-Launcher/releases/latest) | Best for |
|---:|---|---|
| **1** | **`Unified Game Library_*_x64-setup.exe`** | Most users — wizard installer, Start Menu + desktop shortcut |
| **2** | **`Unified Game Library_*_x64_en-US.msi`** | IT / silent installs / Microsoft Installer workflows |
| **3** | **`unified-game-library.exe`** | Portable — run without installing (WebView2 usually already on Windows) |

<p align="center">
  <a href="https://github.com/exile-tenshi/Unified-Launcher/releases/latest"><b>→ Go to Releases (latest downloads)</b></a>
</p>

<sub>This project is not affiliated with Valve/Steam, Epic Games, Microsoft/Xbox, Riot Games, or any other publisher or launcher.</sub>

<br />

</div>

---

## Features

| | |
|:---|:---|
| **All-in-one library** | Steam, Epic, Xbox / Microsoft Store, Riot, Start Menu shortcuts, plus custom EXEs |
| **Launch safely** | Uses official launcher protocols where required (better for DRM / anti-cheat than hacks) |
| **Optimize** | PC & VR tweaks tab; per-game **best-effort** profiles for **Source**, **Unity** (registry), and **Unreal** configs |
| **Startup Apps** | Review and disable noisy startup entries (with backups / restore) |
| **Your artwork** | Set local cover images per game |
| **Legal docs** | Terms, Privacy, EULA, Disclaimer, DMCA — in-app and in this repo |

---

## Screenshots

_Add screenshots here later (drag into GitHub Issues or commit under `.github/` for a polished readme.)_

---

## Requirements

- **Windows 10 or 11** (64-bit)
- **Microsoft Edge WebView2** Runtime — normally already installed on Windows 11 and recent Windows 10

---

## Build from source

Requires **Node.js 20+**, **Rust (Cargo)**, and **Visual Studio Build Tools** (Desktop development with C++).

```powershell
cd app
npm ci
npm run tauri build
```

Installers are written to:

- `app\src-tauri\target\release\bundle\nsis\`
- `app\src-tauri\target\release\bundle\msi\`

To force artifacts into the repo `target` folder:

```powershell
$env:CARGO_TARGET_DIR = "$(Resolve-Path .\src-tauri\target)"
npm run tauri build
```

---

## Security note

The app avoids force-killing launcher processes or anti-cheat services. Optional behaviors **minimize or close launcher windows** only — see **Settings** in the app.

---

## Legal

See **`TERMS.md`**, **`PRIVACY.md`**, **`EULA.md`**, **`DISCLAIMER.md`**, **`DMCA.md`** — also available inside the app under **Settings → Legal**.

---

## Legacy

Older Python/Tkinter experiments (`installer.py`, VR helpers, etc.) remain for reference. The shipping desktop app lives in **`app/`**.
