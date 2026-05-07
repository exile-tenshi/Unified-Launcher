import { useState } from "react";
import { applyPcTweak, applyVrProfile, type PcTweak, type VrProfile } from "../hooks/useTauri";

const PROFILES: { id: VrProfile; title: string; description: string }[] = [
  {
    id: "steamVrBalanced",
    title: "SteamVR (Balanced)",
    description:
      "Backs up and applies a balanced SteamVR preset (Home off, smoothing on, mirror off, no forced supersampling).",
  },
  {
    id: "virtualDesktopBalanced",
    title: "Virtual Desktop (Balanced)",
    description:
      "Backs up and applies a low-latency balanced profile (auto bitrate/resolution, sliced encoding, buffering off).",
  },
  {
    id: "viveBalanced",
    title: "Vive Hub / Console (Balanced)",
    description:
      "Backs up and applies balanced Vive streaming/runtime defaults (auto resolution/bitrate, low latency, performance mode).",
  },
];

export default function OptimizeView() {
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState<VrProfile | null>(null);
  const [pcBusy, setPcBusy] = useState<PcTweak | null>(null);

  async function run(profile: VrProfile) {
    setBusy(profile);
    setStatus(null);
    try {
      const res = await applyVrProfile(profile);
      setStatus(res.message + (res.backups?.length ? `\nBackup: ${res.backups[0]}` : ""));
    } catch (e) {
      setStatus(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function runPc(tweak: PcTweak) {
    setPcBusy(tweak);
    setStatus(null);
    try {
      const res = await applyPcTweak(tweak);
      const extra =
        (res.backupPath ? `\nBackup: ${res.backupPath}` : "") +
        (res.adminRequired ? "\nAdmin: required" : "") +
        (res.restartRecommended ? "\nRestart: recommended" : "");
      setStatus(res.message + extra);
    } catch (e) {
      setStatus(String(e));
    } finally {
      setPcBusy(null);
    }
  }

  return (
    <div className="max-w-3xl">
      <h2 className="text-2xl font-semibold tracking-tight">Optimize VR</h2>
      <p className="text-sm text-gray-400 mt-1">
        Safe, reversible tweaks. Each action creates a backup before editing any file.
      </p>

      <div className="mt-6 rounded-2xl border border-gray-800 bg-gray-950/20 p-5">
        <h3 className="text-lg font-semibold">Optimize PC (no overclock)</h3>
        <p className="text-sm text-gray-400 mt-1">
          These are Windows settings that can improve frame pacing and reduce background overhead.
          Each tweak is opt-in and creates a backup snapshot.
        </p>

        <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-3">
          <PcCard
            title="Turn Game Mode on"
            description="Restores Windows Game Mode flags if they were disabled."
            busy={pcBusy === "gameModeOn"}
            onApply={() => runPc("gameModeOn")}
          />
          <PcCard
            title="Disable Game DVR / background capture"
            description="Turns off background capture flags that can steal GPU/CPU."
            busy={pcBusy === "disableGameDvrCapture"}
            onApply={() => runPc("disableGameDvrCapture")}
          />
          <PcCard
            title="Ultimate Performance power plan"
            description="Reduces CPU/device power throttling (Admin)."
            busy={pcBusy === "ultimatePerformancePowerPlan"}
            onApply={() => runPc("ultimatePerformancePowerPlan")}
          />
          <PcCard
            title="Low-latency multimedia gaming profile"
            description="Tunes SystemProfile\\Tasks\\Games scheduling for responsiveness (Admin)."
            busy={pcBusy === "multimediaGamingProfile"}
            onApply={() => runPc("multimediaGamingProfile")}
          />
          <PcCard
            title="TCP gaming defaults"
            description="Normalizes TCP settings (RSS on, autotuning normal, ECN off) (Admin)."
            busy={pcBusy === "tcpGamingDefaults"}
            onApply={() => runPc("tcpGamingDefaults")}
          />
        </div>
      </div>

      <div className="mt-6">
        <h3 className="text-lg font-semibold">Optimize VR</h3>
        <p className="text-sm text-gray-400 mt-1">
          SteamVR, Virtual Desktop, and Vive profiles (backup-first).
        </p>
      </div>

      <div className="mt-6 space-y-3">
        {PROFILES.map((p) => (
          <div
            key={p.id}
            className="rounded-2xl border border-gray-800 bg-gray-950/20 p-5"
          >
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="text-lg font-semibold">{p.title}</div>
                <div className="text-sm text-gray-400 mt-1">{p.description}</div>
              </div>
              <button
                disabled={!!busy}
                onClick={() => run(p.id)}
                className="shrink-0 rounded-xl bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:hover:bg-blue-600 text-white font-semibold py-3 px-4 transition"
              >
                {busy === p.id ? "Applying…" : "Apply"}
              </button>
            </div>
          </div>
        ))}
      </div>

      {status && (
        <div className="mt-4 whitespace-pre-wrap text-sm text-gray-300 rounded-xl border border-gray-800 bg-gray-950/40 p-4">
          {status}
        </div>
      )}
    </div>
  );
}

function PcCard({
  title,
  description,
  busy,
  onApply,
}: {
  title: string;
  description: string;
  busy: boolean;
  onApply: () => void;
}) {
  return (
    <div className="rounded-2xl border border-gray-800 bg-gray-950/20 p-4">
      <div className="font-semibold">{title}</div>
      <div className="text-sm text-gray-400 mt-1">{description}</div>
      <button
        disabled={busy}
        onClick={onApply}
        className="mt-3 rounded-xl bg-gray-800 hover:bg-gray-700 disabled:opacity-50 disabled:hover:bg-gray-800 border border-gray-700 text-white font-semibold py-2.5 px-3 transition"
      >
        {busy ? "Applying…" : "Apply"}
      </button>
    </div>
  );
}

