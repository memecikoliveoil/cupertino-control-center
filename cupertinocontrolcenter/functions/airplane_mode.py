import subprocess
import threading
import glob

class AirPlaneInformation:
    def __init__(self):
        self.state = False
        self.wifi_was_enabled = True
        self.bluetooth_was_enabled = True

    def refresh(self):
        pass

    def get_state(self):
        return self.state

    def toggle(self, enable: bool):
        self.state = enable
        thread = threading.Thread(target=self._execute_toggle, args=(enable,))
        thread.start()
        return True

    def _execute_toggle(self, enable: bool):
        try:
            if enable:
                # Remember Wi-Fi and Bluetooth states to restore them later
                # (nmcli output can be localized, hence "açık")
                res_wifi = subprocess.run(["nmcli", "radio", "wifi"], capture_output=True, text=True, timeout=2)
                self.wifi_was_enabled = "enabled" in res_wifi.stdout.lower() or "açık" in res_wifi.stdout.lower()
                
                res_rf = subprocess.run(["rfkill", "list"], capture_output=True, text=True, timeout=2)
                self.bluetooth_was_enabled = False
                rf_lines = res_rf.stdout.lower().splitlines()
                for i, line in enumerate(rf_lines):
                    if "bluetooth" in line:
                        for sub_line in rf_lines[i:i+4]:
                            if "soft blocked: no" in sub_line:
                                self.bluetooth_was_enabled = True
                                break

                # Airplane Mode on
                subprocess.Popen(["rfkill", "block", "all"], stdout=subprocess.DEVNULL)
                subprocess.Popen(["tlp", "bat"], stdout=subprocess.DEVNULL)
                
                # Turbo off
                try:
                    with open("/sys/devices/system/cpu/intel_pstate/no_turbo", "w") as f:
                        f.write("1")
                except Exception as e: print(f"Could not write no_turbo: {e}")
                
                # CPU governor: powersave
                for gov_path in glob.glob("/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor"):
                    try:
                        with open(gov_path, "w") as f:
                            f.write("powersave")
                    except: pass
            else:
                # Airplane Mode off
                try:
                    with open("/sys/class/power_supply/AC/online", "r") as f:
                        is_ac = f.read().strip() == "1"
                except:
                    status = subprocess.run(["tlp-stat", "-s"], capture_output=True, text=True).stdout
                    is_ac = "Mode = AC" in status

                target_gov = "schedutil" if is_ac else "schedutil"
                
                # Turbo on
                try:
                    with open("/sys/devices/system/cpu/intel_pstate/no_turbo", "w") as f:
                        f.write("0")
                except: pass
                
                subprocess.Popen(["rfkill", "unblock", "all"], stdout=subprocess.DEVNULL)
                subprocess.Popen(["tlp", "start"], stdout=subprocess.DEVNULL)
                
                # Restore the CPU governor
                for gov_path in glob.glob("/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor"):
                    try:
                        with open(gov_path, "w") as f:
                            f.write(target_gov)
                    except: pass
                
                # Restore Wi-Fi and Bluetooth as they were
                if not self.wifi_was_enabled:
                    subprocess.Popen(["nmcli", "radio", "wifi", "off"], stdout=subprocess.DEVNULL)
                if not self.bluetooth_was_enabled:
                    subprocess.Popen(["rfkill", "block", "bluetooth"], stdout=subprocess.DEVNULL)
                    
        except Exception as e:
            print(f"Airplane Mode error: {e}")
