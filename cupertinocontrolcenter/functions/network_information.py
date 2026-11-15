import subprocess
import threading
from gi.repository import GLib
from cupertinocontrolcenter.i18n import _

class NetworkInformation:
    def __init__(self):
        self.device = "wifi"
        self.name = ""
        self.connected = False
        self.enabled = True
        self.refresh()

    def refresh(self):
        try:
            # Is the Wi-Fi radio on? (nmcli output can be localized, hence "kapalı")
            res_radio = subprocess.run(["nmcli", "radio", "wifi"], capture_output=True, text=True, timeout=2)
            wifi_status = res_radio.stdout.strip().lower()

            if "disabled" in wifi_status or "kapalı" in wifi_status:
                self.connected = False
                self.enabled = False
                self.name = _("Off")
                return
            self.enabled = True

            # Name of the active connection on the first Wi-Fi device
            res_ssid = subprocess.run(
                ["sh", "-c", "nmcli -t -f GENERAL.CONNECTION dev show $(nmcli -t -f DEVICE,TYPE dev | grep :wifi | cut -d: -f1 | head -n1) 2>/dev/null | cut -d: -f2-"],
                capture_output=True, text=True, timeout=2
            )
            ssid_name = res_ssid.stdout.strip()

            # Fallback: iwgetid
            if not ssid_name or ssid_name == "--":
                res_backup = subprocess.run(["iwgetid", "-r"], capture_output=True, text=True, timeout=2)
                ssid_name = res_backup.stdout.strip()

            if ssid_name:
                self.connected = True
                self.name = ssid_name
            else:
                self.connected = False
                self.name = _("Not Connected")

        except Exception as e:
            print(f"Could not read the network state: {e}")
            self.connected = False
            self.name = _("Not Connected")
    
    def get_name(self):
        return self.name

    def get_device(self):
        return self.device

    def is_connected(self):
        return self.connected

    def get_wifi(self):
        return "wifi"

    def is_wifi_enabled(self):
        return self.enabled

    def toggle_wifi(self, enable: bool):
        def _worker():
            try:
                state = "on" if enable else "off"
                subprocess.run(["nmcli", "radio", "wifi", state], check=True)
                import time
                time.sleep(0.6)
                GLib.idle_add(self.refresh)
            except Exception as e:
                print(f"Toggle failed: {e}")

        threading.Thread(target=_worker, daemon=True).start()
        return True
