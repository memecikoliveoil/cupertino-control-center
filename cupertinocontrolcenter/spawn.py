"""Starting other programs without waiting for them."""
import subprocess
import threading


def spawn(cmd, **kwargs):
    """Starts a program and returns right away; takes the same arguments as subprocess.Popen.

    A background thread waits for the program to exit, so the finished process is collected
    at once and Python doesn't warn that it was "still running" when it was let go. Use this
    for commands and apps that are started and then left alone (setting the volume, opening
    pavucontrol); keep subprocess.Popen where the output is read or the process is waited for."""
    proc = subprocess.Popen(cmd, **kwargs)
    threading.Thread(target=proc.wait, daemon=True).start()
    return proc
