use crate::engine_detect::detect_game_engine;
use crate::models::{GameEntry, GameLaunch, Launcher, LaunchType};
use regex::Regex;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};

pub fn discover_steam_games() -> Result<Vec<GameEntry>, String> {
    let steam_root = match detect_steam_root() {
        Some(path) => path,
        None => return Ok(vec![]),
    };

    let libraries = detect_steam_libraries(&steam_root);
    let mut games: Vec<GameEntry> = Vec::new();
    for library in libraries {
        let steamapps = library.join("steamapps");
        let manifests = match fs::read_dir(&steamapps) {
            Ok(entries) => entries,
            Err(_) => continue,
        };
        for entry in manifests.flatten() {
            let path = entry.path();
            let Some(file_name) = path.file_name().and_then(|n| n.to_str()) else {
                continue;
            };
            if !file_name.starts_with("appmanifest_") || !file_name.ends_with(".acf") {
                continue;
            }
            if let Ok(text) = fs::read_to_string(&path) {
                if let Some(game) = parse_acf_manifest(&library, &text) {
                    games.push(game);
                }
            }
        }
    }

    games.sort_by(|a, b| a.name.to_lowercase().cmp(&b.name.to_lowercase()));
    Ok(games)
}

fn detect_steam_root() -> Option<PathBuf> {
    let pf86 = env::var_os("ProgramFiles(x86)")
        .or_else(|| env::var_os("ProgramFiles"))
        .map(PathBuf::from)?;
    let candidate = pf86.join("Steam");
    if candidate.exists() {
        return Some(candidate);
    }
    // Last-ditch: common default.
    let fallback = PathBuf::from(r"C:\Program Files (x86)\Steam");
    if fallback.exists() {
        return Some(fallback);
    }
    None
}

fn detect_steam_libraries(steam_root: &Path) -> Vec<PathBuf> {
    let mut libs = vec![steam_root.to_path_buf()];
    let vdf_path = steam_root.join("steamapps").join("libraryfolders.vdf");
    let Ok(text) = fs::read_to_string(vdf_path) else {
        return libs;
    };

    // KeyValues format contains repeated `"path" "X:\..."` entries.
    let re = Regex::new(r#""path"\s+"([^"]+)""#).ok();
    if let Some(re) = re {
        for cap in re.captures_iter(&text) {
            if let Some(p) = cap.get(1).map(|m| m.as_str()) {
                let normalized = p.replace(r"\\", r"\");
                let path = PathBuf::from(normalized);
                if path.exists() && !libs.iter().any(|x| eq_path_ci(x, &path)) {
                    libs.push(path);
                }
            }
        }
    }
    libs
}

fn parse_acf_manifest(library_root: &Path, acf_text: &str) -> Option<GameEntry> {
    // Minimal parsing: appid, name, installdir.
    let appid = capture_kv(acf_text, "appid")?;
    let name = capture_kv(acf_text, "name").unwrap_or_else(|| format!("Steam App {}", appid));
    let installdir = capture_kv(acf_text, "installdir").unwrap_or_else(|| name.clone());

    let install_path_path = library_root
        .join("steamapps")
        .join("common")
        .join(installdir);
    let install_path = install_path_path.to_string_lossy().to_string();
    let engine = detect_game_engine(&install_path_path, &name);

    Some(GameEntry {
        id: format!("steam:{}", appid),
        name,
        launcher: Launcher::Steam,
        installed: true,
        install_path: Some(install_path),
        launch: GameLaunch {
            launch_type: LaunchType::SteamUri,
            launch_target: format!("steam://rungameid/{}", appid),
            args: vec![],
            working_dir: None,
        },
        icon_path: None,
        engine,
    })
}

fn capture_kv(text: &str, key: &str) -> Option<String> {
    let pattern = format!(r#""{}"\s+"([^"]*)""#, regex::escape(key));
    let re = Regex::new(&pattern).ok()?;
    re.captures(text)
        .and_then(|cap| cap.get(1))
        .map(|m| m.as_str().to_string())
}

fn eq_path_ci(a: &Path, b: &Path) -> bool {
    a.to_string_lossy().to_lowercase() == b.to_string_lossy().to_lowercase()
}

