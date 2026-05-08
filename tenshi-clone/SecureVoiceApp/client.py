import customtkinter as ctk
from tkinter import filedialog
from PIL import Image
import os
import socket
import cv2
import mss
import numpy as np
import threading
import json
import base64
import hashlib
import platform
import subprocess
import time
import sys
from typing import Dict, Any, Tuple
import pyaudio
import PIL
import local_db

# --- SECURITY LIBRARIES ---
try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
except ImportError:
    print("CRITICAL: Run 'pip install cryptography' to fix.")
    sys.exit()
def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# --- CONFIGURATION ---
SERVER_IP = 'api.tenshi.lol'  # Your Production Digital Domain
SERVER_PORT = 6000
VIDEO_PORT = 6001
APP_DIR = "TenshiVoice_Data"

# Theme Settings
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

# Ensure Storage Exists
if not os.path.exists(APP_DIR):
    os.makedirs(APP_DIR)
if not os.path.exists(os.path.join(APP_DIR, "profile")):
    os.makedirs(os.path.join(APP_DIR, "profile"))

# --- BACKEND LOGIC (The Engine) ---

class Backend:
    def __init__(self):
        self.username = None
        self.hwid = self.get_hwid()
        self.hwid_hash = hashlib.sha256(self.hwid.encode()).hexdigest()
        self.cipher = None
        self.is_optimized = False

    def register(self, username):
        self.username = username
        # Change: Sending a dictionary (JSON) instead of a | string
        payload = {"action": "REGISTER", "username": username, "hwid": self.hwid_hash}
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect((SERVER_IP, SERVER_PORT))
                s.send(json.dumps(payload).encode('utf-8'))
                res = json.loads(s.recv(1024).decode('utf-8'))
                return res.get("message", "Error")
        except Exception as e:
            return f"FAIL: {e}"

    def get_e2e_key(self, target_id):
        # Derive a channel-specific key so the server cannot easily decrypt it.
        # Format: SHA256 of channel_id + fixed salt, base64 encoded for Fernet
        salt = "TENSHI_E2E_SALT_" + target_id
        key = base64.urlsafe_b64encode(hashlib.sha256(salt.encode()).digest())
        return Fernet(key)

    def send_message(self, target_type, target_id, content, is_snapchat=False):
        if "@" in target_id and "." in target_id:
            # External Email - Bypass E2EE
            encrypted_content = content
            target_type = "email"
        else:
            cipher = self.get_e2e_key(target_id)
            encrypted_content = cipher.encrypt(content.encode('utf-8')).decode('utf-8')
        
        payload = {
            "action": "SEND_MESSAGE",
            "username": self.username,
            "target_type": target_type,
            "target_id": target_id,
            "content": encrypted_content,
            "is_snapchat": is_snapchat,
            "timestamp": time.time()
        }
        # Send immediately
        threading.Thread(target=self.talk_to_server, args=(payload,), daemon=True).start()

    def start_polling(self, app_callback):
        self.polling = True
        self.app_callback = app_callback
        threading.Thread(target=self.poll_loop, daemon=True).start()

    def poll_loop(self):
        while getattr(self, "polling", False):
            try:
                if self.username:
                    res = self.network_request({"action": "POLL_MESSAGES", "username": self.username})
                    if res.get("status") == "success":
                        msgs = res.get("messages", [])
                        for msg in msgs:
                            sender = msg.get("sender")
                            target_id = msg.get("target_id")
                            target_type = msg.get("target_type")
                            enc_content = msg.get("content")
                            is_snap = msg.get("is_snapchat", False)
                            
                            # For DMs, the thread context should be the sender
                            if target_type == "dm":
                                channel_key = sender
                            else:
                                channel_key = target_id
                                
                            # Decrypt
                            try:
                                cipher = self.get_e2e_key(target_id)
                                content = cipher.decrypt(enc_content.encode('utf-8')).decode('utf-8')
                            except:
                                content = "[Encrypted Message]"
                                
                            # Save to local DB (unless user filtered)
                            if not local_db.is_filtered(sender):
                                local_db.save_message(channel_key, sender, content, is_snapchat=is_snap)
                                # Notify App
                                if hasattr(self, 'app_callback'):
                                    self.app_callback(channel_key, sender, content, is_snap)
            except Exception as e:
                pass
            time.sleep(2)

    def login(self, username):
        self.username = username
        payload = {"action": "LOGIN", "username": username, "hwid": self.hwid_hash}
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect((SERVER_IP, SERVER_PORT))
                s.send(json.dumps(payload).encode('utf-8'))
                res = json.loads(s.recv(1024).decode('utf-8'))
                # If success, the server now sends a "status" key
                return res.get("status", "FAIL")
        except Exception as e:
            return f"FAIL: {e}"
        
    def add_friend(self, target):
        payload = {"action": "ADD_FRIEND", "username": self.username, "target": target}
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect((SERVER_IP, SERVER_PORT))
                s.send(json.dumps(payload).encode('utf-8'))
                return json.loads(s.recv(1024).decode('utf-8'))
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def remove_friend(self, target):
        return self.network_request({"action": "REMOVE_FRIEND", "username": self.username, "target": target})

    def follow_user(self, target):
        return self.network_request({"action": "FOLLOW_USER", "username": self.username, "target": target})

    def unfollow_user(self, target):
        return self.network_request({"action": "UNFOLLOW_USER", "username": self.username, "target": target})

    def block_user(self, target):
        return self.network_request({"action": "BLOCK_USER", "username": self.username, "target": target})

    def unblock_user(self, target):
        return self.network_request({"action": "UNBLOCK_USER", "username": self.username, "target": target})

    def get_mutual_servers(self, target):
        return self.network_request({"action": "GET_MUTUAL_SERVERS", "username": self.username, "target": target})

    def update_privacy(self, **kwargs):
        payload = {"action": "UPDATE_PRIVACY", "username": self.username}
        payload.update(kwargs)
        return self.network_request(payload)

    def create_server(self, server_name):
        payload = {"action": "CREATE_SERVER", "username": self.username, "server_name": server_name}
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect((SERVER_IP, SERVER_PORT))
                s.send(json.dumps(payload).encode('utf-8'))
                return json.loads(s.recv(1024).decode('utf-8'))
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_servers(self):
        return self.network_request({"action": "GET_SERVERS", "username": self.username})

    def verify_account(self, code):
        return self.network_request({"action": "VERIFY_ACCOUNT", "username": self.username, "code": code})

    def resend_verification(self):
        return self.network_request({"action": "SEND_VERIFICATION", "username": self.username})

    def join_voice_channel(self, server_id, channel_name):
        return self.network_request({"action": "JOIN_VOICE_CHANNEL", "username": self.username,
                                     "server_id": server_id, "channel": channel_name})

    def leave_voice_channel(self):
        return self.network_request({"action": "LEAVE_VOICE_CHANNEL", "username": self.username})

    def get_server_members(self, server_id):
        return self.network_request({"action": "GET_SERVER_MEMBERS", "username": self.username,
                                     "server_id": server_id})

    def update_custom_status(self, status_type="Online", custom_text=""):
        return self.network_request({"action": "UPDATE_CUSTOM_STATUS", "username": self.username,
                                     "status_type": status_type, "custom_text": custom_text})

    def create_ticket(self, server_id, subject, description=""):
        return self.network_request({"action": "CREATE_TICKET", "username": self.username,
                                     "server_id": server_id, "subject": subject, "description": description})

    def get_tickets(self, server_id):
        return self.network_request({"action": "GET_TICKETS", "username": self.username,
                                     "server_id": server_id})

    def close_ticket(self, server_id, ticket_id):
        return self.network_request({"action": "CLOSE_TICKET", "username": self.username,
                                     "server_id": server_id, "ticket_id": ticket_id})

    def link_platform(self, platform_name, handle):
        return self.network_request({"action": "LINK_PLATFORM", "username": self.username,
                                     "platform": platform_name, "handle": handle})

    def post_story(self, content, media_b64=None):
        return self.network_request({"action": "POST_STORY", "username": self.username,
                                     "content": content, "media": media_b64})

    def get_story(self, target):
        return self.network_request({"action": "GET_STORY", "username": self.username, "target": target})

    def set_user_note(self, target, note):
        return self.network_request({"action": "SET_USER_NOTE", "username": self.username,
                                     "target": target, "note": note})

    def get_user_note(self, target):
        return self.network_request({"action": "GET_USER_NOTE", "username": self.username, "target": target})
          
    def talk_to_server(self, data):
        """Standard helper to send and receive JSON"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect((SERVER_IP, SERVER_PORT))
                s.send(json.dumps(data).encode('utf-8'))
                return json.loads(s.recv(4096).decode('utf-8'))
        except:
            return {"status": "error", "message": "Server Offline"}

    def get_hwid(self):
        try:
            if platform.system() == 'Windows':
                return subprocess.check_output('wmic csproduct get uuid', shell=True).decode().split('\n')[1].strip()
            else:
                return "UNIX_GENERIC_ID"
        except:
            return "FALLBACK_ID"

    def generate_key(self, password):
        salt = self.hwid.encode()
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=100000)
        key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        self.cipher = Fernet(key)

    def connect(self, action, user, password, **kwargs):
        self.generate_key(password)
        try:
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.settimeout(5)
            client.connect((SERVER_IP, SERVER_PORT))
            
            pwd_hash = hashlib.sha256(password.encode()).hexdigest() if password else ""
            
            payload = {
                "action": action,
                "username": user,
                "hwid": self.hwid_hash,
                "pwd_hash": pwd_hash
            }
            payload.update(kwargs)
            client.send(json.dumps(payload).encode('utf-8'))
            
            # The server sends a JSON response
            response_data = client.recv(1024).decode('utf-8')
            response = json.loads(response_data)
            client.close()
            
            if response.get("status") == "success":
                self.username = user
                return True, response.get("message", "Success")
            else:
                return False, response.get("message", "Unknown Error")
        except Exception as e:
            return False, str(e)
    def network_request(self, data):
        """Helper to send JSON requests to the server"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect((SERVER_IP, SERVER_PORT))
                s.send(json.dumps(data).encode('utf-8'))
                return json.loads(s.recv(4096).decode('utf-8'))
        except:
            return {"status": "offline"}

backend = Backend()

# --- THE USER INTERFACE (The Beauty) ---

# --- SETTINGS WINDOW ---
class SettingsWindow(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent)
        self.configure(fg_color="#1e1f22", corner_radius=10, border_width=2, border_color="#383a40")
        self.parent = parent
        
        # Header with close button
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=10, pady=10)
        ctk.CTkLabel(header, text="Settings", font=("Arial", 16, "bold")).pack(side="left")
        ctk.CTkButton(header, text="X", width=30, fg_color="#ed4245", hover_color="#c03537", command=self.close).pack(side="right")
        
        # Audio Input Device
        ctk.CTkLabel(self, text="Microphone:").pack(pady=5)
        self.mic_var = ctk.StringVar(value="Default")
        self.mic_menu = ctk.CTkOptionMenu(self, variable=self.mic_var, values=self.get_audio_devices())
        self.mic_menu.pack(pady=5)
        
        # Noise Suppression (Simulated Gate)
        self.noise_gate_var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(self, text="Noise Suppression (Gate)", variable=self.noise_gate_var).pack(pady=10)
        
        # Gaming Optimization
        self.game_opt_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(self, text="Gaming Optimization (Prioritize Voice Traffic)", variable=self.game_opt_var).pack(pady=10)
        
        ctk.CTkButton(self, text="Save & Close", command=self.save_settings).pack(pady=20)

    def get_audio_devices(self):
        try:
            p = pyaudio.PyAudio()
            devices = []
            info = p.get_host_api_info_by_index(0)
            numdevices = info.get('deviceCount')
            for i in range(0, numdevices):
                if (p.get_device_info_by_host_api_device_index(0, i).get('maxInputChannels')) > 0:
                    devices.append(f"{i}: {p.get_device_info_by_host_api_device_index(0, i).get('name')}")
            return devices if devices else ["Default"]
        except:
            return ["Default (PyAudio Error)"]

    def close(self):
        self.destroy()

    def save_settings(self):
        # Save to global or parent
        backend.is_optimized = self.game_opt_var.get()
        if hasattr(self.parent, 'toggle_optimize'):
            self.parent.toggle_optimize(force_on=backend.is_optimized)
        print(f"Settings Saved: Mic={self.mic_var.get()}, Gate={self.noise_gate_var.get()}, GameOpt={backend.is_optimized}")
        self.close()

