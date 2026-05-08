import socket
import threading
import json
import os
import time
import base64
import hashlib
from typing import Dict, Any
import smtplib
from email.mime.text import MIMEText
import requests
try:
    import redis
    has_redis = True
except ImportError:
    has_redis = False

# --- CONFIGURATION ---
HOST = '0.0.0.0'  # Listen for connections from the internet
PORT = 6000       # The port you opened in your router
VIDEO_PORT = 6001
REGISTRY_FILE = 'server_registry.json'

# --- SMTP CONFIGURATION ---
# Replace with actual tenshi.lol or porkbun SMTP settings
SMTP_HOST = "smtp.yourprovider.com"
SMTP_PORT = 587
SMTP_USER = "noreply@tenshi.lol"
SMTP_PASS = "your_smtp_password"

# --- INFRASTRUCTURE V2.2: UPSTASH REDIS SUPPORT ---
REDIS_URL = os.getenv("UPSTASH_REDIS_URL") # Provided by user later
r_client = None
if has_redis and REDIS_URL:
    try:
        r_client = redis.from_url(REDIS_URL, decode_responses=True)
        print("--- UPSTASH REDIS CONNECTED ---")
    except Exception as e:
        print(f"Redis Connection Failed: {e}")

def load_db(file_path, default_data=None):
    if default_data is None: default_data = {}
    
    # Priority 1: Redis
    if r_client:
        data = r_client.get(file_path)
        if data: return json.loads(data)
    
    # Priority 2: Local JSON
    if os.path.exists(file_path):
        with open(file_path, 'r') as f:
            return json.load(f)
    return default_data

# Message Queues (Ephemeral in memory) target_user -> list of messages
MESSAGE_QUEUE: Dict[str, list] = {}

# Global active connections mapping (username -> connection socket)
ACTIVE_CONNECTIONS: Dict[str, socket.socket] = {}
VOICE_CLIENTS: Dict[socket.socket, str] = {} # socket -> current_channel
VIDEO_CLIENTS: Dict[socket.socket, str] = {} # socket -> username
WEB_VIDEO_BUFFER: Dict[str, str] = {} # target_channel_user -> base64 frame

# Persistent Storage Files
SERVERS_FILE = 'servers.json'
AI_CONTEXT: Dict[str, list] = {} # AI Memory: username -> list of messages
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Try to load from .env if it exists
if not ANTHROPIC_API_KEY and os.path.exists(".env"):
    with open(".env", "r") as f:
        for line in f:
            if line.startswith("ANTHROPIC_API_KEY="):
                ANTHROPIC_API_KEY = line.split("=", 1)[1].strip()

if ANTHROPIC_API_KEY:
    print(f"[SYSTEM] Tenshi AI (Claude) is ACTIVE and READY.")
else:
    print(f"[WARNING] Tenshi AI is OFFLINE. (Missing ANTHROPIC_API_KEY)")

USER_DB = load_db(REGISTRY_FILE)
SERVERS_DB = load_db(SERVERS_FILE)

# Ensure "Tenshi Updates" exists
if "srv_0" not in SERVERS_DB:
    SERVERS_DB["srv_0"] = {
        "name": "Tenshi Updates",
        "owner": "ADMIN",
        "roles": {
            "role_admin": {"name": "Admin", "color": "#ff0000"},
            "role_everyone": {"name": "@everyone", "color": "#ffffff"}
        },
        "channels": {
            "Announcements": {"type": "text", "locked": False, "allowed_roles": []}
        },
        "members": {},
        "invite_only": True
    }


def save_all():
    # Save Local for redundancy
    with open(REGISTRY_FILE, 'w') as f: json.dump(USER_DB, f, indent=4)
    with open(SERVERS_FILE, 'w') as f: json.dump(SERVERS_DB, f, indent=4)
    
    # Save to Redis for high-speed cloud access
    if r_client:
        try:
            r_client.set(REGISTRY_FILE, json.dumps(USER_DB))
            r_client.set(SERVERS_FILE, json.dumps(SERVERS_DB))
        except Exception as e:
            print(f"Redis Save Error: {e}")

def send_external_email(sender_username, recipient_email, content):
    try:
        msg = MIMEText(f"Message from Tenshi user {sender_username}:\n\n{content}\n\n---\nSent via Tenshi Hub")
        msg['Subject'] = f"Tenshi Message from {sender_username}"
        msg['From'] = SMTP_USER
        msg['To'] = recipient_email
        
        # Simulate if defaults are kept
        if SMTP_PASS == "your_smtp_password":
            print(f"[MAIL_SIM] -> To: {recipient_email} | Content: {content}")
            return True
            
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"Failed to send external email: {e}")
        return False

