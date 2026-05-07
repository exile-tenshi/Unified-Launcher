# Unified Launcher (Unified Game Library)

One professional **Windows desktop launcher** that aggregates your games and lets you launch them from one library UI.

Supported sources:
- Steam
- Epic Games Launcher
- Xbox / Microsoft Store (best-effort via installed app IDs)
- Riot (reads Riot ProgramData install metadata)
- Other launchers via Start Menu shortcuts
- Custom games (any EXE + args)

> This project is not affiliated with Valve/Steam, Epic, Microsoft/Xbox, Riot, or any other publisher/launcher.

## Download (for users)
- **Recommended**: go to the **Releases** page and download the latest Windows installer:
  - NSIS installer `.exe` (creates Start Menu + Desktop shortcut)
  - MSI installer `.msi`

If you don’t see a Release yet:
- Open the **Actions** tab and download the latest `windows-bundles` artifact from the build workflow.

## What it does
- **Unified library UI** with search/filter and per-game details.
- **Launch games safely** using official protocols when required (anti-cheat/DRM friendly).
- Optional **VR profiles** (SteamVR / Virtual Desktop / Vive Hub) and **PC tweaks** (no overclocking, backup-first, opt-in).
- Optional **in-game config optimization** for supported engines (currently Unreal detection only).
- Editable **game images** per title (stored locally).

## Build (for developers)
Requirements:
- Node.js 20+
- Rust (Cargo)
- Visual Studio Build Tools (Desktop development with C++)

Build installers:

```powershell
cd app
npm install
npm run tauri build
```

Outputs:
- `app\src-tauri\target\release\bundle\nsis\` (installer `.exe`)
- `app\src-tauri\target\release\bundle\msi\` (installer `.msi`)

## Security / anti-cheat note
This app avoids force-killing launchers/services. It can optionally **minimize** launcher windows after handing off to the game.

## Legal
See: `TERMS.md`, `PRIVACY.md`, `EULA.md`, `DISCLAIMER.md`, `DMCA.md` (also viewable inside the app Settings → Legal).

## Legacy
This repo also contains earlier VR optimization tooling (Python/Tkinter). The new standalone launcher lives in `app/`.
