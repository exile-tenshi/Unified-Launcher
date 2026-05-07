use crate::models::GameEntry;
use serde::{Deserialize, Serialize};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ConfigHint {
    pub label: String,
    pub paths: Vec<String>,
    pub notes: Vec<String>,
}

#[tauri::command]
pub fn find_game_config_hints(entry: GameEntry) -> Result<Vec<ConfigHint>, String> {
    let Some(install_path) = entry.install_path.clone() else {
        return Ok(vec![ConfigHint {
            label: "No install path".to_string(),
            paths: vec![],
            notes: vec!["This entry does not have an install path (e.g., Store apps/shortcuts).".to_string()],
        }]);
    };
    let root = PathBuf::from(install_path);
    if !root.exists() {
        return Ok(vec![ConfigHint {
            label: "Install path missing".to_string(),
            paths: vec![],
            notes: vec!["The install path does not exist on disk.".to_string()],
        }]);
    }

    let mut hints: Vec<ConfigHint> = Vec::new();

    if crate::engine_detect::looks_like_unreal_shipped(&root) {
        hints.push(unreal_hints(&entry, &root));
    }
    if looks_like_unity(&root) {
        hints.push(unity_hints(&entry, &root));
    }
    if looks_like_source(&root) {
        hints.push(source_hints(&entry, &root));
    }

    if hints.is_empty() {
        hints.push(ConfigHint {
            label: "Unknown engine".to_string(),
            paths: vec![],
            notes: vec![
                "Many games store graphics settings in AppData (Documents/Local/LocalLow) rather than the install folder."
                    .to_string(),
                "If you tell me the game name/engine, we can add a dedicated profile.".to_string(),
            ],
        });
    }

    Ok(hints)
}

fn unreal_hints(entry: &GameEntry, root: &Path) -> ConfigHint {
    let mut paths: Vec<String> = Vec::new();
    let mut notes: Vec<String> = Vec::new();

    // Unreal commonly stores user settings in LocalAppData\<Project>\Saved\Config\Windows*\GameUserSettings.ini
    if let Some(local) = env::var_os("LOCALAPPDATA").map(PathBuf::from) {
        // Best-effort: check a few likely folder names.
        let candidates = vec![
            entry.engine.as_ref().and_then(|e| match e {
                crate::models::GameEngine::Unreal { project_name } => Some(project_name.clone()),
                crate::models::GameEngine::Unity { product, .. } => Some(product.clone()),
                crate::models::GameEngine::Source => None,
            }),
            root.file_name().and_then(|s| s.to_str()).map(|s| s.to_string()),
            Some(entry.name.clone()),
        ];
        for cand in candidates.into_iter().flatten() {
            let p1 = local
                .join(&cand)
                .join("Saved")
                .join("Config")
                .join("Windows")
                .join("GameUserSettings.ini");
            let p2 = local
                .join(&cand)
                .join("Saved")
                .join("Config")
                .join("WindowsNoEditor")
                .join("GameUserSettings.ini");
            paths.push(p1.to_string_lossy().to_string());
            paths.push(p2.to_string_lossy().to_string());
        }
    }

    // Also mention install-side configs sometimes used by mods or defaults.
    paths.push(root.join("Engine.ini").to_string_lossy().to_string());
    notes.push(
        "Unreal games usually write user graphics settings to GameUserSettings.ini after you launch once."
            .to_string(),
    );

    dedup(&mut paths);
    ConfigHint {
        label: "Unreal Engine".to_string(),
        paths,
        notes,
    }
}

fn looks_like_unity(root: &Path) -> bool {
    // Unity builds usually have UnityPlayer.dll and a *_Data folder.
    if root.join("UnityPlayer.dll").exists() {
        return true;
    }
    if let Ok(entries) = fs::read_dir(root) {
        for entry in entries.flatten() {
            let p = entry.path();
            if p.is_dir() {
                if let Some(name) = p.file_name().and_then(|s| s.to_str()) {
                    if name.ends_with("_Data") {
                        return true;
                    }
                }
            }
        }
    }
    false
}

fn unity_hints(entry: &GameEntry, _root: &Path) -> ConfigHint {
    let mut paths: Vec<String> = Vec::new();
    let mut notes: Vec<String> = Vec::new();

    // Unity stores most graphics/user prefs in registry + AppData/LocalLow.
    if let Some(user) = env::var_os("USERPROFILE").map(PathBuf::from) {
        // We cannot reliably infer CompanyName; suggest the LocalLow root.
        paths.push(
            user.join("AppData")
                .join("LocalLow")
                .to_string_lossy()
                .to_string(),
        );
    }
    notes.push("Unity games often store settings under AppData\\LocalLow\\<Company>\\<Game> after first launch.".to_string());
    notes.push(format!(
        "For {} specifically, tell me the Company/Product name (or paste Player.log path) and I can wire exact paths.",
        entry.name
    ));

    ConfigHint {
        label: "Unity".to_string(),
        paths,
        notes,
    }
}

fn looks_like_source(root: &Path) -> bool {
    root.join("hl2.exe").exists()
        || root.join("bin").exists() && root.join("platform").exists()
        || root.join("game").exists() && root.join("game").join("cfg").exists()
}

fn source_hints(_entry: &GameEntry, root: &Path) -> ConfigHint {
    let mut paths: Vec<String> = Vec::new();
    let mut notes: Vec<String> = Vec::new();

    // Source-like games often have cfg files under game*/cfg.
    for sub in ["game", "csgo", "csgo\\cfg", "cfg"] {
        let p = root.join(sub);
        paths.push(p.to_string_lossy().to_string());
    }
    notes.push("Source/Source2 titles often store video settings in cfg files and autoexec.cfg under the cfg folder.".to_string());
    ConfigHint {
        label: "Source / Source2 (best-effort)".to_string(),
        paths,
        notes,
    }
}

fn dedup(items: &mut Vec<String>) {
    items.sort_by(|a, b| a.to_lowercase().cmp(&b.to_lowercase()));
    items.dedup_by(|a, b| a.eq_ignore_ascii_case(b));
}

