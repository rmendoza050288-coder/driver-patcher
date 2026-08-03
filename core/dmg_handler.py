import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def mount_dmg(dmg_path: str):
    """Mount a DMG read-only, yield the mount point, and unmount on exit."""
    tmpdir = tempfile.mkdtemp(prefix="driver_patcher_dmg_")
    mounted = False
    try:
        proc = subprocess.run(
            [
                "hdiutil", "attach", dmg_path,
                "-mountpoint", tmpdir,
                "-nobrowse", "-readonly", "-noverify",
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Could not mount DMG: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        mounted = True
        yield Path(tmpdir)
    finally:
        if mounted:
            subprocess.run(
                ["hdiutil", "detach", tmpdir, "-force"],
                capture_output=True,
            )
        shutil.rmtree(tmpdir, ignore_errors=True)


def find_packages(mount_point: Path) -> list[Path]:
    """Return PKG files in the mounted volume (top-level preferred, else recursive)."""
    top = sorted(mount_point.glob("*.pkg"))
    return top if top else sorted(mount_point.rglob("*.pkg"))
