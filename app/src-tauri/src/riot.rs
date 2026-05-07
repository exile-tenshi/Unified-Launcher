use crate::models::{GameEntry, GameLaunch, Launcher, LaunchType};
use regex::Regex;
use serde_json::Value;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};

pub fn discover_riot_games() -> Result<Vec<GameEntry>, String> {
    if !cfg!(target_os = "windows") {
        return Ok(vec![]);
    }

    let Some(riot_client) = detect_riot_client_services_from_programdata().or_else(detect_riot_client_services) else {
        // Missing Riot should never break the unified library.
        return Ok(vec![]);
    };

    let mut games = Vec::new();

    // Preferred detection: Riot writes install metadata to ProgramData.
    games.extend(discover_riot_games_from_metadata(&riot_client));

    // Fallback: common install roots.
    if games.is_empty() {
        let roots = riot_game_roots();
        if roots.iter().any(|p| p.join("VALORANT").exists()) {
            games.push(riot_entry(
                "Valorant",
                "valorant",
                "live",
                &riot_client,
                Some(roots.iter().find(|p| p.join("VALORANT").exists()).unwrap().join("VALORANT")),
            ));
        }
        if roots.iter().any(|p| p.join("League of Legends").exists()) {
            games.push(riot_entry(
                "League of Legends",
                "league_of_legends",
                "live",
                &riot_client,
                Some(
                    roots.iter()
                        .find(|p| p.join("League of Legends").exists())
                        .unwrap()
                        .join("League of Legends"),
                ),
            ));
        }
        if roots.iter().any(|p| p.join("Legends of Runeterra").exists()) {
            games.push(riot_entry(
                "Legends of Runeterra",
                "bacon",
                "live",
                &riot_client,
                Some(
                    roots.iter()
                        .find(|p| p.join("Legends of Runeterra").exists())
                        .unwrap()
                        .join("Legends of Runeterra"),
                ),
            ));
        }
    }

    games.sort_by(|a, b| a.name.to_lowercase().cmp(&b.name.to_lowercase()));
    Ok(games)
}

fn riot_entry(
    name: &str,
    product: &str,
    patchline: &str,
    riot_client_services: &Path,
    install_path: Option<PathBuf>,
) -> GameEntry {
    GameEntry {
        id: format!("riot:{}", product),
        name: name.to_string(),
        launcher: Launcher::Riot,
        installed: true,
        install_path: install_path.map(|p| p.to_string_lossy().to_string()),
        launch: GameLaunch {
            launch_type: LaunchType::Exe,
            launch_target: riot_client_services.to_string_lossy().to_string(),
            args: vec![
                format!("--launch-product={}", product),
                format!("--launch-patchline={}", patchline),
            ],
            working_dir: riot_client_services.parent().map(|p| p.to_string_lossy().to_string()),
        },
        icon_path: None,
        engine: None,
    }
}

fn detect_riot_client_services() -> Option<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Some(pf) = env::var_os("ProgramFiles").map(PathBuf::from) {
        candidates.push(pf.join("Riot Games").join("Riot Client").join("RiotClientServices.exe"));
    }
    if let Some(pf86) = env::var_os("ProgramFiles(x86)").map(PathBuf::from) {
        candidates.push(pf86.join("Riot Games").join("Riot Client").join("RiotClientServices.exe"));
    }
    if let Some(local) = env::var_os("LOCALAPPDATA").map(PathBuf::from) {
        candidates.push(local.join("Riot Games").join("Riot Client").join("RiotClientServices.exe"));
    }
    for c in candidates {
        if c.exists() {
            return Some(c);
        }
    }
    None
}

