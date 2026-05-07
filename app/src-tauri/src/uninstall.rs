use crate::models::{GameEntry, Launcher};
use std::process::Command;

#[tauri::command]
pub fn uninstall_game(entry: GameEntry) -> Result<String, String> {
    if !cfg!(target_os = "windows") {
        return Err("Uninstall is only implemented on Windows.".to_string());
    }

    // We intentionally do NOT delete folders ourselves.
    // We launch official uninstall handlers (Steam uninstall URI, registry UninstallString, etc).
    match entry.launcher {
        Launcher::Steam => {
            // Our steam ids are "steam:<appid>".
            if let Some(appid) = entry.id.strip_prefix("steam:") {
                let uri = format!("steam://uninstall/{}", appid);
                open_uri(&uri)?;
                return Ok("Opened Steam uninstall prompt (removes game from PC).".to_string());
            }
            // If Steam id missing, try registry uninstall match by name, else fall back.
            if let Some(cmd) = find_uninstall_command(&entry.name) {
                launch_uninstall_command(&cmd)?;
                return Ok("Launched uninstall command.".to_string());
            }
            open_apps_features()?;
            Ok("Could not find uninstall command; opened Apps & features.".to_string())
        }
        Launcher::Epic | Launcher::Xbox | Launcher::Riot | Launcher::Other => {
            // Best-effort: find uninstall command from registry by display name.
            // If not found, open Apps & features (complete uninstall is still possible there).
            if let Some(cmd) = find_uninstall_command(&entry.name) {
                launch_uninstall_command(&cmd)?;
                return Ok("Launched uninstall command (removes from PC).".to_string());
            }
            open_apps_features()?;
            Ok("No uninstall command found automatically; opened Apps & features (you can uninstall completely there).".to_string())
        }
    }
}

fn open_apps_features() -> Result<(), String> {
    // Windows 10/11 Settings entrypoint.
    open_uri("ms-settings:appsfeatures")
}

fn open_uri(uri: &str) -> Result<(), String> {
    Command::new("cmd")
        .args(["/d", "/s", "/c", "start", "", uri])
        .spawn()
        .map(|_| ())
        .map_err(|e| e.to_string())
}

fn find_uninstall_command(game_name: &str) -> Option<String> {
    // Query multiple uninstall registry locations and pick the closest DisplayName match.
    // We keep it PowerShell-based to avoid additional dependencies.
    let name = game_name.replace("'", "''");
    let script = format!(
        r#"
$ErrorActionPreference='SilentlyContinue'
$q = '{name}'.ToLower()
$paths = @(
 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
 'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
)
$hits = @()
foreach ($p in $paths) {{
  Get-ItemProperty -Path $p -ErrorAction SilentlyContinue | ForEach-Object {{
    $dn = [string]$_.DisplayName
    if ([string]::IsNullOrWhiteSpace($dn)) {{ return }}
    $dnl = $dn.ToLower()
    if ($dnl -like "*$q*") {{
      $cmd = [string]$_.QuietUninstallString
      if ([string]::IsNullOrWhiteSpace($cmd)) {{ $cmd = [string]$_.UninstallString }}
      if ([string]::IsNullOrWhiteSpace($cmd)) {{ return }}
      $hits += [pscustomobject]@{{ DisplayName=$dn; Cmd=$cmd; Score=100 }}
    }}
  }}
}}
if ($hits.Count -eq 0) {{
  # Fuzzy fallback: token overlap scoring
  $tokens = $q -split '\s+' | Where-Object {{ $_.Length -ge 4 }}
  foreach ($p in $paths) {{
    Get-ItemProperty -Path $p -ErrorAction SilentlyContinue | ForEach-Object {{
      $dn = [string]$_.DisplayName
      if ([string]::IsNullOrWhiteSpace($dn)) {{ return }}
      $dnl = $dn.ToLower()
      $score = 0
      foreach ($t in $tokens) {{
        if ($dnl -like "*$t*") {{ $score += 5 }}
      }}
      if ($score -gt 0) {{
        $cmd = [string]$_.QuietUninstallString
        if ([string]::IsNullOrWhiteSpace($cmd)) {{ $cmd = [string]$_.UninstallString }}
        if ([string]::IsNullOrWhiteSpace($cmd)) {{ return }}
        $hits += [pscustomobject]@{{ DisplayName=$dn; Cmd=$cmd; Score=$score }}
      }}
    }}
  }}
}}
if ($hits.Count -eq 0) {{ exit 2 }}
$best = $hits | Sort-Object -Property Score -Descending | Select-Object -First 1
Write-Output $best.Cmd
"#,
        name = name
    );

    let out = Command::new("powershell.exe")
        .args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", &script])
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let cmd = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if cmd.is_empty() {
        None
    } else {
        Some(cmd)
    }
}

fn launch_uninstall_command(cmd: &str) -> Result<(), String> {
    // Many UninstallString values are like:
    // - MsiExec.exe /I{GUID}
    // - "C:\Path\unins000.exe" /SILENT
    // We'll just hand the whole string to cmd.exe.
    Command::new("cmd")
        .args(["/d", "/s", "/c", "start", "", cmd])
        .spawn()
        .map(|_| ())
        .map_err(|e| e.to_string())
}

