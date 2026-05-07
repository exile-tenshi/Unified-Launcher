use serde::{Deserialize, Serialize};
use std::fs;
use std::process::Command;
use tauri::{AppHandle, Manager};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct StartupApp {
    pub id: String,
    pub name: String,
    pub source: StartupSource,
    pub command: String,
    pub enabled: bool,
    pub admin_required: bool,
    pub notes: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum StartupSource {
    HkcuRun,
    HklmRun,
    StartupFolderUser,
    StartupFolderAllUsers,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SetStartupEnabledResult {
    pub applied: bool,
    pub message: String,
    pub backup_path: Option<String>,
    pub admin_required: bool,
}

#[tauri::command]
pub fn list_startup_apps() -> Result<Vec<StartupApp>, String> {
    if !cfg!(target_os = "windows") {
        return Ok(vec![]);
    }

    // We use PowerShell to enumerate startup sources without adding a winreg dependency.
    // IDs are stable-ish and include source + key + name/path.
    let script = r#"
$ErrorActionPreference='SilentlyContinue'

function Emit-RunKey($source, $path, $adminRequired) {
  if (!(Test-Path $path)) { return }
  $props = Get-ItemProperty -Path $path
  foreach ($p in $props.PSObject.Properties) {
    if ($p.Name -in @('PSPath','PSParentPath','PSChildName','PSDrive','PSProvider')) { continue }
    $name = [string]$p.Name
    $value = [string]$p.Value
    if ([string]::IsNullOrWhiteSpace($name)) { continue }
    $enabled = -not ($name.ToLower().StartsWith('ugl_disabled_'))
    $origName = if ($enabled) { $name } else { $name.Substring(13) } # strip UGL_DISABLED_
    $id = ($source + '::' + $origName)
    [pscustomobject]@{
      id=$id; name=$origName; source=$source; command=$value; enabled=$enabled; adminRequired=$adminRequired; notes=@()
    }
  }
}

function Emit-StartupFolder($source, $folder, $adminRequired) {
  if (!(Test-Path $folder)) { return }
  $files = Get-ChildItem -Path $folder -File -ErrorAction SilentlyContinue
  foreach ($f in $files) {
    $enabled = -not ($f.FullName.ToLower().Contains('\disabled by ugl\'))
    $id = ($source + '::' + $f.FullName)
    [pscustomobject]@{
      id=$id; name=$f.BaseName; source=$source; command=$f.FullName; enabled=$enabled; adminRequired=$adminRequired; notes=@('Shortcut/file in Startup folder')
    }
  }
}

$out = @()
Emit-RunKey 'hkcuRun' 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' $false | ForEach-Object { $out += $_ }
Emit-RunKey 'hklmRun' 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run' $true  | ForEach-Object { $out += $_ }

$userStartup = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
$allStartup  = Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs\Startup'
Emit-StartupFolder 'startupFolderUser' $userStartup $false | ForEach-Object { $out += $_ }
Emit-StartupFolder 'startupFolderAllUsers' $allStartup $true | ForEach-Object { $out += $_ }

$out | ConvertTo-Json -Depth 6
"#;

    let (code, stdout, stderr) = run_powershell(script)?;
    if code != 0 {
        let msg = format!("Failed to list startup apps.\n{}\n{}", stdout.trim(), stderr.trim())
            .trim()
            .to_string();
        return Err(if msg.is_empty() {
            "Failed to list startup apps.".to_string()
        } else {
            msg
        });
    }
    let trimmed = stdout.trim();
    if trimmed.is_empty() {
        return Ok(vec![]);
    }
    let val: serde_json::Value = serde_json::from_str(trimmed).map_err(|e| e.to_string())?;
    let arr = match val {
        serde_json::Value::Array(a) => a,
        other => vec![other],
    };

    let mut out = Vec::new();
    for item in arr {
        let id = item.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string();
        if id.is_empty() {
            continue;
        }
        let source_str = item
            .get("source")
            .and_then(|v| v.as_str())
            .unwrap_or("hkcuRun");
        let source = match source_str {
            "hkcuRun" => StartupSource::HkcuRun,
            "hklmRun" => StartupSource::HklmRun,
            "startupFolderUser" => StartupSource::StartupFolderUser,
            "startupFolderAllUsers" => StartupSource::StartupFolderAllUsers,
            _ => StartupSource::HkcuRun,
        };
        out.push(StartupApp {
            id,
            name: item
                .get("name")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            source,
            command: item
                .get("command")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            enabled: item.get("enabled").and_then(|v| v.as_bool()).unwrap_or(true),
            admin_required: item
                .get("adminRequired")
                .and_then(|v| v.as_bool())
                .unwrap_or(false),
            notes: item
                .get("notes")
                .and_then(|v| v.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|x| x.as_str().map(|s| s.to_string()))
                        .collect()
                })
                .unwrap_or_else(Vec::new),
        });
    }
    out.sort_by(|a, b| a.name.to_lowercase().cmp(&b.name.to_lowercase()));
    Ok(out)
}

#[tauri::command]
pub fn set_startup_app_enabled(
    app: AppHandle,
    id: String,
    enabled: bool,
) -> Result<SetStartupEnabledResult, String> {
    if !cfg!(target_os = "windows") {
        return Ok(SetStartupEnabledResult {
            applied: false,
            message: "Startup management is only implemented on Windows.".to_string(),
            backup_path: None,
            admin_required: false,
        });
    }

    // Backups go into app_data/backups/startup-<timestamp>.json
    let backup_dir = app
        .path()
        .app_data_dir()
        .map_err(|e| e.to_string())?
        .join("backups");
    fs::create_dir_all(&backup_dir).map_err(|e| e.to_string())?;
    let backup_path = backup_dir.join(format!("startup-{}.json", unix_stamp()));

    let require_admin = id.starts_with("hklmRun::") || id.starts_with("startupFolderAllUsers::");
    if require_admin && !is_admin()? {
        return Ok(SetStartupEnabledResult {
            applied: false,
            message: "Administrator rights required. Relaunch the app as Admin.".to_string(),
            backup_path: None,
            admin_required: true,
        });
    }

    // PowerShell applies the change and prints a small JSON "before" snapshot we write as backup.
    let script = format!(
        r#"
$ErrorActionPreference='Stop'
$id = '{id}'
$enable = {enable}

function Backup($obj) {{ $obj | ConvertTo-Json -Depth 6 }}

if ($id.StartsWith('hkcuRun::') -or $id.StartsWith('hklmRun::')) {{
  $isHklm = $id.StartsWith('hklmRun::')
  $name = $id.Split('::',2)[1]
  $path = if ($isHklm) {{ 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run' }} else {{ 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' }}
  $disabledName = ('UGL_DISABLED_' + $name)

  $before = [pscustomobject]@{{
    id=$id; path=$path; name=$name;
    enabledNameExists = [bool](Get-ItemProperty -Path $path -Name $name -ErrorAction SilentlyContinue);
    disabledNameExists = [bool](Get-ItemProperty -Path $path -Name $disabledName -ErrorAction SilentlyContinue);
    enabledValue = (Get-ItemProperty -Path $path -Name $name -ErrorAction SilentlyContinue).$name;
    disabledValue = (Get-ItemProperty -Path $path -Name $disabledName -ErrorAction SilentlyContinue).$disabledName;
  }}
  Backup $before

  if ($enable) {{
    $v = (Get-ItemProperty -Path $path -Name $disabledName -ErrorAction SilentlyContinue).$disabledName
    if ($null -ne $v) {{
      Remove-ItemProperty -Path $path -Name $disabledName -ErrorAction SilentlyContinue
      New-ItemProperty -Path $path -Name $name -PropertyType String -Value $v -Force | Out-Null
    }}
  }} else {{
    $v = (Get-ItemProperty -Path $path -Name $name -ErrorAction SilentlyContinue).$name
    if ($null -ne $v) {{
      Remove-ItemProperty -Path $path -Name $name -ErrorAction SilentlyContinue
      New-ItemProperty -Path $path -Name $disabledName -PropertyType String -Value $v -Force | Out-Null
    }}
  }}

  exit 0
}}

if ($id.StartsWith('startupFolderUser::') -or $id.StartsWith('startupFolderAllUsers::')) {{
  $full = $id.Split('::',2)[1]
  $folder = Split-Path -Parent $full
  $disabledDir = Join-Path $folder 'Disabled by UGL'
  New-Item -ItemType Directory -Path $disabledDir -Force | Out-Null
  $target = Join-Path $disabledDir (Split-Path -Leaf $full)

  $before = [pscustomobject]@{{ id=$id; full=$full; disabledDir=$disabledDir; target=$target; exists=(Test-Path $full); targetExists=(Test-Path $target) }}
  Backup $before

  if ($enable) {{
    if (Test-Path $target) {{
      Move-Item -LiteralPath $target -Destination $full -Force
    }}
  }} else {{
    if (Test-Path $full) {{
      Move-Item -LiteralPath $full -Destination $target -Force
    }}
  }}
  exit 0
}}

Write-Error 'Unknown startup item id.'
"#,
        id = id.replace("'", "''"),
        enable = if enabled { "$true" } else { "$false" }
    );

    let (code, stdout, stderr) = run_powershell(&script)?;
    if !stdout.trim().is_empty() {
        let _ = fs::write(&backup_path, &stdout).map_err(|e| e.to_string())?;
    }
    if code != 0 {
        let combined = format!("{}\n{}", stdout.trim(), stderr.trim()).trim().to_string();
        return Ok(SetStartupEnabledResult {
            applied: false,
            message: if combined.is_empty() {
                "Failed to update startup item.".to_string()
            } else {
                combined
            },
            backup_path: if backup_path.exists() {
                Some(backup_path.to_string_lossy().to_string())
            } else {
                None
            },
            admin_required: require_admin,
        });
    }

    Ok(SetStartupEnabledResult {
        applied: true,
        message: "Updated startup item.".to_string(),
        backup_path: if backup_path.exists() {
            Some(backup_path.to_string_lossy().to_string())
        } else {
            None
        },
        admin_required: require_admin,
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