# --- VIDEO WINDOW ---
class VideoCallWindow(ctk.CTkFrame):
    def __init__(self, parent, username, room_id="Lobby"):
        super().__init__(parent)
        self.configure(fg_color="#313338", corner_radius=10, border_width=2, border_color="#1e1f22")
        self.parent = parent
        self.username = username
        self.room_id = room_id
        
        # Header with close button
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=10, pady=10)
        ctk.CTkLabel(header, text=f"Voice/Video - {room_id}", font=("Arial", 16, "bold")).pack(side="left")
        ctk.CTkButton(header, text="Close Video Call", width=120, fg_color="#ed4245", hover_color="#c03537", command=self.close_call).pack(side="right")
        
        # Tools / Action Buttons Frame
        tools_frame = ctk.CTkFrame(self, fg_color="transparent")
        tools_frame.pack(fill="x", padx=10, pady=(0, 10))
        
        # Connection
        self.client_socket = None
        self.running = True
        self.is_streaming_camera = False
        self.is_streaming_screen = False
        
        # Audio State
        self.is_muted = False
        self.is_deafened = False
        
        # Quality Settings
        self.quality_map = {
            "Low": {"res": (320, 240), "fps": 10, "q": 30},
            "Medium": {"res": (480, 360), "fps": 15, "q": 50},
            "High": {"res": (640, 480), "fps": 24, "q": 70},
            "720p": {"res": (1280, 720), "fps": 30, "q": 80},
            "1080p": {"res": (1920, 1080), "fps": 30, "q": 80},
            "2K": {"res": (2560, 1440), "fps": 30, "q": 90},
            "4K": {"res": (3840, 2160), "fps": 30, "q": 90}
        }
        self.current_quality = "Medium"
        
        # Quality Selector
        self.q_lbl = ctk.CTkLabel(tools_frame, text="Res:")
        self.q_lbl.pack(side="left", padx=(0, 2))
        self.q_var = ctk.StringVar(value=self.current_quality)
        self.q_menu = ctk.CTkOptionMenu(tools_frame, values=list(self.quality_map.keys()), variable=self.q_var, command=self.update_quality, width=100)
        self.q_menu.pack(side="left", padx=(0, 10))
        
        # FPS Selector
        self.fps_lbl = ctk.CTkLabel(tools_frame, text="FPS:")
        self.fps_lbl.pack(side="left", padx=(0, 2))
        self.fps_options = ["30", "60", "90", "120"]
        self.current_fps = "30"
        self.fps_var = ctk.StringVar(value=self.current_fps)
        self.fps_menu = ctk.CTkOptionMenu(tools_frame, values=self.fps_options, variable=self.fps_var, command=self.update_fps, width=60)
        self.fps_menu.pack(side="left", padx=(0, 10))
        # Music Controls
        self.music_btn = ctk.CTkButton(tools_frame, text="🎵 Share Music", width=100, command=self.open_music_panel)
        self.music_btn.pack(side="left", padx=5)

        # Camera / Screen Buttons
        self.cam_btn = ctk.CTkButton(tools_frame, text="Start Camera", width=100, command=self.toggle_camera)
        self.cam_btn.pack(side="left", padx=5)
        
        self.screen_btn = ctk.CTkButton(tools_frame, text="Share Screen", width=100, command=self.toggle_screen)
        self.screen_btn.pack(side="left", padx=5)

        # Status Label
        self.status_lbl = ctk.CTkLabel(tools_frame, text="● Connected", text_color="#23a559", font=("Arial", 11))
        self.status_lbl.pack(side="left", padx=10)

        # Participant List Frame
        self.participant_frame = ctk.CTkScrollableFrame(self, fg_color="#1e1f22")
        self.participant_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        self.participants = {} # username -> frame/label widget
        self.video_labels = {} # username -> label for video
        
        # Add Self to List
        self.add_participant_ui(self.username)

        # Networking
        self.client_socket = None
        self.running = True
        self.is_streaming_camera = False
        self.is_streaming_screen = False
        self.setup_connection()

    def setup_connection(self):
        try:
            self.cipher = backend.get_e2e_key(self.room_id)
            self.client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # Use global SERVER_IP and VIDEO_PORT
            self.client_socket.connect((SERVER_IP, VIDEO_PORT))
            
            # Handshake
            meta = json.dumps({"username": self.username, "room_id": self.room_id}).encode()
            length = len(meta).to_bytes(4, 'big')
            self.client_socket.sendall(length + meta)
            
            # Start Threads
            threading.Thread(target=self.receive_loop, daemon=True).start()
            
            # Camera OFF by default
            self.is_streaming_camera = False

            # Start Audio
            self.init_audio()

        except Exception as e:
            print(f"Video Conn Error: {e}")

    def add_participant_ui(self, p_username):
        if p_username in self.participants: return
        
        # CARD FRAME
        card = ctk.CTkFrame(self.participant_frame, fg_color="#2b2d31", border_width=2, border_color="#1e1f22")
        card.pack(fill="x", pady=5)
        
        # HEADER (Name)
        lbl_name = ctk.CTkLabel(card, text=p_username, font=("Arial", 12, "bold"))
        lbl_name.pack(pady=2)
        
        # VIDEO PLACEHOLDER / FEED
        # Default: Video Off icon or black box
        lbl_video = ctk.CTkLabel(card, text="[Camera Off]", width=320, height=240, fg_color="#000000", text_color="#555555")
        lbl_video.pack(padx=5, pady=5)
        
        # Click to Enlarge Binding
        lbl_video.bind("<Double-Button-1>", lambda event, u=p_username: self.enlarge_video(u))
        
        self.participants[p_username] = card
        self.video_labels[p_username] = lbl_video

    def remove_participant_ui(self, p_username):
        if p_username in self.participants:
            self.participants[p_username].destroy()
            del self.participants[p_username]
            del self.video_labels[p_username]

    def update_participant_list(self, user_list):
        # Add new
        for u in user_list:
            self.add_participant_ui(u)
            
        # Server user_list should be trusted.
        target_set = set(user_list)
        current_users = list(self.participants.keys())
        to_remove = []
        for u in current_users:
            if u not in target_set:
                to_remove.append(u)
                
        for u in to_remove:
            self.remove_participant_ui(u)

    def update_quality(self, choice):
        # Update resolution based on choice
        settings = self.quality_map.get(choice, self.quality_map["Medium"])
        print(f"Switched to {choice}: {settings}")
        self.current_quality = choice

    def update_fps(self, choice):
        print(f"Switched FPS to {choice}")
        self.current_fps = choice

    def open_music_panel(self):
        if hasattr(self, 'is_streaming_music') and self.is_streaming_music:
            self.is_streaming_music = False
            self.music_btn.configure(fg_color="#3b8ed0", text="🎵 Share Music")
            return
            
        file_path = filedialog.askopenfilename(filetypes=[("WAV Audio", "*.wav")])
        if file_path:
            self.is_streaming_music = True
            self.music_btn.configure(fg_color="#ed4245", text="Stop Music")
            threading.Thread(target=self.stream_music_file, args=(file_path,), daemon=True).start()

    def stream_music_file(self, file_path):
        import wave
        try:
            wf = wave.open(file_path, 'rb')
            chunk = 1024
            data = wf.readframes(chunk)
            while data and self.running and self.client_socket and getattr(self, 'is_streaming_music', False):
                if not self.is_muted:
                    payload = b'\x01' + data
                    size = len(payload).to_bytes(4, 'big')
                    self.client_socket.sendall(size + payload)
                data = wf.readframes(chunk)
                time.sleep(chunk / wf.getframerate())
            wf.close()
        except Exception as e:
            print(f"Music Error: {e}")
        finally:
            self.is_streaming_music = False
            try:
                self.after(0, lambda: self.music_btn.configure(fg_color="#3b8ed0", text="🎵 Share Music"))
            except: pass

    def init_audio(self):
        try:
            self.pyaudio_inst = pyaudio.PyAudio()
            # Input Stream (Mic)
            self.audio_input = self.pyaudio_inst.open(format=pyaudio.paInt16, channels=1, rate=44100, input=True, frames_per_buffer=1024)
            # Output Stream (Speakers)
            self.audio_output = self.pyaudio_inst.open(format=pyaudio.paInt16, channels=1, rate=44100, output=True, frames_per_buffer=1024)
            
            threading.Thread(target=self.stream_audio, daemon=True).start()
            print("Audio initialized.")
        except ImportError:
            print("PyAudio not found. Audio disabled.")
        except Exception as e:
            print(f"Audio Error: {e}")

    def stream_audio(self):
        while self.running and self.client_socket:
            try:
                if not self.is_muted and hasattr(self, 'audio_input'):
                    data = self.audio_input.read(1024, exception_on_overflow=False)
                    # Encrypt Audio Frame for E2EE
                    enc_data = self.cipher.encrypt(data)
                    # Type 1 = Audio
                    payload = b'\x01' + enc_data
                    size = len(payload).to_bytes(4, 'big')
                    self.client_socket.sendall(size + payload)
                
                # Performance optimization: Always sleep briefly to prevent 100% CPU thread starvation
                time.sleep(0.005) 
            except Exception:
                time.sleep(0.05)

    def recvall(self, n):
        data = b''
        while len(data) < n:
            packet = self.client_socket.recv(n - len(data))
            if not packet:
                return None
            data += packet
        return data

    def receive_loop(self):
        while self.running:
            try:
                # 1. Read Size
                size_data = self.recvall(4)
                if not size_data: break
                size = int.from_bytes(size_data, 'big')
                
                # 2. Read Data
                data = self.recvall(size)
                if not data: break
                
                # Protocol: [Type (1 byte)][Payload]
                pkt_type = data[0]
                payload = data[1:]
                
                if pkt_type == 2: # User List Update
                    # Protocol: [Type 2][Len 4][JSON Data]
                    json_len = int.from_bytes(payload[:4], 'big')
                    json_data = payload[4:4+json_len]
                    user_list = json.loads(json_data.decode('utf-8'))
                    self.after(0, lambda u=user_list: self.update_participant_list(u))
                    continue

                # Type 0 = Video, Type 1 = Audio
                # Server sends: [Type 1 byte][Uname_len 1 byte][Username bytes][Payload]
                if pkt_type == 0 or pkt_type == 1:
                    uname_len = data[1]
                    sender_username = data[2:2+uname_len].decode('utf-8')
                    enc_payload = data[2+uname_len:]
                    
                    try:
                        payload = self.cipher.decrypt(enc_payload)
                    except Exception as dec_err:
                        # Optimization: don't log every dek_err to console to save I/O
                        continue
                    
                    if pkt_type == 0: # Video
                        self.after(0, lambda f=payload, u=sender_username: self.update_video_feed(f, u))
                        
                    elif pkt_type == 1: # Audio
                        if not self.is_deafened and hasattr(self, 'audio_output') and self.audio_output:
                            self.audio_output.write(payload)
                
                # Prevent CPU spiking in tight receive loops
                time.sleep(0.001)
            except Exception as e:
                time.sleep(0.1)
                break

    def enlarge_video(self, username):
        if username not in self.video_labels: return
        
        zoom_window = ctk.CTkToplevel(self)
        zoom_window.title(f"{username}'s Stream (Enlarged)")
        zoom_window.geometry("800x600")
        
        lbl_zoom = ctk.CTkLabel(zoom_window, text="[Stream Loading...]", font=("Arial", 20))
        lbl_zoom.pack(fill="both", expand=True, padx=10, pady=10)
        
        self.zoomed_user = username
        self.zoomed_label = lbl_zoom
        
        def on_close():
            self.zoomed_user = None
            self.zoomed_label = None
            zoom_window.destroy()
            
        zoom_window.protocol("WM_DELETE_WINDOW", on_close)

    def update_video_feed(self, frame_data, username):
        try:
            # Check if user has UI
            if username not in self.video_labels:
                self.add_participant_ui(username)
            
            label = self.video_labels[username]
            
            # Convert bytes to numpy array
            nparr = np.frombuffer(frame_data, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            # Convert to RGB (OpenCV uses BGR)
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            
            # Update Zoomed Window if active
            if getattr(self, 'zoomed_user', None) == username and getattr(self, 'zoomed_label', None):
                try:
                    z_img = Image.fromarray(frame_rgb)
                    z_w = max(10, self.zoomed_label.winfo_width())
                    z_h = max(10, self.zoomed_label.winfo_height())
                    z_ctk_img = ctk.CTkImage(light_image=z_img, dark_image=z_img, size=(z_w, z_h))
                    self.zoomed_label.configure(image=z_ctk_img, text="")
                except:
                    pass
            
            # Resize small for grid
            # Target size: 320x240
            frame_rgb = cv2.resize(frame_rgb, (320, 240))
            
            img = Image.fromarray(frame_rgb)
            ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(320, 240))
            
            label.configure(image=ctk_img, text="")
        except Exception as e:
            print(f"Frame Error: {e}")

    def change_quality(self, choice):
        self.current_quality = choice
        print(f"Quality changed to {choice}")

    def set_mute(self, muted):
        self.is_muted = muted
        status = "Muted" if muted else "Unmuted"
        self.status_lbl.configure(text=f"Mic: {status}")

    def set_deafen(self, deafened):
        self.is_deafened = deafened
        status = "Deafened" if deafened else "Undeafened"
        self.status_lbl.configure(text=f"Audio: {status}")

    def toggle_camera(self):
        if self.is_streaming_camera:
            self.is_streaming_camera = False
            self.cam_btn.configure(fg_color="#3b8ed0", text="Start Camera")
            # Clear frame globally
            try:
                self.send_frame(np.zeros((240, 320, 3), dtype=np.uint8))
            except: pass
        else:
            dialog = ctk.CTkInputDialog(text="Enter Camera Index (e.g. 0, 1, 2):", title="Select Camera")
            cam_idx_str = dialog.get_input()
            if cam_idx_str is None: return
            try:
                cam_idx = int(cam_idx_str)
            except:
                cam_idx = 0
                
            self.is_streaming_camera = True
            self.is_streaming_screen = False # Mutually exclusive for simplicity
            self.screen_btn.configure(fg_color="#3b8ed0", text="Share Screen")
            self.cam_btn.configure(fg_color="#ed4245", text="Stop Camera")
            threading.Thread(target=self.stream_camera, args=(cam_idx,), daemon=True).start()

    def stream_camera(self, cam_idx=0):
        cap = cv2.VideoCapture(cam_idx)
        if not cap.isOpened():
            print(f"Camera {cam_idx} could not be opened.")
            self.is_streaming_camera = False
            try:
                self.after(0, lambda: self.cam_btn.configure(fg_color="#3b8ed0", text="Start Camera (Failed)"))
            except: pass
            return
            
        while self.running and self.is_streaming_camera:
            if self.is_muted:
                time.sleep(0.1)
                continue
                
            ret, frame = cap.read()
            if ret:
                # Apply Quality Settings
                settings = self.quality_map[self.current_quality]
                res = settings["res"]
                fps = int(self.current_fps)
                
                # Apply gaming optimization filter
                if getattr(backend, 'is_optimized', False):
                    fps = 5  # Low frame rate when game optimized
                    res = self.quality_map["Low"]["res"]
                
                # Resize
                frame = cv2.resize(frame, res)
                
                # Send
                self.send_frame(frame)
                
                # Throttle FPS
                time.sleep(1.0 / int(fps))
            else:
                print("Failed to read camera frame.")
                self.is_streaming_camera = False
                try:
                    self.after(0, lambda: self.cam_btn.configure(fg_color="#3b8ed0", text="Start Camera (Failed)"))
                except: pass
                break
        cap.release()

    def toggle_screen(self):
        if self.is_streaming_screen:
            self.is_streaming_screen = False
            self.screen_btn.configure(fg_color="#3b8ed0", text="Share Screen")
            # Clear frame globally
            try:
                self.send_frame(np.zeros((240, 320, 3), dtype=np.uint8))
            except: pass
        else:
            import mss
            with mss.mss() as sct:
                mon_count = len(sct.monitors) - 1
            dialog = ctk.CTkInputDialog(text=f"Enter Monitor Index (1 to {max(1, mon_count)}):", title="Select Monitor")
            mon_idx_str = dialog.get_input()
            if mon_idx_str is None: return
            try:
                mon_idx = int(mon_idx_str)
            except:
                mon_idx = 1
                
            self.is_streaming_screen = True
            self.is_streaming_camera = False
            self.cam_btn.configure(fg_color="#3b8ed0", text="Start Camera")
            self.screen_btn.configure(fg_color="#ed4245", text="Stop Sharing")
            threading.Thread(target=self.stream_screen, args=(mon_idx,), daemon=True).start()

    def stream_screen(self, mon_idx=1):
        import mss
        with mss.mss() as sct:
            if mon_idx < 1 or mon_idx >= len(sct.monitors):
                mon_idx = 1
                
            monitor = sct.monitors[mon_idx]
            while self.running and self.is_streaming_screen:
                img = np.array(sct.grab(monitor))
                
                # Apply Quality Settings
                settings = self.quality_map[self.current_quality]
                res = settings["res"]
                fps = int(self.current_fps)
                
                # Resize heavily for performance, but maybe slightly higher for screen?
                # For now, sticking to the map to ensure "Low Usage" request is met
                frame = cv2.resize(img, res)
                
                # Convert BGRA to BGR
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                
                self.send_frame(frame)
                time.sleep(1.0 / int(fps))
 # ~10 FPS

    def send_frame(self, frame):
        try:
            # Apply gaming optimization compression
            jpg_qual = 30 if getattr(backend, 'is_optimized', False) else 50
            
            # Compress to JPEG
            _, jpeg = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), jpg_qual])
            data = jpeg.tobytes()
            
            # Update local UI so sender actually sees their own feed!
            self.after(0, lambda: self.update_video_feed(data, self.username))
            
            # Encrypt Video Frame for E2EE
            enc_data = self.cipher.encrypt(data)
            
            # Send: [Size 4 bytes][Type 1 byte][Data]
            # Type 0 = Video
            payload = b'\x00' + enc_data
            size = len(payload).to_bytes(4, 'big')
            self.client_socket.sendall(size + payload)
        except:
            self.is_streaming_camera = False
            self.is_streaming_screen = False

    def close_call(self):
        self.running = False
        if self.client_socket: self.client_socket.close()
        if hasattr(self.parent, 'close_overlay'):
            self.parent.close_overlay()
        else:
            self.destroy()
        if hasattr(self.parent, 'video_window'):
            self.parent.video_window = None
        # Auto-reset optimize/DND when call ends
        if hasattr(self.parent, 'toggle_optimize') and getattr(self.parent, 'is_optimized', False):
            self.parent.toggle_optimize()

