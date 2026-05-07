import { useEffect, useState } from "react";
import { readFile } from "@tauri-apps/plugin-fs";
import { open } from "@tauri-apps/plugin-dialog";

const KEY_PREFIX = "ugl_artwork:";

export function getArtworkKey(gameId: string) {
  return `${KEY_PREFIX}${gameId}`;
}

export function useArtwork(gameId: string) {
  const [dataUrl, setDataUrl] = useState<string | null>(null);

  useEffect(() => {
    const key = getArtworkKey(gameId);
    const stored = localStorage.getItem(key);
    setDataUrl(stored || null);
  }, [gameId]);

  return dataUrl;
}

export async function pickAndSetArtwork(gameId: string) {
  const selected = await open({
    multiple: false,
    directory: false,
    filters: [{ name: "Images", extensions: ["png", "jpg", "jpeg", "webp"] }],
  });
  if (typeof selected !== "string") return;

  const bytes = await readFile(selected);
  const ext = selected.split(".").pop()?.toLowerCase() || "png";
  const mime =
    ext === "jpg" || ext === "jpeg"
      ? "image/jpeg"
      : ext === "webp"
        ? "image/webp"
        : "image/png";

  const base64 = toBase64(bytes);
  const url = `data:${mime};base64,${base64}`;
  localStorage.setItem(getArtworkKey(gameId), url);
}

export function clearArtwork(gameId: string) {
  localStorage.removeItem(getArtworkKey(gameId));
}

function toBase64(bytes: Uint8Array) {
  let binary = "";
  const chunkSize = 0x8000;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
  }
  return btoa(binary);
}

