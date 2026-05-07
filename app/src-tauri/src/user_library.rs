use crate::models::{GameEntry, Launcher};
use serde_json::Value;
use std::fs;
use std::path::PathBuf;
use tauri::{AppHandle, Manager};

const CUSTOM_GAMES_FILE: &str = "custom_games.json";

pub fn load_custom_games(app: &AppHandle) -> Result<Vec<GameEntry>, String> {
    let path = custom_games_path(app)?;
    if !path.exists() {
        return Ok(vec![]);
    }
    let text = fs::read_to_string(&path).map_err(|e| e.to_string())?;
    if text.trim().is_empty() {
        return Ok(vec![]);
    }

    // Be lenient: if the file is an object with `games`, accept it.
    let parsed: Value = serde_json::from_str(&text).map_err(|e| e.to_string())?;
    let games_val = parsed
        .get("games")
        .cloned()
        .unwrap_or_else(|| parsed.clone());
    let mut games: Vec<GameEntry> = serde_json::from_value(games_val).map_err(|e| e.to_string())?;

    // Normalize: custom library entries should be marked as Other.
    for game in &mut games {
        game.launcher = Launcher::Other;
        game.engine = None;
    }
    Ok(games)
}

#[tauri::command]
pub fn save_custom_games(app: AppHandle, games: Vec<GameEntry>) -> Result<(), String> {
    let path = custom_games_path(&app)?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let payload = serde_json::json!({ "games": games });
    fs::write(&path, serde_json::to_string_pretty(&payload).map_err(|e| e.to_string())?).map_err(|e| e.to_string())
}

fn custom_games_path(app: &AppHandle) -> Result<PathBuf, String> {
    let dir = app
        .path()
        .app_data_dir()
        .map_err(|e| e.to_string())?;
    Ok(dir.join(CUSTOM_GAMES_FILE))
}

