use crate::models::GameEngine;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};

/// Best-effort engine classification from install folder + game title (for Steam and disk installs).
pub fn detect_game_engine(install_path: &Path, game_name: &str) -> Option<GameEngine> {
    if let Some(u) = detect_unreal_uproject(install_path) {
        return Some(u);
    }
    // Shipped Unreal (no .uproject) before Unity/Source — avoids tagging UE games as Source.
    if looks_like_unreal_shipped(install_path) {
        return None;
    }
    if looks_like_unity(install_path) {
        if let Some((company, product)) = infer_unity_local_low(install_path, game_name) {
            return Some(GameEngine::Unity { company, product });
        }
    }
    if looks_like_source(install_path) {
        return Some(GameEngine::Source);
    }
    None
}

fn detect_unreal_uproject(install_path: &Path) -> Option<GameEngine> {
    if let Ok(entries) = fs::read_dir(install_path) {
        for entry in entries.flatten() {
            let p = entry.path();
            if p.is_file()
                && p.extension()
                    .and_then(|e| e.to_str())
                    .map(|e| e.eq_ignore_ascii_case("uproject"))
                    .unwrap_or(false)
            {
                let stem = p.file_stem().and_then(|s| s.to_str())?.to_string();
                return Some(GameEngine::Unreal {
                    project_name: stem,
                });
            }
        }
    }
    if let Ok(entries) = fs::read_dir(install_path) {
        for entry in entries.flatten() {
            let p = entry.path();
            if !p.is_dir() {
                continue;
            }
            if let Ok(children) = fs::read_dir(&p) {
                for child in children.flatten() {
                    let cp = child.path();
                    if cp.is_file()
                        && cp.extension()
                            .and_then(|e| e.to_str())
                            .map(|e| e.eq_ignore_ascii_case("uproject"))
                            .unwrap_or(false)
                    {
                        let stem = cp.file_stem().and_then(|s| s.to_str())?.to_string();
                        return Some(GameEngine::Unreal {
                            project_name: stem,
                        });
                    }
                }
            }
        }
    }
    None
}

pub fn looks_like_unreal_shipped(root: &Path) -> bool {
    if unreal_shipped_markers(root) {
        return true;
    }
    // Steam often uses .../common/<Game>/<Inner>/<Project>/Content/Paks — scan shallow tree.
    let mut stack: Vec<PathBuf> = vec![root.to_path_buf()];
    let mut checked = 0u32;
    while let Some(dir) = stack.pop() {
        checked += 1;
        if checked > 280 {
            break;
        }
        let Ok(rd) = fs::read_dir(&dir) else {
            continue;
        };
        for e in rd.flatten() {
            let p = e.path();
            if !p.is_dir() {
                continue;
            }
            if unreal_shipped_markers(&p) {
                return true;
            }
            stack.push(p);
        }
    }
    false
}

fn unreal_shipped_markers(p: &Path) -> bool {
    p.join("Content").join("Paks").exists()
        || p.join("Engine").exists()
        || (p.join("Binaries").join("Win64").exists() && p.join("Content").join("Paks").exists())
}

fn looks_like_unity(root: &Path) -> bool {
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

fn looks_like_source(root: &Path) -> bool {
    root.join("hl2.exe").exists()
        || (root.join("bin").exists() && root.join("platform").exists())
        || (root.join("game").exists() && root.join("game").join("cfg").exists())
        || root.join("csgo.exe").exists()
        || root.join("dota2.exe").exists()
}

fn infer_unity_local_low(install_path: &Path, game_name: &str) -> Option<(String, String)> {
    let userprofile = env::var_os("USERPROFILE").map(PathBuf::from)?;
    let local_low = userprofile.join("AppData").join("LocalLow");
    if !local_low.is_dir() {
        return None;
    }

    let game_hint = sanitize_hint(game_name);
    let install_hint = install_path
        .file_name()
        .and_then(|s| s.to_str())
        .map(sanitize_hint)
        .unwrap_or_default();

    let mut best: Option<(String, String, i32)> = None;
    let Ok(companies) = fs::read_dir(&local_low) else {
        return None;
    };
    for company_entry in companies.flatten() {
        let company_path = company_entry.path();
        if !company_path.is_dir() {
            continue;
        }
        let company_name = company_path.file_name().and_then(|s| s.to_str())?.to_string();
        let Ok(products) = fs::read_dir(&company_path) else {
            continue;
        };
        for product_entry in products.flatten() {
            let product_path = product_entry.path();
            if !product_path.is_dir() {
                continue;
            }
            let product_name = product_path
                .file_name()
                .and_then(|s| s.to_str())?
                .to_string();
            if !unity_product_dir_likely(&product_path) {
                continue;
            }
            let cand_company = sanitize_hint(&company_name);
            let cand_product = sanitize_hint(&product_name);
            let mut score: i32 = 0;
            if !game_hint.is_empty() {
                if cand_product.contains(&game_hint) || game_hint.contains(&cand_product) {
                    score += 10;
                }
                if cand_company.contains(&game_hint) {
                    score += 3;
                }
            }
            if !install_hint.is_empty() {
                if cand_product.contains(&install_hint) || install_hint.contains(&cand_product) {
                    score += 10;
                }
                if cand_company.contains(&install_hint) {
                    score += 2;
                }
            }
            for t in tokens_from_sanitized(&game_hint)
                .into_iter()
                .chain(tokens_from_sanitized(&install_hint))
            {
                if t.len() >= 4 {
                    if cand_product.contains(&t) {
                        score += 3;
                    }
                    if cand_company.contains(&t) {
                        score += 1;
                    }
                }
            }
            if score < 6 {
                continue;
            }
            let replace = match &best {
                None => true,
                Some((_, _, s)) => score > *s,
            };
            if replace {
                best = Some((company_name.clone(), product_name.clone(), score));
            }
        }
    }

    best.map(|(c, p, _)| (c, p))
}

fn unity_product_dir_likely(dir: &Path) -> bool {
    if dir.join("Player.log").exists() || dir.join("Player-prev.log").exists() {
        return true;
    }
    // IL2CPP / some titles only leave Unity subfolder or analytics
    if dir.join("Unity").is_dir() {
        return true;
    }
    if let Ok(rd) = fs::read_dir(dir) {
        for e in rd.flatten() {
            let n = e.file_name().to_string_lossy().to_lowercase();
            if n.ends_with("_data") {
                continue;
            }
            if n.ends_with(".log") {
                return true;
            }
        }
    }
    false
}

fn sanitize_hint(name: &str) -> String {
    name.to_lowercase()
        .chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .collect()
}

fn tokens_from_sanitized(h: &str) -> Vec<String> {
    if h.len() < 8 {
        return vec![h.to_string()];
    }
    let mut out = Vec::new();
    let mut cur = String::new();
    for ch in h.chars() {
        if ch.is_ascii_alphanumeric() {
            cur.push(ch);
        } else if !cur.is_empty() {
            if cur.len() >= 4 {
                out.push(cur.clone());
            }
            cur.clear();
        }
    }
    if cur.len() >= 4 {
        out.push(cur);
    }
    out
}

/// LocalLow-based Unity detection for per-game optimization when `GameEntry.engine` is unset.
pub fn guess_unity_from_entry(entry: &crate::models::GameEntry) -> Option<(String, String)> {
    let install = entry.install_path.as_ref().map(PathBuf::from)?;
    infer_unity_local_low(&install, &entry.name)
}
