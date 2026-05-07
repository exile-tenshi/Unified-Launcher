# Tauri React Starter

A modern desktop application template built with **Tauri 2 + React + TypeScript + Tailwind CSS**.

Ship cross-platform desktop apps with web technologies — fast, secure, and tiny bundles.

![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)

## ✨ Features

- ⚡ **Tauri 2** — Rust-powered backend, tiny binaries (~3MB)
- ⚛️ **React 18** with TypeScript
- 🎨 **Tailwind CSS** — utility-first styling
- 🔌 **IPC Bridge** — type-safe Rust ↔ JS communication
- 📁 **File System Access** — native file dialogs & operations
- 🔔 **System Notifications** — native OS notifications
- 🪟 **Multi-window** support ready
- 🗃️ **Local Storage** — persistent app data with Tauri Store
- 🔄 **Auto-updater** config included
- 📦 **Cross-platform builds** — Windows, macOS, Linux

## 📋 Prerequisites

- [Node.js](https://nodejs.org/) >= 18
- [Rust](https://rustup.rs/) (latest stable)
- Platform-specific dependencies: see [Tauri Prerequisites](https://v2.tauri.app/start/prerequisites/)

## 🚀 Quick Start

```bash
# Install dependencies
npm install

# Start dev server (opens desktop window)
npm run tauri dev

# Build for production
npm run tauri build
```

## 📁 Project Structure

```
├── src/                  # React frontend
│   ├── components/       # UI components
│   │   ├── Layout.tsx
│   │   ├── Sidebar.tsx
│   │   ├── FileExplorer.tsx
│   │   └── Greet.tsx
│   ├── hooks/            # Custom hooks
│   │   └── useTauri.ts
│   ├── App.tsx
│   ├── App.css
│   ├── main.tsx
│   └── index.css
├── src-tauri/            # Rust backend
│   ├── src/
│   │   └── main.rs       # Tauri commands & setup
│   ├── Cargo.toml
│   ├── tauri.conf.json
│   └── build.rs
├── public/
├── index.html
├── package.json
├── tsconfig.json
├── tailwind.config.js
├── postcss.config.js
└── vite.config.ts
```

## 🔌 IPC Commands

The template includes example Tauri commands you can call from React:

```typescript
import { invoke } from "@tauri-apps/api/core";

// Greet command
const message = await invoke<string>("greet", { name: "World" });

// Read file
const content = await invoke<string>("read_file", { path: "/tmp/test.txt" });

// Get system info
const info = await invoke<SystemInfo>("get_system_info");
```

## 🛠️ Customization

### Adding New Commands

1. Define the command in `src-tauri/src/main.rs`:

```rust
#[tauri::command]
fn my_command(arg: String) -> Result<String, String> {
    Ok(format!("Hello, {arg}!"))
}
```

2. Register it in the builder:

```rust
.invoke_handler(tauri::generate_handler![my_command])
```

3. Call from React:

```typescript
const result = await invoke<string>("my_command", { arg: "test" });
```

### Building for Production

```bash
# Build optimized binary
npm run tauri build

# Output: src-tauri/target/release/bundle/
```

## 📄 License

MIT — use it however you want.

## 🔗 Links

- [Tauri Docs](https://v2.tauri.app/)
- [React Docs](https://react.dev/)
- [Tailwind CSS](https://tailwindcss.com/)