def handle_client(conn, addr):
    try:
        raw_data = conn.recv(1024).decode('utf-8')
        if not raw_data: return
        
        # We are moving to JSON for complex requests
        try:
            request = json.loads(raw_data)
        except json.JSONDecodeError:
            print(f"ERROR: Received non-JSON data: {raw_data}") 
            return

        action = request.get("action")
        username = request.get("username")
        
        response = {"status": "error", "message": "Unknown Action"}

        if action == "REGISTER":
            if username not in USER_DB:
                email = request.get('email', '').strip()
                phone = request.get('phone', '').strip()
                
                if not email and not phone:
                    conn.send(json.dumps({"status": "fail", "message": "Email or Phone is required"}).encode('utf-8'))
                    return
                    
                USER_DB[username] = {
                    "hwid": request['hwid'],
                    "pwd_hash": request.get('pwd_hash', ''), # Store password hash
                    "email": email,
                    "phone": phone,
                    "allowed_hwids": [request['hwid']],
                    "friends": [], 
                    "pending_reqs": [], 
                    "following": [],
                    "followers": [],
                    "blocked": [],
                    "hidden_from": [],  # users who won't see this user online
                    "servers": ["srv_0"], # Auto-join Tenshi Updates
                    "bio": "New to Tenshi",
                    "pronouns": "",
                    "banner_color": "#2b2d31",
                    "status": "Online",
                    "connections": {},
                    "privacy": {
                        "hide_online_status": False,
                        "hide_server_membership": False,
                        "dms_from_friends_only": False,
                        "auto_accept_friends": False,   # auto-accept if mutual server
                        "auto_decline_strangers": False  # auto-decline if no shared server
                    }
                }
                save_all()
                response = {"status": "success", "message": "Account Created"}
        
        elif action == "ADD_FRIEND":
            target = request.get("target")

            # Allow adding external emails as friends
            if "@" in target and "." in target:
                if target not in USER_DB[username]["friends"]:
                    USER_DB[username]["friends"].append(target)
                    save_all()
                response = {"status": "success", "message": "Email added to contacts"}
                
            elif target in USER_DB and target != username:
                # Check if sender is blocked
                if username in USER_DB[target].get("blocked", []):
                    response = {"status": "fail", "message": "User not found"}
                elif username not in USER_DB[target]["friends"] and username not in USER_DB[target].get("pending_reqs", []):
                    # Check target's privacy: auto_decline_strangers
                    target_privacy = USER_DB[target].get("privacy", {})
                    # Find mutual servers
                    sender_servers = set(USER_DB[username].get("servers", []))
                    target_servers = set(USER_DB[target].get("servers", []))
                    has_mutual = bool(sender_servers & target_servers)
                    
                    if target_privacy.get("auto_decline_strangers") and not has_mutual:
                        response = {"status": "fail", "message": "This user only accepts requests from people in mutual servers"}
                    elif target_privacy.get("auto_accept_friends") and has_mutual:
                        # Auto-accept
                        USER_DB[target]["friends"].append(username)
                        USER_DB[username]["friends"].append(target)
                        save_all()
                        response = {"status": "success", "message": f"Auto-added! You and {target} share a server."}
                    else:
                        USER_DB[target].setdefault("pending_reqs", []).append(username)
                        save_all()
                        response = {"status": "success", "message": f"Request sent to {target}"}
                else:
                    response = {"status": "fail", "message": "Already friends or request pending"}
            else:
                response = {"status": "fail", "message": "User not found"}

        elif action == "ACCEPT_FRIEND":
            target = request.get("target")
            if target in USER_DB and target in USER_DB[username].get("pending_reqs", []):
                USER_DB[username]["pending_reqs"].remove(target)
                USER_DB[username].setdefault("friends", []).append(target)
                USER_DB[target].setdefault("friends", []).append(username)
                save_all()
                response = {"status": "success", "message": f"Added {target}"}
            else:
                response = {"status": "fail", "message": "Request not found"}

        elif action == "DECLINE_FRIEND":
            target = request.get("target")
            if target in USER_DB and target in USER_DB[username].get("pending_reqs", []):
                USER_DB[username]["pending_reqs"].remove(target)
                save_all()
                response = {"status": "success", "message": f"Declined {target}"}
            else:
                response = {"status": "fail", "message": "Request not found"}

        elif action == "REMOVE_FRIEND":
            target = request.get("target")
            if target in USER_DB:
                USER_DB[username].get("friends", []).remove(target) if target in USER_DB[username].get("friends", []) else None
                USER_DB[target].get("friends", []).remove(username) if username in USER_DB[target].get("friends", []) else None
                save_all()
                response = {"status": "success", "message": f"Removed {target}"}
            else:
                response = {"status": "fail", "message": "User not found"}

        elif action == "FOLLOW_USER":
            target = request.get("target")
            if target in USER_DB and target != username:
                USER_DB[username].setdefault("following", [])
                USER_DB[target].setdefault("followers", [])
                if target not in USER_DB[username]["following"]:
                    USER_DB[username]["following"].append(target)
                    USER_DB[target]["followers"].append(username)
                    save_all()
                response = {"status": "success", "message": f"Now following {target}"}
            else:
                response = {"status": "fail", "message": "User not found"}

        elif action == "UNFOLLOW_USER":
            target = request.get("target")
            if target in USER_DB:
                following = USER_DB[username].get("following", [])
                followers = USER_DB[target].get("followers", [])
                if target in following: following.remove(target)
                if username in followers: followers.remove(username)
                save_all()
                response = {"status": "success", "message": f"Unfollowed {target}"}
            else:
                response = {"status": "fail", "message": "User not found"}

        elif action == "BLOCK_USER":
            target = request.get("target")
            if target in USER_DB and target != username:
                USER_DB[username].setdefault("blocked", [])
                if target not in USER_DB[username]["blocked"]:
                    USER_DB[username]["blocked"].append(target)
                # Also remove from friends if they are friends
                if target in USER_DB[username].get("friends", []):
                    USER_DB[username]["friends"].remove(target)
                if username in USER_DB[target].get("friends", []):
                    USER_DB[target]["friends"].remove(username)
                save_all()
                response = {"status": "success", "message": f"Blocked {target}"}
            else:
                response = {"status": "fail", "message": "User not found"}

        elif action == "UNBLOCK_USER":
            target = request.get("target")
            if target in USER_DB:
                blocked = USER_DB[username].get("blocked", [])
                if target in blocked: blocked.remove(target)
                save_all()
                response = {"status": "success", "message": f"Unblocked {target}"}
            else:
                response = {"status": "fail", "message": "User not found"}

        elif action == "GET_MUTUAL_SERVERS":
            target = request.get("target")
            if target in USER_DB and username in USER_DB:
                my_servers = set(USER_DB[username].get("servers", []))
                their_servers = set(USER_DB[target].get("servers", []))
                mutual = list(my_servers & their_servers)
                mutual_names = [SERVERS_DB[s]["name"] for s in mutual if s in SERVERS_DB]
                response = {"status": "success", "mutual_servers": mutual_names}
            else:
                response = {"status": "fail", "message": "User not found"}

        elif action == "GET_JOINED_SERVERS":
            if username in USER_DB:
                joined = []
                for s_id in USER_DB[username].get("servers", []):
                    if s_id in SERVERS_DB:
                        s_data = SERVERS_DB[s_id]
                        joined.append({
                            "id": s_id,
                            "name": s_data.get("name"),
                            "icon": s_data.get("name")[0].upper(),
                            "channels": s_data.get("channels", {}),
                            "roles": s_data.get("roles", {})
                        })
                response = {"status": "success", "servers": joined}
            else:
                response = {"status": "fail", "message": "User not found"}

        elif action == "UPDATE_CHANNEL_ROLES":
            server_id = request.get("server_id")
            if server_id in SERVERS_DB:
                if SERVERS_DB[server_id].get("owner") == username:
                    c_name = request.get("channel_name")
                    if c_name in SERVERS_DB[server_id]["channels"]:
                        SERVERS_DB[server_id]["channels"][c_name]["locked"] = request.get("locked")
                        SERVERS_DB[server_id]["channels"][c_name]["allowed_roles"] = request.get("allowed_roles")
                        save_all()
                        response = {"status": "success"}
                    else:
                        response = {"status": "fail", "message": "Channel not found"}
                else:
                    response = {"status": "fail", "message": "Only the Server Owner can edit roles"}
            else:
                response = {"status": "fail", "message": "Server error"}

        elif action == "UPDATE_PRIVACY":
            if username in USER_DB:
                privacy = USER_DB[username].setdefault("privacy", {})
                for key in ["hide_online_status", "hide_server_membership", "dms_from_friends_only",
                            "auto_accept_friends", "auto_decline_strangers"]:
                    if key in request:
                        privacy[key] = request[key]
                save_all()
                response = {"status": "success", "message": "Privacy settings updated"}
            else:
                response = {"status": "fail", "message": "User not found"}

        elif action == "GET_RELATIONSHIPS":
            if username in USER_DB:
                data = USER_DB[username]
                response = {
                    "status": "success",
                    "friends": data.get("friends", []),
                    "pending": data.get("pending_reqs", []),
                    "following": data.get("following", []),
                    "blocked": data.get("blocked", [])
                }
            else:
                response = {"status": "fail", "message": "User error"}

        # --- BROWSER CAMERA / VOICE BRIDGE ---
        elif action == "SEND_VIDEO_FRAME":
            # Web App sends the raw base64 frame payload targeting a specific channel
            channel = request.get("channel")
            frame_data = request.get("frame")
            if channel and frame_data:
                # We need to broadcast this to all listeners of 'channel'
                # For a stateless HTTP approach, we buffer the latest frame globally
                global WEB_VIDEO_BUFFER
                if 'WEB_VIDEO_BUFFER' not in globals():
                    WEB_VIDEO_BUFFER = {}
                WEB_VIDEO_BUFFER[f"{channel}_{username}"] = frame_data
                response = {"status": "success"}
            else:
                response = {"status": "fail"}
                
        elif action == "POLL_VIDEO":
            channel = request.get("channel")
            if channel:
                if 'WEB_VIDEO_BUFFER' not in globals():
                    globals()['WEB_VIDEO_BUFFER'] = {}
                # Return all latest frames for users in this channel
                channel_frames = {user: frame for key, frame in WEB_VIDEO_BUFFER.items() if key.startswith(f"{channel}_") for user in [key.split("_", 1)[1]] if user != username}
                response = {"status": "success", "frames": channel_frames}
            else:
                response = {"status": "fail"}
        # -------------------------------------

        elif action == "CREATE_SERVER":
            server_name = request.get("server_name")
            server_id = f"srv_{len(SERVERS_DB) + 1}"
            SERVERS_DB[server_id] = {
                "name": server_name,
                "owner": username,
                "roles": {
                    "role_admin": {"name": "Admin", "color": "#ff0000"},
                    "role_everyone": {"name": "@everyone", "color": "#ffffff"}
                },
                "channels": {
                    "General": {"type": "text", "locked": False, "allowed_roles": []}, 
                    "Memes": {"type": "text", "locked": False, "allowed_roles": []}, 
                    "Off-Topic": {"type": "text", "locked": False, "allowed_roles": []},
                    "Lounge": {"type": "voice", "locked": False, "allowed_roles": []}, 
                    "Gaming": {"type": "voice", "locked": False, "allowed_roles": []}, 
                    "Music": {"type": "voice", "locked": False, "allowed_roles": []}
                },
                "members": {username: ["role_admin"]},
                "invite_only": False 
            }
            USER_DB[username]["servers"].append(server_id)
            save_all()
            response = {"status": "success", "server_id": server_id}

        elif action == "LOGIN":
            if username in USER_DB:
                # Verify password if it exists (legacy accounts might not have it yet)
                stored_hash = USER_DB[username].get("pwd_hash", "")
                if stored_hash and stored_hash != request.get("pwd_hash", ""):
                    response = {"status": "fail", "message": "Invalid Password"}
                else:
                    allowed = USER_DB[username].get("allowed_hwids", [USER_DB[username].get("hwid")])
                    if request.get("hwid") in allowed:
                         response = {"status": "success", "message": "Login Successful"}
                    else:
                         response = {"status": "fail", "message": "Hardware ID Mismatch"}
            else:
                response = {"status": "fail", "message": "User not found"}
                
        elif action == "LINK_DEVICE":
            if username in USER_DB:
                stored_hash = USER_DB[username].get("pwd_hash", "")
                if stored_hash and stored_hash != request.get("pwd_hash", ""):
                    response = {"status": "fail", "message": "Invalid Password"}
                else:
                    allowed = USER_DB[username].get("allowed_hwids", [USER_DB[username].get("hwid")])
                    new_hwid = request.get("hwid")
                    if new_hwid not in allowed:
                        allowed.append(new_hwid)
                        USER_DB[username]["allowed_hwids"] = allowed
                        save_all()
                    response = {"status": "success", "message": "Device Linked Successfully"}
            else:
                response = {"status": "fail", "message": "User not found"}
                
        elif action == "RECOVER_PASSWORD":
            if username in USER_DB:
                import random
                code = str(random.randint(100000, 999999))
                USER_DB[username]["recovery_code"] = code
                save_all()
                email = USER_DB[username].get("email", "")
                phone = USER_DB[username].get("phone", "")
                
                # Simulate sending
                contact = email if email else phone
                print(f"[MAIL_SIM] Sending Recovery Code {code} to {contact}")
                
                response = {"status": "success", "message": f"Recovery code sent to {contact}"}
            else:
                response = {"status": "fail", "message": "User not found"}
                
        elif action == "RESET_PASSWORD":
            if username in USER_DB:
                code = request.get("recovery_code")
                if "recovery_code" in USER_DB[username] and USER_DB[username]["recovery_code"] == code:
                    USER_DB[username]["pwd_hash"] = request.get("new_pwd_hash")
                    del USER_DB[username]["recovery_code"]
                    save_all()
                    response = {"status": "success", "message": "Password updated. You can now login."}
                else:
                    response = {"status": "fail", "message": "Invalid recovery code"}
            else:
                response = {"status": "fail", "message": "User not found"}

        elif action == "UPDATE_PROFILE":
            if username in USER_DB:
                USER_DB[username]["bio"] = request.get("bio", USER_DB[username].get("bio", ""))
                USER_DB[username]["pronouns"] = request.get("pronouns", USER_DB[username].get("pronouns", ""))
                USER_DB[username]["banner_color"] = request.get("banner_color", USER_DB[username].get("banner_color", "#2b2d31"))
                USER_DB[username]["status"] = request.get("status", USER_DB[username].get("status", "Online"))
                USER_DB[username]["connections"] = request.get("connections", USER_DB[username].get("connections", {}))
                save_all()
                response = {"status": "success", "message": "Profile Updated"}
            else:
                response = {"status": "fail", "message": "User not found"}

        elif action == "LOCK_CHANNEL":
            server_id = request.get("server_id")
            channel_name = request.get("channel_name")
            roles = request.get("allowed_roles", ["role_admin"])
            
            if server_id in SERVERS_DB and SERVERS_DB[server_id]["owner"] == username:
                if channel_name in SERVERS_DB[server_id]["channels"]:
                    SERVERS_DB[server_id]["channels"][channel_name]["locked"] = True
                    SERVERS_DB[server_id]["channels"][channel_name]["allowed_roles"] = roles
                    save_all()
                    response = {"status": "success", "message": f"{channel_name} is now locked"}
                else:
                    response = {"status": "fail", "message": "Channel not found"}
            else:
                response = {"status": "fail", "message": "Unauthorized"}
                
        elif action == "UNLOCK_CHANNEL":
            server_id = request.get("server_id")
            channel_name = request.get("channel_name")
            
            if server_id in SERVERS_DB and SERVERS_DB[server_id]["owner"] == username:
                if channel_name in SERVERS_DB[server_id]["channels"]:
                    SERVERS_DB[server_id]["channels"][channel_name]["locked"] = False
                    SERVERS_DB[server_id]["channels"][channel_name]["allowed_roles"] = []
                    save_all()
                    response = {"status": "success", "message": f"{channel_name} is now unlocked"}
                else:
                    response = {"status": "fail", "message": "Channel not found"}
            else:
                response = {"status": "fail", "message": "Unauthorized"}

        elif action == "CREATE_ROLE":
            server_id = request.get("server_id")
            role_name = request.get("role_name", "New Role")
            role_color = request.get("role_color", "#ffffff")
            
            if server_id in SERVERS_DB and SERVERS_DB[server_id]["owner"] == username:
                new_role_id = f"role_{len(SERVERS_DB[server_id]['roles']) + 1}"
                SERVERS_DB[server_id]["roles"][new_role_id] = {"name": role_name, "color": role_color}
                save_all()
                response = {"status": "success", "message": f"Role {role_name} created", "role_id": new_role_id}
            else:
                response = {"status": "fail", "message": "Unauthorized"}

        elif action == "ASSIGN_ROLE":
            server_id = request.get("server_id")
            target_user = request.get("target")
            role_id = request.get("role_id")
            
            if server_id in SERVERS_DB and SERVERS_DB[server_id]["owner"] == username:
                if target_user in SERVERS_DB[server_id]["members"]:
                    if role_id in SERVERS_DB[server_id]["roles"]:
                        if role_id not in SERVERS_DB[server_id]["members"][target_user]:
                            SERVERS_DB[server_id]["members"][target_user].append(role_id)
                            save_all()
                            response = {"status": "success", "message": f"Role assigned to {target_user}"}
                        else:
                            response = {"status": "fail", "message": "User already has role"}
                    else:
                        response = {"status": "fail", "message": "Role not found"}
                else:
                    response = {"status": "fail", "message": "User not in server"}
            else:
                response = {"status": "fail", "message": "Unauthorized"}

        elif action == "GET_PROFILE":
            target = request.get("target")
            if target in USER_DB:
                data = USER_DB[target]
                response = {
                    "status": "success",
                    "bio": data.get("bio", "New to Tenshi"),
                    "pronouns": data.get("pronouns", ""),
                    "banner_color": data.get("banner_color", "#2b2d31"),
                    "user_status": data.get("status", "Online"),
                    "status_color": data.get("status_color", "#23a559"),
                    "privacy": data.get("privacy", {}),
                    "connections": data.get("connections", {}),
                    "created_at": "2024"  # Placeholder
                }
            else:
                response = {"status": "fail", "message": "User not found"}

        elif action == "GET_SERVERS":
            # Return list of servers this user is in
            if username in USER_DB:
                user_servers = USER_DB[username].get("servers", [])
                server_dict = {}
                for s_id in user_servers:
                    if s_id in SERVERS_DB:
                        # Return full server details (channels, etc.)
                        server_dict[s_id] = SERVERS_DB[s_id]
                response = {"status": "success", "servers": server_dict}
            else:
                response = {"status": "fail", "servers": {}}

        elif action == "LEAVE_SERVER":
            server_id = request.get("server_id")
            if server_id in SERVERS_DB:
                # Remove from Server Members
                if username in SERVERS_DB[server_id]["members"]:
                    del SERVERS_DB[server_id]["members"][username]
                
                # Remove from User's Server List
                if username in USER_DB and server_id in USER_DB[username]["servers"]:
                    USER_DB[username]["servers"].remove(server_id)
                    save_all()
                    response = {"status": "success", "message": "Left server"}
                else:
                    response = {"status": "fail", "message": "Not in server"}
            else:
                response = {"status": "fail", "message": "Server not found"}

        elif action == "CREATE_CHANNEL":
            server_id = request.get("server_id")
            channel_name = request.get("channel_name")
            channel_type = request.get("channel_type", "text")
            
            if server_id in SERVERS_DB and SERVERS_DB[server_id]["owner"] == username:
                if channel_name not in SERVERS_DB[server_id]["channels"]:
                    SERVERS_DB[server_id]["channels"][channel_name] = {
                        "type": channel_type,
                        "locked": False,
                        "allowed_roles": []
                    }
                    save_all()
                    response = {"status": "success", "message": f"Channel {channel_name} created"}
                else:
                    response = {"status": "fail", "message": "Channel already exists"}
            else:
                response = {"status": "fail", "message": "Unauthorized"}
                
        elif action == "RENAME_CHANNEL":
            server_id = request.get("server_id")
            old_name = request.get("channel_name")
            new_name = request.get("new_name")
            
            if server_id in SERVERS_DB and SERVERS_DB[server_id]["owner"] == username:
                if old_name in SERVERS_DB[server_id]["channels"]:
                    if new_name not in SERVERS_DB[server_id]["channels"]:
                        # Rename strategy: copy old data to new key, delete old key
                        SERVERS_DB[server_id]["channels"][new_name] = SERVERS_DB[server_id]["channels"][old_name]
                        del SERVERS_DB[server_id]["channels"][old_name]
                        save_all()
                        response = {"status": "success", "message": f"Channel renamed to {new_name}"}
                    else:
                        response = {"status": "fail", "message": "Channel name already exists"}
                else:
                    response = {"status": "fail", "message": "Channel not found"}
            else:
                response = {"status": "fail", "message": "Unauthorized"}
                
        elif action == "GET_PUBLIC_SERVERS":
            public_servers = []
            for s_id, s_data in SERVERS_DB.items():
                if not s_data.get("invite_only", False):
                    public_servers.append({
                        "id": s_id,
                        "name": s_data.get("name"),
                        "owner": s_data.get("owner"),
                        "member_count": len(s_data.get("members", {})),
                        "is_promoted": s_data.get("is_promoted", False)
                    })
            # Sort: Promoted servers first, then by member count
            public_servers.sort(key=lambda x: (x['is_promoted'], x['member_count']), reverse=True)
            response = {"status": "success", "servers": public_servers}

        elif action == "BOOST_SERVER":
            server_id = request.get("server_id")
            if server_id in SERVERS_DB:
                if SERVERS_DB[server_id].get("owner") == username:
                    SERVERS_DB[server_id]["is_promoted"] = True
                    save_all()
                    response = {"status": "success", "message": f"Server {SERVERS_DB[server_id]['name']} boosted to top!"}
                else:
                    response = {"status": "fail", "message": "Only owner can boost"}
            else:
                response = {"status": "fail", "message": "Server not found"}
            
        elif action == "ADMIN_BROADCAST":
            if username == "ADMIN": # Hardcoded admin check for now
                content = request.get("content")
                msg_obj = {
                    "sender": "SYSTEM",
                    "target_id": "ALL",
                    "target_type": "broadcast",
                    "content": content,
                    "is_snapchat": False,
                    "timestamp": time.time()
                }
                # Deliver to every registered user's queue
                for u in USER_DB:
                    MESSAGE_QUEUE.setdefault(u, []).append(msg_obj)
                response = {"status": "success", "message": f"Broadcast sent to {len(USER_DB)} users"}
            else:
                response = {"status": "fail", "message": "Only ADMIN can broadcast"}

        elif action == "AI_CHAT":
            # Assistant Proxy for Claude 
            user_msg = request.get("content")
            if not ANTHROPIC_API_KEY:
                response = {"status": "fail", "message": "AI Assistant is offline (API Key required)"}
            else:
                try:
                    # Provide 'Tenshi Brand' personality
                    system_prompt = "You are the Tenshi Hub AI Assistant. You are helpful, cool, and part of a high-end social and voice ecosystem for gamers and creators. Keep responses concise unless asked for more detail."
                    
                    # Manage Context
                    history = AI_CONTEXT.setdefault(username, [])
                    history.append({"role": "user", "content": user_msg})
                    if len(history) > 10: history = history[-10:] # Keep last 10
                    
                    anthropic_res = requests.post(
                        "https://api.anthropic.com/v1/messages",
                        headers={
                            "x-api-key": ANTHROPIC_API_KEY,
                            "anthropic-version": "2023-06-01",
                            "content-type": "application/json"
                        },
                        json={
                            "model": "claude-3-5-sonnet-20240620",
                            "max_tokens": 1024,
                            "system": system_prompt,
                            "messages": history
                        },
                        timeout=10
                    )
                    
                    if anthropic_res.status_code == 200:
                        res_json = anthropic_res.json()
                        ai_resp = res_json["content"][0]["text"]
                        history.append({"role": "assistant", "content": ai_resp})
                        AI_CONTEXT[username] = history # Update
                        response = {"status": "success", "response": ai_resp}
                    else:
                        response = {"status": "fail", "message": f"AI Error: {anthropic_res.text}"}
                except Exception as e:
                    response = {"status": "error", "message": str(e)}

        elif action == "JOIN_SERVER":
            server_id = request.get("server_id")
            if server_id in SERVERS_DB:
                if not SERVERS_DB[server_id].get("invite_only", False):
                    if username not in SERVERS_DB[server_id]["members"]:
                        SERVERS_DB[server_id]["members"][username] = []
                        
                    if username in USER_DB and server_id not in USER_DB[username]["servers"]:
                        USER_DB[username]["servers"].append(server_id)
                        
                    save_all()
                    response = {"status": "success", "message": f"Joined {SERVERS_DB[server_id]['name']}"}
                else:
                    response = {"status": "fail", "message": "Server is private"}
            else:
                response = {"status": "fail", "message": "Server not found"}

        elif action == "SEND_MESSAGE":
            target_type = request.get("target_type") # "dm" or "server"
            target_id = request.get("target_id")
            content = request.get("content") # Encrypted content
            is_snapchat = request.get("is_snapchat", False)
            
            msg_obj = {
                "sender": username,
                "target_id": target_id,
                "target_type": target_type,
                "content": content,
                "is_snapchat": is_snapchat,
                "timestamp": request.get("timestamp")
            }
            
            if target_type == "dm":
                MESSAGE_QUEUE.setdefault(target_id, []).append(msg_obj)
                response = {"status": "success"}
            elif target_type == "email":
                success = send_external_email(username, target_id, content)
                if success:
                    response = {"status": "success", "message": "Email dispatched"}
                else:
                    response = {"status": "fail", "message": "SMTP Failed"}
            elif target_type == "server" and target_id in SERVERS_DB:
                # Deliver to all members
                for member in SERVERS_DB[target_id]["members"]:
                    # Don't send back to sender
                    if member != username:
                        MESSAGE_QUEUE.setdefault(member, []).append(msg_obj)
                response = {"status": "success"}
            else:
                response = {"status": "fail", "message": "Invalid target"}
                
        elif action == "POLL_MESSAGES":
            # Return queued messages and clear queue for this user
            msgs = MESSAGE_QUEUE.pop(username, [])
            response = {"status": "success", "messages": msgs}

        conn.send(json.dumps(response).encode('utf-8'))
    except Exception as e:
        print(f"Server Error: {e}")
    finally:
        conn.close()

