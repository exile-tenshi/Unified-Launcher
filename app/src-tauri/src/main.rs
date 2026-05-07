#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod models;
mod shortcuts;
mod user_library;
mod steam;
mod epic;
mod riot;
mod xbox;
mod optimize;
mod vr_profiles;
mod pc_tweaks;
mod config_finder;
mod startup_apps;
mod uninstall;
mod engine_detect;

use crate::models::{GameEntry, GameLaunch, HideLauncherMode, LaunchRequest, LaunchType};
use tauri::AppHandle;
use std::thread;
use std::time::Duration;
use std::process::Command;

#[tauri::command]
fn discover_games(app: AppHandle) -> Result<Vec<GameEntry>, String> {
    let mut all = Vec::new();
    all.extend(steam::discover_steam_games()?);
    all.extend(epic::discover_epic_games()?);
    all.extend(riot::discover_riot_games()?);
    all.extend(xbox::discover_xbox_games()?);
    all.extend(shortcuts::discover_start_menu_shortcuts()?);
    all.extend(user_library::load_custom_games(&app)?);
    Ok(all)
}

#[tauri::command]
fn launch_game(app: AppHandle, request: LaunchRequest) -> Result<(), String> {
    if let Some(boost) = &request.session_boost {
        if boost.enabled {
            // Do NOT touch Windows/system services. Only close user apps with visible windows.
            // This is intentionally conservative to avoid breaking audio, drivers, overlays, or anti-cheat.
            let allow = boost.allow_processes.clone();
            let aggressive = boost.aggressive_force_kill;
            let _ = close_background_app_windows(&allow, aggressive);
        }
    }

    let result = match request.launch.launch_type {
        LaunchType::SteamUri | LaunchType::EpicUri => open_uri(&request.launch.launch_target),
        LaunchType::Aumid => open_aumid(&request.launch.launch_target),
        LaunchType::Exe => open_exe(&request.launch),
        LaunchType::Shortcut => open_shortcut(&request.launch.launch_target),
    };

    if result.is_ok() && request.hide_launcher_window {
        // Avoid killing processes (anti-cheat/DRM risk). Only minimize/close the launcher UI window.
        let launcher_process = match request.launch.launch_type {
            LaunchType::SteamUri => Some("steam"),
            LaunchType::EpicUri => Some("EpicGamesLauncher"),
            LaunchType::Exe => {
                // Riot uses RiotClientServices.exe; treat as launcher UI.
                let target = request.launch.launch_target.to_lowercase();
                if target.contains("riotclientservices.exe") {
                    Some("RiotClientServices")
                } else {
                    None
                }
            }
            _ => None,
        };
        if let Some(name) = launcher_process {
            let mode = request
                .hide_launcher_mode
                .unwrap_or(HideLauncherMode::Minimize);
            // Epic stability: minimize by default even if caller asks close (unless explicitly set).
            let effective_mode = if matches!(request.launch.launch_type, LaunchType::EpicUri)
                && matches!(mode, HideLauncherMode::CloseWindow)
            {
                // Still allow explicit close for Epic if they picked it; we just default to Minimize.
                HideLauncherMode::CloseWindow
            } else {
                mode
            };
            // Give the launcher a moment to hand off to the game.
            thread::spawn(move || {
                thread::sleep(Duration::from_secs(3));
                let _ = match effective_mode {
                    HideLauncherMode::Minimize => minimize_main_window(name),
                    HideLauncherMode::CloseWindow => close_main_window(name),
                };
            });
        }
    }

    if result.is_ok() && request.close_app_after_launch {
        // Give the game a moment to take focus, then exit our app.
        thread::spawn(move || {
            thread::sleep(Duration::from_millis(900));
            app.exit(0);
        });
    }

    result
}

fn close_background_app_windows(allow_processes: &[String], aggressive_force_kill: bool) -> Result<(), String> {
    if !cfg!(target_os = "windows") {
        return Ok(());
    }

    // Normalize allowlist to process names (no .exe), lowercased.
    let allow = allow_processes
        .iter()
        .map(|p| p.trim().trim_end_matches(".exe").to_lowercase())
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>();

    // Always protect shell/system UX processes even if they have windows.
    // This is *not* exhaustive, but it covers the common ones we must never close.
    let protected = vec![
        "explorer",
        "dwm",
        "sihost",
        "runtimebroker",
        "startmenuexperiencehost",
        "searchhost",
        "shellexperiencehost",
        "taskmgr",
        "applicationframehost",
        "systemsettings",
    ];

    // Also protect our own process.
    let mut protected_all = protected;
    protected_all.push("unified game library");

    // PowerShell: close main windows for user apps, excluding allow/protected.
    // Optionally force-kill remaining ones AFTER a polite close.
    // We only target processes with MainWindowHandle != 0.
    let allow_ps = allow
        .iter()
        .map(|s| format!("'{}'", s.replace("'", "''")))
        .collect::<Vec<_>>()
        .join(",");
    let prot_ps = protected_all
        .iter()
        .map(|s| format!("'{}'", s.replace("'", "''")))
        .collect::<Vec<_>>()
        .join(",");

    let script = format!(
        r#"
$ErrorActionPreference='SilentlyContinue'
$allow = @({allow_ps})
$protected = @({prot_ps})

$targets =
  Get-Process -ErrorAction SilentlyContinue |
  Where-Object {{ $_.MainWindowHandle -ne 0 }} |
  Where-Object {{
    $n = $_.ProcessName.ToLower()
    -not ($allow -contains $n) -and -not ($protected -contains $n)
  }}

foreach ($p in $targets) {{
  try {{ $null = $p.CloseMainWindow() }} catch {{ }}
}}
Start-Sleep -Milliseconds 900

if ({aggressive}) {{
  foreach ($p in $targets) {{
    try {{
      if (!$p.HasExited) {{ Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }}
    }} catch {{ }}
  }}
}}
"#,
        allow_ps = allow_ps,
        prot_ps = prot_ps,
        aggressive = if aggressive_force_kill { "$true" } else { "$false" },
    );

    let out = Command::new("powershell.exe")
        .args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", &script])
        .output()
        .map_err(|e| e.to_string())?;
    if !out.status.success() {
        // Don't fail game launch if the boost step fails.
        return Ok(());
    }
    Ok(())
}

