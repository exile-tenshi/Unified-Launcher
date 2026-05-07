import { useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { GameEntry, Launcher, StartupApp } from "../types";

export function useGameLibrary() {
  const [games, setGames] = useState<GameEntry[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    setIsLoading(true);
    setError(null);
    try {
      const result = await invoke<GameEntry[]>("discover_games");
      setGames(result);
    } catch (e) {
      setError(String(e));
      setGames([]);
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  const counts = useMemo(() => {
    const byLauncher: Record<Launcher, number> = {
      steam: 0,
      epic: 0,
      xbox: 0,
      riot: 0,
      other: 0,
    };
    for (const g of games) byLauncher[g.launcher] += 1;
    return { total: games.length, byLauncher };
  }, [games]);

  return { games, isLoading, error, refresh, counts };
}

export async function launchGame(game: GameEntry) {
  const hide = localStorage.getItem("ugl_hideLauncherWindow") === "1";
  const closeApp = localStorage.getItem("ugl_closeAfterLaunch") === "1";
  const mode = (localStorage.getItem("ugl_launcherHideMode") || "minimize") as
    | "minimize"
    | "closeWindow";
  const boostEnabled = localStorage.getItem("ugl_boost_enabled") === "1";
  const boostAggressive = localStorage.getItem("ugl_boost_aggressive") === "1";
  const boostAllowRaw =
    localStorage.getItem("ugl_boost_allow") ||
    "msedge,chrome,firefox,iexplore";
  const boostAllowProcesses = boostAllowRaw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  await invoke("launch_game", {
    request: {
      launch: game.launch,
      hideLauncherWindow: hide,
      hideLauncherMode: mode,
      closeAppAfterLaunch: closeApp,
      sessionBoost: {
        enabled: boostEnabled,
        allowProcesses: boostAllowProcesses,
        aggressiveForceKill: boostAggressive,
      },
    },
  });
}

export async function saveCustomGames(games: GameEntry[]) {
  await invoke("save_custom_games", { games });
}

export async function optimizeGame(entry: GameEntry) {
  return await invoke<{ applied: boolean; message: string; backups: string[] }>(
    "optimize_game",
    { entry, goal: "highFpsGoodGraphics" }
  );
}

export type VrProfile = "steamVrBalanced" | "virtualDesktopBalanced" | "viveBalanced";

export async function applyVrProfile(profile: VrProfile) {
  return await invoke<{ applied: boolean; message: string; backups: string[] }>(
    "apply_vr_profile",
    { profile }
  );
}

export type PcTweak =
  | "gameModeOn"
  | "disableGameDvrCapture"
  | "ultimatePerformancePowerPlan"
  | "multimediaGamingProfile"
  | "tcpGamingDefaults";

export async function applyPcTweak(tweak: PcTweak) {
  return await invoke<{
    applied: boolean;
    message: string;
    backupPath?: string | null;
    restartRecommended: boolean;
    adminRequired: boolean;
  }>("apply_pc_tweak", { tweak });
}

export async function findConfigHints(entry: GameEntry) {
  return await invoke<
    { label: string; paths: string[]; notes: string[] }[]
  >("find_game_config_hints", { entry });
}

export async function uninstallGame(entry: GameEntry) {
  return await invoke<string>("uninstall_game", { entry });
}

export async function listStartupApps() {
  return await invoke<StartupApp[]>("list_startup_apps");
}

export async function setStartupAppEnabled(id: string, enabled: boolean) {
  return await invoke<{
    applied: boolean;
    message: string;
    backupPath?: string | null;
    adminRequired: boolean;
  }>("set_startup_app_enabled", { id, enabled });
}
