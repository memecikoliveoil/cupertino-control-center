import subprocess
from cupertinocontrolcenter.spawn import spawn

class SoundInformation:
    """Backend for system volume control."""

    def __init__(self):
        self.refresh()

    def refresh(self):
        """Reads the volume and the mute state."""
        try:
            result = subprocess.run(
                ["pactl", "get-sink-volume", "@DEFAULT_SINK@"],
                capture_output=True, text=True, check=True, timeout=2
            )
            # parse output like: "Volume: front-left: 65536 / 100% / 0.00 dB, ..."
            self.percent = int(result.stdout.split("/")[1].strip().replace("%", ""))
        except Exception:
            self.percent = 100
        try:
            out = subprocess.run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"],
                                 capture_output=True, text=True, timeout=2).stdout
            # "Mute: yes"; pactl translates this line, so accept localized "yes" too
            self.muted = out.strip().split()[-1].lower() in ("yes", "evet", "true", "1") if out.strip() else False
        except Exception:
            self.muted = False

    def get_volume(self):
        """Return current volume (percent)."""
        return self.percent

    def set_volume(self, percent):
        """Sets the volume. Moving the slider while muted also unmutes, as on macOS."""
        try:
            if getattr(self, "muted", False) and percent > 0:
                spawn(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.muted = False
            # Not waiting for pactl keeps the slider smooth while dragging
            spawn(
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{percent}%"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            self.percent = percent
        except Exception as e:
            print(f"Failed to set volume: {e}")
