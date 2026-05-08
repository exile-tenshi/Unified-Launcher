import customtkinter as ctk
import subprocess
import threading
import sys
import os
import urllib.request
import json
import time
from tkinter import messagebox

# --- CONFIGURATION ---
CURRENT_VERSION = "3.0.0"
UPDATE_URL = "https://api.tenshi.lol/version"
GAME_EXECUTABLE_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "Tenshi_Game.exe"))

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class TenshiHub(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Tenshi Hub")
        self.geometry("600x400")
        self.resizable(False, False)
        
        # Center window
        self.update_idletasks()
        width = self.winfo_width()
        height = self.winfo_height()
        x = (self.winfo_screenwidth() // 2) - (width // 2)
        y = (self.winfo_screenheight() // 2) - (height // 2)
        self.geometry('{}x{}+{}+{}'.format(width, height, x, y))

        # --- Splash Screen / Updater UI ---
        self.splash_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.splash_frame.pack(fill="both", expand=True)

        self.logo_label = ctk.CTkLabel(self.splash_frame, text="TENSHI", font=("Impact", 60), text_color="#5865F2")
        self.logo_label.pack(pady=(120, 10))
        
        self.status_label = ctk.CTkLabel(self.splash_frame, text="Checking for updates...", font=("Arial", 14), text_color="gray")
        self.status_label.pack()

        # Start updater check in background
        threading.Thread(target=self.check_for_updates, daemon=True).start()

    def check_for_updates(self):
        time.sleep(1) # Simulated network delay for effect
        latest_version = CURRENT_VERSION
        
        try:
            req = urllib.request.Request(UPDATE_URL, headers={'User-Agent': 'TenshiHub/1.0'})
            with urllib.request.urlopen(req, timeout=3) as r:
                data = json.loads(r.read().decode())
                if 'version' in data:
                    latest_version = data['version']
        except Exception as e:
            print(f"Update check failed (offline or endpoint missing): {e}")

        # In a real scenario, if latest_version > CURRENT_VERSION, download zip and extract.
        if latest_version != CURRENT_VERSION:
            self.after(0, lambda: self.status_label.configure(text=f"Update found (v{latest_version})! Downloading...", text_color="#FEE75C"))
            time.sleep(2) # Simulating download
            self.after(0, lambda: self.status_label.configure(text="Update complete! Restarting...", text_color="#23a559"))
            time.sleep(1)

        # Transition to Main Hub
        self.after(0, self.show_main_hub)

    def show_main_hub(self):
        self.splash_frame.destroy()
        
        # --- Main Hub UI ---
        main_frame = ctk.CTkFrame(self, fg_color="transparent")
        main_frame.pack(fill="both", expand=True, padx=40, pady=40)
        
        title = ctk.CTkLabel(main_frame, text="Welcome back to Tenshi.", font=("Arial", 24, "bold"))
        title.pack(pady=(0, 30))

        # Split into two columns for massive buttons
        btn_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        btn_frame.pack(fill="both", expand=True)
        
        # GAME BUTTON
        self.btn_game = ctk.CTkButton(btn_frame, text="🎮\n\nPLAY GAME", font=("Arial", 20, "bold"), 
                                      width=220, height=200, fg_color="#ed4245", hover_color="#c03537",
                                      command=self.launch_game)
        self.btn_game.pack(side="left", padx=15, expand=True)

        # VOICE / CHAT BUTTON
        self.btn_voice = ctk.CTkButton(btn_frame, text="💬\n\nVOICE / CHAT", font=("Arial", 20, "bold"), 
                                       width=220, height=200, fg_color="#5865F2", hover_color="#4752C4",
                                       command=self.launch_voice)
        self.btn_voice.pack(side="right", padx=15, expand=True)

        footer = ctk.CTkLabel(main_frame, text=f"Hub Version v{CURRENT_VERSION}  |  Connected to tenshi.lol", font=("Arial", 10), text_color="gray")
        footer.pack(side="bottom", pady=(20, 0))

    def launch_game(self):
        self.btn_game.configure(text="🎮\n\nLAUNCHING...", state="disabled")
        self.update()
        
        # Check if game exists where expected
        if os.path.exists(GAME_EXECUTABLE_PATH):
            subprocess.Popen([GAME_EXECUTABLE_PATH])
            self.destroy() # Close Hub
        else:
            # Fallback for testing / dev environment
            test_py_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "main.py"))
            if os.path.exists(test_py_path):
                subprocess.Popen([sys.executable, test_py_path], cwd=os.path.dirname(test_py_path))
                self.destroy()
            else:
                self.btn_game.configure(text="🎮\n\nPLAY GAME", state="normal")
                messagebox.showwarning("Game Not Found", "The game executable could not be found.\nPlease ensure the game folder is linked correctly to the Tenshi Voice Hub directory.")

    def launch_voice(self):
        self.btn_voice.configure(text="💬\n\nCONNECTING...", state="disabled")
        self.update()
        
        client_script = os.path.abspath(os.path.join(os.path.dirname(__file__), "client.py"))
        if os.path.exists(client_script):
            subprocess.Popen([sys.executable, client_script], cwd=os.path.dirname(client_script))
            self.destroy()
        else:
            self.btn_voice.configure(text="💬\n\nVOICE / CHAT", state="normal")
            messagebox.showerror("Error", "client.py not found!")

if __name__ == "__main__":
    app = TenshiHub()
    app.mainloop()