# --- VIDEO STUFF ---
VIDEO_PORT = 6001
VIDEO_ROOMS = {} # room_id -> list of (conn, username)

def broadcast_user_list(room_id):
    if room_id not in VIDEO_ROOMS: return
    
    # Get List of Usernames
    users = [u[1] for u in VIDEO_ROOMS[room_id]]
    user_json = json.dumps(users).encode('utf-8')
    
    # Protocol: [Type 2][Length 4][JSON Data]
    payload = b'\x02' + len(user_json).to_bytes(4, 'big') + user_json
    
    for conn, _ in VIDEO_ROOMS[room_id]:
        try:
            # Send length prefix for payload (standard 4 byte size header for client to read)
            # Client reads 4 bytes size -> reads size bytes -> determines type from first byte
            # So: Length = len(payload)
            final_pkt = len(payload).to_bytes(4, 'big') + payload
            conn.sendall(final_pkt)
        except:
            pass

def handle_video_client(conn, addr):
    try:
        # Handshake: Receive JSON with room & username
        # Expected Format: [4 bytes length][JSON Data]
        header_len = conn.recv(4)
        if not header_len: return
        length = int.from_bytes(header_len, byteorder='big')
        
        info_bytes = conn.recv(length)
        info = json.loads(info_bytes.decode('utf-8'))
        
        room_id = info.get('room_id', 'default')
        username = info.get('username', 'Unknown')
        
        if room_id not in VIDEO_ROOMS: VIDEO_ROOMS[room_id] = []
        VIDEO_ROOMS[room_id].append((conn, username))
        
        print(f"[VIDEO] {username} joined room {room_id}")
        broadcast_user_list(room_id)
        
        while True:
            # Receive Packet Size [4 bytes]
            size_data = conn.recv(4)
            if not size_data: break
            packet_size = int.from_bytes(size_data, byteorder='big')
            
            # Receive Packet Data (Type + Payload)
            packet_data = b""
            while len(packet_data) < packet_size:
                chunk = conn.recv(packet_size - len(packet_data))
                if not chunk: break
                packet_data += chunk
            
            if not packet_data: break
            
            # Broadcast to others in room
            # Protocol update: Client sends [Type][Payload].
            # Server wraps it and inserts the sender username:
            # [Type 1 byte][Uname_len 1 byte][Username bytes][Payload]
            
            pkt_type = packet_data[0:1]
            raw_payload = packet_data[1:]
            
            uname_bytes = username.encode('utf-8')
            uname_len = len(uname_bytes).to_bytes(1, 'big')
            
            new_packet_data = pkt_type + uname_len + uname_bytes + raw_payload
            final_pkt = len(new_packet_data).to_bytes(4, 'big') + new_packet_data
            
            for client, client_name in list(VIDEO_ROOMS[room_id]):
                if client != conn:
                    try:
                        client.sendall(final_pkt)
                    except:
                        pass # Let the main loop or finally block handle cleanup

    except Exception as e:
        print(f"[VIDEO] Error with {addr}: {e}")
    finally:
        # Remove from room
        if 'room_id' in locals() and room_id in VIDEO_ROOMS:
            # Safe removal
            VIDEO_ROOMS[room_id] = [c for c in VIDEO_ROOMS[room_id] if c[0] != conn]
            broadcast_user_list(room_id)
            print(f"[VIDEO] {username} left room {room_id}")
            
        conn.close()

