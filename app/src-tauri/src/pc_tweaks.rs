use serde::{Deserialize, Serialize};
use std::fs;
use std::process::Command;
use tauri::{AppHandle, Manager};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ApplyTweakResult {
    pub applied: bool,
    pub message: String,
    pub backup_path: Option<String>,
    pub restart_recommended: bool,
    pub admin_required: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum PcTweak {
    GameModeOn,
    DisableGameDvrCapture,
    UltimatePerformancePowerPlan,
    MultimediaGamingProfile,
    TcpGamingDefaults,
}

#[tauri::command]
pub fn apply_pc_tweak(app: AppHandle, tweak: PcTweak) -> Result<ApplyTweakResult, String> {
    if !cfg!(target_os = "windows") {
        return Err("PC tweaks are only implemented on Windows.".to_string());
    }

    match tweak {
        PcTweak::GameModeOn => apply_with_backup(&app, "game-mode", false, false, script_game_mode_on()),
        PcTweak::DisableGameDvrCapture => {
            apply_with_backup(&app, "game-dvr-off", false, false, script_game_dvr_off())
        }
        PcTweak::UltimatePerformancePowerPlan => apply_with_backup(
            &app,
            "ultimate-power",
            true,
            false,
            script_power_ultimate(),
        ),
        PcTweak::MultimediaGamingProfile => apply_with_backup(
            &app,
            "multimedia-gaming-profile",
            true,
            false,
            script_multimedia_profile(),
        ),
        PcTweak::TcpGamingDefaults => apply_with_backup(&app, "tcp-defaults", true, false, script_tcp_defaults()),
    }
}

fn apply_with_backup(
    app: &AppHandle,
    label: &str,
    admin_required: bool,
    restart_recommended: bool,
    apply_script: String,
) -> Result<ApplyTweakResult, String> {
    if admin_required && !is_admin()? {
        return Ok(ApplyTweakResult {
            applied: false,
            message: "Administrator rights required. Relaunch the app as Admin.".to_string(),
            backup_path: None,
            restart_recommended,
            admin_required,
        });
    }

    let backup_dir = app
        .path()
        .app_data_dir()
        .map_err(|e| e.to_string())?
        .join("backups");
    fs::create_dir_all(&backup_dir).map_err(|e| e.to_string())?;
    let stamp = unix_stamp();
    let backup_path = backup_dir.join(format!("{}-{}.json", label, stamp));

    // Each script prints a JSON snapshot of the "before" state on stdout, then applies changes.
    let (code, stdout, stderr) = run_powershell(&apply_script)?;
    let combined = format!("{}\n{}", stdout.trim(), stderr.trim()).trim().to_string();
    if !stdout.trim().is_empty() {
        let _ = fs::write(&backup_path, stdout).map_err(|e| e.to_string())?;
    }

    if code != 0 {
        return Ok(ApplyTweakResult {
            applied: false,
            message: if combined.is_empty() { "Tweak failed.".to_string() } else { combined },
            backup_path: if backup_path.exists() {
                Some(backup_path.to_string_lossy().to_string())
            } else {
                None
            },
            restart_recommended,
            admin_required,
        });
    }

    Ok(ApplyTweakResult {
        applied: true,
        message: "Applied tweak successfully.".to_string(),
        backup_path: if backup_path.exists() {
            Some(backup_path.to_string_lossy().to_string())
        } else {
            None
        },
        restart_recommended,
        admin_required,
    })
}

fn run_powershell(script: &str) -> Result<(i32, String, String), String> {
    let out = Command::new("powershell.exe")
        .args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script])
        .output()
        .map_err(|e| e.to_string())?;
    let code = out.status.code().unwrap_or(1);
    Ok((
        code,
        String::from_utf8_lossy(&out.stdout).to_string(),
        String::from_utf8_lossy(&out.stderr).to_string(),
    ))
}

