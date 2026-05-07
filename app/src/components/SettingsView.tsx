import { useMemo, useState } from "react";
import type { GameEntry } from "../types";
import { listStartupApps, saveCustomGames, setStartupAppEnabled } from "../hooks/useTauri";
import type { StartupApp } from "../types";
import { open } from "@tauri-apps/plugin-dialog";
import termsText from "../legal/terms.md?raw";
import privacyText from "../legal/privacy.md?raw";
import eulaText from "../legal/eula.md?raw";
import disclaimerText from "../legal/disclaimer.md?raw";
import dmcaText from "../legal/dmca.md?raw";

export default function SettingsView({
  customGames,
  onCustomGamesChanged,
}: {
  customGames: GameEntry[];
  onCustomGamesChanged: (next: GameEntry[]) => void;
}) {
  const [name, setName] = useState("");
  const [exePath, setExePath] = useState("");
  const [args, setArgs] = useState("");
  const [workingDir, setWorkingDir] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hideLauncherWindow, setHideLauncherWindow] = useState(
    localStorage.getItem("ugl_hideLauncherWindow") === "1"
  );
  const [launcherHideMode, setLauncherHideMode] = useState<"minimize" | "closeWindow">(
    (localStorage.getItem("ugl_launcherHideMode") as any) || "minimize"
  );
  const [closeAfterLaunch, setCloseAfterLaunch] = useState(
    localStorage.getItem("ugl_closeAfterLaunch") === "1"
  );
  const [boostEnabled, setBoostEnabled] = useState(
    localStorage.getItem("ugl_boost_enabled") === "1"
  );
  const [boostAggressive, setBoostAggressive] = useState(
    localStorage.getItem("ugl_boost_aggressive") === "1"
  );
  const [boostAllow, setBoostAllow] = useState(
    localStorage.getItem("ugl_boost_allow") || "msedge,chrome,firefox,iexplore"
  );
  const [legalOpen, setLegalOpen] = useState<
    "terms" | "privacy" | "eula" | "disclaimer" | "dmca" | null
  >(null);
  const [startupApps, setStartupApps] = useState<StartupApp[]>([]);
  const [startupLoading, setStartupLoading] = useState(false);
  const [startupMsg, setStartupMsg] = useState<string | null>(null);

  const canAdd = useMemo(() => name.trim() && exePath.trim(), [name, exePath]);

  async function browseExe() {
    const selected = await open({
      multiple: false,
      directory: false,
      filters: [{ name: "Executable", extensions: ["exe"] }],
    });
    if (typeof selected === "string") setExePath(selected);
  }

  async function addCustom() {
    if (!canAdd) return;
    setSaving(true);
    setError(null);
    try {
      const entry: GameEntry = {
        id: `custom:${crypto.randomUUID()}`,
        name: name.trim(),
        launcher: "other",
        installed: true,
        installPath: workingDir.trim() || null,
        iconPath: null,
        launch: {
          launchType: "exe",
          launchTarget: exePath.trim(),
          args: args
            .split(" ")
            .map((s) => s.trim())
            .filter(Boolean),
          workingDir: workingDir.trim() || null,
        },
      };
      const next = [entry, ...customGames];
      await saveCustomGames(next);
      onCustomGamesChanged(next);
      setName("");
      setExePath("");
      setArgs("");
      setWorkingDir("");
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  async function remove(id: string) {
    setSaving(true);
    setError(null);
    try {
      const next = customGames.filter((g) => g.id !== id);
      await saveCustomGames(next);
      onCustomGamesChanged(next);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  async function refreshStartup() {
    setStartupLoading(true);
    setStartupMsg(null);
    try {
      const items = await listStartupApps();
      setStartupApps(items);
    } catch (e) {
      setStartupMsg(String(e));
    } finally {
      setStartupLoading(false);
    }
  }

  return (
    <div className="max-w-3xl">
      <h2 className="text-2xl font-semibold tracking-tight">Settings</h2>
      <p className="text-sm text-gray-400 mt-1">
        Add custom games (any launcher) and manage your local library entries.
      </p>

      {error && (
        <div className="mt-4 rounded-xl border border-red-800 bg-red-950/40 p-3 text-sm text-red-200">
          {error}
        </div>
      )}

      <div className="mt-6 rounded-2xl border border-gray-800 bg-gray-950/20 p-5">
        <h3 className="text-lg font-semibold">Launch behavior</h3>
        <p className="text-sm text-gray-400 mt-1">
          To avoid anti-cheat risk, this only closes/minimizes launcher windows (it does not kill background services).
        </p>

        <label className="mt-4 flex items-center gap-3 text-sm">
          <input
            type="checkbox"
            checked={hideLauncherWindow}
            onChange={(e) => {
              const next = e.target.checked;
              setHideLauncherWindow(next);
              localStorage.setItem("ugl_hideLauncherWindow", next ? "1" : "0");
            }}
            className="h-4 w-4"
          />
          <span>Hide launcher window after launching a game</span>
        </label>

        <div className="mt-3">
          <div className="text-xs text-gray-400 mb-1">Hide mode (recommended: Minimize)</div>
          <select
            value={launcherHideMode}
            onChange={(e) => {
              const next = e.target.value as "minimize" | "closeWindow";
              setLauncherHideMode(next);
              localStorage.setItem("ugl_launcherHideMode", next);
            }}
            className="bg-gray-900 border border-gray-700 rounded-xl px-3 py-2 text-sm"
          >
            <option value="minimize">Minimize launcher window</option>
            <option value="closeWindow">Close launcher window (may break some games)</option>
          </select>
          <div className="text-xs text-gray-500 mt-2">
            Epic titles are most stable with Minimize. Closing windows can cause weird behavior on exit for some launcher-managed games.
          </div>
        </div>

        <label className="mt-4 flex items-center gap-3 text-sm">
          <input
            type="checkbox"
            checked={closeAfterLaunch}
            onChange={(e) => {
              const next = e.target.checked;
              setCloseAfterLaunch(next);
              localStorage.setItem("ugl_closeAfterLaunch", next ? "1" : "0");
            }}
            className="h-4 w-4"
          />
          <span>Close Unified Game Library after launching a game</span>
        </label>
      </div>

      <div className="mt-6 rounded-2xl border border-gray-800 bg-gray-950/20 p-5">
        <h3 className="text-lg font-semibold">Game Session Boost (optional)</h3>
        <p className="text-sm text-gray-400 mt-1">
          This can close background app windows right before launching a game. It does <span className="text-gray-300">not</span> touch Windows services, audio, drivers, or anti-cheat.
          Browsers are kept open via the allowlist.
        </p>

        <label className="mt-4 flex items-center gap-3 text-sm">
          <input
            type="checkbox"
            checked={boostEnabled}
            onChange={(e) => {
              const next = e.target.checked;
              setBoostEnabled(next);
              localStorage.setItem("ugl_boost_enabled", next ? "1" : "0");
            }}
            className="h-4 w-4"
          />
          <span>Close background app windows before launching games</span>
        </label>

        <div className="mt-3">
          <div className="text-xs text-gray-400 mb-1">Keep these processes open (comma-separated, no .exe)</div>
          <input
            value={boostAllow}
            onChange={(e) => {
              const next = e.target.value;
              setBoostAllow(next);
              localStorage.setItem("ugl_boost_allow", next);
            }}
            placeholder="msedge,chrome,firefox,iexplore"
            className="w-full bg-gray-900 border border-gray-700 rounded-xl px-3 py-2 text-sm font-mono"
          />
          <div className="text-xs text-gray-500 mt-2">
            Tip: add <span className="font-mono">discord</span> or <span className="font-mono">obs64</span> if you want to keep them open.
          </div>
        </div>

        <label className="mt-4 flex items-center gap-3 text-sm">
          <input
            type="checkbox"
            checked={boostAggressive}
            onChange={(e) => {
              const next = e.target.checked;
              setBoostAggressive(next);
              localStorage.setItem("ugl_boost_aggressive", next ? "1" : "0");
            }}
            className="h-4 w-4"
          />
          <span>Aggressive mode (force-close remaining apps after polite close)</span>
        </label>

        <div className="mt-2 text-xs text-amber-200/80">
          Aggressive mode can cause unsaved work loss. Recommended: leave off.
        </div>
      </div>

      <div className="mt-6 rounded-2xl border border-gray-800 bg-gray-950/20 p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 className="text-lg font-semibold">Startup Apps</h3>
            <p className="text-sm text-gray-400 mt-1">
              Disable unnecessary startup entries to reduce background load. This doesn’t uninstall anything.
            </p>
          </div>
          <button
            onClick={refreshStartup}
            disabled={startupLoading}
            className="px-3 py-2 rounded-xl bg-gray-800 hover:bg-gray-700 disabled:opacity-50 border border-gray-700 text-sm"
          >
            {startupLoading ? "Refreshing…" : "Refresh"}
          </button>
        </div>

        {startupMsg && (
          <div className="mt-3 rounded-xl border border-amber-900/60 bg-amber-950/30 p-3 text-sm text-amber-100/90 whitespace-pre-wrap">
            {startupMsg}
          </div>
        )}

        <div className="mt-4 space-y-2">
          {startupApps.length === 0 ? (
            <div className="text-sm text-gray-500">
              Click <span className="text-gray-300">Refresh</span> to load startup apps.
            </div>
          ) : (
            startupApps.map((it) => (
              <div
                key={it.id}
                className="rounded-xl border border-gray-800 bg-gray-900/30 p-3"
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="font-medium truncate">{it.name || it.id}</div>
                    <div className="text-xs text-gray-500 font-mono truncate">
                      {it.command}
                    </div>
                    <div className="mt-1 text-[11px] text-gray-500">
                      Source:{" "}
                      <span className="font-mono text-gray-400">{it.source}</span>
                      {it.adminRequired ? (
                        <span className="ml-2 text-amber-200/80">
                          (Admin required to change)
                        </span>
                      ) : null}
                    </div>
                  </div>
                  <label className="flex items-center gap-2 text-sm select-none">
                    <input
                      type="checkbox"
                      checked={it.enabled}
                      onChange={async (e) => {
                        const next = e.target.checked;
                        setStartupMsg(null);
                        // optimistic UI
                        setStartupApps((prev) =>
                          prev.map((x) => (x.id === it.id ? { ...x, enabled: next } : x))
                        );
                        try {
                          const res = await setStartupAppEnabled(it.id, next);
                          if (!res.applied) {
                            // revert
                            setStartupApps((prev) =>
                              prev.map((x) =>
                                x.id === it.id ? { ...x, enabled: !next } : x
                              )
                            );
                          }
                          setStartupMsg(res.message);
                        } catch (err) {
                          setStartupApps((prev) =>
                            prev.map((x) => (x.id === it.id ? { ...x, enabled: !next } : x))
                          );
                          setStartupMsg(String(err));
                        }
                      }}
                      className="h-4 w-4"
                    />
                    <span className="text-gray-200">{it.enabled ? "Enabled" : "Disabled"}</span>
                  </label>
                </div>
              </div>
            ))
          )}
        </div>

        <div className="mt-3 text-xs text-gray-500">
          Disabled items are preserved so you can re-enable them later.
        </div>
      </div>

      <div className="mt-6 rounded-2xl border border-gray-800 bg-gray-950/20 p-5">
        <h3 className="text-lg font-semibold">Legal</h3>
        <p className="text-sm text-gray-400 mt-1">
          Terms, privacy, and other legal documents.
        </p>
        <div className="mt-4 flex gap-2">
          <button
            onClick={() => setLegalOpen("terms")}
            className="px-3 py-2 rounded-xl bg-gray-800 hover:bg-gray-700 border border-gray-700 text-sm"
          >
            View Terms
          </button>
          <button
            onClick={() => setLegalOpen("privacy")}
            className="px-3 py-2 rounded-xl bg-gray-800 hover:bg-gray-700 border border-gray-700 text-sm"
          >
            View Privacy
          </button>
          <button
            onClick={() => setLegalOpen("eula")}
            className="px-3 py-2 rounded-xl bg-gray-800 hover:bg-gray-700 border border-gray-700 text-sm"
          >
            View EULA
          </button>
        </div>
        <div className="mt-2 flex gap-2">
          <button
            onClick={() => setLegalOpen("disclaimer")}
            className="px-3 py-2 rounded-xl bg-gray-800 hover:bg-gray-700 border border-gray-700 text-sm"
          >
            View Disclaimer
          </button>
          <button
            onClick={() => setLegalOpen("dmca")}
            className="px-3 py-2 rounded-xl bg-gray-800 hover:bg-gray-700 border border-gray-700 text-sm"
          >
            View DMCA/Copyright
          </button>
        </div>
      </div>

      <div className="mt-6 rounded-2xl border border-gray-800 bg-gray-950/20 p-5">
        <h3 className="text-lg font-semibold">Add custom game</h3>
        <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-3">
          <Field label="Name">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Game name"
              className="w-full bg-gray-900 border border-gray-700 rounded-xl px-3 py-2 text-sm"
            />
          </Field>
          <Field label="Working directory (optional)">
            <input
              value={workingDir}
              onChange={(e) => setWorkingDir(e.target.value)}
              placeholder="C:\\Games\\MyGame"
              className="w-full bg-gray-900 border border-gray-700 rounded-xl px-3 py-2 text-sm"
            />
          </Field>
          <Field label="Executable">
            <div className="flex gap-2">
              <input
                value={exePath}
                onChange={(e) => setExePath(e.target.value)}
                placeholder="C:\\Games\\MyGame\\Game.exe"
                className="flex-1 bg-gray-900 border border-gray-700 rounded-xl px-3 py-2 text-sm"
              />
              <button
                onClick={browseExe}
                className="px-3 py-2 rounded-xl bg-gray-800 hover:bg-gray-700 border border-gray-700 text-sm"
              >
                Browse
              </button>
            </div>
          </Field>
          <Field label="Args (optional)">
            <input
              value={args}
              onChange={(e) => setArgs(e.target.value)}
              placeholder="-fullscreen -novid"
              className="w-full bg-gray-900 border border-gray-700 rounded-xl px-3 py-2 text-sm"
            />
          </Field>
        </div>

        <button
          onClick={addCustom}
          disabled={!canAdd || saving}
          className="mt-4 rounded-xl bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:hover:bg-blue-600 text-white font-semibold py-3 px-4 transition"
        >
          {saving ? "Saving…" : "Add to library"}
        </button>
      </div>

      <div className="mt-6 rounded-2xl border border-gray-800 bg-gray-950/20 p-5">
        <h3 className="text-lg font-semibold">Custom games</h3>
        <p className="text-sm text-gray-400 mt-1">
          Stored locally in your app data folder.
        </p>

        <div className="mt-4 space-y-2">
          {customGames.length === 0 ? (
            <div className="text-sm text-gray-500">No custom games yet.</div>
          ) : (
            customGames.map((g) => (
              <div
                key={g.id}
                className="flex items-center justify-between gap-3 rounded-xl border border-gray-800 bg-gray-900/40 p-3"
              >
                <div className="min-w-0">
                  <div className="font-medium truncate">{g.name}</div>
                  <div className="text-xs text-gray-500 font-mono truncate">
                    {g.launch.launchTarget}
                  </div>
                </div>
                <button
                  onClick={() => remove(g.id)}
                  disabled={saving}
                  className="px-3 py-2 rounded-xl bg-gray-800 hover:bg-gray-700 border border-gray-700 text-sm"
                >
                  Remove
                </button>
              </div>
            ))
          )}
        </div>
      </div>

      {legalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-6 bg-black/60">
          <div className="w-full max-w-3xl rounded-2xl border border-gray-800 bg-gray-950 p-5">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="text-lg font-semibold">
                  {legalOpen === "terms"
                    ? "Terms of Service"
                    : legalOpen === "privacy"
                      ? "Privacy Policy"
                      : legalOpen === "eula"
                        ? "EULA"
                        : legalOpen === "disclaimer"
                          ? "Disclaimer"
                          : "DMCA / Copyright"}
                </div>
                <div className="text-xs text-gray-500 mt-1">
                  Included with the app for reference.
                </div>
              </div>
              <button
                onClick={() => setLegalOpen(null)}
                className="text-gray-400 hover:text-gray-200"
                aria-label="Close legal"
              >
                ✕
              </button>
            </div>

            <pre className="mt-4 max-h-[60vh] overflow-auto whitespace-pre-wrap text-sm text-gray-200 bg-gray-900/40 border border-gray-800 rounded-xl p-4">
              {legalOpen === "terms"
                ? termsText
                : legalOpen === "privacy"
                  ? privacyText
                  : legalOpen === "eula"
                    ? eulaText
                    : legalOpen === "disclaimer"
                      ? disclaimerText
                      : dmcaText}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="text-xs text-gray-400 mb-1">{label}</div>
      {children}
    </label>
  );
}

