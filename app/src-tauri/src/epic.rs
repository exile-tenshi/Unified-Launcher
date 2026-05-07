use crate::models::{GameEntry, GameLaunch, Launcher, LaunchType};
use serde_json::Value;
use std::fs;
use std::path::PathBuf;

pub fn discover_epic_games() -> Result<Vec<GameEntry>, String> {
    if !cfg!(target_os = "windows") {
        return Ok(vec![]);
    }

    let manifests_dir = PathBuf::from(r"C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests");
    if !manifests_dir.exists() {
        return Ok(vec![]);
    }

    let mut games = Vec::new();
    let entries = fs::read_dir(&manifests_dir).map_err(|e| e.to_string())?;
    for entry in entries.flatten() {
        let path = entry.path();
        if !path.is_file() || path.extension().and_then(|e| e.to_str()).unwrap_or("") != "item" {
            continue;
        }
        let Ok(text) = fs::read_to_string(&path) else {
            continue;
        };
        let Ok(json) = serde_json::from_str::<Value>(&text) else {
            continue;
        };
        if let Some(game) = parse_item_manifest(&json) {
            games.push(game);
        }
    }

    games.sort_by(|a, b| a.name.to_lowercase().cmp(&b.name.to_lowercase()));
    Ok(games)
}

fn parse_item_manifest(v: &Value) -> Option<GameEntry> {
    // Common fields observed in Epic .item manifests:
    // - DisplayName
    // - InstallLocation
    // - AppName (or MainGameAppName)
    // - CatalogItemId
    let name = v.get("DisplayName")?.as_str()?.trim().to_string();
    let install_location = v
        .get("InstallLocation")
        .and_then(|x| x.as_str())
        .map(|s| s.to_string());

    let app_name = v
        .get("AppName")
        .and_then(|x| x.as_str())
        .or_else(|| v.get("MainGameAppName").and_then(|x| x.as_str()))
        .map(|s| s.to_string());

    let catalog_item_id = v
        .get("CatalogItemId")
        .and_then(|x| x.as_str())
        .map(|s| s.to_string());

    let launch_id = app_name.or(catalog_item_id)?;
    let uri = format!("com.epicgames.launcher://apps/{}?action=launch&silent=true", url_escape(&launch_id));

    Some(GameEntry {
        id: format!("epic:{}", launch_id),
        name,
        launcher: Launcher::Epic,
        installed: true,
        install_path: install_location,
        launch: GameLaunch {
            launch_type: LaunchType::EpicUri,
            launch_target: uri,
            args: vec![],
            working_dir: None,
        },
        icon_path: None,
        engine: None,
    })
}

fn url_escape(input: &str) -> String {
    // Good enough for Epic IDs: preserve alnum and a few safe chars.
    input
        .chars()
        .map(|c| match c {
            'A'..='Z' | 'a'..='z' | '0'..='9' | '-' | '_' | '.' | '~' => c.to_string(),
            other => format!("%{:02X}", other as u32),
        })
        .collect()
}