fn is_admin() -> Result<bool, String> {
    let script = r#"
$p = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if ($p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { "true" } else { "false" }
"#;
    let (code, stdout, _stderr) = run_powershell(script)?;
    if code != 0 {
        return Ok(false);
    }
    Ok(stdout.trim().eq_ignore_ascii_case("true"))
}

fn unix_stamp() -> u64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

fn script_game_mode_on() -> String {
    r#"
$ErrorActionPreference = "Stop"
$before = [pscustomobject]@{
  AllowAutoGameMode = (Get-ItemProperty -Path "HKCU:\Software\Microsoft\GameBar" -Name "AllowAutoGameMode" -ErrorAction SilentlyContinue).AllowAutoGameMode
  AutoGameModeEnabled = (Get-ItemProperty -Path "HKCU:\Software\Microsoft\GameBar" -Name "AutoGameModeEnabled" -ErrorAction SilentlyContinue).AutoGameModeEnabled
}
$before | ConvertTo-Json -Depth 3
New-Item -Path "HKCU:\Software\Microsoft\GameBar" -Force | Out-Null
Set-ItemProperty -Path "HKCU:\Software\Microsoft\GameBar" -Name "AllowAutoGameMode" -Type DWord -Value 1
Set-ItemProperty -Path "HKCU:\Software\Microsoft\GameBar" -Name "AutoGameModeEnabled" -Type DWord -Value 1
"#.to_string()
}

fn script_game_dvr_off() -> String {
    r#"
$ErrorActionPreference = "Stop"
$before = [pscustomobject]@{
  GameDVR_Enabled = (Get-ItemProperty -Path "HKCU:\System\GameConfigStore" -Name "GameDVR_Enabled" -ErrorAction SilentlyContinue).GameDVR_Enabled
  GameDVR_FSEBehaviorMode = (Get-ItemProperty -Path "HKCU:\System\GameConfigStore" -Name "GameDVR_FSEBehaviorMode" -ErrorAction SilentlyContinue).GameDVR_FSEBehaviorMode
  AppCaptureEnabled = (Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\GameDVR" -Name "AppCaptureEnabled" -ErrorAction SilentlyContinue).AppCaptureEnabled
}
$before | ConvertTo-Json -Depth 3
New-Item -Path "HKCU:\System\GameConfigStore" -Force | Out-Null
New-Item -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\GameDVR" -Force | Out-Null
Set-ItemProperty -Path "HKCU:\System\GameConfigStore" -Name "GameDVR_Enabled" -Type DWord -Value 0
Set-ItemProperty -Path "HKCU:\System\GameConfigStore" -Name "GameDVR_FSEBehaviorMode" -Type DWord -Value 2
Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\GameDVR" -Name "AppCaptureEnabled" -Type DWord -Value 0
"#.to_string()
}

fn script_power_ultimate() -> String {
    // Ultimate Performance GUID
    let guid = "e9a42b02-d5df-448d-aa00-03f14749eb61";
    format!(
        r#"
$ErrorActionPreference = "Stop"
$before = powercfg /getactivescheme
[pscustomobject]@{{ ActivePowerPlan = $before }} | ConvertTo-Json -Depth 3
powercfg -duplicatescheme {guid} | Out-Null
powercfg /setactive {guid} | Out-Null
"#
    )
}

fn script_multimedia_profile() -> String {
    r#"
$ErrorActionPreference = "Stop"
$path = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"
$games = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile\Tasks\Games"
$before = [pscustomobject]@{
  NetworkThrottlingIndex = (Get-ItemProperty -Path $path -Name "NetworkThrottlingIndex" -ErrorAction SilentlyContinue).NetworkThrottlingIndex
  SystemResponsiveness = (Get-ItemProperty -Path $path -Name "SystemResponsiveness" -ErrorAction SilentlyContinue).SystemResponsiveness
  GamesGpuPriority = (Get-ItemProperty -Path $games -Name "GPU Priority" -ErrorAction SilentlyContinue)."GPU Priority"
  GamesPriority = (Get-ItemProperty -Path $games -Name "Priority" -ErrorAction SilentlyContinue).Priority
  GamesScheduling = (Get-ItemProperty -Path $games -Name "Scheduling Category" -ErrorAction SilentlyContinue)."Scheduling Category"
  GamesSfio = (Get-ItemProperty -Path $games -Name "SFIO Priority" -ErrorAction SilentlyContinue)."SFIO Priority"
}
$before | ConvertTo-Json -Depth 3
New-Item -Path $path -Force | Out-Null
New-Item -Path $games -Force | Out-Null
Set-ItemProperty -Path $path -Name "NetworkThrottlingIndex" -Type DWord -Value 0xffffffff
Set-ItemProperty -Path $path -Name "SystemResponsiveness" -Type DWord -Value 0
Set-ItemProperty -Path $games -Name "GPU Priority" -Type DWord -Value 8
Set-ItemProperty -Path $games -Name "Priority" -Type DWord -Value 6
Set-ItemProperty -Path $games -Name "Scheduling Category" -Type String -Value "High"
Set-ItemProperty -Path $games -Name "SFIO Priority" -Type String -Value "High"
"#.to_string()
}

fn script_tcp_defaults() -> String {
    r#"
$ErrorActionPreference = "Stop"
$before = netsh int tcp show global
[pscustomobject]@{ TcpGlobalBefore = $before } | ConvertTo-Json -Depth 3
netsh int tcp set heuristics disabled | Out-Null
netsh int tcp set global autotuninglevel=normal | Out-Null
netsh int tcp set global rss=enabled | Out-Null
netsh int tcp set global ecncapability=disabled | Out-Null
"#.to_string()
}

