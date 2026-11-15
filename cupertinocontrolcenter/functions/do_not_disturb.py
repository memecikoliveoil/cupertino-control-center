import subprocess

class DoNotDisturbInformation:
    def __init__(self):
        self.state = self._get_state()

    def _get_state(self):
        try:
            result = subprocess.run(
                ["xfconf-query", "-c", "xfce4-notifyd", "-p", "/do-not-disturb"],
                capture_output=True, text=True
            )
            return result.stdout.strip() == "true"
        except Exception:
            return False

    def get_state(self):
        return self.state

    def toggle(self):
        new_state = not self.state
        subprocess.run([
            "xfconf-query", "-c", "xfce4-notifyd", "-p", "/do-not-disturb", "-s",
            "true" if new_state else "false"
        ])
        self.state = new_state
