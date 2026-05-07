use crate::models::{GameEntry, GameLaunch, Launcher, LaunchType};
use serde_json::Value;
use std::process::Command;

pub fn discover_xbox_games() -> Result<Vec<GameEntry>, String> {
    if !cfg!(target_os = "windows") {
        return Ok(vec![]);
    }

    // Best-effort enumeration of Start menu apps with AUMIDs.
    // This can include non-games; the UI will allow filtering.
    let script = r#"
$ErrorActionPreference = 'SilentlyContinue'
$apps = Get-StartApps | Select-Object Name, AppID
$apps | ConvertTo-Json -Depth 3
"#;

    let output = match run_powershell(script) {
        Ok(v) => v,
        Err(_) => return Ok(vec![]),
    };
    let value: Value = serde_json::from_str(&output).map_err(|e| e.to_string())?;
    let rows = match value {
        Value::Array(items) => items,
        Value::Object(_) => vec![value],
        _ => vec![],
    };

    let mut entries = Vec::new();
    for row in rows {
        let Some(name) = row.get("Name").and_then(|v| v.as_str()) else {
            continue;
        };
        let Some(appid) = row.get("AppID").and_then(|v| v.as_str()) else {
            continue;
        };
        let name = name.trim();
        let appid = appid.trim();
        if name.is_empty() || appid.is_empty() {
            continue;
        }

        // Heuristic classification: mark as Xbox if it looks like an MSIX AUMID.
        // Many Store/Game Pass titles fit this; some Windows apps will too.
        let launcher = Launcher::Xbox;
        entries.push(GameEntry {
            id: format!("xbox:{}", appid),
            name: name.to_string(),
            launcher,
            installed: true,
            install_path: None,
            launch: GameLaunch {
                launch_type: LaunchType::Aumid,
                launch_target: appid.to_string(),
                args: vec![],
                working_dir: None,
            },
            icon_path: None,
            engine: None,
        });
    }

    entries.sort_by(|a, b| a.name.to_lowercase().cmp(&b.name.to_lowercase()));
    // Cap to keep UI responsive on machines with tons of apps.
    if entries.len() > 400 {
        entries.truncate(400);
    }
    Ok(entries)
}

fn run_powershell(script: &str) -> Result<String, String> {
    let out = Command::new("powershell.exe")
        .args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script])
        .output()
        .map_err(|e| e.to_string())?;
    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
    let stderr = String::from_utf8_lossy(&out.stderr).to_string();
    let combined = format!("{}{}", stdout, stderr).trim().to_string();
    if !out.status.success() && combined.is_empty() {
        return Err(format!("PowerShell failed: {:?}", out.status.code()));
    }
    Ok(combined)
}

