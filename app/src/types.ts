export type Launcher = "steam" | "epic" | "xbox" | "riot" | "other";

export type LaunchType = "steamUri" | "epicUri" | "aumid" | "exe" | "shortcut";

export interface GameLaunch {
  launchType: LaunchType;
  launchTarget: string;
  args: string[];
  workingDir?: string | null;
}

export interface GameEntry {
  id: string;
  name: string;
  launcher: Launcher;
  installed: boolean;
  installPath?: string | null;
  launch: GameLaunch;
  iconPath?: string | null;
  engine?: GameEngine | null;
}

export type GameEngine =
  | { type: "unreal"; projectName: string }
  | { type: "unity"; company: string; product: string }
  | { type: "source" };

export interface GameSessionBoost {
  enabled: boolean;
  allowProcesses: string[];
  aggressiveForceKill: boolean;
}

export type StartupSource =
  | "hkcuRun"
  | "hklmRun"
  | "startupFolderUser"
  | "startupFolderAllUsers";

export interface StartupApp {
  id: string;
  name: string;
  source: StartupSource;
  command: string;
  enabled: boolean;
  adminRequired: boolean;
  notes: string[];
}

