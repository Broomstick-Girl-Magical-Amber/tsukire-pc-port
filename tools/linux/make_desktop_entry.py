#!/usr/bin/env python3
"""make_desktop_entry.py - application menu entry and icon for the native Linux build.

Writes, for every built edition (or the one given with --edition):
  $XDG_DATA_HOME/applications/<game>.desktop                  (default ~/.local/share/applications)
  $XDG_DATA_HOME/icons/hicolor/256x256/apps/<game>.png        (the largest image of the game's .ico)
<game> is the binary's name (tsukihime, tsukihime_global), which is also the window's application id (Wayland app_id,
X11 WM_CLASS): GNOME on Wayland has no protocol for a window icon and shows the icon of this .desktop file.

  python3 tools/linux/make_desktop_entry.py                   all built editions
  python3 tools/linux/make_desktop_entry.py --edition global  only one
  python3 tools/linux/make_desktop_entry.py --remove          take the entries and icons away

Nothing is written inside the repository. Run it again if the repository folder moves (the entry points at the binary).
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
EDITIONS = {
    "japan": ("tsukihime", "Tsukihime -A piece of blue glass moon-"),
    "global": ("tsukihime_global", "TSUKIHIME -A piece of blue glass moon- (Global)"),
}


def data_home() -> Path:
    env = os.environ.get("XDG_DATA_HOME")
    return Path(env) if env else Path.home() / ".local" / "share"


def ico_png(game: str) -> bytes:
    """The largest PNG image of games/<game>/*.ico, or of games/tsukihime_common/*.ico (as CMakeLists.txt)."""
    for folder in (ROOT / "framework" / "games" / game, ROOT / "framework" / "games" / "tsukihime_common"):
        icos = sorted(folder.glob("*.ico"))
        if icos:
            break
    else:
        raise SystemExit("no .ico in framework/games/%s or framework/games/tsukihime_common" % game)
    data = icos[0].read_bytes()
    best, best_w = b"", 0
    for k in range(int.from_bytes(data[4:6], "little")):
        entry = data[6 + 16 * k:22 + 16 * k]
        if len(entry) < 16:
            break
        width = entry[0] or 256
        size = int.from_bytes(entry[8:12], "little")
        offset = int.from_bytes(entry[12:16], "little")
        image = data[offset:offset + size]
        if image[:4] == b"\x89PNG" and width > best_w:
            best, best_w = image, width
    if not best:
        raise SystemExit("%s has no PNG image" % icos[0])
    return best


def exec_quote(path: Path) -> str:
    """A path as an Exec argument of the Desktop Entry spec: quoted, with its reserved characters escaped."""
    text = str(path)
    for ch in ('\\', '"', '`', '$'):
        text = text.replace(ch, '\\' + ch)
    return '"%s"' % text.replace('%', '%%')


def refresh(apps: Path, icons: Path) -> None:
    """Optional caches of the desktop; the entries work without them."""
    for cmd in (["update-desktop-database", str(apps)], ["gtk-update-icon-cache", "-q", "-t", str(icons)]):
        if shutil.which(cmd[0]):
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def main() -> int:
    ap = argparse.ArgumentParser(description="Application menu entry and icon for the native Linux build.")
    ap.add_argument("--edition", choices=sorted(EDITIONS), help="only this edition (default: every built one)")
    ap.add_argument("--remove", action="store_true", help="remove the entries and icons")
    args = ap.parse_args()

    apps = data_home() / "applications"
    icons = data_home() / "icons" / "hicolor"
    names = [args.edition] if args.edition else sorted(EDITIONS)
    done = 0
    for name in names:
        game, title = EDITIONS[name]
        desktop = apps / (game + ".desktop")
        icon = icons / "256x256" / "apps" / (game + ".png")
        if args.remove:
            for f in (desktop, icon):
                if f.exists():
                    f.unlink()
                    print("removed %s" % f)
                    done += 1
            continue
        binary = ROOT / "framework" / "build" / game / game
        if not binary.is_file():
            if args.edition:
                print("%s is not built: %s does not exist" % (name, binary.relative_to(ROOT)), file=sys.stderr)
                return 1
            continue
        icon.parent.mkdir(parents=True, exist_ok=True)
        icon.write_bytes(ico_png(game))
        apps.mkdir(parents=True, exist_ok=True)
        desktop.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=%s\n"
            "Exec=%s\n"
            "Path=%s\n"
            "Icon=%s\n"
            "StartupWMClass=%s\n"
            "Terminal=false\n"
            "Categories=Game;\n" % (title, exec_quote(binary), binary.parent, game, game),
            encoding="utf-8")
        desktop.chmod(0o755)
        print("%s: %s" % (name, desktop))
        done += 1
    if not done:
        print("nothing to do (no built edition: framework/build/<game>/<game>)" if not args.remove else "nothing to remove")
    refresh(apps, icons)
    return 0


if __name__ == "__main__":
    sys.exit(main())