fn open_uri(uri: &str) -> Result<(), String> {
    if cfg!(target_os = "windows") {
        Command::new("cmd")
            .args(["/d", "/s", "/c", "start", "", uri])
            .spawn()
            .map(|_| ())
            .map_err(|e| e.to_string())
    } else {
        Err("URI launch is only implemented on Windows for now.".to_string())
    }
}

fn open_aumid(aumid: &str) -> Result<(), String> {
    if cfg!(target_os = "windows") {
        // Explorer can launch MSIX apps via the AppsFolder virtual folder.
        let target = format!(r"shell:AppsFolder\{}", aumid);
        Command::new("explorer.exe")
            .arg(target)
            .spawn()
            .map(|_| ())
            .map_err(|e| e.to_string())
    } else {
        Err("AUMID launch is only implemented on Windows.".to_string())
    }
}

fn open_exe(launch: &GameLaunch) -> Result<(), String> {
    let mut cmd = Command::new(&launch.launch_target);
    cmd.args(&launch.args);
    if let Some(dir) = &launch.working_dir {
        cmd.current_dir(dir);
    }
    cmd.spawn().map(|_| ()).map_err(|e| e.to_string())
}

fn open_shortcut(path: &str) -> Result<(), String> {
    if cfg!(target_os = "windows") {
        Command::new("cmd")
            .args(["/d", "/s", "/c", "start", "", path])
            .spawn()
            .map(|_| ())
            .map_err(|e| e.to_string())
    } else {
        Err("Shortcut launch is only implemented on Windows.".to_string())
    }
}

fn close_main_window(process_name: &str) -> Result<(), String> {
    if !cfg!(target_os = "windows") {
        return Ok(());
    }
    // CloseMainWindow sends a polite WM_CLOSE to the process main window; it does NOT terminate services.
    let script = format!(
        r#"$ErrorActionPreference='SilentlyContinue'; Get-Process -Name '{}' -ErrorAction SilentlyContinue | Where-Object {{ $_.MainWindowHandle -ne 0 }} | ForEach-Object {{ $_.CloseMainWindow() | Out-Null }}"#,
        process_name.replace("'", "''")
    );
    let out = Command::new("powershell.exe")
        .args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", &script])
        .output()
        .map_err(|e| e.to_string())?;
    if !out.status.success() {
        // Ignore failures (some launchers have no visible window / are already minimized).
        return Ok(());
    }
    Ok(())
}

fn minimize_main_window(process_name: &str) -> Result<(), String> {
    if !cfg!(target_os = "windows") {
        return Ok(());
    }
    // Minimize the main window (ShowWindowAsync SW_MINIMIZE).
    let script = format!(
        r#"$ErrorActionPreference='SilentlyContinue';
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class Win32 {{
  [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
}}
"@;
Get-Process -Name '{name}' -ErrorAction SilentlyContinue |
  Where-Object {{ $_.MainWindowHandle -ne 0 }} |
  ForEach-Object {{ [Win32]::ShowWindowAsync($_.MainWindowHandle, 6) | Out-Null }}"#,
        name = process_name.replace("'", "''")
    );
    let out = Command::new("powershell.exe")
        .args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", &script])
        .output()
        .map_err(|e| e.to_string())?;
    if !out.status.success() {
        return Ok(());
    }
    Ok(())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_store::Builder::default().build())
        .invoke_handler(tauri::generate_handler![
            discover_games,
            launch_game,
            uninstall::uninstall_game,
            user_library::save_custom_games,
            optimize::optimize_game,
            vr_profiles::apply_vr_profile,
            pc_tweaks::apply_pc_tweak,
            config_finder::find_game_config_hints,
            startup_apps::list_startup_apps,
            startup_apps::set_startup_app_enabled
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
