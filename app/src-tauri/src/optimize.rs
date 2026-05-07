use crate::engine_detect::{self, guess_unity_from_entry};
use crate::models::{GameEngine, GameEntry};
use regex::Regex;
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OptimizeResult {
    pub applied: bool,
    pub message: String,
    pub backups: Vec<String>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum OptimizeGoal {
    HighFpsGoodGraphics,
}

#[tauri::command]
pub fn optimize_game(entry: GameEntry, goal: OptimizeGoal) -> Result<OptimizeResult, String> {
    let mut notes: Vec<String> = Vec::new();

    // 1) Source-style video / machine configs (Steam userdata + install tree)
    match try_optimize_source(&entry, goal) {
        Ok(Some(r)) => {
            if r.applied {
                return Ok(r);
            }
            if !r.message.is_empty() {
                notes.push(r.message);
            }
        }
        Ok(None) => {}
        Err(e) => notes.push(format!("(Source configs) {}", e)),
    }

    // 2) Unity PlayerPrefs (registry) — explicit tag or LocalLow match
    let unity_ids = entry.engine.as_ref().and_then(|e| match e {
        GameEngine::Unity { company, product } => Some((company.clone(), product.clone())),
        _ => None,
    });
    let unity_ids = unity_ids.or_else(|| guess_unity_from_entry(&entry));
    if let Some((company, product)) = unity_ids {
        match optimize_unity_registry(&company, &product, goal) {
            Ok(r) => {
                if r.applied {
                    return Ok(r);
                }
                if !r.message.is_empty() {
                    notes.push(r.message);
                }
            }
            Err(e) => notes.push(format!("(Unity registry) {}", e)),
        }
    }

    // 3) Unreal — explicit project_name or install-based guess
    let unreal_project = entry.engine.as_ref().and_then(|e| match e {
        GameEngine::Unreal { project_name } => Some(project_name.clone()),
        _ => None,
    });
    let unreal_project = unreal_project.or_else(|| {
        guess_unreal_engine(&entry).and_then(|e| match e {
            GameEngine::Unreal { project_name } => Some(project_name),
            _ => None,
        })
    });
    if let Some(project_name) = unreal_project {
        let r = optimize_unreal(&project_name, goal)?;
        if r.applied {
            return Ok(r);
        }
        if !r.message.is_empty() {
            notes.push(r.message);
        }
    } else if entry.install_path.is_some() {
        let install = PathBuf::from(entry.install_path.as_ref().unwrap());
        if engine_detect::looks_like_unreal_shipped(&install) {
            notes.push(
                "Install looks like Unreal but no settings folder was matched to this game title."
                    .to_string(),
            );
        }
    }

    let candidates = unreal_projects_with_settings();
    let notes_text = notes
        .into_iter()
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join("\n");
    let msg = if candidates.is_empty() {
        let mut base = "No per-game graphics profile was applied.\n\n\
        Tried: Source-style video configs, Unity registry (if a matching LocalLow folder exists), and Unreal GameUserSettings.ini.\n\n\
        Tip: launch the game once so it writes local settings, then retry. Many store titles also need the official launcher for first-time setup."
            .to_string();
        if !notes_text.is_empty() {
            base.push_str("\n\nLast attempts:\n");
            base.push_str(&notes_text);
        }
        base
    } else {
        format!(
            "No per-game graphics profile was applied for this title.\n\n\
            Unreal-style settings exist under %LOCALAPPDATA% for:\n{}\n\n\
            If one of these is your game, launch it once and retry; names do not always match the Store/Steam title.\n\n\
            Other hints:\n{}",
            candidates
                .into_iter()
                .take(14)
                .map(|p| format!("- {}", p))
                .collect::<Vec<_>>()
                .join("\n"),
            notes_text
        )
    };

    Ok(OptimizeResult {
        applied: false,
        message: msg,
        backups: vec![],
    })
}

fn try_optimize_source(entry: &GameEntry, goal: OptimizeGoal) -> Result<Option<OptimizeResult>, String> {
    let paths = collect_source_video_paths(entry);
    if paths.is_empty() {
        return Ok(None);
    }

    let mut backups: Vec<String> = Vec::new();
    let mut touched: Vec<String> = Vec::new();
    let mut err_lines: Vec<String> = Vec::new();

    for p in paths {
        match apply_source_video_file(&p, goal) {
            Ok(Some(bak)) => {
                touched.push(p.to_string_lossy().to_string());
                backups.push(bak);
            }
            Ok(None) => {}
            Err(e) => err_lines.push(format!("{} → {}", p.to_string_lossy(), e)),
        }
    }

    if !touched.is_empty() {
        let mut msg = format!(
            "Updated Source-style config(s) for higher FPS (VSync off where supported, balanced quality levels). Files:\n{}",
            touched
                .iter()
                .map(|s| format!("- {}", s))
                .collect::<Vec<_>>()
                .join("\n")
        );
        if !err_lines.is_empty() {
            msg.push_str("\n\nPartial errors:\n");
            msg.push_str(&err_lines.join("\n"));
        }
        return Ok(Some(OptimizeResult {
            applied: true,
            message: msg,
            backups,
        }));
    }

    if err_lines.is_empty() {
        return Ok(None);
    }

    let mut msg = "Source-style config file(s) could not be updated.".to_string();
    msg.push_str("\n\n");
    msg.push_str(&err_lines.join("\n"));
    Ok(Some(OptimizeResult {
        applied: false,
        message: msg,
        backups,
    }))
}

/// Returns backup path if file was modified.
fn apply_source_video_file(path: &Path, _goal: OptimizeGoal) -> Result<Option<String>, String> {
    let original = fs::read_to_string(path).map_err(|e| e.to_string())?;
    let mut s = original.clone();

    let dword_keys = [
        ("setting.mat_vsync", "0"),
        ("setting.wait_for_vsync", "0"),
        ("setting.vsync", "0"),
        ("setting.cpu_level", "2"),
        ("setting.gpu_mem_level", "2"),
        ("setting.gpu_level", "2"),
        ("setting.shaderquality", "1"),
    ];
    for (key, val) in dword_keys {
        let re = Regex::new(&format!(
            r#"(?m)("{}")(\s+)("\d+")"#,
            regex::escape(key)
        ))
        .map_err(|e| e.to_string())?;
        s = re
            .replace_all(&s, format!(r#"${{1}}${{2}}"{}""#, val))
            .to_string();
    }

    // Common convar-style entries in vcfg / keyvalues:
    for (key, val) in [("r_draw_vsync", "0"), ("r_vsync", "0"), ("mat_vsync", "0")] {
        let re = Regex::new(&format!(
            r#"(?m)("{}")(\s+)("\d+")"#,
            regex::escape(key)
        ))
        .map_err(|e| e.to_string())?;
        s = re
            .replace_all(&s, format!(r#"${{1}}${{2}}"{}""#, val))
            .to_string();
    }

    if s == original {
        return Ok(None);
    }

    let backup_path = backup_file(path, &original)?;
    fs::write(path, s).map_err(|e| e.to_string())?;
    Ok(Some(backup_path.to_string_lossy().to_string()))
}

fn collect_source_video_paths(entry: &GameEntry) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let mut seen = HashSet::new();

    if let Some(inst_str) = entry.install_path.as_ref() {
        let inst = PathBuf::from(inst_str);
        if inst.is_dir() {
            let mut budget = 900usize;
            walk_collect_source_cfgs(&inst, &mut out, &mut seen, 0, 7, &mut budget);
        }
    }

    if let Some(steam_root) = entry
        .install_path
        .as_ref()
        .and_then(|p| steam_root_from_install(Path::new(p)))
    {
        if let Some(appid) = steam_app_id(entry) {
            let userdata = steam_root.join("userdata");
            if let Ok(users) = fs::read_dir(&userdata) {
                for u in users.flatten() {
                    let cfg_dir = u.path().join(&appid).join("local").join("cfg");
                    if !cfg_dir.is_dir() {
                        continue;
                    }
                    if let Ok(files) = fs::read_dir(&cfg_dir) {
                        for f in files.flatten() {
                            let p = f.path();
                            if p.is_file() && is_source_cfg_file(&p) {
                                let ps = p.to_string_lossy().to_lowercase();
                                if seen.insert(ps) {
                                    out.push(p);
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    out
}

fn steam_app_id(entry: &GameEntry) -> Option<String> {
    if matches!(entry.launcher, crate::models::Launcher::Steam) {
        entry.id.strip_prefix("steam:").map(|s| s.to_string())
    } else {
        None
    }
}

fn steam_root_from_install(install: &Path) -> Option<PathBuf> {
    for a in install.ancestors() {
        if let Some(name) = a.file_name().and_then(|s| s.to_str()) {
            if name.eq_ignore_ascii_case("Steam") {
                return Some(a.to_path_buf());
            }
        }
    }
    None
}

fn walk_collect_source_cfgs(
    dir: &Path,
    out: &mut Vec<PathBuf>,
    seen: &mut HashSet<String>,
    depth: usize,
    max_depth: usize,
    budget: &mut usize,
) {
    if depth > max_depth || *budget == 0 {
        return;
    }
    *budget -= 1;
    let Ok(rd) = fs::read_dir(dir) else {
        return;
    };
    for e in rd.flatten() {
        let p = e.path();
        let name = p
            .file_name()
            .map(|s| s.to_string_lossy().to_lowercase())
            .unwrap_or_default();
        if p.is_dir() {
            if name == "node_modules" || name == ".git" || name == "compatdata" {
                continue;
            }
            walk_collect_source_cfgs(p.as_path(), out, seen, depth + 1, max_depth, budget);
        } else if p.is_file() && is_source_cfg_file(&p) {
            let ps = p.to_string_lossy().to_lowercase();
            if seen.insert(ps) {
                out.push(p);
            }
        }
    }
}

fn is_source_cfg_file(path: &Path) -> bool {
    let Some(name) = path.file_name().and_then(|s| s.to_str()) else {
        return false;
    };
    let n = name.to_lowercase();
    n.contains("video") || n.ends_with("_video.txt") || n == "machine_convars.vcfg" || n == "cs2_video.txt"
}

fn optimize_unity_registry(company: &str, product: &str, _goal: OptimizeGoal) -> Result<OptimizeResult, String> {
    if !cfg!(target_os = "windows") {
        return Err("Unity registry optimization is Windows-only.".to_string());
    }

    let company = company.replace('\'', "''");
    let product = product.replace('\'', "''");

    let stamp = unix_stamp();
    let backup_file_ps = env::temp_dir().join(format!("ugl_unity_prefs_backup_{}.reg", stamp));
    let backup_str = backup_file_ps.to_string_lossy().replace('\'', "''");

    let script = format!(
        r#"
$ErrorActionPreference = 'Stop'
$k = Join-Path (Join-Path 'HKCU:\Software' '{company}') '{product}'
if (!(Test-Path -LiteralPath $k)) {{
  Write-Output "NO_KEY"
  exit 0
}}
$regPath = "HKCU\Software\{company}\{product}"
cmd /c "reg export `"$regPath`" `"$env:TEMP\uglunity.tmp`" /y" | Out-Null
$tmp = Join-Path $env:TEMP 'uglunity.tmp'
if (Test-Path $tmp) {{
  Copy-Item -LiteralPath $tmp -Destination '{backup_str}' -Force
  Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
}}
$n = 0
Get-Item -LiteralPath $k | Select-Object -ExpandProperty Property | ForEach-Object {{
  $prop = $_
  if ($prop -in @('PSPath','PSParentPath','PSChildName','PSDrive','PSProvider')) {{ return }}
  $low = $prop.ToLower()
  if ($low -match 'vsync|vblank|verticalsync|waitforvsync') {{
    try {{
      Set-ItemProperty -LiteralPath $k -Name $prop -Value 0 -Type DWord -ErrorAction Stop
      $n++
    }} catch {{
      try {{
        Set-ItemProperty -LiteralPath $k -Name $prop -Value 0 -ErrorAction Stop
        $n++
      }} catch {{ }}
    }}
  }}
}}
Write-Output ("OK:" + $n)
"#,
        company = company,
        product = product,
        backup_str = backup_str,
    );

    let out = Command::new("powershell.exe")
        .args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", &script])
        .output()
        .map_err(|e| e.to_string())?;

    let stdout = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if stdout.contains("NO_KEY") {
        return Ok(OptimizeResult {
            applied: false,
            message: format!(
                "Unity PlayerPrefs registry key not found yet for {} / {}. Launch the game once, then retry.",
                company, product
            ),
            backups: vec![],
        });
    }
    if !out.status.success() {
        let stderr = String::from_utf8_lossy(&out.stderr);
        return Err(format!(
            "Unity registry tweak failed.\n{}\n{}",
            stdout, stderr
        ));
    }

    let changed = stdout
        .strip_prefix("OK:")
        .and_then(|s| s.parse::<u32>().ok())
        .unwrap_or(0);

    let mut backups = vec![];
    if backup_file_ps.exists() {
        backups.push(backup_file_ps.to_string_lossy().to_string());
    }

    if changed > 0 {
        Ok(OptimizeResult {
            applied: true,
            message: format!(
                "Unity: turned off {} vsync-related PlayerPrefs (if present). Registry backup: {}",
                changed,
                backup_file_ps.display()
            ),
            backups,
        })
    } else {
        Ok(OptimizeResult {
            applied: false,
            message: format!(
                "Unity data folder matched ({} / {}) but no vsync-related DWORD prefs were found to change.",
                company, product
            ),
            backups,
        })
    }
}

fn guess_unreal_engine(entry: &GameEntry) -> Option<GameEngine> {
    // Many shipped Unreal games do not ship a .uproject. Best-effort:
    // 1) If install path exists, look for Unreal-ish folder markers.
    // 2) If found, guess ProjectName by finding a matching LocalAppData folder that has GameUserSettings.ini.
    let install = entry.install_path.as_ref().map(PathBuf::from);
    let is_unreal = install
        .as_ref()
        .is_some_and(|p| engine_detect::looks_like_unreal_shipped(p));
    if !is_unreal {
        return None;
    }

    let local = env::var_os("LOCALAPPDATA").map(PathBuf::from)?;
    let game_name_hint = sanitize_hint(&entry.name);
    let install_hint = install
        .as_ref()
        .and_then(|p| p.file_name().and_then(|s| s.to_str()))
        .map(sanitize_hint)
        .unwrap_or_default();
    let Ok(children) = fs::read_dir(&local) else {
        return None;
    };

    let mut recent_hits: Vec<(String, u64)> = Vec::new();
    for child in children.flatten() {
        let p = child.path();
        if !p.is_dir() {
            continue;
        }
        let folder = p.file_name().and_then(|s| s.to_str()).unwrap_or("").to_lowercase();
        if !folder.contains(&game_name_hint) && game_name_hint.len() >= 4 {
            continue;
        }
        if has_unreal_gus(&p) {
            let project = p.file_name().and_then(|s| s.to_str()).unwrap_or("").to_string();
            if !project.is_empty() {
                if let Some(ts) = gus_mtime_unix(&p) {
                    recent_hits.push((project.clone(), ts));
                }
                return Some(GameEngine::Unreal { project_name: project });
            }
        }
    }

    let mut scored: Vec<(String, i32)> = Vec::new();
    let Ok(children2) = fs::read_dir(&local) else {
        return None;
    };
    for child in children2.flatten() {
        let p = child.path();
        if !p.is_dir() || !has_unreal_gus(&p) {
            continue;
        }
        let name = p.file_name().and_then(|s| s.to_str()).unwrap_or("").to_string();
        if name.is_empty() {
            continue;
        }
        let cand = sanitize_hint(&name);
        let mut score = 0i32;
        if !game_name_hint.is_empty() && (cand.contains(&game_name_hint) || game_name_hint.contains(&cand)) {
            score += 6;
        }
        if !install_hint.is_empty() && (cand.contains(&install_hint) || install_hint.contains(&cand)) {
            score += 6;
        }
        for t in tokens_from_hint(&game_name_hint).into_iter().chain(tokens_from_hint(&install_hint)) {
            if t.len() >= 4 && cand.contains(&t) {
                score += 2;
            }
        }
        if let Some(ts) = gus_mtime_unix(&p) {
            if ts > 0 {
                score += 1;
            }
        }
        if score > 0 {
            scored.push((name, score));
        }
    }
    scored.sort_by(|a, b| b.1.cmp(&a.1));
    if let Some((best, best_score)) = scored.first().cloned() {
        let second = scored.get(1).map(|x| x.1).unwrap_or(0);
        if best_score >= 6 && best_score >= second + 2 {
            return Some(GameEngine::Unreal {
                project_name: best,
            });
        }
    }

    recent_hits.sort_by(|a, b| b.1.cmp(&a.1));
    if let Some((project, _)) = recent_hits.first() {
        return Some(GameEngine::Unreal {
            project_name: project.clone(),
        });
    }
    None
}

fn tokens_from_hint(h: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut cur = String::new();
    for ch in h.chars() {
        if ch.is_ascii_alphanumeric() {
            cur.push(ch);
        } else if !cur.is_empty() {
            out.push(cur.clone());
            cur.clear();
        }
    }
    if !cur.is_empty() {
        out.push(cur);
    }
    out
}

fn has_unreal_gus(project_dir: &Path) -> bool {
    project_dir
        .join("Saved")
        .join("Config")
        .join("Windows")
        .join("GameUserSettings.ini")
        .exists()
        || project_dir
            .join("Saved")
            .join("Config")
            .join("WindowsNoEditor")
            .join("GameUserSettings.ini")
        .exists()
        || project_dir
            .join("Saved")
            .join("Config")
            .join("WindowsClient")
            .join("GameUserSettings.ini")
        .exists()
}

fn gus_mtime_unix(project_dir: &Path) -> Option<u64> {
    let candidates = [
        project_dir
            .join("Saved")
            .join("Config")
            .join("Windows")
            .join("GameUserSettings.ini"),
        project_dir
            .join("Saved")
            .join("Config")
            .join("WindowsNoEditor")
            .join("GameUserSettings.ini"),
        project_dir
            .join("Saved")
            .join("Config")
            .join("WindowsClient")
            .join("GameUserSettings.ini"),
    ];
    for c in candidates {
        if let Ok(meta) = fs::metadata(&c) {
            if let Ok(mtime) = meta.modified() {
                if let Ok(d) = mtime.duration_since(std::time::UNIX_EPOCH) {
                    return Some(d.as_secs());
                }
            }
        }
    }
    None
}

fn unreal_projects_with_settings() -> Vec<String> {
    let Some(local) = env::var_os("LOCALAPPDATA").map(PathBuf::from) else {
        return vec![];
    };
    let Ok(children) = fs::read_dir(&local) else {
        return vec![];
    };
    let mut out = Vec::new();
    for child in children.flatten() {
        let p = child.path();
        if !p.is_dir() {
            continue;
        }
        if has_unreal_gus(&p) {
            if let Some(name) = p.file_name().and_then(|s| s.to_str()) {
                out.push(name.to_string());
            }
        }
    }
    out.sort_by(|a, b| a.to_lowercase().cmp(&b.to_lowercase()));
    out
}

fn sanitize_hint(name: &str) -> String {
    let lower = name.to_lowercase();
    lower
        .chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .collect::<String>()
}

fn optimize_unreal(project_name: &str, _goal: OptimizeGoal) -> Result<OptimizeResult, String> {
    if !cfg!(target_os = "windows") {
        return Err("Unreal optimization is only implemented on Windows.".to_string());
    }
    let local = env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .ok_or("LOCALAPPDATA missing")?;

    let candidates = [
        local
            .join(project_name)
            .join("Saved")
            .join("Config")
            .join("Windows")
            .join("GameUserSettings.ini"),
        local
            .join(project_name)
            .join("Saved")
            .join("Config")
            .join("WindowsNoEditor")
            .join("GameUserSettings.ini"),
        local
            .join(project_name)
            .join("Saved")
            .join("Config")
            .join("WindowsClient")
            .join("GameUserSettings.ini"),
    ];

    let mut target: Option<PathBuf> = None;
    for c in &candidates {
        if c.exists() {
            target = Some(c.clone());
            break;
        }
    }

    let Some(target_path) = target else {
        return Ok(OptimizeResult {
            applied: false,
            message: format!(
                "Unreal config not found yet. Launch the game once so it creates GameUserSettings.ini (project: {}).",
                project_name
            ),
            backups: vec![],
        });
    };

    let original = fs::read_to_string(&target_path).unwrap_or_default();
    let backup_path = backup_file(&target_path, &original)?;

    let mut ini = IniDoc::parse(&original);

    ini.set("[/Script/Engine.GameUserSettings]", "bUseVSync", "False");
    ini.set("[/Script/Engine.GameUserSettings]", "FrameRateLimit", "0.000000");
    ini.set("[/Script/Engine.GameUserSettings]", "bUseDynamicResolution", "True");
    ini.set(
        "[/Script/Engine.GameUserSettings]",
        "ResolutionScaleNormalized",
        "1.000000",
    );

    ini.set("[ScalabilityGroups]", "sg.ViewDistanceQuality", "3");
    ini.set("[ScalabilityGroups]", "sg.AntiAliasingQuality", "3");
    ini.set("[ScalabilityGroups]", "sg.ShadowQuality", "2");
    ini.set("[ScalabilityGroups]", "sg.PostProcessQuality", "2");
    ini.set("[ScalabilityGroups]", "sg.TextureQuality", "3");
    ini.set("[ScalabilityGroups]", "sg.EffectsQuality", "2");
    ini.set("[ScalabilityGroups]", "sg.FoliageQuality", "2");
    ini.set("[ScalabilityGroups]", "sg.ShadingQuality", "3");

    if let Some(parent) = target_path.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    fs::write(&target_path, ini.render()).map_err(|e| e.to_string())?;

    Ok(OptimizeResult {
        applied: true,
        message: "Applied Unreal High FPS + good graphics profile (backup created).".to_string(),
        backups: vec![backup_path.to_string_lossy().to_string()],
    })
}

fn backup_file(path: &Path, contents: &str) -> Result<PathBuf, String> {
    let stamp = unix_stamp();
    let backup = PathBuf::from(format!(
        "{}.bak.{}",
        path.to_string_lossy(),
        stamp
    ));
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

fn split_kv(line: &str) -> Option<(&str, &str)> {
    let idx = line.find('=')?;
    Some((&line[..idx].trim(), &line[idx + 1..].trim()))
}

struct IniDoc {
    lines: Vec<String>,
}

impl IniDoc {
    fn parse(input: &str) -> Self {
        Self {
            lines: input.lines().map(|l| l.to_string()).collect(),
        }
    }

    fn set(&mut self, section: &str, key: &str, value: &str) {
        let section_header = section.trim();
        let mut in_section = false;
        let mut section_found = false;

        for i in 0..self.lines.len() {
            let line = self.lines[i].trim();
            if line.starts_with('[') && line.ends_with(']') {
                in_section = line.eq_ignore_ascii_case(section_header);
                if in_section {
                    section_found = true;
                }
                continue;
            }
            if in_section {
                if let Some((k, _)) = split_kv(line) {
                    if k.eq_ignore_ascii_case(key) {
                        self.lines[i] = format!("{}={}", key, value);
                        return;
                    }
                }
            }
        }

        if !section_found {
            if !self.lines.is_empty() && !self.lines.last().unwrap_or(&"".to_string()).is_empty() {
                self.lines.push("".to_string());
            }
            self.lines.push(section_header.to_string());
        }

        self.lines.push(format!("{}={}", key, value));
    }

    fn render(&self) -> String {
        let mut out = self.lines.join("\n");
        out.push('\n');
        out
    }
}