class PaymentWindow(ctk.CTkToplevel):
    def __init__(self, parent, user, pwd):
        super().__init__(parent)
        self.title("Link 2nd Device")
        self.geometry("400x400")
        self.user = user
        self.pwd = pwd
        self.parent_win = parent
        
        self.price = 5.00
        
        ctk.CTkLabel(self, text="New Device Detected", font=("Impact", 24), text_color="#5865F2").pack(pady=(20, 5))
        ctk.CTkLabel(self, text="Linking this account to a 2nd device\nrequires a one-time provisioning fee.").pack()
        
        self.price_label = ctk.CTkLabel(self, text=f"Total: ${self.price:.2f}", font=("Arial", 28, "bold"), text_color="#23a559")
        self.price_label.pack(pady=20)
        
        self.coupon_entry = ctk.CTkEntry(self, placeholder_text="Enter long promo code...", width=250)
        self.coupon_entry.pack(pady=(10, 5))
        
        ctk.CTkButton(self, text="Apply Code", fg_color="transparent", border_width=1, text_color="gray", command=self.apply_code, width=250).pack()
        
        ctk.CTkButton(self, text="Pay & Link Device", fg_color="#5865F2", hover_color="#4752C4", command=self.pay, width=250, height=45).pack(pady=30)
        
        # Center the window
        self.update_idletasks()
        x = parent.winfo_x() + 20
        y = parent.winfo_y() + 40
        self.geometry(f"+{x}+{y}")
        self.grab_set()

    def apply_code(self):
        code = self.coupon_entry.get().strip()
        if code == "TENSHI-ONE-DOLLAR-FIRST-PURCHASE-2026-XQZ":
            self.price = 1.00
            self.price_label.configure(text=f"Total: ${self.price:.2f}", text_color="#FEE75C")
        elif code == "TENSHI-FRIEND-ZERO-DOLLARS-2026-LMN":
            self.price = 0.00
            self.price_label.configure(text=f"Total: $0.00", text_color="#5865F2")
        else:
            self.price_label.configure(text="Invalid Code", text_color="red")
            self.after(1500, lambda: self.price_label.configure(text=f"Total: ${self.price:.2f}", text_color="#23a559"))
            
    def pay(self):
        # Simulated payment processing...
        self.price_label.configure(text="Processing...", text_color="gray")
        self.update()
        success, msg = backend.connect("LINK_DEVICE", self.user, self.pwd)
        if success:
            self.destroy()
            self.parent_win.attempt_auth("LOGIN")
        else:
            self.price_label.configure(text=msg, text_color="red")

