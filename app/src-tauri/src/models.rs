use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct GameEntry {
    pub id: String,
    pub name: String,
    pub launcher: Launcher,
    pub installed: bool,
    pub install_path: Option<String>,
    pub launch: GameLaunch,
    pub icon_path: Option<String>,
    pub engine: Option<GameEngine>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum Launcher {
    Steam,
    Epic,
    Xbox,
    Riot,
    Other,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct GameLaunch {
    pub launch_type: LaunchType,
    pub launch_target: String,
    pub args: Vec<String>,
    pub working_dir: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum LaunchType {
    SteamUri,
    EpicUri,
    Aumid,
    Exe,
    Shortcut,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", tag = "type")]
pub enum GameEngine {
    Unreal { project_name: String },
    /// Unity PlayerPrefs on Windows: HKCU\Software\{company}\{product}
    Unity {
        company: String,
        product: String,
    },
    /// Source / Source 2 style video / machine config on disk.
    Source,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LaunchRequest {
    pub launch: GameLaunch,
    pub hide_launcher_window: bool,
    pub hide_launcher_mode: Option<HideLauncherMode>,
    pub close_app_after_launch: bool,
    pub session_boost: Option<GameSessionBoost>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum HideLauncherMode {
    Minimize,
    CloseWindow,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct GameSessionBoost {
    /// If true, we will attempt to close background app windows before launching the game.
    /// This only targets user apps with a visible main window (MainWindowHandle != 0).
    pub enabled: bool,
    /// Process names to keep open (without .exe), e.g. ["msedge","chrome","firefox"].
    pub allow_processes: Vec<String>,
    /// If true, we may force-kill remaining user apps after a polite close (higher risk).
    pub aggressive_force_kill: bool,
}