fn detect_riot_client_services_from_programdata() -> Option<PathBuf> {
    let program_data = env::var_os("ProgramData").map(PathBuf::from)?;
    let installs = program_data.join("Riot Games").join("RiotClientInstalls.json");
    let text = fs::read_to_string(installs).ok()?;
    let json: Value = serde_json::from_str(&text).ok()?;

    // RiotClientInstalls.json varies; try common keys.
    let candidates = [
        json.get("rc_default")
            .and_then(|v| v.as_str())
            .map(|s| PathBuf::from(s).join("RiotClientServices.exe")),
        json.get("rc_live")
            .and_then(|v| v.as_str())
            .map(|s| PathBuf::from(s).join("RiotClientServices.exe")),
        json.get("associated_client")
            .and_then(|v| v.as_str())
            .map(|s| PathBuf::from(s).join("RiotClientServices.exe")),
        json.get("client_install_path")
            .and_then(|v| v.as_str())
            .map(|s| PathBuf::from(s).join("RiotClientServices.exe")),
    ];

    for c in candidates.into_iter().flatten() {
        if c.exists() {
            return Some(c);
        }
    }
    None
}

fn discover_riot_games_from_metadata(riot_client_services: &Path) -> Vec<GameEntry> {
    let mut games = Vec::new();
    let Some(program_data) = env::var_os("ProgramData").map(PathBuf::from) else {
        return games;
    };
    let metadata_dir = program_data.join("Riot Games").join("Metadata");
    if !metadata_dir.exists() {
        return games;
    }

    let re_install = Regex::new(r"(?im)^\s*product_install_full_path\s*:\s*(.+?)\s*$").ok();
    let re_root = Regex::new(r"(?im)^\s*product_install_root\s*:\s*(.+?)\s*$").ok();

    let Ok(entries) = fs::read_dir(metadata_dir) else {
        return games;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let Some(name) = path.file_name().and_then(|n| n.to_str()) else {
            continue;
        };
        if !name.ends_with(".product_settings.yaml") {
            continue;
        }
        // Filename pattern: <product>.<patchline>.product_settings.yaml
        let stem = name.trim_end_matches(".product_settings.yaml");
        let mut parts = stem.split('.');
        let product = parts.next().unwrap_or("").to_string();
        let patchline = parts.next().unwrap_or("live").to_string();
        if product.is_empty() {
            continue;
        }

        let Ok(text) = fs::read_to_string(&path) else {
            continue;
        };
        let install_path = re_install
            .as_ref()
            .and_then(|re| re.captures(&text))
            .and_then(|cap| cap.get(1))
            .map(|m| clean_yaml_scalar(m.as_str()));
        let install_root = re_root
            .as_ref()
            .and_then(|re| re.captures(&text))
            .and_then(|cap| cap.get(1))
            .map(|m| clean_yaml_scalar(m.as_str()));

        let install = install_path.or(install_root);
        // Only show if path exists (installed).
        if let Some(p) = install.as_ref() {
            if !PathBuf::from(p).exists() {
                continue;
            }
        } else {
            continue;
        }

        let display = riot_display_name(&product);
        games.push(riot_entry(
            display,
            &product,
            &patchline,
            riot_client_services,
            install.map(PathBuf::from),
        ));
    }

    // De-dupe by product.
    games.sort_by(|a, b| a.id.cmp(&b.id));
    games.dedup_by(|a, b| a.id == b.id);
    games
}

fn clean_yaml_scalar(s: &str) -> String {
    let trimmed = s.trim().trim_matches('"').trim_matches('\'').trim();
    trimmed.to_string()
}

fn riot_display_name(product: &str) -> &str {
    match product {
        "valorant" => "VALORANT",
        "league_of_legends" => "League of Legends",
        "bacon" => "Legends of Runeterra",
        "tft" => "Teamfight Tactics",
        _ => product,
    }
}

fn riot_game_roots() -> Vec<PathBuf> {
    let mut roots = Vec::new();
    if let Some(pf) = env::var_os("ProgramFiles").map(PathBuf::from) {
        roots.push(pf.join("Riot Games"));
    }
    if let Some(pf86) = env::var_os("ProgramFiles(x86)").map(PathBuf::from) {
        roots.push(pf86.join("Riot Games"));
    }
    if let Some(system_drive) = env::var_os("SystemDrive").map(PathBuf::from) {
        roots.push(system_drive.join("Riot Games"));
    }
    roots
}

