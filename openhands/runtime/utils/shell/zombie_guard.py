import subprocess
from openhands.core.logger import openhands_logger as logger

def cleanup():
    """Find and kill zombie tmux processes safely."""
    try:
        ps = subprocess.run(
            ["ps", "aux"], capture_output=True, text=True, timeout=5
        )
        if ps.returncode != 0:
            return

        zombies = []
        for line in ps.stdout.splitlines():
            if "tmux" in line and "<defunct>" in line:
                parts = line.split()
                try:
                    zombies.append(int(parts[1]))
                except (ValueError, IndexError):
                    pass

        for pid in zombies:
            try:
                subprocess.run(["kill", "-9", str(pid)], timeout=3)
                logger.debug(f"Killed zombie tmux process: {pid}")
            except Exception:
                logger.debug(f"Failed to kill zombie {pid}")

        if zombies:
            logger.info(f"Cleaned up {len(zombies)} zombie tmux processes")

    except Exception as e:
        logger.debug(f"Zombie cleanup failed: {e}")
