import subprocess
import math
import os
from cupertinocontrolcenter.spawn import spawn

class BrightnessInformation:
    """Backend for reading/setting brightness via brightnessctl."""
    def __init__(self):
        self.device = None
        self.refresh()

    def refresh(self):
        # brightnessctl -m prints: device,class,current,percent%,max
        try:
            out = subprocess.run(["brightnessctl", "-m"], capture_output=True, text=True,
                                 check=True, timeout=2).stdout.strip().splitlines()[0].split(",")
            self.device = out[0]
            self.current = int(out[2])
            self.max_brightness = int(out[4])
            # round(), not int(): truncating reads back a value set to 47% as 46%
            self.percent = int(round(self.current * 100 / self.max_brightness))
        except Exception:
            self.current = 100
            self.max_brightness = 100
            self.percent = 100

    def set_brightness(self, percent):
        """0% turns the backlight off (like the lowest brightness key step). Anything above 0%
        is at least one hardware level, so on panels with few levels (e.g. 15) a low
        percentage doesn't round down to off. The raw level is written directly, without
        waiting, so the slider stays smooth while dragging."""
        try:
            m = max(1, self.max_brightness)
            raw = 0 if percent <= 0 else max(1, int(round(percent * m / 100)))
            self.last_raw = raw     # level written by the slider, compared on the next read
            spawn(["brightnessctl", "-q", "set", str(raw)],
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.percent = percent
        except Exception as e:
            print(f"Failed to set brightness: {e}")

    def sysfs_path(self):
        """The kernel's brightness file. Reading it is far cheaper than running a program,
        so it can be polled often to follow brightness-key changes."""
        if not self.device:
            return None
        p = f"/sys/class/backlight/{self.device}/brightness"
        return p if os.path.exists(p) else None

    def read_fast(self):
        """Percentage read from the sysfs file, falling back to brightnessctl."""
        p = self.sysfs_path()
        if p:
            try:
                with open(p) as f:
                    self.current = int(f.read().strip())
                self.percent = int(round(self.current * 100 / max(1, self.max_brightness)))
                return self.percent
            except Exception:
                pass
        self.refresh()
        return self.percent

    def get_current_brightness(self):
        return self.percent