def start_video_server():
    vid_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    vid_sock.bind((HOST, VIDEO_PORT))
    vid_sock.listen()
    print(f"--- VIDEO SERVER ONLINE ---")
    print(f"Listening on Port: {VIDEO_PORT}")
    
    while True:
        conn, addr = vid_sock.accept()
        threading.Thread(target=handle_video_client, args=(conn, addr), daemon=True).start()

# --- HTTP WEB API BRIDGE ---
from http.server import BaseHTTPRequestHandler, HTTPServer

class DummyConn:
    def __init__(self, data):
        self.data = data
        self.response = None
    
    def recv(self, buflen):
        d = self.data
        self.data = b'' # only return once
        return d
        
    def send(self, data):
        self.response = data
        
    def close(self):
        pass

class APIHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            import os
            web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "TenshiWeb")
            path = self.path
            if path == "/" or path == "": path = "/index.html"
            
            if ".." in path:
                self.send_response(403)
                self.end_headers()
                return
                
            file_path = os.path.abspath(os.path.join(web_dir, path.lstrip("/")))
            if not file_path.startswith(os.path.abspath(web_dir)):
                self.send_response(403)
                self.end_headers()
                return
                
            if os.path.exists(file_path):
                self.send_response(200)
                if file_path.endswith(".html"): self.send_header('Content-type', 'text/html; charset=utf-8')
                elif file_path.endswith(".css"): self.send_header('Content-type', 'text/css')
                elif file_path.endswith(".js"): self.send_header('Content-type', 'application/javascript')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                
                with open(file_path, 'rb') as f:
                    self.wfile.write(f.read())
            else:
                self.send_response(404)
                self.end_headers()
        except Exception as e:
            print(f"GET Error: {e}")
            self.send_response(500)
            self.end_headers()

    def do_POST(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            
            dummy = DummyConn(post_data)
            # handle_client expects (conn, addr). Just pass our dummy.
            handle_client(dummy, self.client_address)
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            if dummy.response:
                self.wfile.write(dummy.response)
            else:
                self.wfile.write(b'{"status":"error","message":"No TCP response generated"}')
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            print(f"API Error: {e}")
            
    def do_OPTIONS(self):
        self.send_response(200, "ok")
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header("Access-Control-Allow-Headers", "X-Requested-With, Content-type")
        self.end_headers()

def start_http_server():
    try:
        server = HTTPServer(('0.0.0.0', 8080), APIHandler)
        print("--- WEB API BRIDGE ONLINE ---")
        print("Listening on Port: 8080")
        server.serve_forever()
    except Exception as e:
        print(f"HTTP Server failed to start: {e}")

def start_server():
    # Start Video Server in Background Thread
    threading.Thread(target=start_video_server, daemon=True).start()
    # Start HTTP Web API in Background Thread
    threading.Thread(target=start_http_server, daemon=True).start()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind((HOST, PORT))
    server.listen()
    print(f"--- TENSHI VOICE SERVER ONLINE ---")
    print(f"Listening on Port: {PORT}")
    print(f"Registry File: {REGISTRY_FILE}")
    print("----------------------------------")
    
    while True:
        conn, addr = server.accept()
        thread = threading.Thread(target=handle_client, args=(conn, addr))
        thread.start()

if __name__ == "__main__":
    start_server()