import { ReactNode } from "react";

interface LayoutProps {
  children: ReactNode;
}

export default function Layout({ children }: LayoutProps) {
  return (
    <div className="h-screen flex flex-col bg-gray-900 text-white">
      <div
        data-tauri-drag-region
        className="h-8 bg-gray-950 flex items-center justify-center select-none"
      >
        <span className="text-xs text-gray-400">Unified Game Library</span>
      </div>
      <div className="flex-1 overflow-hidden">{children}</div>
    </div>
  );
}
