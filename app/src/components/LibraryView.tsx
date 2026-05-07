import { useMemo, useState } from "react";
import type { GameEntry, Launcher } from "../types";
import { findConfigHints, launchGame, optimizeGame, uninstallGame } from "../hooks/useTauri";
import { clearArtwork, pickAndSetArtwork, useArtwork } from "../hooks/useArtwork";

const LAUNCHER_LABELS: Record<Launcher, string> = {
  steam: "Steam",
  epic: "Epic",
  xbox: "Xbox",
  riot: "Riot",
  other: "Other",
};

export default function LibraryView({
  games,
  isLoading,
  error,
  onRefresh,
  onOpenOptimize,
}: {
  games: GameEntry[];
  isLoading: boolean;
  error: string | null;
  onRefresh: () => void;
  onOpenOptimize: () => void;
}) {
  const [query, setQuery] = useState("");
  const [launcherFilter, setLauncherFilter] = useState<Launcher | "all">("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return games.filter((g) => {
      if (launcherFilter !== "all" && g.launcher !== launcherFilter) return false;
      if (!q) return true;
      return (
        g.name.toLowerCase().includes(q) ||
        (g.installPath || "").toLowerCase().includes(q)
      );
    });
  }, [games, query, launcherFilter]);

  const selected = useMemo(
    () => games.find((g) => g.id === selectedId) || null,
    [games, selectedId]
  );

  const [optStatus, setOptStatus] = useState<string | null>(null);
  const [uninstallStatus, setUninstallStatus] = useState<string | null>(null);
  const isUnreal = !!selected?.engine && selected.engine.type === "unreal";
  const isUnity = !!selected?.engine && selected.engine.type === "unity";
  const isSource = !!selected?.engine && selected.engine.type === "source";
  // Allow best-effort optimization attempts even when the engine isn't detected yet.
  // The backend will only apply changes when a supported config is found (backup-first).
  const canOptimize = !!selected?.installPath;
  const optimizeHint = !selected
    ? ""
    : isUnreal
      ? "Unreal config optimization is available for this game."
      : isUnity
        ? "Unity: best-effort adjusts vsync-related PlayerPrefs in the registry when present (launch once first)."
        : isSource
          ? "Source / Source 2: best-effort edits video / machine config files when found (Steam userdata + install folder)."
          : "Best-effort optimization tries Source configs, Unity registry (LocalLow match), then Unreal GameUserSettings.ini. Use the Optimize tab for global PC/VR tweaks.";

  return (
    <div className="h-full flex gap-6">
      <section className="flex-1 min-w-0">
        <header className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-2xl font-semibold tracking-tight">Library</h2>
            <p className="text-sm text-gray-400">
              {isLoading ? "Scanning launchers…" : `${filtered.length} shown / ${games.length} total`}
            </p>
          </div>
          <button
            onClick={onRefresh}
            className="px-3 py-2 rounded-lg bg-gray-800 hover:bg-gray-700 border border-gray-700 text-sm"
          >
            Refresh
          </button>
        </header>

        <div className="flex gap-3 mb-4">
          <div className="flex-1">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search games…"
              className="w-full bg-gray-900 border border-gray-700 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-600/40 focus:border-blue-600"
            />
          </div>
          <select
            value={launcherFilter}
            onChange={(e) => setLauncherFilter(e.target.value as any)}
            className="bg-gray-900 border border-gray-700 rounded-xl px-3 py-2.5 text-sm"
          >
            <option value="all">All launchers</option>
            <option value="steam">Steam</option>
            <option value="epic">Epic</option>
            <option value="xbox">Xbox</option>
            <option value="riot">Riot</option>
            <option value="other">Other</option>
          </select>
        </div>

        {error && (
          <div className="mb-4 rounded-xl border border-red-800 bg-red-950/40 p-3 text-sm text-red-200">
            {error}
          </div>
        )}

        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3 overflow-auto pr-1 max-h-[calc(100vh-210px)]">
          {filtered.map((g) => (
            <GameCard
              key={g.id}
              game={g}
              selected={g.id === selectedId}
              onClick={() => setSelectedId(g.id)}
            />
          ))}
        </div>
      </section>

      <aside className="w-[360px] shrink-0 hidden lg:block">
        <div className="h-full rounded-2xl border border-gray-800 bg-gray-950/20 p-5">
          {selected ? (
            <>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h3 className="text-lg font-semibold truncate">{selected.name}</h3>
                  <div className="text-sm text-gray-400">
                    {LAUNCHER_LABELS[selected.launcher]}
                  </div>
                </div>
                <button
                  onClick={() => setSelectedId(null)}
                  className="text-gray-400 hover:text-gray-200"
                  aria-label="Close details"
                >
                  ✕
                </button>
              </div>

              <div className="mt-4 space-y-2 text-sm">
                <Row label="Launch type" value={selected.launch.launchType} />
                {selected.installPath && (
                  <Row label="Install path" value={selected.installPath} mono />
                )}
                {selected.engine && (
                  <Row
                    label="Engine"
                    value={(() => {
                      const e = selected.engine;
                      if (!e) return "";
                      if (e.type === "unreal") return `Unreal (${e.projectName})`;
                      if (e.type === "unity") return `Unity (${e.company} / ${e.product})`;
                      return "Source / Source 2";
                    })()}
                  />
                )}
              </div>

              <GameArtworkEditor gameId={selected.id} />
              <ConfigHints entry={selected} />

              <button
                onClick={() => launchGame(selected)}
                className="mt-6 w-full rounded-xl bg-blue-600 hover:bg-blue-500 text-white font-semibold py-3 transition"
              >
                Launch
              </button>

              <button
                disabled={!canOptimize}
                onClick={async () => {
                  setOptStatus(null);
                  try {
                    const result = await optimizeGame(selected);
                    setOptStatus(result.message);
                  } catch (e) {
                    setOptStatus(String(e));
                  }
                }}
                className="mt-3 w-full rounded-xl bg-gray-800 hover:bg-gray-700 disabled:opacity-50 disabled:hover:bg-gray-800 border border-gray-700 text-white font-semibold py-3 transition"
              >
                {isUnreal ? "Optimize (High FPS + good graphics)" : "Optimize (best‑effort)"}
              </button>

              <div className="mt-2 text-xs text-gray-500">{optimizeHint}</div>

              <button
                onClick={onOpenOptimize}
                className="mt-3 w-full rounded-xl bg-gray-900 hover:bg-gray-800 border border-gray-800 text-white font-semibold py-3 transition"
              >
                Open Optimize tab
              </button>

              {optStatus && (
                <div className="mt-3 text-xs text-gray-400 whitespace-pre-wrap">
                  {optStatus}
                </div>
              )}

              <button
                onClick={async () => {
                  setUninstallStatus(null);
                  try {
                    const msg = await uninstallGame(selected);
                    setUninstallStatus(msg);
                  } catch (e) {
                    setUninstallStatus(String(e));
                  }
                }}
                className="mt-6 w-full rounded-xl bg-red-950/30 hover:bg-red-950/40 border border-red-900/60 text-red-100 font-semibold py-3 transition"
              >
                Uninstall…
              </button>
              {uninstallStatus && (
                <div className="mt-3 text-xs text-gray-400 whitespace-pre-wrap">
                  {uninstallStatus}
                </div>
              )}

              <div className="mt-4 text-xs text-gray-500">
                If a launcher is closed, Windows will open it in the background to launch the game.
              </div>
            </>
          ) : (
            <div className="h-full flex items-center justify-center text-sm text-gray-500">
              Select a game to see details.
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}

function Row({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="text-gray-400">{label}</div>
      <div className={`text-right text-gray-200 ${mono ? "font-mono text-xs" : ""}`}>
        {value}
      </div>
    </div>
  );
}

function GameCard({
  game,
  selected,
  onClick,
}: {
  game: GameEntry;
  selected: boolean;
  onClick: () => void;
}) {
  const art = useArtwork(game.id);
  return (
    <button
      onClick={onClick}
      className={`text-left rounded-xl border p-4 transition ${
        selected
          ? "border-blue-600 bg-blue-600/10"
          : "border-gray-800 bg-gray-950/20 hover:bg-gray-800/30 hover:border-gray-700"
      }`}
    >
      <div className="flex items-start gap-3">
        <div className="h-12 w-12 rounded-lg overflow-hidden bg-gray-800 border border-gray-700 shrink-0">
          {art ? (
            <img src={art} alt="" className="h-full w-full object-cover" />
          ) : (
            <div className="h-full w-full flex items-center justify-center text-[10px] text-gray-400">
              {LAUNCHER_LABELS[game.launcher]}
            </div>
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="font-medium truncate">{game.name}</div>
              <div className="text-xs text-gray-400 mt-1">
                {LAUNCHER_LABELS[game.launcher]}
              </div>
            </div>
            <span className="text-[11px] px-2 py-1 rounded-full bg-gray-800 border border-gray-700 text-gray-200">
              {LAUNCHER_LABELS[game.launcher]}
            </span>
          </div>
          {game.installPath && (
            <div className="mt-2 text-xs text-gray-500 truncate font-mono">
              {game.installPath}
            </div>
          )}
        </div>
      </div>
    </button>
  );
}

function GameArtworkEditor({ gameId }: { gameId: string }) {
  const art = useArtwork(gameId);
  const [, force] = useState(0);
  return (
    <div className="mt-4 rounded-xl border border-gray-800 bg-gray-950/30 p-3">
      <div className="flex items-center justify-between gap-3">
        <div className="text-sm font-semibold">Game image</div>
        <div className="flex gap-2">
          <button
            onClick={async () => {
              await pickAndSetArtwork(gameId);
              force((x) => x + 1);
            }}
            className="px-3 py-2 rounded-xl bg-gray-800 hover:bg-gray-700 border border-gray-700 text-sm"
          >
            {art ? "Change" : "Set"}
          </button>
          {art && (
            <button
              onClick={() => {
                clearArtwork(gameId);
                force((x) => x + 1);
              }}
              className="px-3 py-2 rounded-xl bg-gray-900 hover:bg-gray-800 border border-gray-800 text-sm"
            >
              Clear
            </button>
          )}
        </div>
      </div>
      {art ? (
        <img
          src={art}
          alt=""
          className="mt-3 w-full h-40 object-cover rounded-lg border border-gray-800"
        />
      ) : (
        <div className="mt-3 text-xs text-gray-500">
          Set a custom image (stored locally on this PC).
        </div>
      )}
    </div>
  );
}

function ConfigHints({ entry }: { entry: GameEntry }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [hints, setHints] = useState<{ label: string; paths: string[]; notes: string[] }[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  return (
    <div className="mt-4 rounded-xl border border-gray-800 bg-gray-950/30 p-3">
      <div className="flex items-center justify-between gap-3">
        <div className="text-sm font-semibold">Config locations</div>
        <button
          onClick={async () => {
            const next = !open;
            setOpen(next);
            if (next && !hints && !loading) {
              setLoading(true);
              setErr(null);
              try {
                const res = await findConfigHints(entry);
                setHints(res);
              } catch (e) {
                setErr(String(e));
              } finally {
                setLoading(false);
              }
            }
          }}
          className="px-3 py-2 rounded-xl bg-gray-800 hover:bg-gray-700 border border-gray-700 text-sm"
        >
          {open ? "Hide" : "Show"}
        </button>
      </div>

      {open && (
        <div className="mt-3 text-xs text-gray-300 space-y-3">
          {loading && <div className="text-gray-500">Loading…</div>}
          {err && <div className="text-red-300">{err}</div>}
          {hints?.map((h) => (
            <div key={h.label} className="border border-gray-800 rounded-lg p-3 bg-gray-900/30">
              <div className="font-semibold text-gray-200">{h.label}</div>
              {h.paths.length > 0 && (
                <ul className="mt-2 space-y-1">
                  {h.paths.slice(0, 10).map((p) => (
                    <li key={p} className="font-mono text-gray-400 break-all">
                      {p}
                    </li>
                  ))}
                </ul>
              )}
              {h.notes.length > 0 && (
                <ul className="mt-2 space-y-1 list-disc list-inside text-gray-400">
                  {h.notes.map((n, i) => (
                    <li key={i}>{n}</li>
                  ))}
                </ul>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