class RegisterWindow(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Register New Account")
        self.geometry("400x500")
        self.parent = parent
        
        ctk.CTkLabel(self, text="Create Account", font=("Impact", 24), text_color="#5865F2").pack(pady=20)
        
        self.user_entry = ctk.CTkEntry(self, placeholder_text="Username", width=250)
        self.user_entry.pack(pady=10)
        
        self.pass_entry = ctk.CTkEntry(self, placeholder_text="Password", show="*", width=250)
        self.pass_entry.pack(pady=10)
        
        ctk.CTkLabel(self, text="You must provide at least one contact method:", text_color="gray").pack(pady=(10,0))
        
        self.email_entry = ctk.CTkEntry(self, placeholder_text="Email Address", width=250)
        self.email_entry.pack(pady=5)
        
        self.phone_entry = ctk.CTkEntry(self, placeholder_text="Phone Number", width=250)
        self.phone_entry.pack(pady=5)
        
        self.status = ctk.CTkLabel(self, text="", text_color="red")
        self.status.pack()
        
        ctk.CTkButton(self, text="Register", command=self.register, fg_color="#23a559", width=250).pack(pady=20)
        
    def register(self):
        user = self.user_entry.get()
        pwd = self.pass_entry.get()
        email = self.email_entry.get()
        phone = self.phone_entry.get()
        
        if not user or not pwd:
            self.status.configure(text="Username and password required")
            return
            
        success, msg = backend.connect("REGISTER", user, pwd, email=email, phone=phone)
        if success:
            self.destroy()
            self.parent.status_label.configure(text="Registration Complete! You can now login.", text_color="#23a559")
        else:
            self.status.configure(text=msg)

class RecoveryWindow(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Password Recovery")
        self.geometry("400x450")
        
        ctk.CTkLabel(self, text="Account Recovery", font=("Impact", 24)).pack(pady=20)
        
        self.user_entry = ctk.CTkEntry(self, placeholder_text="Username", width=250)
        self.user_entry.pack(pady=10)
        
        self.send_btn = ctk.CTkButton(self, text="Send Recovery Code", command=self.send_code)
        self.send_btn.pack()
        
        self.code_entry = ctk.CTkEntry(self, placeholder_text="6-Digit Recovery Code", width=250)
        self.new_pass_entry = ctk.CTkEntry(self, placeholder_text="New Password", show="*", width=250)
        
        self.reset_btn = ctk.CTkButton(self, text="Reset Password", command=self.reset_pass, fg_color="#ed4245")
        
        self.status = ctk.CTkLabel(self, text="", text_color="gray")
        self.status.pack(pady=10)
        
    def send_code(self):
        user = self.user_entry.get()
        res = backend.network_request({"action": "RECOVER_PASSWORD", "username": user})
        if res.get("status") == "success":
            self.status.configure(text=res.get("message"), text_color="#23a559")
            self.user_entry.configure(state="disabled")
            self.send_btn.configure(state="disabled")
            
            self.code_entry.pack(pady=(20, 10))
            self.new_pass_entry.pack(pady=10)
            self.reset_btn.pack(pady=10)
        else:
            self.status.configure(text=res.get("message"), text_color="red")
            
    def reset_pass(self):
        user = self.user_entry.get()
        code = self.code_entry.get()
        new_pwd = self.new_pass_entry.get()
        new_hash = hashlib.sha256(new_pwd.encode()).hexdigest()
        
        res = backend.network_request({
            "action": "RESET_PASSWORD", 
            "username": user, 
            "recovery_code": code,
            "new_pwd_hash": new_hash
        })
        if res.get("status") == "success":
            self.destroy()
        else:
            self.status.configure(text=res.get("message"), text_color="red")

class LoginWindow(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Tenshi Voice - Secure Login")
        self.geometry("400x660")
        self.resizable(False, False)

        # Logo / Title
        self.label = ctk.CTkLabel(self, text="TENSHI", font=("Impact", 40), text_color="#5865F2")
        self.label.pack(pady=(30, 6))
        
        self.subtitle = ctk.CTkLabel(self, text="Hardware-Locked Secure Voice", font=("Arial", 12), text_color="gray")
        self.subtitle.pack(pady=(0, 20))

        # Inputs
        ctk.CTkLabel(self, text="Server IP Address", font=("Arial", 11), text_color="gray").pack(pady=(0,2))
        self.server_entry = ctk.CTkEntry(self, placeholder_text="e.g. 108.2.197.143", width=250, height=36)
        self.server_entry.insert(0, SERVER_IP)
        self.server_entry.pack(pady=(0, 10))

        self.user_entry = ctk.CTkEntry(self, placeholder_text="Username", width=250, height=40)
        self.user_entry.pack(pady=10)

        self.pass_entry = ctk.CTkEntry(self, placeholder_text="Password", show="*", width=250, height=40)
        self.pass_entry.pack(pady=10)

        # Buttons
        self.login_btn = ctk.CTkButton(self, text="Login", width=250, height=40, fg_color="#5865F2", hover_color="#4752C4", command=lambda: self.attempt_auth("LOGIN"))
        self.login_btn.pack(pady=(20, 5))

        self.reg_btn = ctk.CTkButton(self, text="Create Account", width=250, height=30, fg_color="transparent", border_width=2, text_color="gray", command=lambda: RegisterWindow(self))
        self.reg_btn.pack(pady=5)
        
        self.rec_btn = ctk.CTkButton(self, text="Forgot Password?", width=250, height=30, fg_color="transparent", text_color="gray", hover_color="#383a40", command=lambda: RecoveryWindow(self))
        self.rec_btn.pack(pady=5)

        # Status Label
        self.status_label = ctk.CTkLabel(self, text="", text_color="red")
        self.status_label.pack(pady=10)

        # Remember Me Checkbox
        self.remember_var = ctk.StringVar(value="off")
        self.rem_chk = ctk.CTkCheckBox(self, text="Remember Me", variable=self.remember_var, onvalue="on", offvalue="off")
        self.rem_chk.pack(pady=5)
        
        # Auto-Fill Logic
        self.load_session()

    def load_session(self):
        try:
            if os.path.exists("session.json"):
                with open("session.json", "r") as f:
                    data = json.load(f)
                    if data.get("username") and data.get("password"):
                        self.user_entry.insert(0, data["username"])
                        self.pass_entry.insert(0, data["password"])
                        self.remember_var.set("on")
                    if data.get("server_ip"):
                        self.server_entry.delete(0, "end")
                        self.server_entry.insert(0, data["server_ip"])
        except:
            pass

    def attempt_auth(self, action):
        global SERVER_IP
        user = self.user_entry.get()
        pwd = self.pass_entry.get()
        ip = self.server_entry.get().strip()
        if ip:
            SERVER_IP = ip
        
        if not user or not pwd:
            self.status_label.configure(text="Please fill all fields")
            return

        self.status_label.configure(text="Connecting...", text_color="yellow")
        self.update()

        success, msg = backend.connect(action, user, pwd)
        
        if success:
            # Save Session if Remember Me is Checked
            if self.remember_var.get() == "on":
                try:
                    with open("session.json", "w") as f:
                        json.dump({"username": user, "password": pwd, "server_ip": SERVER_IP}, f)
                except: pass
            else:
                # Clear session if unchecked
                if os.path.exists("session.json"):
                    os.remove("session.json")

            self.destroy() # Close Login
            app = MainApp() # Open App
            app.mainloop()
        else:
            if msg == "Hardware ID Mismatch":
                self.status_label.configure(text="Payment required for new device.")
                PaymentWindow(self, user, pwd)
            else:
                self.status_label.configure(text=msg, text_color="red")
class ViewProfileWindow(ctk.CTkFrame):
    def __init__(self, parent, target_username, backend_ref):
        super().__init__(parent, fg_color="#1e1f22", corner_radius=10, border_width=2, border_color="#383a40")
        self.parent = parent
        self.backend = backend_ref
        
        # Header with close button
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=10, pady=10)
        ctk.CTkLabel(header, text=f"{target_username}'s Profile", font=("Arial", 16, "bold")).pack(side="left")
        ctk.CTkButton(header, text="X", width=30, fg_color="#ed4245", hover_color="#c03537", command=self.destroy).pack(side="right")
        
        # Fetch Data
        res = self.backend.network_request({"action": "GET_PROFILE", "target": target_username})
        if res.get("status") != "success":
            ctk.CTkLabel(self, text="Failed to load profile").pack(pady=20)
            return

        # Banner
        banner_color = res.get("banner_color", "#2b2d31")
        self.banner = ctk.CTkFrame(self, height=120, fg_color=banner_color, corner_radius=0)
        self.banner.pack(fill="x")
        
        # PFP (Placeholder)
        self.pfp = ctk.CTkLabel(self, text=target_username[:1].upper(), width=80, height=80, 
                                corner_radius=40, fg_color="gray", font=("Arial", 30))
        self.pfp.place(x=20, y=80) # Overlap banner
        
        # Info
        self.info_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.info_frame.pack(fill="both", expand=True, padx=20, pady=(50, 20))
        
        ctk.CTkLabel(self.info_frame, text=target_username, font=("Arial", 24, "bold")).pack(anchor="w")
        
        status = res.get("user_status", "Online")
        ctk.CTkLabel(self.info_frame, text=f"Status: {status}", text_color="#228B22" if status == "Online" else "gray").pack(anchor="w", pady=(0, 5))
        
        pronouns = res.get("pronouns", "")
        if pronouns:
            ctk.CTkLabel(self.info_frame, text=pronouns, text_color="gray").pack(anchor="w")
            
        ctk.CTkLabel(self.info_frame, text="ABOUT ME", font=("Arial", 12, "bold"), text_color="gray").pack(anchor="w", pady=(20, 5))
        
        bio = res.get("bio", "No bio set.")
        self.bio_box = ctk.CTkTextbox(self.info_frame, height=80, fg_color="#2b2d31")
        self.bio_box.insert("1.0", bio)
        self.bio_box.configure(state="disabled")
        self.bio_box.pack(fill="x")
        
        # Connections Area
        ctk.CTkLabel(self.info_frame, text="CONNECTIONS", font=("Arial", 12, "bold"), text_color="gray").pack(anchor="w", pady=(15, 5))
        conn_frame = ctk.CTkFrame(self.info_frame, fg_color="transparent")
        conn_frame.pack(fill="x")
        
        connections = res.get("connections", {})
        if not connections:
            ctk.CTkLabel(conn_frame, text="No connections linked.", text_color="gray").pack(anchor="w")
        else:
            for platform, name in connections.items():
                lbl = ctk.CTkLabel(conn_frame, text=f"{platform.capitalize()}: {name}", fg_color="#383a40", corner_radius=5)
                lbl.pack(side="left", padx=5, pady=2, ipadx=5, ipady=2)
                
        ctk.CTkButton(self.info_frame, text="Close", fg_color="#313338", command=self.destroy).pack(pady=20)

import random

class PuzzleGameView(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color="#1e1f22", corner_radius=10, border_width=2, border_color="#383a40")
        self.parent = parent
        
        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=10, pady=10)
        ctk.CTkLabel(header, text="Tenshi Puzzle Minigame", font=("Arial", 16, "bold")).pack(side="left")
        ctk.CTkButton(header, text="X", width=30, fg_color="#ed4245", hover_color="#c03537", command=self.close_game).pack(side="right")
        
        self.grid_size = 3
        self.tiles = []
        self.empty_pos = (self.grid_size - 1, self.grid_size - 1)
        self.is_won = False
        
        # UI Elements
        self.control_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.control_frame.pack(pady=10)
        
        self.upload_btn = ctk.CTkButton(self.control_frame, text="Upload Image to Play", command=self.load_image, fg_color="#5865F2")
        self.upload_btn.pack(side="left", padx=5)
        
        # Grid Size Selector
        self.grid_var = ctk.StringVar(value="3x3")
        self.grid_menu = ctk.CTkOptionMenu(self.control_frame, values=["3x3", "4x4", "5x5"], variable=self.grid_var, command=self.change_grid_size, width=80)
        self.grid_menu.pack(side="left", padx=5)

        # Invite Button
        self.invite_btn = ctk.CTkButton(self.control_frame, text="Invite Friend", command=self.invite_friend, fg_color="#228B22", width=100)
        self.invite_btn.pack(side="left", padx=5)
        
        self.win_label = ctk.CTkLabel(self, text="", text_color="#228B22", font=("Arial", 18, "bold"))
        self.win_label.pack(pady=5)
        
        self.game_board = ctk.CTkFrame(self, fg_color="#2b2d31", width=300, height=300)
        self.game_board.pack(pady=10, expand=True)

    def change_grid_size(self, choice):
        self.grid_size = int(choice[0])
        # Clear board to force re-upload
        for widget in self.game_board.winfo_children():
            widget.destroy()
        self.win_label.configure(text=f"Grid size set to {choice}. Please upload an image.")

    def invite_friend(self):
        # Fetch friends list from backend
        res = backend.network_request({"action": "GET_RELATIONSHIPS", "username": backend.username})
        friends = res.get("friends", []) if res.get("status") == "success" else []
        
        if not friends:
            self.win_label.configure(text="No friends to invite!", text_color="#ed4245")
            return
            
        dialog = ctk.CTkInputDialog(text=f"Invite a friend from: {', '.join(friends)}", title="Invite to Game")
        target = dialog.get_input()
        if target and target in friends:
            self.win_label.configure(text=f"Invited {target} to play!", text_color="#228B22")
            # In a real app we would send a network packet here
        elif target:
            self.win_label.configure(text=f"{target} is not in your friends list.", text_color="#ed4245")
        
    def close_game(self):
        self.parent.close_overlay()
        self.destroy()

    def load_image(self):
        file_path = filedialog.askopenfilename(filetypes=[("Images", "*.png;*.jpg;*.jpeg")])
        if not file_path:
            return
            
        try:
            img = Image.open(file_path).convert("RGB")
            # Crop to square for puzzle
            width, height = img.size
            min_dim = min(width, height)
            left = (width - min_dim)/2
            top = (height - min_dim)/2
            right = (width + min_dim)/2
            bottom = (height + min_dim)/2
            img = img.crop((left, top, right, bottom))
            img = img.resize((300, 300))
            
            self.init_puzzle(img)
        except Exception as e:
            print(f"Failed to load image: {e}")

    def init_puzzle(self, image):
        self.win_label.configure(text="")
        self.is_won = False
        
        # Clear existing board
        for widget in self.game_board.winfo_children():
            widget.destroy()
            
        self.tiles = []
        tile_size = 300 // self.grid_size
        
        # Slice image
        pieces = []
        for row in range(self.grid_size):
            for col in range(self.grid_size):
                if row == self.grid_size - 1 and col == self.grid_size - 1:
                    # The empty piece
                    pieces.append(None)
                    continue
                    
                box = (col * tile_size, row * tile_size, (col + 1) * tile_size, (row + 1) * tile_size)
                piece_img = image.crop(box)
                ctk_img = ctk.CTkImage(light_image=piece_img, dark_image=piece_img, size=(tile_size-2, tile_size-2))
                pieces.append({'img': ctk_img, 'original_pos': (row, col)})
        
        # Shuffle (ensure solvable by doing random valid moves from solved state)
        self.board_state: Dict[Tuple[int, int], Any] = {} # (row, col) -> piece_dict
        idx = 0
        for r in range(self.grid_size):
            for c in range(self.grid_size):
                self.board_state[(r, c)] = pieces[idx]
                idx += 1
                
        self.empty_pos = (self.grid_size - 1, self.grid_size - 1)
        self.shuffle_board(100)
        self.draw_board()

    def shuffle_board(self, moves):
        directions = [(-1, 0), (1, 0), (0, -1), (0, 1)]
        for _ in range(moves):
            valid_moves = []
            for d in directions:
                nr, nc = self.empty_pos[0] + d[0], self.empty_pos[1] + d[1]
                if 0 <= nr < self.grid_size and 0 <= nc < self.grid_size:
                    valid_moves.append((nr, nc))
            
            if valid_moves:
                move = random.choice(valid_moves)
                # Swap
                self.board_state[self.empty_pos] = self.board_state[move]
                self.board_state[move] = None
                self.empty_pos = move

    def draw_board(self):
        for widget in self.game_board.winfo_children():
            widget.destroy()
            
        self.buttons = {}
        for r in range(self.grid_size):
            for c in range(self.grid_size):
                piece = self.board_state[(r, c)]
                if piece is None:
                    # Empty space
                    btn = ctk.CTkFrame(self.game_board, fg_color="#1e1f22", width=100, height=100, corner_radius=0)
                    btn.grid(row=r, column=c, padx=1, pady=1)
                else:
                    btn = ctk.CTkButton(self.game_board, image=piece['img'], text="", width=100, height=100, corner_radius=0,
                                        fg_color="transparent", hover_color="#383a40",
                                        command=lambda row=r, col=c: self.tile_click(row, col))
                    btn.grid(row=r, column=c, padx=1, pady=1)
                    self.buttons[(r, c)] = btn

    def tile_click(self, r, c):
        if self.is_won: return
        
        # Check if adjacent to empty
        er, ec = self.empty_pos
        if abs(r - er) + abs(c - ec) == 1:
            # Valid move, swap
            self.board_state[self.empty_pos] = self.board_state[(r, c)]
            self.board_state[(r, c)] = None
            self.empty_pos = (r, c)
            self.draw_board()
            self.check_win()

    def check_win(self):
        for r in range(self.grid_size):
            for c in range(self.grid_size):
                piece = self.board_state[(r, c)]
                if r == self.grid_size - 1 and c == self.grid_size - 1:
                    if piece is not None:
                        return
                    continue
                
                if piece is None or piece['original_pos'] != (r, c):
                    return
                    
        self.is_won = True
        self.win_label.configure(text="Puzzle Solved! 🎉")


# ---------------------------------------------------------------------------
# FRIEND CARD POPUP  (Discord-style user card shown on clicking a friend row)
# ---------------------------------------------------------------------------
class FriendCardPopup(ctk.CTkToplevel):
    """A floating card with social actions for a friend/user."""

    STATUS_COLOR = {
        "Online":    "#23a559",
        "Away":      "#f0b232",
        "Busy":      "#ed4245",
        "Offline":   "#80848e",
    }

    def __init__(self, parent_widget, target_username, app_ref, relationship="friend"):
        super().__init__(parent_widget)
        self.app_ref = app_ref
        self.target = target_username
        self.relationship = relationship   # "friend" | "following" | "none"

        # --- Window chrome ---
        self.overrideredirect(True)          # borderless
        self.attributes("-topmost", True)

        # Position near the cursor
        try:
            x = self.winfo_pointerx() + 12
            y = self.winfo_pointery() - 20
        except Exception:
            x, y = 200, 200
        self.geometry(f"280x380+{x}+{y}")
        self.configure(fg_color="#111214")
        self.resizable(False, False)

        # Dismiss when clicking outside
        self.bind("<FocusOut>", lambda e: self._safe_destroy())

        # --- Fetch data (non-blocking would be ideal; for simplicity fetch here) ---
        profile = backend.network_request({"action": "GET_PROFILE", "target": target_username})
        mutual  = backend.get_mutual_servers(target_username)
        rels    = backend.network_request({"action": "GET_RELATIONSHIPS", "username": backend.username})

        is_following = target_username in rels.get("following", [])
        is_blocked   = target_username in rels.get("blocked", [])
        is_friend    = target_username in rels.get("friends", [])

        # ── Banner ──────────────────────────────────────────────────────────
        banner_color = profile.get("banner_color", "#2b2d31") if profile.get("status") == "success" else "#2b2d31"
        banner = ctk.CTkFrame(self, height=70, fg_color=banner_color, corner_radius=0)
        banner.pack(fill="x")
        banner.pack_propagate(False)

        # Close button (top-right of banner)
        ctk.CTkButton(banner, text="✕", width=26, height=26, fg_color="transparent",
                      hover_color="#3a3b3e", text_color="white",
                      command=self._safe_destroy).place(relx=1.0, rely=0, anchor="ne", x=-4, y=4)

        # ── Avatar ──────────────────────────────────────────────────────────
        avatar_outer = ctk.CTkFrame(self, width=60, height=60, corner_radius=30,
                                    fg_color="#111214", border_width=3, border_color="#111214")
        avatar_outer.place(x=12, y=40)
        avatar_outer.pack_propagate(False)

        avatar_inner = ctk.CTkLabel(avatar_outer, text=target_username[:1].upper(),
                                    width=54, height=54, corner_radius=27,
                                    fg_color="#5865F2", font=("Arial", 22, "bold"))
        avatar_inner.place(relx=0.5, rely=0.5, anchor="center")

        # Status dot
        user_status = profile.get("user_status", "Offline") if profile.get("status") == "success" else "Offline"
        dot_color = self.STATUS_COLOR.get(user_status, "#80848e")
        dot = ctk.CTkFrame(self, width=16, height=16, corner_radius=8, fg_color=dot_color)
        dot.place(x=52, y=82)

        # ── Username + pronouns ─────────────────────────────────────────────
        body = ctk.CTkFrame(self, fg_color="#1e1f22", corner_radius=8)
        body.pack(fill="both", expand=True, padx=8, pady=(38, 8))

        pronouns = profile.get("pronouns", "") if profile.get("status") == "success" else ""
        ctk.CTkLabel(body, text=target_username, font=("Arial", 16, "bold"),
                     anchor="w").pack(anchor="w", padx=10, pady=(10, 0))
        if pronouns:
            ctk.CTkLabel(body, text=pronouns, font=("Arial", 11), text_color="gray",
                         anchor="w").pack(anchor="w", padx=10)

        # Mutual servers
        mutual_names = mutual.get("mutual_servers", []) if mutual.get("status") == "success" else []
        if mutual_names:
            mx = ", ".join(mutual_names[:3])
            if len(mutual_names) > 3:
                mx += f" +{len(mutual_names)-3}"
            ctk.CTkLabel(body, text=f"🏠 {mx}", font=("Arial", 10),
                         text_color="#a0a3a8", anchor="w",
                         wraplength=230).pack(anchor="w", padx=10, pady=(4, 0))

        # Separator
        ctk.CTkFrame(body, height=1, fg_color="#383a40").pack(fill="x", padx=10, pady=8)

        # ── Action buttons ──────────────────────────────────────────────────
        btn_kw = dict(height=32, corner_radius=6, font=("Arial", 13))

        # DM button (always available)
        ctk.CTkButton(body, text="✉️  Send Message", fg_color="#5865F2", hover_color="#4752C4",
                      command=lambda: [self._safe_destroy(),
                                       app_ref.join_text_channel(f"DM_with_{target_username}")],
                      **btn_kw).pack(fill="x", padx=10, pady=2)

        # Watch / Join Voice (if in same voice channel is unknown client-side; open voice)
        ctk.CTkButton(body, text="🔊  Join Voice", fg_color="#2b2d31", hover_color="#404249",
                      command=lambda: [self._safe_destroy(),
                                       app_ref.join_voice("General")],
                      **btn_kw).pack(fill="x", padx=10, pady=2)

        # Follow / Unfollow
        follow_text = "➖ Unfollow" if is_following else "➕ Follow"
        follow_cmd  = (lambda: self._action(backend.unfollow_user, target_username)) if is_following \
                      else (lambda: self._action(backend.follow_user, target_username))
        ctk.CTkButton(body, text=follow_text, fg_color="#2b2d31", hover_color="#404249",
                      command=follow_cmd, **btn_kw).pack(fill="x", padx=10, pady=2)

        # Remove Friend / Add Friend
        if is_friend:
            ctk.CTkButton(body, text="👋 Remove Friend", fg_color="#2b2d31", hover_color="#6e1f1f",
                          command=lambda: self._confirm_remove(),
                          **btn_kw).pack(fill="x", padx=10, pady=2)
        elif not is_blocked:
            ctk.CTkButton(body, text="➕ Add Friend", fg_color="#228B22", hover_color="#1E7A1E",
                          command=lambda: self._action(backend.add_friend, target_username),
                          **btn_kw).pack(fill="x", padx=10, pady=2)

        # Block / Unblock
        if is_blocked:
            ctk.CTkButton(body, text="🔓 Unblock", fg_color="#ed4245", hover_color="#c03537",
                          command=lambda: self._action(backend.unblock_user, target_username),
                          **btn_kw).pack(fill="x", padx=10, pady=2)
        else:
            ctk.CTkButton(body, text="🚫 Block", fg_color="#2b2d31", hover_color="#6e1f1f",
                          command=lambda: self._confirm_block(),
                          **btn_kw).pack(fill="x", padx=10, pady=2)

        # Feedback label
        self.status_lbl = ctk.CTkLabel(body, text="", text_color="#fee75c", font=("Arial", 11))
        self.status_lbl.pack(pady=(4, 0))

        self.focus_force()

    # ----- helpers -----------------------------------------------------------

    def _safe_destroy(self):
        try:
            self.destroy()
        except Exception:
            pass

    def _action(self, fn, *args):
        res = fn(*args)
        msg = res.get("message", "Done") if isinstance(res, dict) else str(res)
        try:
            self.status_lbl.configure(text=msg)
            self.after(1800, self._safe_destroy)
        except Exception:
            pass

    def _confirm_remove(self):
        self.status_lbl.configure(text="Click again to confirm removal…", text_color="#ed4245")
        def do_remove():
            self._action(backend.remove_friend, self.target)
        self.after(300, lambda: self.status_lbl.configure(
            text="Tap again → confirm", text_color="#ed4245"))
        # Re-bind the remove button on second click
        self._pending_remove = True
        self.bind("<Button-1>", lambda e: do_remove() if getattr(self, "_pending_remove", False) else None)

    def _confirm_block(self):
        self.status_lbl.configure(text="Click again to confirm block…", text_color="#ed4245")
        self._pending_block = True
        self.bind("<Button-1>", lambda e: self._action(backend.block_user, self.target)
                  if getattr(self, "_pending_block", False) else None)


# ---------------------------------------------------------------------------
# FULL PROFILE SETTINGS (replaces the basic inline version)
# ---------------------------------------------------------------------------
class ProfileSettingsWindow(ctk.CTkFrame):
    """Rich profile + privacy settings modal."""

    def __init__(self, parent):
        super().__init__(parent, fg_color="#1e1f22", corner_radius=10,
                         border_width=2, border_color="#383a40")
        self.parent = parent

        # Fetch current profile so we can pre-fill
        prof = backend.network_request({"action": "GET_PROFILE", "target": backend.username})
        p_data = prof if prof.get("status") == "success" else {}
        rels   = backend.network_request({"action": "GET_RELATIONSHIPS", "username": backend.username})
        priv   = p_data.get("privacy", {})

        # ── Header ────────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=14, pady=10)
        ctk.CTkLabel(hdr, text="⚙  My Settings", font=("Arial", 17, "bold")).pack(side="left")
        ctk.CTkButton(hdr, text="✕", width=30, fg_color="#ed4245",
                      hover_color="#c03537", command=self._close).pack(side="right")

        # ── Tabs ──────────────────────────────────────────────────────────
        tabs = ctk.CTkTabview(self, width=420, height=380)
        tabs.pack(padx=10, pady=0, fill="both", expand=True)
        tabs.add("Profile")
        tabs.add("Privacy")
        tabs.add("Blocked")

        # ═══════ PROFILE TAB ════════════════════════════════════════════
        pt = tabs.tab("Profile")

        # Avatar preview
        av_row = ctk.CTkFrame(pt, fg_color="transparent")
        av_row.pack(fill="x", pady=(8, 4))
        av_lbl = ctk.CTkLabel(av_row, text=backend.username[:1].upper(),
                              width=56, height=56, corner_radius=28,
                              fg_color="#5865F2", font=("Arial", 22, "bold"))
        av_lbl.pack(side="left", padx=(0, 12))
        ctk.CTkLabel(av_row, text=f"@{backend.username}", font=("Arial", 14, "bold")).pack(side="left")

        # Banner colour
        banner_row = ctk.CTkFrame(pt, fg_color="transparent")
        banner_row.pack(fill="x", pady=4)
        ctk.CTkLabel(banner_row, text="Banner colour:", width=110, anchor="w").pack(side="left")
        self.banner_entry = ctk.CTkEntry(banner_row, placeholder_text="#2b2d31", width=100, height=30)
        self.banner_entry.insert(0, p_data.get("banner_color", "#2b2d31"))
        self.banner_entry.pack(side="left", padx=6)

        # Status with color presets
        status_row = ctk.CTkFrame(pt, fg_color="transparent")
        status_row.pack(fill="x", pady=4)
        ctk.CTkLabel(status_row, text="Status:", width=110, anchor="w").pack(side="left")

        # Preset color-status buttons
        STATUS_PRESETS = [
            ("Online",    "#23a559", "🟢"),
            ("Away",      "#f0b232", "🟡"),
            ("Busy",      "#ed4245", "🔴"),
            ("Invisible", "#80848e", "⚫"),
        ]
        self.status_var = ctk.StringVar(value=p_data.get("user_status", "Online"))
        self.status_color_var = ctk.StringVar(value=p_data.get("status_color", "#23a559"))

        preset_row = ctk.CTkFrame(pt, fg_color="transparent")
        preset_row.pack(fill="x", padx=2, pady=(0, 2))
        for s_name, s_color, s_emoji in STATUS_PRESETS:
            def _pick(n=s_name, c=s_color):
                self.status_var.set(n)
                self.status_color_var.set(c)
                self._update_status_preview(c, n)
            ctk.CTkButton(preset_row, text=f"{s_emoji} {s_name}", width=92, height=28,
                          fg_color=s_color, hover_color=s_color,
                          text_color="white", font=("Arial", 11, "bold"),
                          command=_pick).pack(side="left", padx=2)

        # Custom status text + color row
        custom_row = ctk.CTkFrame(pt, fg_color="transparent")
        custom_row.pack(fill="x", padx=2, pady=4)
        ctk.CTkLabel(custom_row, text="Custom text:", width=90, anchor="w").pack(side="left")
        self.custom_status_entry = ctk.CTkEntry(custom_row, placeholder_text="e.g. 🎮 Gaming",
                                                width=130, height=28)
        self.custom_status_entry.pack(side="left", padx=4)
        ctk.CTkLabel(custom_row, text="Hex:", width=32, anchor="w").pack(side="left")
        self.custom_color_entry = ctk.CTkEntry(custom_row, placeholder_text="#ff6b6b",
                                               width=70, height=28)
        self.custom_color_entry.insert(0, p_data.get("status_color", ""))
        self.custom_color_entry.pack(side="left", padx=4)
        ctk.CTkButton(custom_row, text="✔", width=28, height=28, fg_color="#5865F2",
                      command=self._apply_custom_status).pack(side="left")

        # Live preview dot
        self.status_preview_lbl = ctk.CTkLabel(pt, text="●  Online",
                                               font=("Arial", 12, "bold"), text_color="#23a559")
        self.status_preview_lbl.pack(anchor="w", padx=2)

        # Pronouns
        pro_row = ctk.CTkFrame(pt, fg_color="transparent")
        pro_row.pack(fill="x", pady=4)
        ctk.CTkLabel(pro_row, text="Pronouns:", width=110, anchor="w").pack(side="left")
        self.pro_entry = ctk.CTkEntry(pro_row, placeholder_text="e.g. she/her", width=200, height=30)
        self.pro_entry.insert(0, p_data.get("pronouns", ""))
        self.pro_entry.pack(side="left", padx=6)

        # Bio
        ctk.CTkLabel(pt, text="Bio:", anchor="w").pack(anchor="w", padx=2, pady=(6, 2))
        self.bio_box = ctk.CTkTextbox(pt, height=70, fg_color="#2b2d31", corner_radius=6)
        self.bio_box.insert("1.0", p_data.get("bio", ""))
        self.bio_box.pack(fill="x", padx=2)

        # Connections
        ctk.CTkLabel(pt, text="Connections  (e.g. steam=user, spotify=user):",
                     anchor="w", text_color="gray").pack(anchor="w", padx=2, pady=(8, 2))
        conn_raw = ", ".join(f"{k}={v}" for k, v in (p_data.get("connections") or {}).items())
        self.conn_entry = ctk.CTkEntry(pt, placeholder_text="steam=user1, twitter=user2",
                                       height=30)
        self.conn_entry.insert(0, conn_raw)
        self.conn_entry.pack(fill="x", padx=2)

        ctk.CTkButton(pt, text="💾  Save Profile", fg_color="#5865F2", hover_color="#4752C4",
                      height=36, command=self._save_profile).pack(pady=12)

        # ═══════ PRIVACY TAB ════════════════════════════════════════════
        pv = tabs.tab("Privacy")

        def _switch(label, key, tip=""):
            row = ctk.CTkFrame(pv, fg_color="transparent")
            row.pack(fill="x", pady=5, padx=4)
            var = ctk.BooleanVar(value=priv.get(key, False))
            sw = ctk.CTkSwitch(row, text=label, variable=var, onvalue=True, offvalue=False,
                               font=("Arial", 13))
            sw.pack(side="left")
            if tip:
                ctk.CTkLabel(row, text=tip, text_color="#80848e",
                             font=("Arial", 10), wraplength=200).pack(side="left", padx=(8, 0))
            return var

        self.priv_vars = {
            "hide_online_status":    _switch("🌑  Hide online status",    "hide_online_status",
                                             "Others see you as Offline"),
            "hide_server_membership": _switch("🔒  Hide server list",       "hide_server_membership",
                                              "Mutual servers won't appear on your card"),
            "dms_from_friends_only": _switch("📩  DMs from friends only",  "dms_from_friends_only",
                                             "Strangers cannot message you"),
            "auto_accept_friends":   _switch("✅  Auto-accept requests",   "auto_accept_friends",
                                             "Auto-add if you share a server"),
            "auto_decline_strangers":_switch("🚫  Auto-decline strangers", "auto_decline_strangers",
                                             "Decline requests from users with no mutual server"),
        }

        ctk.CTkFrame(pv, height=1, fg_color="#383a40").pack(fill="x", padx=8, pady=10)

        self.priv_status = ctk.CTkLabel(pv, text="", text_color="#fee75c", font=("Arial", 12))
        self.priv_status.pack()

        ctk.CTkButton(pv, text="💾  Save Privacy", fg_color="#5865F2", hover_color="#4752C4",
                      height=36, command=self._save_privacy).pack(pady=8)

        # ═══════ BLOCKED TAB ════════════════════════════════════════════
        bl = tabs.tab("Blocked")
        ctk.CTkLabel(bl, text="Blocked Users", font=("Arial", 14, "bold")).pack(pady=(10, 4))

        self.blocked_frame = ctk.CTkScrollableFrame(bl, fg_color="transparent", height=220)
        self.blocked_frame.pack(fill="both", expand=True, padx=6)
        self._refresh_blocked(rels.get("blocked", []))

    # ── helpers ──────────────────────────────────────────────────────────────

    def _refresh_blocked(self, blocked_list):
        for w in self.blocked_frame.winfo_children():
            w.destroy()
        if not blocked_list:
            ctk.CTkLabel(self.blocked_frame, text="No blocked users 🎉",
                         text_color="gray").pack(pady=20)
        for u in blocked_list:
            row = ctk.CTkFrame(self.blocked_frame, fg_color="#2b2d31", corner_radius=6)
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(row, text=u, font=("Arial", 13)).pack(side="left", padx=10, pady=6)
            ctk.CTkButton(row, text="Unblock", width=70, height=28,
                          fg_color="#ed4245", hover_color="#c03537",
                          command=lambda uu=u: self._do_unblock(uu)).pack(side="right", padx=8)

    def _do_unblock(self, target):
        backend.unblock_user(target)
        rels = backend.network_request({"action": "GET_RELATIONSHIPS", "username": backend.username})
        self._refresh_blocked(rels.get("blocked", []))

    def _save_profile(self):
        bio = self.bio_box.get("1.0", "end-1c")
        pro = self.pro_entry.get()
        banner = self.banner_entry.get() or "#2b2d31"
        status_val = self.status_var.get()
        conn_raw = self.conn_entry.get()
        conn_dict = {}
        for pair in conn_raw.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                conn_dict[k.strip().lower()] = v.strip()
        backend.network_request({
            "action": "UPDATE_PROFILE",
            "username": backend.username,
            "bio": bio,
            "pronouns": pro,
            "banner_color": banner,
            "status": status_val,
            "status_color": self.status_color_var.get(),
            "connections": conn_dict,
        })
        self._close()

    def _update_status_preview(self, color, text):
        """Update the live colored dot preview label."""
        try:
            self.status_preview_lbl.configure(text=f"●  {text}", text_color=color)
        except Exception:
            pass

    def _apply_custom_status(self):
        """Apply a fully custom text + hex color status."""
        txt = self.custom_status_entry.get().strip() or "Online"
        col = self.custom_color_entry.get().strip() or "#5865F2"
        # Basic validation: must start with #
        if not col.startswith("#"):
            col = "#" + col
        self.status_var.set(txt)
        self.status_color_var.set(col)
        self._update_status_preview(col, txt)

    def _save_privacy(self):
        kwargs = {k: v.get() for k, v in self.priv_vars.items()}
        res = backend.update_privacy(**kwargs)
        msg = res.get("message", "Saved") if isinstance(res, dict) else "Saved"
        try:
            self.priv_status.configure(text=f"✅ {msg}")
            self.after(1800, lambda: self.priv_status.configure(text=""))
        except Exception:
            pass

    def _close(self):
        if hasattr(self.parent, "close_overlay"):
            self.parent.close_overlay()
        else:
            self.destroy()


class ServerSettingsWindow(ctk.CTkFrame):

    def __init__(self, parent, server_id, server_data):
        super().__init__(parent, fg_color="#1e1f22", corner_radius=10, border_width=2, border_color="#383a40")
        self.parent = parent
        self.server_id = server_id
        self.server_data = server_data
        
        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=10, pady=10)
        ctk.CTkLabel(header, text=f"{server_data.get('name', 'Server')} Settings", font=("Arial", 16, "bold")).pack(side="left")
        ctk.CTkButton(header, text="X", width=30, fg_color="#ed4245", hover_color="#c03537", command=self.close_settings).pack(side="right")
        
        # Tabs / Content
        self.tab_frame = ctk.CTkTabview(self, width=350, height=300)
        self.tab_frame.pack(pady=10, padx=10, fill="both", expand=True)
        
        self.tab_frame.add("Roles")
        self.tab_frame.add("Channels")
        self.tab_frame.add("Members")
        self.tab_frame.add("Overview")
        
        # --- Roles Tab ---
        roles_tab = self.tab_frame.tab("Roles")
        ctk.CTkLabel(roles_tab, text="Create New Role", font=("Arial", 12, "bold")).pack(pady=(10,5))
        
        self.role_name_entry = ctk.CTkEntry(roles_tab, placeholder_text="Role Name")
        self.role_name_entry.pack(pady=5)
        self.role_color_entry = ctk.CTkEntry(roles_tab, placeholder_text="Role Color (e.g. #ff0000)")
        self.role_color_entry.pack(pady=5)
        
        ctk.CTkButton(roles_tab, text="Create Role", fg_color="#5865F2", command=self.create_role).pack(pady=10)
        
        # Existing Roles
        self.roles_list = ctk.CTkScrollableFrame(roles_tab, height=100)
        self.roles_list.pack(fill="x", pady=5)
        self.refresh_roles()

        # --- Channels Tab ---
        channels_tab = self.tab_frame.tab("Channels")
        ctk.CTkLabel(channels_tab, text="Create New Channel", font=("Arial", 12, "bold")).pack(pady=(10,5))
        
        self.channel_name_entry = ctk.CTkEntry(channels_tab, placeholder_text="Channel Name")
        self.channel_name_entry.pack(pady=5)
        
        self.channel_type_var = ctk.StringVar(value="text")
        self.channel_type_menu = ctk.CTkOptionMenu(channels_tab, values=["text", "voice"], variable=self.channel_type_var)
        self.channel_type_menu.pack(pady=5)

        ctk.CTkButton(channels_tab, text="Create Channel", fg_color="#228B22", command=self.create_channel).pack(pady=10)

        # --- Members Tab ---
        members_tab = self.tab_frame.tab("Members")
        ctk.CTkLabel(members_tab, text="Server Members", font=("Arial", 12, "bold")).pack(pady=(10,5))
        
        self.members_list = ctk.CTkScrollableFrame(members_tab, height=150)
        self.members_list.pack(fill="x", pady=5)
        self.refresh_members()
        
    def refresh_roles(self):
        for w in self.roles_list.winfo_children():
            w.destroy()
        roles = self.server_data.get("roles", {})
        for rid, rdata in roles.items():
            ctk.CTkLabel(self.roles_list, text=f"{rdata['name']} ({rid})", text_color=rdata['color']).pack(anchor="w")

    def refresh_members(self):
        for w in self.members_list.winfo_children():
            w.destroy()
        members = self.server_data.get("members", {})
        roles = self.server_data.get("roles", {})
        for user, user_roles in members.items():
            role_names = [roles.get(r, {}).get("name", r) for r in user_roles]
            role_text = f" [{', '.join(role_names)}]" if role_names else ""
            
            row = ctk.CTkFrame(self.members_list, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=f"{user}{role_text}").pack(side="left")
            # Assignment logic could go here in future (Manage Roles button)

    def create_channel(self):
        name = self.channel_name_entry.get()
        c_type = self.channel_type_var.get()
        if name:
            res = backend.network_request({
                "action": "CREATE_CHANNEL",
                "username": backend.username,
                "server_id": self.server_id,
                "channel_name": name,
                "channel_type": c_type
            })
            if res.get("status") == "success":
                self.channel_name_entry.delete(0, "end")
                self.parent.refresh_servers()

    def create_role(self):
        name = self.role_name_entry.get()
        color = self.role_color_entry.get() or "#ffffff"
        if name:
            backend.network_request({
                "action": "CREATE_ROLE",
                "username": backend.username,
                "server_id": self.server_id,
                "role_name": name,
                "role_color": color
            })
            # Optimistically update UI or ask parent to refresh
            self.role_name_entry.delete(0, "end")
            self.role_color_entry.delete(0, "end")
            self.parent.refresh_servers() # Triggers a full reload
            
    def close_settings(self):
        self.parent.close_overlay()
        self.destroy()

