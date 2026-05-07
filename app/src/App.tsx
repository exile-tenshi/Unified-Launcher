import { useMemo, useState } from "react";
import Layout from "./components/Layout";
import LibraryView from "./components/LibraryView";
import OptimizeView from "./components/OptimizeView";
import SettingsView from "./components/SettingsView";
import { useGameLibrary } from "./hooks/useTauri";
import type { GameEntry } from "./types";

type Tab = "library" | "optimize" | "settings";

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>("library");
  const { games, isLoading, error, refresh } = useGameLibrary();

  const customGames = useMemo(() => games.filter((g) => g.id.startsWith("custom:")), [games]);

  const [customOverride, setCustomOverride] = useState<GameEntry[] | null>(null);
  const effectiveCustom = customOverride ?? customGames;

  return (
    <Layout>
      <div className="flex h-full">
        <nav className="w-64 bg-gray-950/60 border-r border-gray-800 p-4 flex flex-col gap-1">
          <div className="px-2 mb-4">
            <div className="text-lg font-semibold tracking-tight">Unified Library</div>
            <div className="text-xs text-gray-500">Steam • Epic • Xbox • Riot • Other</div>
          </div>

          {([
            { id: "library", label: "Library" },
            { id: "optimize", label: "Optimize" },
            { id: "settings", label: "Settings" },
          ] as const).map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`text-left px-3 py-2 rounded-lg text-sm transition-colors ${
                activeTab === tab.id
                  ? "bg-blue-600/90 text-white"
                  : "text-gray-200 hover:bg-gray-800/60"
              }`}
            >
              {tab.label}
            </button>
          ))}

          <div className="mt-auto px-2 pt-4 text-xs text-gray-500">
            Tip: Use Settings → Custom games to add any EXE.
          </div>
        </nav>

        <main className="flex-1 p-6 overflow-auto">
          {activeTab === "library" && (
            <LibraryView
              games={[...effectiveCustom, ...games.filter((g) => !g.id.startsWith("custom:"))]}
              isLoading={isLoading}
              error={error}
              onRefresh={() => {
                setCustomOverride(null);
                refresh();
              }}
              onOpenOptimize={() => setActiveTab("optimize")}
            />
          )}

          {activeTab === "optimize" && <OptimizeView />}

          {activeTab === "settings" && (
            <SettingsView
              customGames={effectiveCustom}
              onCustomGamesChanged={(next) => setCustomOverride(next)}
            />
          )}
        </main>
      </div>
    </Layout>
  );
}
