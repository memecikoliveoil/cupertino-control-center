import threading
import subprocess
import time
from pydbus import SystemBus
from cupertinocontrolcenter.i18n import _

class BluetoothInformation:
    def __init__(self):
        self.power = False
        self.connected_devices = []
        self.refresh()

    def refresh(self):
        self.connected_devices = []
        try:
            bus = SystemBus()
            self.adapter = bus.get("org.bluez", "/org/bluez/hci0")
            self.power = bool(self.adapter.Powered)

            # Names of connected devices
            mngr = bus.get("org.bluez", "/")
            objs = mngr.GetManagedObjects()
            for path, ifaces in objs.items():
                dev = ifaces.get("org.bluez.Device1")
                if dev and dev.get("Connected", False):
                    name = dev.get("Name", _("Unknown Device"))
                    self.connected_devices.append(name)
        except Exception:
            self.power = False
            self.connected_devices = []

    def get_power(self):
        return self.power

    def get_connected_devices(self):
        return self.connected_devices

    def toggle(self, enable: bool):
        def _worker():
            try:
                # Stop any running scan first; BlueZ can stay busy otherwise
                subprocess.run(["pkill", "-f", "bluetoothctl scan on"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(["bluetoothctl", "scan", "off"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                # rfkill block/unblock
                action = "unblock" if enable else "block"
                subprocess.run(["rfkill", action, "bluetooth"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                # Give the adapter a moment to come up
                time.sleep(0.15)

                # Power the adapter through D-Bus
                try:
                    bus = SystemBus()
                    adapter = bus.get("org.bluez", "/org/bluez/hci0")
                    adapter.Powered = bool(enable)
                except Exception as dbus_err:
                    # Fallback when D-Bus answers org.bluez.Error.Busy
                    cmd_param = "on" if enable else "off"
                    subprocess.run(["bluetoothctl", "power", cmd_param], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                # Let BlueZ settle before reading the state back
                time.sleep(0.2)
                
                from gi.repository import GLib
                GLib.idle_add(self.refresh)
            except Exception as e:
                print(f"Async Bluetooth toggle failed: {e}")

        # In a thread so the UI doesn't freeze
        thread = threading.Thread(target=_worker)
        thread.daemon = True
        thread.start()
        return True