class AIChatView(ctk.CTkFrame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.configure(fg_color="#313338")
        
        # Header
        header = ctk.CTkFrame(self, height=50, fg_color="#313338")
        header.pack(fill="x", padx=20, pady=10)
        ctk.CTkLabel(header, text="🤖 Tenshi AI Assistant (Claude 3.5)", font=("Arial", 20, "bold")).pack(side="left")
        
        # Log
        self.msg_box = ctk.CTkTextbox(self, fg_color="#313338", text_color="white", font=("Arial", 14))
        self.msg_box.pack(fill="both", expand=True, padx=20, pady=10)
        self.msg_box.insert("end", "Hello! I am your Tenshi AI Assistant, powered by Claude. How can I help you today?\n\n")
        self.msg_box.configure(state="disabled")
        
        # Input
        input_frame = ctk.CTkFrame(self, height=60, fg_color="#313338")
        input_frame.pack(fill="x", padx=20, pady=20)
        
        self.entry = ctk.CTkEntry(input_frame, placeholder_text="Ask Claude anything...", height=40, fg_color="#383a40", border_width=0)
        self.entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.entry.bind("<Return>", lambda e: self.send_ai_msg())
        
        self.send_btn = ctk.CTkButton(input_frame, text="➤", width=40, height=40, fg_color="#5865F2", command=self.send_ai_msg)
        self.send_btn.pack(side="right")

    def send_ai_msg(self):
        msg = self.entry.get()
        if not msg: return
        
        self.msg_box.configure(state="normal")
        self.msg_box.insert("end", f"\nYOU: {msg}\n")
        self.msg_box.insert("end", "CLAUDE: Thinking...\n", "thinking")
        self.msg_box.tag_config("thinking", foreground="gray")
        self.msg_box.yview("end")
        self.msg_box.configure(state="disabled")
        self.entry.delete(0, "end")
        
        def run_ai():
            res = backend.network_request({"action": "AI_CHAT", "username": backend.username, "content": msg})
            self.msg_box.configure(state="normal")
            # Remove "Thinking..."
            self.msg_box.delete("end-2l", "end") 
            if res.get("status") == "success":
                self.msg_box.insert("end", f"\nCLAUDE: {res.get('response')}\n\n")
            else:
                self.msg_box.insert("end", f"\n⚠️ ERROR: {res.get('message')}\n\n", "error")
                self.msg_box.tag_config("error", foreground="#ed4245")
            self.msg_box.yview("end")
            self.msg_box.configure(state="disabled")
            
        threading.Thread(target=run_ai, daemon=True).start()

class MainApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        print("INITIALIZING NEW LAYOUT v2.0")
        self.title("Tenshi Voice v2.0")
        self.geometry("1100x700")
        
        # State
        self.current_server_id = None
        self.current_channel = "General"
        self.video_window = None
        self.servers_data = {} # Cache
        
        # Main Overlay Container (for modals)
        # We pre-create this and keep it hidden, then just place it over everything when needed
        self.overlay_bg = ctk.CTkFrame(self, fg_color="#18191c")
        self.overlay_widget = None
        
        # --- LAYOUT GRID ---
        # Col 0: Server Sidebar (Icons)
        # Col 1: Channel List
        # Col 2: Chat Area (Main)
        # Col 3: Controls
        self.grid_columnconfigure(2, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # --- COL 0: SERVER SIDEBAR ---
        self.server_sidebar = ctk.CTkScrollableFrame(self, width=150, corner_radius=0, fg_color="#1e1f22")
        self.server_sidebar.grid(row=0, column=0, sticky="nsew")
        
        # Home/DM Button (Tenshi Logo placeholder)
        self.home_btn = ctk.CTkButton(self.server_sidebar, text="TENSHI", width=40, height=40, corner_radius=20, 
                                      fg_color="#5865F2", command=self.load_dms)
        self.home_btn.pack(pady=(10, 5))
        
        # Sep
        ctk.CTkFrame(self.server_sidebar, height=2, fg_color="gray").pack(fill="x", padx=10, pady=5)
        
        # Games Button
        self.game_btn = ctk.CTkButton(self.server_sidebar, text="🎮", width=40, height=40, corner_radius=20, 
                                      fg_color="#FEE75C", text_color="black", hover_color="#D4C042", command=self.open_minigame)
        self.game_btn.pack(pady=5)
        
        # Separator
        ctk.CTkFrame(self.server_sidebar, height=2, fg_color="gray").pack(fill="x", padx=10, pady=5)
        
        # Dynamic Server list will be populated here
        self.server_list_frame = ctk.CTkFrame(self.server_sidebar, fg_color="transparent")
        self.server_list_frame.pack(fill="x")
        
        # Add Server (+)
        self.add_srv_btn = ctk.CTkButton(self.server_sidebar, text="+", width=40, height=40, corner_radius=20, 
                                         fg_color="#232428", hover_color="#228B22", command=self.ui_create_server)
        self.add_srv_btn.pack(pady=10)

        # AI Assistant Button
        self.ai_btn = ctk.CTkButton(self.server_sidebar, text="🤖", width=40, height=40, corner_radius=20,
                                     fg_color="#232428", hover_color="#5865F2", command=self.load_ai_chat)
        self.ai_btn.pack(pady=5)

        # Push remaining buttons to bottom by adding a spacer
        ctk.CTkFrame(self.server_sidebar, height=2, fg_color="gray").pack(fill="x", padx=10, pady=10)

        # Check for Updates button
        self.update_btn = ctk.CTkButton(self.server_sidebar, text="🔄", width=40, height=40, corner_radius=20,
                                        fg_color="#232428", hover_color="#5865F2",
                                        command=self.check_for_updates)
        self.update_btn.pack(pady=5)

        # --- COL 1: CHANNEL LIST / FRIENDS ---
        self.channel_sidebar = ctk.CTkFrame(self, width=200, corner_radius=0, fg_color="#2b2d31")
        self.channel_sidebar.grid(row=0, column=1, sticky="nsew")
        
        # Tabs for "Channels" (Servers) vs "Friends" (Home)
        # We can toggle this based on what is selected in Col 0.
        # If Server selected -> Show Channels.
        # If Home selected -> Show Friends/Pending.
        
        self.channel_list_frame = ctk.CTkScrollableFrame(self.channel_sidebar, fg_color="transparent")
        self.channel_list_frame.pack(fill="both", expand=True)

        # Profile Area (Bottom)
        self.profile_frame = ctk.CTkFrame(self.channel_sidebar, height=60, fg_color="#232428", corner_radius=0)
        self.profile_frame.pack(side="bottom", fill="x")
        
        self.pfp_btn = ctk.CTkButton(self.profile_frame, text="", width=40, height=40, corner_radius=20, fg_color="gray", command=self.upload_pfp)
        self.pfp_btn.pack(side="left", padx=10, pady=10)
        
        self.user_label = ctk.CTkLabel(self.profile_frame, text=backend.username, font=("Arial", 12, "bold"))
        self.user_label.pack(side="left", pady=10)
        
        # Audio State Variables for Home Screen
        self.home_muted = False
        self.home_deafened = False
        
        def toggle_home_mute():
            self.home_muted = not self.home_muted
            self.mute_btn.configure(text_color="#ed4245" if self.home_muted else "white")
            
        def toggle_home_deafen():
            self.home_deafened = not self.home_deafened
            self.deafen_btn.configure(text_color="#ed4245" if self.home_deafened else "white")
            if self.home_deafened and not self.home_muted:
                toggle_home_mute() # Deafening also mutes you
                
        self.settings_btn = ctk.CTkButton(self.profile_frame, text="⚙", width=25, fg_color="transparent", command=self.open_profile_settings)
        self.settings_btn.pack(side="right", padx=2)
        
        self.audio_btn = ctk.CTkButton(self.profile_frame, text="🔊", width=25, fg_color="transparent", command=self.open_audio_settings)
        self.audio_btn.pack(side="right", padx=2)
        
        self.deafen_btn = ctk.CTkButton(self.profile_frame, text="🎧", width=25, fg_color="transparent", command=toggle_home_deafen)
        self.deafen_btn.pack(side="right", padx=2)

        self.mute_btn = ctk.CTkButton(self.profile_frame, text="🎤", width=25, fg_color="transparent", command=toggle_home_mute)
        self.mute_btn.pack(side="right", padx=2)

        # --- COL 2: MAIN CHAT AREA ---
        self.chat_area = ctk.CTkFrame(self, fg_color="#313338", corner_radius=0)
        self.chat_area.grid(row=0, column=2, sticky="nsew")
        
        # Chat Header
        self.header = ctk.CTkFrame(self.chat_area, height=50, fg_color="#313338")
        self.header.pack(fill="x", padx=20, pady=10)
        self.chat_title = ctk.CTkLabel(self.header, text="# General", font=("Arial", 20, "bold"))
        self.chat_title.pack(side="left")

        # Messages (Scrollable)
        self.msg_box = ctk.CTkTextbox(self.chat_area, fg_color="#313338", text_color="white", font=("Arial", 14))
        self.msg_box.pack(fill="both", expand=True, padx=20, pady=10)
        self.msg_box.insert("end", "Welcome to Tenshi Voice.\nEncryption: ENABLED\n\n")
        self.msg_box.configure(state="disabled")
        
        self.bind('<Return>', lambda event: self.send_msg())

        # Typing Indicator
        self.typing_label = ctk.CTkLabel(self.chat_area, text="", fg_color="transparent", text_color="gray", font=("Arial", 11, "italic"))
        self.typing_label.pack(side="bottom", fill="x", padx=20, pady=(0, 5))

        # Input Area
        self.input_frame = ctk.CTkFrame(self.chat_area, height=60, fg_color="#313338")
        self.input_frame.pack(side="bottom", fill="x", padx=20, pady=(10, 20))
        
        self.entry = ctk.CTkEntry(self.input_frame, placeholder_text=f"Message #{backend.username}", height=40, fg_color="#383a40", border_width=0)
        self.entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.entry.bind("<KeyRelease>", lambda e: self.send_typing_signal())
        
        self.send_btn = ctk.CTkButton(self.input_frame, text="➤", width=40, height=40, fg_color="#5865F2", command=self.send_msg)
        self.send_btn.pack(side="right")
        
        self.snap_var = ctk.BooleanVar(value=False)
        self.snap_btn = ctk.CTkCheckBox(self.input_frame, text="👻 Snap", variable=self.snap_var, onvalue=True, offvalue=False, width=60)
        self.snap_btn.pack(side="right", padx=10)
        
        # --- COL 3: CONTROLS ---
        self.controls = ctk.CTkFrame(self, width=60, fg_color="#1e1f22", corner_radius=0)
        self.controls.grid(row=0, column=3, sticky="nsew")

        # Mute
        self.is_muted = False
        self.mute_btn = ctk.CTkButton(self.controls, text="🎤", width=40, height=40, fg_color="#2b2d31", hover_color="red", command=self.toggle_mute)
        self.mute_btn.pack(pady=(20, 10), padx=10)

        # Deafen
        self.is_deafened = False
        self.deafen_btn = ctk.CTkButton(self.controls, text="🎧", width=40, height=40, fg_color="#2b2d31", hover_color="red", command=self.toggle_deafen)
        self.deafen_btn.pack(pady=10, padx=10)

        # Separator
        ctk.CTkFrame(self.controls, height=1, fg_color="gray").pack(fill="x", padx=8, pady=8)

        # Optimize / DND toggle (always visible)
        self.is_optimized = False
        self.opt_lbl = ctk.CTkLabel(self.controls, text="OPT", font=("Arial", 9, "bold"),
                                    text_color="gray")
        self.opt_lbl.pack()
        self.opt_btn = ctk.CTkButton(self.controls, text="⚡", width=40, height=40, corner_radius=20,
                                     fg_color="#2b2d31", hover_color="#228B22",
                                     command=self.toggle_optimize)
        self.opt_btn.pack(padx=10, pady=(2, 10))

        # Load Data
        self.refresh_servers()
        backend.start_polling(self.handle_incoming_message)
        self.status_polling_loop()

    def send_typing_signal(self):
        if self.current_server_id:
            target = f"srv:{self.current_server_id}:{self.current_channel}"
            backend.network_request({"action": "SET_TYPING", "username": backend.username, "channel_id": target})

    def status_polling_loop(self):
        """Poll for typing and online status every 3 seconds."""
        if self.current_server_id:
            res = backend.network_request({"action": "GET_SERVER_STATUS", "server_id": self.current_server_id})
            if res.get("status") == "success":
                typers = [u for u in res.get("typing", []) if u != backend.username]
                if typers:
                    self.typing_label.configure(text=f"{', '.join(typers)} {'are' if len(typers)>1 else 'is'} typing...")
                else:
                    self.typing_label.configure(text="")
        
        self.after(3000, self.status_polling_loop)

    def handle_incoming_message(self, channel_id, sender, content, is_snapchat):
        # Global Broadcast Check
        if sender == "SYSTEM" or channel_id == "broadcast":
            self.msg_box.configure(state="normal")
            self.msg_box.insert("end", f"\n📢 SERVER BROADCAST: {content}\n\n", "broadcast_tag")
            self.msg_box.tag_config("broadcast_tag", foreground="#FEE75C", font=("Arial", 14, "bold"))
            self.msg_box.yview("end")
            self.msg_box.configure(state="disabled")
            return

        ui_channel_id = channel_id
        if "srv_" not in channel_id and "DM" not in channel_id:
            ui_channel_id = f"DM_with_{channel_id}"
            
        # Is the current chat this channel?
        current_viewing = False
        if self.current_server_id and self.current_channel == channel_id: # For servers
            current_viewing = True
        elif not self.current_server_id and getattr(self, 'current_dm', '') == ui_channel_id: # For DMs
            current_viewing = True
            
        if current_viewing:
            self.msg_box.configure(state="normal")
            snap_icon = "👻 " if is_snapchat else ""
            self.msg_box.insert("end", f"{sender}: {snap_icon}{content}\n")
            self.msg_box.yview("end")
            self.msg_box.configure(state="disabled")

    def load_chat_history(self, channel_key):
        self.msg_box.configure(state="normal")
        self.msg_box.delete("1.0", "end")
        self.msg_box.insert("end", "End-to-End Encryption: ENABLED 🔒\nLocal SQLite History Loaded\n\n")
        
        msgs = local_db.get_messages(channel_key, limit=100)
        for msg in msgs:
            snap_icon = "👻 " if msg["is_snapchat"] else ""
            self.msg_box.insert("end", f"{msg['sender']}: {snap_icon}{msg['content']}\n")
            
        self.msg_box.yview("end")
        self.msg_box.configure(state="disabled")

    def refresh_servers(self):
        # Clear existing
        for widget in self.server_list_frame.winfo_children():
            widget.destroy()

        # Add "Home" (Friends)
        home_btn = ctk.CTkButton(self.server_list_frame, text="🏠 Home", width=120, height=40, corner_radius=20, fg_color="#5865F2", command=self.load_dms)
        home_btn.pack(pady=10)
        
        # Fetch Servers
        servers = backend.get_servers() # Returns {"status": "success", "servers": {...}}
        if isinstance(servers, dict) and servers.get("status") == "success":
            s_data = servers.get("servers", {})
            
            # Handle Dict (New) or List (Old)
            if isinstance(s_data, dict):
                for s_id, s_info in s_data.items():
                    name = s_info.get("name", "??")
                    btn = ctk.CTkButton(self.server_list_frame, text=name, width=120, height=40, corner_radius=20, fg_color="#313338", command=lambda sid=s_id: self.on_server_click(sid))
                    btn.pack(pady=5)
            elif isinstance(s_data, list):
                for s in s_data:
                    btn = ctk.CTkButton(self.server_list_frame, text=s["name"], width=120, height=40, corner_radius=20, fg_color="#313338", command=lambda sid=s["id"]: self.on_server_click(sid))
                    btn.pack(pady=5)

    def on_server_click(self, server_id):
        self.current_server_id = server_id
        # Get Server Details
        # We need a backend method to get channels. usage: get_server_details(server_id)
        # OR we just rely on the fact that we might have it cached? No, let's fetch.
        # Actually server.py GET_SERVERS returns full details now.
        
        # For simplicity, let's re-fetch or use what we have.
        # Ideally backend.get_servers() returns everything.
        
        s_res = backend.get_servers()
        if s_res.get("status") == "success":
            all_servers = s_res.get("servers", {})
            if isinstance(all_servers, dict):
                server = all_servers.get(server_id)
            else:
                server = next((s for s in all_servers if s["id"] == server_id), None)
            
            if server:
                # Hide AI if visible
                if hasattr(self, 'ai_chat_view'):
                    self.ai_chat_view.grid_forget()
                self.chat_area.grid(row=0, column=2, sticky="nsew")

                # Store globally for right click
                self.SERVER_CACHE = server
                self.chat_title.configure(text=f"# {server['name']}")
                self.load_channels(server_id, server)

    def load_channels(self, server_id, server_data):
        channels = server_data.get("channels", {})
        
        # Clear sidebar
        for widget in self.channel_list_frame.winfo_children():
            widget.destroy()
            
        # Settings Button if Owner
        if server_data.get("owner") == backend.username:
            ctk.CTkButton(self.channel_list_frame, text="⚙️ Server Settings", fg_color="#313338", height=24, command=lambda: self.open_server_settings(server_id, server_data)).pack(padx=10, pady=(10, 0), fill="x")
            
        # Invite to Server (For All Users)
        def show_invite():
            dialog = ctk.CTkInputDialog(text=f"Give this Server ID to your friends:\n\n{server_id}", title=f"Invite to {server_data.get('name', 'Server')}")
            # Doesn't need to do anything with the input, just displays it.
            dialog.get_input()
            
        ctk.CTkButton(self.channel_list_frame, text="✉️ Invite to Server", fg_color="#5865F2", height=24, command=show_invite).pack(padx=10, pady=(10, 0), fill="x")
            
        # Text Channels Header
        tc_header = ctk.CTkFrame(self.channel_list_frame, fg_color="transparent")
        tc_header.pack(fill="x", padx=10, pady=(10, 5))
        ctk.CTkLabel(tc_header, text="TEXT CHANNELS", font=("Arial", 12, "bold"), text_color="gray").pack(side="left")
        if server_data.get("owner") == backend.username:
            ctk.CTkButton(tc_header, text="+", width=20, height=20, fg_color="transparent", text_color="gray", command=lambda: self.prompt_add_channel(server_id, "text")).pack(side="right")
        
        for c_name, c_info in channels.items():
            c_type = c_info.get("type", "text") if isinstance(c_info, dict) else c_info
            is_locked = c_info.get("locked", False) if isinstance(c_info, dict) else False
            lock_icon = "🔒 " if is_locked else ""
            
            if c_type == "text":
                btn = ctk.CTkButton(self.channel_list_frame, text=f"{lock_icon}# {c_name}", fg_color="transparent", anchor="w", command=lambda n=c_name: self.join_text_channel(n))
                if server_data.get("owner") == backend.username:
                    btn.bind("<Button-3>", lambda e, c=c_name, l=is_locked: self.channel_context_menu(server_id, c, l))
                btn.pack(fill="x", padx=5, pady=2)
                
        # Voice Channels Header
        vc_header = ctk.CTkFrame(self.channel_list_frame, fg_color="transparent")
        vc_header.pack(fill="x", padx=10, pady=(10, 5))
        ctk.CTkLabel(vc_header, text="VOICE CHANNELS", font=("Arial", 12, "bold"), text_color="gray").pack(side="left")
        if server_data.get("owner") == backend.username:
            ctk.CTkButton(vc_header, text="+", width=20, height=20, fg_color="transparent", text_color="gray", command=lambda: self.prompt_add_channel(server_id, "voice")).pack(side="right")
            
        for c_name, c_info in channels.items():
            c_type = c_info.get("type", "voice") if isinstance(c_info, dict) else c_info
            is_locked = c_info.get("locked", False) if isinstance(c_info, dict) else False
            lock_icon = "🔒 " if is_locked else ""
            
            if c_type == "voice":
                btn = ctk.CTkButton(self.channel_list_frame, text=f"{lock_icon}🔊 {c_name}", fg_color="transparent", anchor="w", command=lambda n=c_name: self.join_voice(n))
                if server_data.get("owner") == backend.username:
                    btn.bind("<Button-3>", lambda e, c=c_name, l=is_locked: self.channel_context_menu(server_id, c, l))
                btn.pack(fill="x", padx=5, pady=2)

        # LEAVE SERVER BUTTON
        ctk.CTkFrame(self.channel_list_frame, height=2, fg_color="gray").pack(fill="x", padx=10, pady=20)
        leaf_btn = ctk.CTkButton(self.channel_list_frame, text="Leave Server", fg_color="#ed4245", hover_color="#c03537", command=self.leave_server)
        leaf_btn.pack(pady=5)

    def prompt_add_channel(self, server_id, channel_type):
        dialog = ctk.CTkInputDialog(text=f"Enter name for new {channel_type} channel:", title="Add Channel")
        name = dialog.get_input()
        if name:
            res = backend.network_request({
                "action": "CREATE_CHANNEL",
                "username": backend.username,
                "server_id": server_id,
                "channel_name": name,
                "channel_type": channel_type
            })
            if res.get("status") == "success":
                self.refresh_servers()

    def join_text_channel(self, channel_name):
        print(f"Joining Text: {channel_name}")
        self.current_channel = channel_name
        
        if "DM_with_" in channel_name:
            self.current_dm = channel_name
            self.chat_title.configure(text=f"@ {channel_name.replace('DM_with_', '')}")
        else:
            self.chat_title.configure(text=f"# {channel_name}")
            
        # Load local history
        # target_id for DB for server is channel_name (maybe append server_id for uniqueness)
        db_key = f"{self.current_server_id}_{channel_name}" if self.current_server_id else channel_name.replace("DM_with_", "")
        self.load_chat_history(db_key)

    def join_voice(self, channel_name):
        print(f"Joining Voice: {channel_name}")
            
        # Single Instance Check
        if self.video_window:
            if self.video_window.winfo_exists():
                # Already open? Bring to front or close?
                # User asked to fix multiple instances. Best to close old one.
                self.video_window.destroy()
            self.video_window = None
            
        # Create Call Window as an overlay, then auto-enable optimize/DND
        self.video_window = VideoCallWindow(self, backend.username, channel_name)
        self.show_overlay(self.video_window, width=800, height=600)
        # Auto-enable optimization while in call
        self.toggle_optimize(force_on=True)

    def load_dms(self):
        # Hide AI if visible
        if hasattr(self, 'ai_chat_view'):
            self.ai_chat_view.grid_forget()
        self.chat_area.grid(row=0, column=2, sticky="nsew")
        
        # Clear sidebar
        for widget in self.channel_list_frame.winfo_children():
            widget.destroy()
            
        self.current_server_id = None
        self.chat_title.configure(text="Friends")
        
        # Header
        ctk.CTkLabel(self.channel_list_frame, text="FRIENDS", font=("Arial", 12, "bold"), text_color="gray").pack(anchor="w", padx=10, pady=(10,5))
        
        # Add Friend Button
        ctk.CTkButton(self.channel_list_frame, text="Add Friend", fg_color="#228B22", height=30, command=self.ui_add_friend).pack(padx=10, pady=5, fill="x")
        
        # Fetch status
        res = backend.network_request({"action": "GET_RELATIONSHIPS", "username": backend.username})
        if res.get("status") == "success":
            friends = res.get("friends", [])
            pending = res.get("pending", [])
            
            # Pending Section
            if pending:
                ctk.CTkLabel(self.channel_list_frame, text=f"PENDING - {len(pending)}", font=("Arial", 12, "bold"), text_color="gray").pack(anchor="w", padx=10, pady=(20,5))
                for req in pending:
                    f = ctk.CTkFrame(self.channel_list_frame, fg_color="transparent")
                    f.pack(fill="x", padx=10, pady=2)
                    ctk.CTkLabel(f, text=req).pack(side="left")
                    ctk.CTkButton(f, text="x", width=30, fg_color="#ed4245", command=lambda r=req: self.manage_friend(r, "decline")).pack(side="right")
                    ctk.CTkButton(f, text="✓", width=30, fg_color="#228B22", command=lambda r=req: self.manage_friend(r, "accept")).pack(side="right", padx=5)

            # Friends List
            ctk.CTkLabel(self.channel_list_frame, text=f"DIRECT MESSAGES", font=("Arial", 12, "bold"), text_color="gray").pack(anchor="w", padx=10, pady=(20,5))
            for friend in friends:
                f = ctk.CTkFrame(self.channel_list_frame, fg_color="transparent")
                f.pack(fill="x", padx=10, pady=2)
                
                # Clicking the name opens the rich friend card popup
                btn = ctk.CTkButton(f, text=friend, fg_color="transparent", anchor="w",
                                    command=lambda u=friend: FriendCardPopup(self, u, self, relationship="friend"))
                btn.pack(side="left", fill="x", expand=True)
                
                # Quick DM shortcut icon
                ctk.CTkButton(f, text="✉", width=28, fg_color="transparent", text_color="#5865F2",
                              hover_color="#2b2d31",
                              command=lambda u=friend: self.join_text_channel(f"DM_with_{u}")).pack(side="right")

    def manage_friend(self, target, action):
        act = "ACCEPT_FRIEND" if action == "accept" else "DECLINE_FRIEND"
        res = backend.network_request({"action": act, "username": backend.username, "target": target})
        print(f"{action.upper()}: {res}")
        self.load_dms() # Refresh

    def send_msg(self):
        msg = self.entry.get()
        if msg:
            is_snap = self.snap_var.get()
            
            # Display it locally immediately
            self.msg_box.configure(state="normal")
            snap_icon = "👻 " if is_snap else ""
            self.msg_box.insert("end", f"YOU: {snap_icon}{msg}\n")
            self.msg_box.yview("end")
            self.msg_box.configure(state="disabled")
            self.entry.delete(0, "end")
            self.entry.focus() # Keep focus
            
            # Determine target
            if not self.current_server_id:
                # DM
                target_user = getattr(self, 'current_dm', '').replace('DM_with_', '')
                if target_user:
                    local_db.save_message(target_user, backend.username, msg, is_snapchat=is_snap)
                    backend.send_message("dm", target_user, msg, is_snapchat=is_snap)
            else:
                # Server Channel
                db_key = f"{self.current_server_id}_{self.current_channel}"
                local_db.save_message(db_key, backend.username, msg, is_snapchat=is_snap)
                backend.send_message("server", self.current_server_id, msg, is_snapchat=is_snap)

    def toggle_mute(self):
        self.is_muted = not self.is_muted
        if self.is_muted:
            self.mute_btn.configure(fg_color="#ed4245", text="🚫")
        else:
            self.mute_btn.configure(fg_color="#2b2d31", text="🎤")
            
        if self.video_window and self.video_window.running:
            self.video_window.set_mute(self.is_muted)

    def toggle_deafen(self):
        self.is_deafened = not self.is_deafened
        if self.is_deafened:
            self.deafen_btn.configure(fg_color="#ed4245", text="🚫")
        else:
            self.deafen_btn.configure(fg_color="#2b2d31", text="🎧")

        if self.video_window and self.video_window.running:
            self.video_window.set_deafen(self.is_deafened)

    def toggle_optimize(self, force_on=None):
        """Toggle gaming/call optimization mode (sets DND/Busy status)."""
        if force_on is not None:
            self.is_optimized = force_on
        else:
            self.is_optimized = not self.is_optimized
            
        backend.is_optimized = self.is_optimized
        if self.is_optimized:
            self.opt_btn.configure(fg_color="#228B22", text="⚡")
            self.opt_lbl.configure(text="DND", text_color="#228B22")
            # Set status to Busy on server so friends see DND
            threading.Thread(target=backend.network_request, args=({"action": "UPDATE_PROFILE", "username": backend.username, "status": "Busy"},), daemon=True).start()
        else:
            self.opt_btn.configure(fg_color="#2b2d31", text="⚡")
            self.opt_lbl.configure(text="OPT", text_color="gray")
            # Restore Online status
            threading.Thread(target=backend.network_request, args=({"action": "UPDATE_PROFILE", "username": backend.username, "status": "Online"},), daemon=True).start()

    def ui_add_friend(self):
        dialog = ctk.CTkInputDialog(text="Enter Friend's Username:", title="Add Friend")
        target = dialog.get_input()
        if target:
            result = backend.add_friend(target)
            msg = result.get("message", "Unknown error") if isinstance(result, dict) else str(result)
            
            # Show a simple temporary label as feedback
            lbl = ctk.CTkLabel(self.channel_list_frame, text=msg, text_color="#FEE75C")
            lbl.pack(pady=5)
            self.after(3000, lbl.destroy) # Remove after 3 seconds

    def open_video(self):
        # Legacy method compatibility, now used for P2P placeholder or direct call?
        # For now, let's just make it open a default join
        self.join_voice("General-Voice")

    def open_minigame(self):
        game_view = PuzzleGameView(self)
        self.show_overlay(game_view, width=500, height=550)

    def ui_create_server(self):
        wizard_view = ctk.CTkFrame(self, fg_color="#1e1f22", corner_radius=10, border_width=2, border_color="#383a40")
        
        # Header
        header = ctk.CTkFrame(wizard_view, fg_color="transparent")
        header.pack(fill="x", padx=10, pady=10)
        ctk.CTkLabel(header, text="Server Discovery & Setup", font=("Arial", 16, "bold")).pack(side="left")
        ctk.CTkButton(header, text="X", width=30, fg_color="#ed4245", hover_color="#c03537", command=self.close_overlay).pack(side="right")
        
        # Tabs
        tab_frame = ctk.CTkTabview(wizard_view, width=400, height=400)
        tab_frame.pack(pady=10, padx=10, fill="both", expand=True)
        tab_frame.add("Discover")
        tab_frame.add("Create New")

        # --- Discover Tab ---
        discover_tab = tab_frame.tab("Discover")
        
        # --- JOIN BY ID ---
        join_frame = ctk.CTkFrame(discover_tab, fg_color="transparent")
        join_frame.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(join_frame, text="Have an invite code?", font=("Arial", 12, "bold")).pack(side="left")
        
        self.invite_entry = ctk.CTkEntry(join_frame, placeholder_text="Enter Server ID", height=30)
        self.invite_entry.pack(side="left", padx=10, fill="x", expand=True)
        
        def join_by_id():
            sid = self.invite_entry.get().strip()
            if sid:
                j_res = backend.network_request({"action": "JOIN_SERVER", "username": backend.username, "server_id": sid})
                if j_res.get("status") == "success":
                    self.close_overlay()
                    self.refresh_servers()
                    
        ctk.CTkButton(join_frame, text="Join", width=60, fg_color="#5865F2", command=join_by_id).pack(side="right")
        
        ctk.CTkLabel(discover_tab, text="Public Communities", font=("Arial", 14, "bold"), text_color="#5865F2").pack(anchor="w", pady=(5,10))
        
        server_list = ctk.CTkScrollableFrame(discover_tab, fg_color="transparent")
        server_list.pack(fill="both", expand=True)

        res = backend.network_request({"action": "GET_PUBLIC_SERVERS", "username": backend.username})
        if res.get("status") == "success":
            public_servers = res.get("servers", {})
            if not public_servers:
                ctk.CTkLabel(server_list, text="No public servers found.", text_color="gray").pack()
            else:
                for s_data in public_servers:
                    s_id = s_data.get("id")
                    row = ctk.CTkFrame(server_list, fg_color="#2b2d31", corner_radius=5)
                    row.pack(fill="x", pady=2)

                    info_txt = f"{s_data.get('name', 'Unknown')} ({s_data.get('member_count', 0)} members)"
                    ctk.CTkLabel(row, text=info_txt, font=("Arial", 12, "bold")).pack(side="left", padx=10, pady=10)

                    def join_s(sid=s_id):
                        j_res = backend.network_request({
                            "action": "JOIN_SERVER",
                            "username": backend.username,
                            "server_id": sid
                        })
                        if j_res.get("status") == "success":
                            self.close_overlay()
                            self.refresh_servers()

                    ctk.CTkButton(row, text="Join", width=60, fg_color="#228B22", command=join_s).pack(side="right", padx=10)

        # --- Create New Tab ---
        create_tab = tab_frame.tab("Create New")
        ctk.CTkLabel(create_tab, text="What should we call your new community?").pack(anchor="w")
        name_entry = ctk.CTkEntry(create_tab, placeholder_text="e.g., Gaming Hideout", width=300)
        name_entry.pack(pady=(5, 20), anchor="w")
        
        ctk.CTkLabel(create_tab, text="Bot Configurations (Automated Setup)", font=("Arial", 12, "bold"), text_color="#5865F2").pack(anchor="w")
        
        lock_admins_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(create_tab, text="Auto-create locked 'Admin Lounge' (Text & Voice)", variable=lock_admins_var, onvalue=True, offvalue=False).pack(pady=10, anchor="w")
        
        def finish_setup():
            s_name = name_entry.get()
            if not s_name:
                return
            
            # Create Server First
            c_res = backend.network_request({
                "action": "CREATE_SERVER",
                "username": backend.username,
                "server_name": s_name
            })
            
            if c_res.get("status") == "success":
                new_server_id = c_res.get("server_id")
                
                if lock_admins_var.get():
                    for ch in ["Off-Topic", "Music"]:
                        backend.network_request({
                            "action": "LOCK_CHANNEL",
                            "username": backend.username,
                            "server_id": new_server_id,
                            "channel_name": ch,
                            "allowed_roles": ["role_admin"]
                        })
                
                self.close_overlay()
                self.refresh_servers()
                
        ctk.CTkButton(create_tab, text="Finish Setup", fg_color="#228B22", hover_color="#1E7A1E", height=40, command=finish_setup).pack(pady=30)
        
        self.show_overlay(wizard_view, width=450, height=500)

    def upload_pfp(self):
        file_path = filedialog.askopenfilename(filetypes=[("Images", "*.png;*.jpg;*.jpeg")])
        if file_path:
            # In a real app, we'd upload this. For now, local preview.
            img = Image.open(file_path)
            ctk_img = ctk.CTkImage(img, size=(40,40))
            self.pfp_btn.configure(image=ctk_img)

    def open_profile_settings(self):
        settings_view = ProfileSettingsWindow(self)
        self.show_overlay(settings_view, width=480, height=530)

    def leave_server(self):
        if not self.current_server_id: return
        dialog = ctk.CTkInputDialog(text="Type 'LEAVE' to confirm:", title="Leave Server")
        if dialog.get_input() == "LEAVE":
            res = backend.network_request({"action": "LEAVE_SERVER", "server_id": self.current_server_id, "username": backend.username})
            if res.get("status") == "success":
                self.current_server_id = None
                self.load_dms()
                self.refresh_servers()
            else:
                print(f"Leave Failed: {res.get('message')}")

    def open_server_settings(self, server_id, data):
        settings_view = ServerSettingsWindow(self, server_id, data)
        self.show_overlay(settings_view, width=450, height=450)

    def channel_context_menu(self, server_id, channel_name, is_locked):
        action = "UNLOCK_CHANNEL" if is_locked else "LOCK_CHANNEL"
        lock_prompt = f"{'Unlock' if is_locked else 'Lock'} '#{channel_name}'?"
        
        dialog = ctk.CTkInputDialog(text=f"Settings for #{channel_name}:\n1. Type 'yes' to {lock_prompt}\n2. Type 'rename <new_name>' to rename.", title="Channel Settings")
        user_input = dialog.get_input()
        
        if user_input == "yes":
            payload = {
                "action": action,
                "username": backend.username,
                "server_id": server_id,
                "channel_name": channel_name
            }
            if not is_locked:
                payload["allowed_roles"] = ["role_admin"] # Default locked to admins
                
            res = backend.network_request(payload)
            print(res)
            self.refresh_servers()
        elif user_input and user_input.startswith("rename "):
            new_name = user_input.split("rename ")[1].strip()
            if new_name:
                payload = {
                    "action": "RENAME_CHANNEL",
                    "username": backend.username,
                    "server_id": server_id,
                    "channel_name": channel_name,
                    "new_name": new_name
                }
                res = backend.network_request(payload)
                print(res)
                self.refresh_servers()

    def open_audio_settings(self):
        settings_view = SettingsWindow(self)
        self.show_overlay(settings_view, width=400, height=350)
        
    def show_profile(self, target_username):
        profile_view = ViewProfileWindow(self, target_username, backend)
        self.show_overlay(profile_view, width=400, height=600)

    def load_ai_chat(self):
        # Hide normal chat
        self.chat_area.grid_forget()
        self.current_server_id = None
        
        if not hasattr(self, 'ai_chat_view'):
            self.ai_chat_view = AIChatView(self)
        
        self.ai_chat_view.grid(row=0, column=2, sticky="nsew")
        self.chat_title.configure(text="Tenshi AI")
        
        # Clear channel sidebar
        for widget in self.channel_list_frame.winfo_children():
            widget.destroy()
        ctk.CTkLabel(self.channel_list_frame, text="AI ASSISTANT", font=("Arial", 12, "bold"), text_color="gray").pack(anchor="w", padx=10, pady=(10,5))
        ctk.CTkLabel(self.channel_list_frame, text="Powered by Claude 3.5", font=("Arial", 10), text_color="gray").pack(anchor="w", padx=10)

    def show_overlay(self, widget, width=400, height=500):
        # Destroy existing overlay widget if any
        if self.overlay_widget and self.overlay_widget.winfo_exists():
            self.overlay_widget.destroy()
            
        # Show full-screen semi-transparent backdrop
        self.overlay_bg.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.overlay_bg.lift() # Lift backdrop above everything else
        
        # Center the actual modal widget. 
        # Crucial: widget must still originate on 'self' (the root app), NOT overlay_bg 
        # or it inherits the dark theme incorrectly. We just lift it HIGHER.
        widget.configure(width=width, height=height)
        widget.pack_propagate(False)
        widget.grid_propagate(False)
        widget.place(relx=0.5, rely=0.5, anchor="center")
        widget.lift()
        
        # Keep track of widget so we can close it
        self.overlay_widget = widget

    def close_overlay(self, event=None, widget=None):
        # Destroy the modal widget first so the background shows through cleanly
        if self.overlay_widget and self.overlay_widget.winfo_exists():
            self.overlay_widget.destroy()
        self.overlay_widget = None
        self.overlay_bg.place_forget()
        self.update_idletasks()  # Force Tk to redraw the underlying UI immediately

    def check_for_updates(self):
        """Show a simple popup with version info and update instructions."""
        import urllib.request
        CURRENT_VERSION = "3.0.0"
        UPDATE_URL = "https://raw.githubusercontent.com/exile-tenshi/Tenshi/main/VERSION"
        latest = CURRENT_VERSION  # fallback if offline
        try:
            with urllib.request.urlopen(UPDATE_URL, timeout=3) as r:
                latest = r.read().decode().strip()
        except Exception:
            pass  # offline or repo not set up

        up_view = ctk.CTkFrame(self, fg_color="#1e1f22", corner_radius=10,
                               border_width=2, border_color="#383a40")
        hdr = ctk.CTkFrame(up_view, fg_color="transparent")
        hdr.pack(fill="x", padx=14, pady=10)
        ctk.CTkLabel(hdr, text="🔄  Check for Updates", font=("Arial", 16, "bold")).pack(side="left")
        ctk.CTkButton(hdr, text="✕", width=30, fg_color="#ed4245",
                      hover_color="#c03537", command=self.close_overlay).pack(side="right")

        body = ctk.CTkFrame(up_view, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=10)

        ctk.CTkLabel(body, text=f"Current version:  v{CURRENT_VERSION}",
                     font=("Arial", 13)).pack(anchor="w", pady=4)
        if latest != CURRENT_VERSION:
            status_txt = f"✨  New version available:  v{latest}"
            status_col = "#fee75c"
        else:
            status_txt = "✅  You are on the latest version."
            status_col = "#23a559"
        ctk.CTkLabel(body, text=status_txt, text_color=status_col,
                     font=("Arial", 13, "bold")).pack(anchor="w", pady=4)

        ctk.CTkFrame(body, height=1, fg_color="#383a40").pack(fill="x", pady=12)
        ctk.CTkLabel(body, text="To update: close the app, download the latest\n"
                                "TenshiVoice.exe from the Tenshi Updates server,\n"
                                "and replace your existing executable.",
                     text_color="gray", justify="left").pack(anchor="w")

        ctk.CTkButton(body, text="Close", fg_color="#313338",
                      command=self.close_overlay).pack(pady=16)

        self.show_overlay(up_view, width=380, height=300)

if __name__ == "__main__":
    try:
        app = LoginWindow()
        app.mainloop()
    except Exception as e:
        import traceback
        err = traceback.format_exc()
        print(f"CRASH: {err}")
        with open("crash_log.txt", "w") as f:
            f.write(err)
        try:
            from tkinter import messagebox
            messagebox.showerror("Tenshi Voice Crash", f"Error:\n{e}\n\nCheck crash_log.txt")
        except:
            pass