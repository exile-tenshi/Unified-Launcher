use serde::{Deserialize, Serialize};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ApplyProfileResult {
    pub applied: bool,
    pub message: String,
    pub backups: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum VrProfile {
    SteamVrBalanced,
    VirtualDesktopBalanced,
    ViveBalanced,
}

#[tauri::command]
pub fn apply_vr_profile(profile: VrProfile) -> Result<ApplyProfileResult, String> {
    match profile {
        VrProfile::SteamVrBalanced => apply_steamvr_balanced(),
        VrProfile::VirtualDesktopBalanced => apply_virtual_desktop_balanced(),
        VrProfile::ViveBalanced => apply_vive_balanced(),
    }
}

fn apply_steamvr_balanced() -> Result<ApplyProfileResult, String> {
    if !cfg!(target_os = "windows") {
        return Err("SteamVR profile is only implemented on Windows.".to_string());
    }

    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Some(local) = env::var_os("LOCALAPPDATA").map(PathBuf::from) {
        candidates.push(local.join("openvr").join("steamvr.vrsettings"));
    }
    if let Some(pf86) = env::var_os("ProgramFiles(x86)").map(PathBuf::from) {
        candidates.push(pf86.join("Steam").join("config").join("steamvr.vrsettings"));
    }
    if let Some(pf) = env::var_os("ProgramFiles").map(PathBuf::from) {
        candidates.push(pf.join("Steam").join("config").join("steamvr.vrsettings"));
    }

    let checked = candidates.clone();
    let path = candidates.into_iter().find(|p| p.exists()).ok_or_else(|| {
        format!(
            "SteamVR settings file not found. Start SteamVR once, then retry.\nChecked:\n{}",
            checked
                .iter()
                .map(|p| format!("- {}", p.to_string_lossy()))
                .collect::<Vec<_>>()
                .join("\n")
        )
    })?;

    let original = fs::read_to_string(&path).unwrap_or_else(|_| "{}".to_string());
    let backup = backup_text_file(&path, &original)?;

    let mut json: serde_json::Value = serde_json::from_str(&original).unwrap_or_else(|_| serde_json::json!({}));
    if !json.is_object() {
        json = serde_json::json!({});
    }
    let steamvr = json
        .as_object_mut()
        .unwrap()
        .entry("steamvr")
        .or_insert_with(|| serde_json::json!({}));
    if !steamvr.is_object() {
        *steamvr = serde_json::json!({});
    }
    let obj = steamvr.as_object_mut().unwrap();
    obj.insert("enableHomeApp".to_string(), serde_json::Value::Bool(false));
    obj.insert("supersampleManualOverride".to_string(), serde_json::Value::Bool(false));
    obj.insert("allowSupersampleFiltering".to_string(), serde_json::Value::Bool(true));
    obj.insert("motionSmoothing".to_string(), serde_json::Value::Bool(true));
    obj.insert("showMirrorView".to_string(), serde_json::Value::Bool(false));

    fs::write(&path, serde_json::to_string_pretty(&json).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;

    Ok(ApplyProfileResult {
        applied: true,
        message: format!("Applied SteamVR balanced profile to {}", path.to_string_lossy()),
        backups: vec![backup.to_string_lossy().to_string()],
    })
}

fn apply_virtual_desktop_balanced() -> Result<ApplyProfileResult, String> {
    if !cfg!(target_os = "windows") {
        return Err("Virtual Desktop profile is only implemented on Windows.".to_string());
    }
    let local = env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .ok_or("LOCALAPPDATA missing")?;
    let roaming = env::var_os("APPDATA")
        .map(PathBuf::from)
        .ok_or("APPDATA missing")?;

    let candidates = [
        local.join("Virtual Desktop Streamer").join("settings.json"),
        local.join("Virtual Desktop").join("settings.json"),
        roaming.join("Virtual Desktop Streamer").join("settings.json"),
        roaming.join("Virtual Desktop").join("settings.json"),
    ];
    let checked = candidates.iter().cloned().collect::<Vec<_>>();
    let path = candidates
        .into_iter()
        .find(|p| p.exists())
        .or_else(|| find_first_existing_json(&checked, &["settings.json", "streamer.json", "config.json"]))
        .ok_or_else(|| {
            format!(
                "Virtual Desktop settings not found. Open the Streamer once, then retry.\nChecked:\n{}",
                checked
                    .iter()
                    .map(|p| format!("- {}", p.to_string_lossy()))
                    .collect::<Vec<_>>()
                    .join("\n")
            )
        })?;

    let original = fs::read_to_string(&path).unwrap_or_else(|_| "{}".to_string());
    let backup = backup_text_file(&path, &original)?;

    let mut json: serde_json::Value = serde_json::from_str(&original).unwrap_or_else(|_| serde_json::json!({}));
    if !json.is_object() {
        json = serde_json::json!({});
    }

    // Best-effort keys (different versions use different structures). We store under a clear subtree too.
    set_nested(&mut json, &["streaming", "profile"], serde_json::Value::String("Balanced".into()));
    set_nested(&mut json, &["streaming", "preferredCodec"], serde_json::Value::String("Auto".into()));
    set_nested(&mut json, &["streaming", "autoBitrate"], serde_json::Value::Bool(true));
    set_nested(&mut json, &["streaming", "autoResolution"], serde_json::Value::Bool(true));
    set_nested(&mut json, &["streaming", "slicedEncoding"], serde_json::Value::Bool(true));
    set_nested(&mut json, &["streaming", "videoBuffering"], serde_json::Value::Bool(false));
    set_nested(&mut json, &["streaming", "preferLowLatency"], serde_json::Value::Bool(true));
    set_nested(&mut json, &["graphics", "dynamicQuality"], serde_json::Value::Bool(true));

    set_nested(
        &mut json,
        &["UnifiedGameLibrary", "virtualDesktopProfile"],
        serde_json::Value::String("Balanced".into()),
    );

    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    fs::write(&path, serde_json::to_string_pretty(&json).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;

    Ok(ApplyProfileResult {
        applied: true,
        message: format!("Applied Virtual Desktop balanced profile to {}", path.to_string_lossy()),
        backups: vec![backup.to_string_lossy().to_string()],
    })
}

fn apply_vive_balanced() -> Result<ApplyProfileResult, String> {
    if !cfg!(target_os = "windows") {
        return Err("Vive profile is only implemented on Windows.".to_string());
    }
    let local = env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .ok_or("LOCALAPPDATA missing")?;
    let roaming = env::var_os("APPDATA")
        .map(PathBuf::from)
        .ok_or("APPDATA missing")?;
    let program_data = env::var_os("PROGRAMDATA").map(PathBuf::from);

    let mut candidates = vec![
        local.join("VIVE").join("ViveHub").join("settings.json"),
        local.join("HTC").join("ViveHub").join("settings.json"),
        roaming.join("VIVE").join("ViveHub").join("settings.json"),
        local.join("VIVE").join("ViveConsole").join("settings.json"),
        local.join("HTC").join("ViveConsole").join("settings.json"),
    ];
    if let Some(pd) = program_data {
        candidates.push(pd.join("VIVE").join("ViveHub").join("settings.json"));
    }

    let checked = candidates.clone();
    let path = candidates
        .into_iter()
        .find(|p| p.exists())
        .or_else(|| find_first_existing_json(&checked, &["settings.json", "vivehub.json", "ViveHub.json", "ViveConsole.json", "config.json"]))
        .ok_or_else(|| {
            format!(
                "Vive Hub/Console settings not found. Open Vive Hub once, then retry.\nChecked:\n{}",
                checked
                    .iter()
                    .map(|p| format!("- {}", p.to_string_lossy()))
                    .collect::<Vec<_>>()
                    .join("\n")
            )
        })?;

    let original = fs::read_to_string(&path).unwrap_or_else(|_| "{}".to_string());
    let backup = backup_text_file(&path, &original)?;

    let mut json: serde_json::Value = serde_json::from_str(&original).unwrap_or_else(|_| serde_json::json!({}));
    if !json.is_object() {
        json = serde_json::json!({});
    }

    set_nested(&mut json, &["profile"], serde_json::Value::String("Balanced".into()));
    set_nested(&mut json, &["graphics", "profile"], serde_json::Value::String("Balanced".into()));
    set_nested(&mut json, &["graphics", "autoResolution"], serde_json::Value::Bool(true));
    set_nested(&mut json, &["graphics", "motionCompensation"], serde_json::Value::Bool(true));
    set_nested(&mut json, &["graphics", "lowLatencyMode"], serde_json::Value::Bool(true));
    set_nested(&mut json, &["streaming", "profile"], serde_json::Value::String("Balanced".into()));
    set_nested(&mut json, &["streaming", "autoBitrate"], serde_json::Value::Bool(true));
    set_nested(&mut json, &["runtime", "disableHomeOnLaunch"], serde_json::Value::Bool(true));
    set_nested(&mut json, &["runtime", "preferPerformanceMode"], serde_json::Value::Bool(true));

    set_nested(
        &mut json,
        &["UnifiedGameLibrary", "viveProfile"],
        serde_json::Value::String("Balanced".into()),
    );

    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    fs::write(&path, serde_json::to_string_pretty(&json).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;

    Ok(ApplyProfileResult {
        applied: true,
        message: format!("Applied Vive balanced profile to {}", path.to_string_lossy()),
        backups: vec![backup.to_string_lossy().to_string()],
    })
}

fn backup_text_file(path: &Path, contents: &str) -> Result<PathBuf, String> {
    let stamp = unix_stamp();
    let backup = PathBuf::from(format!("{}.bak.{}", path.to_string_lossy(), stamp));
    fs::write(&backup, contents).map_err(|e| e.to_string())?;
    Ok(backup)
}

fn unix_stamp() -> u64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

fn set_nested(root: &mut serde_json::Value, path: &[&str], value: serde_json::Value) {
    if !root.is_object() {
        *root = serde_json::json!({});
    }
    let mut cur = root;
    for (i, key) in path.iter().enumerate() {
        let last = i == path.len() - 1;
        if last {
            if let Some(map) = cur.as_object_mut() {
                map.insert((*key).to_string(), value);
            }
            return;
        }
        let next = cur
            .as_object_mut()
            .unwrap()
            .entry((*key).to_string())
            .or_insert_with(|| serde_json::json!({}));
        if !next.is_object() {
            *next = serde_json::json!({});
        }
        cur = next;
    }
}

fn find_first_existing_json(roots: &[PathBuf], file_names: &[&str]) -> Option<PathBuf> {
    // Very conservative search: look one level deep under each root's parent.
    for root in roots {
        let parent = root.parent().map(|p| p.to_path_buf());
        if let Some(parent) = parent {
            for name in file_names {
                let candidate = parent.join(name);
                if candidate.exists() {
                    return Some(candidate);
                }
            }
            if let Ok(children) = fs::read_dir(&parent) {
                for child in children.flatten() {
                    let p = child.path();
                    if !p.is_dir() {
                        continue;
                    }
                    for name in file_names {
                        let c = p.join(name);
                        if c.exists() {
                            return Some(c);
                        }
                    }
                }
            }
        }
    }
    None
}

