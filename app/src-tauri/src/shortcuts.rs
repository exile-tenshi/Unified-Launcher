use crate::models::{GameEntry, GameLaunch, Launcher, LaunchType};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};

pub fn discover_start_menu_shortcuts() -> Result<Vec<GameEntry>, String> {
    if !cfg!(target_os = "windows") {
        return Ok(vec![]);
    }

    let mut roots: Vec<PathBuf> = Vec::new();
    if let Some(appdata) = env::var_os("APPDATA").map(PathBuf::from) {
        roots.push(
            appdata
                .join("Microsoft")
                .join("Windows")
                .join("Start Menu")
                .join("Programs"),
        );
    }
    if let Some(programdata) = env::var_os("ProgramData").map(PathBuf::from) {
        roots.push(
            programdata
                .join("Microsoft")
                .join("Windows")
                .join("Start Menu")
                .join("Programs"),
        );
    }

    let mut entries: Vec<GameEntry> = Vec::new();
    for root in roots {
        if !root.exists() {
            continue;
        }
        collect_shortcuts(&root, &mut entries);
    }

    // De-dupe by launch target path (case-insensitive).
    entries.sort_by(|a, b| a.launch.launch_target.to_lowercase().cmp(&b.launch.launch_target.to_lowercase()));
    entries.dedup_by(|a, b| a.launch.launch_target.eq_ignore_ascii_case(&b.launch.launch_target));

    // Keep stable name ordering for UI.
    entries.sort_by(|a, b| a.name.to_lowercase().cmp(&b.name.to_lowercase()));
    Ok(entries)
}

fn collect_shortcuts(root: &Path, out: &mut Vec<GameEntry>) {
    let Ok(dir) = fs::read_dir(root) else {
        return;
    };
    for entry in dir.flatten() {
        let path = entry.path();
        if path.is_dir() {
            collect_shortcuts(&path, out);
            continue;
        }
        let Some(ext) = path.extension().and_then(|e| e.to_str()) else {
            continue;
        };
        if !ext.eq_ignore_ascii_case("lnk") {
            continue;
        }

        let name = path
            .file_stem()
            .and_then(|s| s.to_str())
            .unwrap_or("Shortcut")
            .to_string();
        let target = path.to_string_lossy().to_string();

        out.push(GameEntry {
            id: format!("shortcut:{}", target),
            name,
            launcher: Launcher::Other,
            installed: true,
            install_path: None,
            launch: GameLaunch {
                launch_type: LaunchType::Shortcut,
                launch_target: target,
                args: vec![],
                working_dir: None,
            },
            icon_path: None,
            engine: None,
        });
    }
}

