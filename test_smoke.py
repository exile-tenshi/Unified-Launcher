import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import os

import installer
import vrchat_steamvr_optimizer as opt


class OptimizerSmokeTests(unittest.TestCase):
    def test_actions_are_well_formed_and_unique(self) -> None:
        actions = opt.build_actions({})
        keys = [action.key for action in actions]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertGreaterEqual(len(actions), 20)
        for action in actions:
            self.assertTrue(action.key)
            self.assertTrue(action.category)
            self.assertTrue(action.title)
            self.assertTrue(action.description)
            self.assertTrue(action.commands)
            self.assertTrue(callable(action.apply))

        categories = {action.category for action in actions}
        self.assertIn("Prerequisites", categories)
        self.assertIn("Compatibility", categories)
        self.assertIn("Vive Hub", categories)
        self.assertIn("Virtual Desktop", categories)
        self.assertIn("Steam Link", categories)
        self.assertIn("OVR Tools", categories)
        self.assertIn("MagicChatbox", categories)
        self.assertIn("VRCFaceTracking", categories)
        self.assertIn("Performance + Graphics", categories)
        self.assertIn("Power", categories)
        self.assertIn("Network", categories)

    def test_find_steam_app_uses_library_structure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library = Path(temp_dir)
            app_dir = library / "steamapps" / "common" / "VRChat"
            app_dir.mkdir(parents=True)
            self.assertEqual(opt.find_steam_app([library], "VRChat"), str(app_dir))
            self.assertIsNone(opt.find_steam_app([library], "MissingApp"))

    def test_executable_candidates_include_pcvr_runtime_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vrchat = root / "steamapps" / "common" / "VRChat" / "VRChat.exe"
            steamvr = root / "steamapps" / "common" / "SteamVR" / "bin" / "win64" / "vrserver.exe"
            vd = root / "Virtual Desktop Streamer" / "VirtualDesktop.Streamer.exe"
            vive = root / "VIVE" / "ViveHub.exe"
            link = root / "Steam" / "steam.exe"
            ovr = root / "OVR Toolkit" / "OVR Toolkit.exe"
            chatbox = root / "MagicChatbox" / "MagicChatbox.exe"
            vrcft = root / "VRCFaceTracking" / "VRCFaceTracking.exe"
            for path in [vrchat, steamvr, vd, vive, link, ovr, chatbox, vrcft]:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")

            steam = {
                "VRChatPath": str(vrchat.parent),
                "SteamVRPath": str(root / "steamapps" / "common" / "SteamVR"),
                "InstallPath": str(root / "Steam"),
            }
            fake_runtimes = {
                "VirtualDesktop": [str(vd)],
                "Vive": [str(vive)],
                "SteamLink": [str(link)],
                "OvrTools": [str(ovr)],
                "MagicChatbox": [str(chatbox)],
                "VRCFaceTracking": [str(vrcft)],
            }
            with patch.object(opt, "detect_pcvr_runtimes", return_value=fake_runtimes):
                labels = [label for label, _path in opt.executable_candidates(steam)]

        self.assertIn("VRChat", labels)
        self.assertIn("SteamVR Server", labels)
        self.assertIn("Virtual Desktop Streamer", labels)
        self.assertIn("Vive Hub / Console", labels)
        self.assertIn("Steam Link / Remote Play", labels)
        self.assertIn("OVR / OpenVR Overlay Tool", labels)
        self.assertIn("MagicChatbox", labels)
        self.assertIn("VRCFaceTracking", labels)

    def test_load_json_file_and_steamvr_balanced_preset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = root / "steamvr.vrsettings"
            settings.write_text(json.dumps({"steamvr": {"enableHomeApp": True}}), encoding="utf-8")
            backup_dir = root / "backups"
            messages: list[str] = []

            with patch.object(opt, "BACKUP_DIR", backup_dir):
                with patch.object(opt, "detect_steam", return_value={"SteamVRSettings": [str(settings)]}):
                    opt.apply_steamvr_balanced_quality(messages.append)

            data = json.loads(settings.read_text(encoding="utf-8"))
            steamvr = data["steamvr"]
            self.assertFalse(steamvr["enableHomeApp"])
            self.assertFalse(steamvr["supersampleManualOverride"])
            self.assertTrue(steamvr["allowSupersampleFiltering"])
            self.assertTrue(steamvr["motionSmoothing"])
            self.assertFalse(steamvr["showMirrorView"])
            self.assertTrue(list(backup_dir.glob("*.bak")))
            self.assertTrue(any("Applied balanced SteamVR" in message for message in messages))

    def test_virtual_desktop_and_vive_settings_writers_use_temp_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            backup_dir = root / "backups"
            vd_settings = root / "vd" / "settings.json"
            vive_settings = root / "vive" / "settings.json"
            vd_settings.parent.mkdir()
            vive_settings.parent.mkdir()
            vd_settings.write_text("{}", encoding="utf-8")
            vive_settings.write_text("{}", encoding="utf-8")
            runtimes = {
                "VirtualDesktopSettings": [str(vd_settings)],
                "ViveSettings": [str(vive_settings)],
            }
            messages: list[str] = []
            with patch.object(opt, "BACKUP_DIR", backup_dir):
                with patch.object(opt, "detect_pcvr_runtimes", return_value=runtimes):
                    with patch.object(opt, "find_registry_paths", return_value=[]):
                        with patch.dict(
                            os.environ,
                            {
                                "LOCALAPPDATA": str(root / "local"),
                                "APPDATA": str(root / "roaming"),
                                "PROGRAMDATA": str(root / "programdata"),
                            },
                        ):
                            opt.apply_virtual_desktop_balanced_settings(messages.append)
                            opt.apply_vive_balanced_settings(messages.append)

            vd_data = json.loads(vd_settings.read_text(encoding="utf-8"))
            vive_data = json.loads(vive_settings.read_text(encoding="utf-8"))
            self.assertEqual(vd_data["streaming"]["profile"], "Balanced")
            self.assertTrue(vd_data["streaming"]["autoBitrate"])
            self.assertEqual(vive_data["graphics"]["profile"], "Balanced")
            self.assertTrue(vive_data["graphics"]["autoResolution"])
            self.assertTrue(list(backup_dir.glob("*.bak")))
            self.assertTrue(any("virtual-desktop" in message.lower() for message in messages))
            self.assertTrue(any("vive" in message.lower() for message in messages))

    def test_installer_bundle_lookup_can_find_embedded_exe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            exe = root / installer.APP_EXE
            exe.write_text("fake", encoding="utf-8")
            with patch.object(installer, "bundle_dir", return_value=root):
                self.assertEqual(installer.source_app_exe(), exe)


if __name__ == "__main__":
    unittest.main(verbosity=2)
