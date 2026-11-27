#!/usr/bin/env python3
"""controls.py - input.ini, title.ini and config.txt of the native Linux build (launcher.sh and the GUI call it).

  controls.py [--root DIR] [--edition japan|global|both] [--tsv] <area> <verb> [args]

  input   show | keys | bind <kb|mouse> <ACTION> <key,key,...> [--add] [--move|--share] | unbind <kb|mouse> <ACTION>
          | clear <kb|mouse> | reset <kb|mouse|all>          keyboard and mouse of the game (input.ini)
  title   show | set <key> <value> | reset                   background video and music of the title screen (title.ini)
  options show | set <name> <value> | reset [<name>] | gpus  options of the game (config.txt, one per edition)

Same rules as the Windows launcher (launcher/src/controls.cpp), same files:
- input.ini and title.ini are written to BOTH editions that have a data folder (data/tsuki, data/tsuki_global); the
  file of each is NX_INPUT_INI / NX_TITLE_INI (config.txt or environment), else next to the binary if it is there, else
  its data folder. config.txt is per edition (data/<edition>/config.txt).
- A device section of input.ini that is present replaces all the defaults of that device; a missing one keeps them.
  Sections the launcher does not edit ([mando]...) and comment lines are kept as they are. CRLF files stay CRLF.
- The game reads these files when it starts: a change made while it runs applies the next time.
Python 3, standard library only. Messages and names are English.
"""
import argparse
import glob
import os
import re
import shutil
import signal
import subprocess
import sys

EDITIONS = {"japan": ("tsuki", "tsukihime"), "global": ("tsuki_global", "tsukihime_global")}


class Fail(Exception):
    pass


# ---------------------------------------------------------------------------------------------------- files
ROOT = "."


def rel(path):
    try:
        r = os.path.relpath(path, ROOT)
    except ValueError:
        return path
    return path if r.startswith("..") else r.replace(os.sep, "/")


def data_dir(ed):
    return os.path.join(ROOT, "data", EDITIONS[ed][0])


def exe_dir(ed):
    return os.path.join(ROOT, "framework", "build", EDITIONS[ed][1])


def exe_path(ed):
    return os.path.join(exe_dir(ed), EDITIONS[ed][1])


def has_data(ed):
    return os.path.isdir(data_dir(ed))


def read_text(path):
    with open(path, "rb") as f:
        raw = f.read()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.decode("utf-8", "replace")


def split_lines(text):
    return text.splitlines()


def is_crlf(text):
    return "\r\n" in text


def write_text(path, lines, crlf=False):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    body = ("\r\n" if crlf else "\n").join(lines) + ("\r\n" if crlf else "\n") if lines else ""
    tmp = path + ".launcher"
    with open(tmp, "wb") as f:
        f.write(body.encode("utf-8"))
    os.replace(tmp, path)


def running_editions():
    """Editions whose game binary is running now (any process with that executable)."""
    out = []
    for ed in EDITIONS:
        try:
            b = os.path.realpath(exe_path(ed))
        except OSError:
            continue
        if not os.path.exists(b):
            continue
        for p in glob.glob("/proc/[0-9]*/exe"):
            try:
                if os.readlink(p) == b:
                    out.append(ed)
                    break
            except OSError:
                pass
    return out


# ---------------------------------------------------------------------------------------------------- config.txt
class Config:
    def __init__(self, ed):
        self.path = os.path.join(data_dir(ed), "config.txt")
        self.exists = os.path.isfile(self.path)
        text = read_text(self.path) if self.exists else ""
        self.lines = split_lines(text)
        self.crlf = is_crlf(text)


def config_key(line):
    s = re.split(r"[;#]", line, maxsplit=1)[0]
    if "=" not in s:
        return None
    k, v = s.split("=", 1)
    k = k.strip().upper()
    return (k, v.strip()) if len(k) >= 4 and k.startswith("NX_") else None


def config_get(cfg, key):
    """The first line of the key with a value (the game sets that one and skips the later ones)."""
    for line in cfg.lines:
        kv = config_key(line)
        if kv and kv[0] == key and kv[1]:
            return kv[1]
    return ""


def config_set(cfg, key, value):
    """'' removes the key."""
    done = False
    i = 0
    while i < len(cfg.lines):
        kv = config_key(cfg.lines[i])
        if kv and kv[0] == key:
            if value and not done:
                cfg.lines[i] = key + "=" + value
                done = True
                i += 1
            else:
                del cfg.lines[i]
            continue
        i += 1
    if value and not done:
        if not cfg.lines:
            cfg.lines = ["; config.txt - options of the game, read when it starts (one NX_NAME=value per line; comments with ; or #).",
                         "; Written by the launcher (options). An option set in the environment wins over this file."]
        cfg.lines.append(key + "=" + value)


def game_file_path(ed, filename, env_name, cfg):
    """input.ini / title.ini of an edition: NX_<NAME> (config.txt, then environment), else next to the binary if it is
    there, else the data folder: the order the game looks in."""
    v = config_get(cfg, env_name) or os.environ.get(env_name, "")
    if v:
        return v if os.path.isabs(v) else os.path.join(ROOT, v)
    beside = os.path.join(exe_dir(ed), filename)
    return beside if os.path.isfile(beside) else os.path.join(data_dir(ed), filename)


def target_paths(shown, filename, env_name):
    """The file of every edition that has a data folder (one entry when both are the same file)."""
    v = []
    for ed in [shown] + [e for e in EDITIONS if e != shown]:
        if not has_data(ed):
            continue
        p = game_file_path(ed, filename, env_name, Config(ed))
        if p not in v:
            v.append(p)
    return v


def source_edition(shown):
    if has_data(shown):
        return shown
    other = "global" if shown == "japan" else "japan"
    return other if has_data(other) else shown


def write_all(paths, lines, crlf):
    for p in paths:
        write_text(p, lines, crlf)
    return " and ".join(rel(p) for p in paths)


def sync_note(paths, source, filename):
    """What the next change will do about editions whose file is missing or different."""
    if not os.path.isfile(source):
        return ""
    text = read_text(source)
    for p in paths:
        if p == source:
            continue
        if not os.path.isfile(p):
            return "%s does not exist in the other edition yet: the next change writes it there too." % filename
        if read_text(p) != text:
            return "The editions have different %s: %s is shown; the next change is saved in both." % (filename, rel(source))
    return ""


def after_write_note():
    run = running_editions()
    return "The game is open: the change applies the next time it starts." if run else ""


# ---------------------------------------------------------------------------------------------------- input.ini
ACTIONS = [
    ("A", "Advance / accept", "A"), ("B", "Hide the text / back", "B"), ("X", "Dialogue menu", "X"),
    ("Y", "System menu", "Y"), ("L", "Auto mode", "L"), ("R", "Fast forward", "R"), ("ZL", "Previous text", "ZL"),
    ("ZR", "Speed up while held", "ZR"), ("UP", "Up, mini menu", "Up"), ("DOWN", "Down, next text", "Down"),
    ("LEFT", "Left", "Left"), ("RIGHT", "Right", "Right"), ("PLUS", "Plus button", "+"), ("MINUS", "Minus button", "-"),
    ("STICKL", "Left stick press", "LS"), ("STICKR", "Right stick press", "RS"),
    ("SUP", "Left stick up, a page up in lists", "LS Up"), ("SDOWN", "Left stick down, a page down in lists", "LS Down"),
    ("SLEFT", "Left stick left", "LS Left"), ("SRIGHT", "Left stick right", "LS Right"),
    ("RSUP", "Right stick up, one item in lists", "RS Up"), ("RSDOWN", "Right stick down, one item in lists", "RS Down"),
    ("RSLEFT", "Right stick left", "RS Left"), ("RSRIGHT", "Right stick right", "RS Right"),
]
ACTION_IDS = [a[0] for a in ACTIONS]
DEVICES = ("kb", "mouse")
SECTION = {"kb": "[teclado]", "mouse": "[mouse]"}

KEY_NAMES = [
    ("Space", 0x20), ("Enter", 0x0D), ("Escape", 0x1B), ("Tab", 0x09), ("Backspace", 0x08),
    ("Shift", 0x10), ("LShift", 0xA0), ("RShift", 0xA1), ("Ctrl", 0x11), ("LCtrl", 0xA2), ("RCtrl", 0xA3),
    ("Alt", 0x12), ("LAlt", 0xA4), ("RAlt", 0xA5), ("Up", 0x26), ("Down", 0x28), ("Left", 0x25),
    ("Right", 0x27), ("PageUp", 0x21), ("PageDown", 0x22), ("Home", 0x24), ("End", 0x23),
    ("Insert", 0x2D), ("Delete", 0x2E), ("Comma", 0xBC), ("Period", 0xBE),
]
KEY_ALIASES = {"ESPACIO": 0x20, "RETURN": 0x0D, "INTRO": 0x0D, "ESC": 0x1B, "CONTROL": 0x11, "ARRIBA": 0x26,
               "ABAJO": 0x28, "IZQUIERDA": 0x25, "DERECHA": 0x27, "INICIO": 0x24, "FIN": 0x23, "SUPR": 0x2E}
VK_F11 = 0x7A
MOUSE_NAMES = [("Right", "Right button", "DERECHO"), ("Middle", "Middle button (wheel click)", "MEDIO"),
               ("WheelUp", "Wheel up", "RUEDAARRIBA"), ("WheelDown", "Wheel down", "RUEDAABAJO")]


def key_vk(up):
    if len(up) == 1 and (("A" <= up <= "Z") or ("0" <= up <= "9")):
        return ord(up)
    m = re.fullmatch(r"F(\d{1,2})", up)
    if m and 1 <= int(m.group(1)) <= 24 and str(int(m.group(1))) == m.group(1):
        return 0x70 + int(m.group(1)) - 1
    m = re.fullmatch(r"NUM(\d)", up)
    if m:
        return 0x60 + int(m.group(1))
    for name, vk in KEY_NAMES:
        if name.upper() == up:
            return vk
    return KEY_ALIASES.get(up, 0)


def vk_name(vk):
    if 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39:
        return chr(vk)
    if 0x70 <= vk <= 0x87:
        return "F%d" % (vk - 0x70 + 1)
    if 0x60 <= vk <= 0x69:
        return "Num%d" % (vk - 0x60)
    for name, v in KEY_NAMES:
        if v == vk:
            return name
    return ""


def canonical(dev, item):
    """Name the game accepts for an item, '' if it does not know it."""
    n = item.strip().upper()
    if not n:
        return ""
    if dev == "kb":
        vk = key_vk(n)
        return vk_name(vk) if vk and vk != VK_F11 else ""
    for name, _, alias in MOUSE_NAMES:
        if name.upper() == n or n == alias:
            return name
    return ""


def action_id(name):
    n = name.strip().upper()
    n = {"+": "PLUS", "-": "MINUS"}.get(n, n)
    return n if n in ACTION_IDS else ""


def default_binds(dev):
    b = {a: [] for a in ACTION_IDS}
    if dev == "kb":   # defaults_keys of input_tsuki.cpp
        b.update({"A": ["Space", "Enter"], "UP": ["Up"], "DOWN": ["Down"], "LEFT": ["Left"], "RIGHT": ["Right"],
                  "L": ["A"], "ZL": ["R"], "R": ["F"], "ZR": ["Ctrl"], "Y": ["Escape", "S", "L", "C"],
                  "X": ["Tab"], "B": ["Backspace"], "SUP": ["PageUp"], "SDOWN": ["PageDown"]})
    else:   # defaults_mouse
        b.update({"Y": ["Right"], "X": ["Middle"], "DOWN": ["WheelDown"], "ZL": ["WheelUp"]})
    return b


def section_dev(name):
    n = name.strip().upper()
    if n in ("TECLADO", "KEYBOARD"):
        return "kb"
    if n in ("MOUSE", "RATON"):
        return "mouse"
    return None   # [mando] / [pad] and unknown ones are kept as they are written


class InputFile:
    def __init__(self, path):
        self.path = path
        self.exists = os.path.isfile(path)
        self.crlf = False
        self.header = []
        self.present = {d: False for d in DEVICES}
        self.head = {d: "" for d in DEVICES}
        self.kept = {d: [] for d in DEVICES}
        self.binds = {d: default_binds(d) for d in DEVICES}
        self.extra = []
        if self.exists:
            self._load(read_text(path))

    def _load(self, text):
        self.crlf = is_crlf(text)
        sec = "pre"   # 'pre' before any section, None for a section the launcher does not edit
        started = set()
        for raw in split_lines(text):
            s = re.split(r"[;#]", raw, maxsplit=1)[0].strip()
            if s.startswith("["):
                close = s.find("]")
                d = section_dev(s[1:close if close >= 0 else None])
                sec = d
                if d is None:
                    self.extra.append(raw)
                elif d not in started:
                    started.add(d)
                    self.present[d] = True
                    self.head[d] = raw.strip()
                    self.binds[d] = {a: [] for a in ACTION_IDS}
                continue
            if sec == "pre":
                self.header.append(raw)
                continue
            if sec is None:
                self.extra.append(raw)
                continue
            if not s:
                if raw.strip():
                    self.kept[sec].append(raw.strip())   # a comment line
                continue
            eq = s.find("=")
            a = action_id(s[:eq]) if eq >= 0 else ""
            if not a:
                self.kept[sec].append(raw.strip())   # not understood: the game logs it and skips it; so is it kept
                continue
            for item in s[eq + 1:].split(","):
                item = item.strip()
                if not item:
                    continue
                add = canonical(sec, item) or item
                if add not in self.binds[sec][a]:
                    self.binds[sec][a].append(add)
        while self.header and not self.header[-1].strip():
            self.header.pop()

    def lines(self):
        out = list(self.header)
        if not self.exists and not out:
            out = ["; input.ini - keyboard and mouse of Tsukihime, written by the launcher.",
                   "; It can be edited by hand too: format and names in framework/games/tsukihime_common/input.ini.example.",
                   "; A section that is here replaces all the defaults of that device; a device without its section keeps them."]
        for d in DEVICES:
            if not self.present[d]:
                continue
            out.append("")
            out.append(self.head[d] or SECTION[d])
            out.extend(self.kept[d])
            for a in ACTION_IDS:
                if self.binds[d][a]:
                    out.append(a.ljust(7) + "= " + ", ".join(self.binds[d][a]))
        if self.extra:
            out.append("")
            out.extend(self.extra)
        return out

    def touch(self, dev):
        """The first change of a device writes all of it (its section replaces the defaults)."""
        if not self.present[dev]:
            self.present[dev] = True
            if not self.head[dev]:
                self.head[dev] = SECTION[dev]


def device_arg(s):
    d = {"kb": "kb", "keyboard": "kb", "teclado": "kb", "key": "kb", "mouse": "mouse", "raton": "mouse"}.get(s.lower())
    if not d:
        raise Fail("unknown device '%s' (keyboard, mouse)" % s)
    return d


def tsv_out(tsv, kind, *cols):
    print("\t".join([kind] + [str(c).replace("\t", " ").replace("\n", " ") for c in cols]))


def input_load(shown):
    src = source_edition(shown)
    cfg = Config(src)
    path = game_file_path(src, "input.ini", "NX_INPUT_INI", cfg)
    paths = target_paths(shown, "input.ini", "NX_INPUT_INI")
    return InputFile(path), paths


def input_save(f, paths):
    if not paths:
        raise Fail("there is no data folder yet (data/tsuki or data/tsuki_global): run 'launcher.sh install' first")
    out = f.lines()   # (the header comment is only for a file that is new)
    f.exists = True
    where = write_all(paths, out, f.crlf)
    print("Saved: " + where)
    n = after_write_note()
    if n:
        print(n)


def cmd_input(a, shown, tsv):
    verb = a.verb
    if verb == "keys":
        names = [n for n, _ in KEY_NAMES] + [chr(c) for c in range(ord("A"), ord("Z") + 1)] + [str(i) for i in range(10)] \
            + ["F%d" % i for i in range(1, 25)] + ["Num%d" % i for i in range(10)]
        if tsv:
            for n in names:
                tsv_out(tsv, "key", n)
            for n, desc, _ in MOUSE_NAMES:
                tsv_out(tsv, "mouse", n, desc)
        else:
            print("Keyboard (F11 is the fullscreen key and cannot be assigned):\n  " + ", ".join(names))
            print("Mouse:\n  " + ", ".join(n for n, _, _ in MOUSE_NAMES))
        return
    f, paths = input_load(shown)
    if verb == "show":
        note = sync_note(paths, f.path, "input.ini") if paths else ""
        if not paths:
            note = "There is no data folder yet (data/tsuki or data/tsuki_global): run 'launcher.sh install' first."
        if tsv:
            tsv_out(tsv, "file", "input.ini", " and ".join(rel(p) for p in paths) or rel(f.path), int(f.exists), "both" if len(paths) == 2 else "one")
            if note:
                tsv_out(tsv, "note", note)
            if running_editions():
                tsv_out(tsv, "note", "The game is open: changes apply the next time it starts.")
            for aid, desc, sw in ACTIONS:
                tsv_out(tsv, "action", aid, desc, sw)
            for d in DEVICES:
                tsv_out(tsv, "device", d, "custom" if f.present[d] else "default")
                for aid in ACTION_IDS:
                    tsv_out(tsv, "bind", d, aid, ", ".join(f.binds[d][aid]), "custom" if f.present[d] else "default")
            return
        print("File: %s%s" % (" and ".join(rel(p) for p in paths) or rel(f.path), "" if f.exists else " (not created yet: the defaults apply)"))
        if note:
            print(note)
        for d in DEVICES:
            print("\n%s (%s):" % ("Keyboard" if d == "kb" else "Mouse", "set in the file" if f.present[d] else "defaults of the port"))
            for aid, desc, sw in ACTIONS:
                if f.binds[d][aid]:
                    print("  %-7s %-40s %s" % (aid, desc, ", ".join(f.binds[d][aid])))
            free = [aid for aid in ACTION_IDS if not f.binds[d][aid]]
            if free:
                print("  without %s: %s" % ("keys" if d == "kb" else "buttons", ", ".join(free)))
        return
    if verb == "bind":
        if len(a.args) < 3:
            raise Fail("usage: controls bind <keyboard|mouse> <ACTION> <key,key,...> [--add] [--move|--share]")
        d = device_arg(a.args[0])
        aid = action_id(a.args[1])
        if not aid:
            raise Fail("unknown action '%s' (%s)" % (a.args[1], ", ".join(ACTION_IDS)))
        items = []
        for chunk in a.args[2:]:
            for it in chunk.split(","):
                if not it.strip():
                    continue
                c = canonical(d, it)
                if not c:
                    raise Fail("the game does not know the %s '%s' (see: launcher.sh controls keys)" % ("key" if d == "kb" else "mouse button", it.strip()))
                if c not in items:
                    items.append(c)
        if a.add:
            items = [x for x in f.binds[d][aid] if x not in items] + items
        clash = [(o, x) for o in ACTION_IDS if o != aid for x in items if x in f.binds[d][o]]
        if clash and not (a.move or a.share):
            raise Fail("%s already used by %s: use --move to take it from there or --share to keep both" %
                       (", ".join(sorted({x for _, x in clash})), ", ".join(sorted({o for o, _ in clash}))))
        f.touch(d)
        if a.move:
            for o, x in clash:
                f.binds[d][o].remove(x)
        f.binds[d][aid] = items
        input_save(f, paths)
        return
    if verb == "unbind":
        if len(a.args) != 2:
            raise Fail("usage: controls unbind <keyboard|mouse> <ACTION>")
        d = device_arg(a.args[0])
        aid = action_id(a.args[1])
        if not aid:
            raise Fail("unknown action '%s'" % a.args[1])
        f.touch(d)
        f.binds[d][aid] = []
        input_save(f, paths)
        return
    if verb == "clear":
        if len(a.args) != 1:
            raise Fail("usage: controls clear <keyboard|mouse>")
        d = device_arg(a.args[0])
        f.touch(d)
        f.binds[d] = {x: [] for x in ACTION_IDS}
        input_save(f, paths)
        return
    if verb == "reset":
        if len(a.args) != 1:
            raise Fail("usage: controls reset <keyboard|mouse|all>")
        devs = list(DEVICES) if a.args[0].lower() == "all" else [device_arg(a.args[0])]
        if not f.exists:
            print("Nothing to reset: there is no input.ini.")
            return
        for d in devs:
            f.present[d] = False
            f.kept[d] = []
            f.binds[d] = default_binds(d)
        input_save(f, paths)
        return
    raise Fail("unknown verb '%s' for controls (show, keys, bind, unbind, clear, reset)" % verb)


# ---------------------------------------------------------------------------------------------------- title.ini
TITLE_DEFAULTS = {"video": "auto", "pool": "1, 2, 3, 4", "music": "follow", "hotkeys": "on", "click": "on",
                  "unlock": "on", "fade": "500", "swap_delay": "450"}
TITLE_KEYS = list(TITLE_DEFAULTS)
TITLE_HELP = {
    "video": "auto (the game's own choice), 1-4, random, cycle",
    "pool": "videos used by random and cycle: numbers 1-4 separated by commas",
    "music": "follow (the video's music), game, 1-4",
    "hotkeys": "on/off: F5 / F6 change the video on the title screen",
    "click": "on/off: a click on the logo changes the video",
    "unlock": "on/off: only the videos your save has unlocked",
    "fade": "milliseconds of each half of the fade (0-5000)",
    "swap_delay": "milliseconds the fade in waits after the swap (0-5000)",
}


def is_title_section(s):
    return s.strip().lower() in ("[title]", "[titulo]")


def title_clean(key, value):
    """Canonical text of a value, or raise Fail."""
    v = value.strip().lower()
    if key == "video":
        if v in ("auto", "game", "default", "off"):
            return "auto"
        if v in ("random", "cycle") or v in ("1", "2", "3", "4"):
            return v
        raise Fail("video: auto, 1, 2, 3, 4, random or cycle")
    if key == "pool":
        p = []
        for x in v.split(","):
            x = x.strip()
            if not x:
                continue
            if x not in ("1", "2", "3", "4"):
                raise Fail("pool: numbers from 1 to 4 separated by commas")
            if int(x) not in p:
                p.append(int(x))
        if not p:
            raise Fail("pool: at least one video (1 to 4)")
        return ", ".join(str(x) for x in p)
    if key == "music":
        if v in ("follow", "video"):
            return "follow"
        if v in ("game", "auto", "default", "off"):
            return "game"
        if v in ("1", "2", "3", "4"):
            return v
        raise Fail("music: follow, game, 1, 2, 3 or 4")
    if key in ("hotkeys", "click", "unlock"):
        if v in ("on", "1", "yes", "true"):
            return "on"
        if v in ("off", "0", "no", "false"):
            return "off"
        raise Fail("%s: on or off" % key)
    if key in ("fade", "swap_delay"):
        if not v.isdigit() or int(v) > 5000:
            raise Fail("%s: a number from 0 to 5000 (milliseconds)" % key)
        return str(int(v))
    raise Fail("unknown title option '%s' (%s)" % (key, ", ".join(TITLE_KEYS)))


class TitleFile:
    def __init__(self, path):
        self.path = path
        self.exists = os.path.isfile(path)
        text = read_text(path) if self.exists else ""
        self.lines = split_lines(text)
        self.crlf = is_crlf(text)

    def active(self):
        """key -> value of the active lines of [title]."""
        out = {}
        inside = False
        for raw in self.lines:
            s = re.split(r"[;#]", raw, maxsplit=1)[0].strip()
            if not s:
                continue
            if s.startswith("["):
                close = s.find("]")
                inside = close >= 0 and is_title_section(s[:close + 1])
                continue
            if inside and "=" in s:
                k, v = s.split("=", 1)
                out.setdefault(k.strip().lower(), v.strip())
        return out

    def values(self):
        act = self.active()
        res = {}
        for k in TITLE_KEYS:
            try:
                res[k] = (title_clean(k, act[k]), True) if k in act else (TITLE_DEFAULTS[k], False)
            except Fail:
                res[k] = (TITLE_DEFAULTS[k], False)   # the game would log it and skip it
        return res

    def set(self, key, value, is_default):
        """key = value in [title]: the active line if there is one; else the commented example line of that key (the
        one title.ini.example has, so the value stays next to its explanation); else at the end of the section. A value
        equal to the default with no line of its own is not written."""
        L = self.lines
        start, end = None, len(L)
        for i, raw in enumerate(L):
            s = raw.strip()
            if not s.startswith("["):
                continue
            if start is None and is_title_section(s[:s.find("]") + 1]):
                start = i
            elif start is not None:
                end = i
                break
        if start is None:
            if is_default:
                return
            if L and L[-1].strip():
                L.append("")
            L.append("[title]")
            start, end = len(L) - 1, len(L)
        line = "%s = %s" % (key, value)
        commented = None
        for i in range(start + 1, end):
            s = L[i].strip()
            comment = bool(s) and s[0] in ";#"
            body = s[1:].strip() if comment else s
            if "=" not in body or body.split("=", 1)[0].strip().lower() != key:
                continue
            if not comment:
                L[i] = line
                return
            if commented is None:
                commented = i
        if is_default:
            return
        if commented is not None:
            L[commented] = line
            return
        at = end
        while at > start + 1 and not L[at - 1].strip():
            at -= 1
        L.insert(at, line)


def title_template():
    p = os.path.join(ROOT, "framework", "games", "tsukihime_common", "title.ini.example")
    if os.path.isfile(p):
        return split_lines(read_text(p))
    return ["; title.ini - video and music of the title screen (written by the launcher).", "", "[title]"]


def cmd_title(a, shown, tsv):
    src = source_edition(shown)
    paths = target_paths(shown, "title.ini", "NX_TITLE_INI")
    path = game_file_path(src, "title.ini", "NX_TITLE_INI", Config(src))
    t = TitleFile(path)
    verb = a.verb
    if verb == "show":
        note = sync_note(paths, path, "title.ini") if paths else \
            "There is no data folder yet (data/tsuki or data/tsuki_global): run 'launcher.sh install' first."
        vals = t.values()
        if tsv:
            tsv_out(tsv, "file", "title.ini", " and ".join(rel(p) for p in paths) or rel(path), int(t.exists), "both" if len(paths) == 2 else "one")
            if note:
                tsv_out(tsv, "note", note)
            if running_editions():
                tsv_out(tsv, "note", "The game is open: changes apply the next time it starts.")
            for k in TITLE_KEYS:
                tsv_out(tsv, "title", k, vals[k][0], "set" if vals[k][1] else "default", TITLE_HELP[k])
            return
        print("File: %s%s" % (" and ".join(rel(p) for p in paths) or rel(path), "" if t.exists else " (not created yet: the defaults apply)"))
        if note:
            print(note)
        for k in TITLE_KEYS:
            print("  %-11s %-14s %s%s" % (k, vals[k][0], "" if vals[k][1] else "(default) ", TITLE_HELP[k]))
        return
    if not paths:
        raise Fail("there is no data folder yet (data/tsuki or data/tsuki_global): run 'launcher.sh install' first")
    if verb == "set":
        if len(a.args) < 2:
            raise Fail("usage: title set <%s> <value>" % "|".join(TITLE_KEYS))
        key = a.args[0].lower()
        if key not in TITLE_KEYS:
            raise Fail("unknown title option '%s' (%s)" % (key, ", ".join(TITLE_KEYS)))
        value = title_clean(key, " ".join(a.args[1:]))
        if not t.exists and not t.lines:
            t.lines = title_template()   # a new title.ini starts from the example: every option explained, commented
        t.set(key, value, value == TITLE_DEFAULTS[key])
    elif verb == "reset":
        if not t.exists:
            print("Nothing to reset: there is no title.ini.")
            return
        for k in TITLE_KEYS:
            t.set(k, TITLE_DEFAULTS[k], True)
    else:
        raise Fail("unknown verb '%s' for title (show, set, reset)" % verb)
    print("Saved: " + write_all(paths, t.lines, t.crlf))
    n = after_write_note()
    if n:
        print(n)


# ---------------------------------------------------------------------------------------------------- config.txt options
HUD = ["auto", "xbox", "ps", "switch", "keyboard", "off"]
SIZE_RE = re.compile(r"(\d+)x(\d+)")


def size_ok(v):
    m = SIZE_RE.fullmatch(v)
    return bool(m) and int(m.group(1)) >= 320 and int(m.group(2)) >= 180


# name, NX variable, help, accepted values
OPTIONS = [
    ("fullscreen", "NX_FULLSCREEN", "start in fullscreen (F11 and Alt+Enter still switch it)", "on|off"),
    ("window-size", "NX_RESOLUTION", "size of the window when it opens, WIDTHxHEIGHT (default 1280x720)", "default|WxH"),
    ("render-res", "NX_RENDER_RES", "internal resolution (the game draws at most 1920x1080)", "default|WxH"),
    ("fps", "NX_FPS_CAP", "frame rate limit: 60 or 30", "60|30"),
    ("strict-vsync", "NX_FIFO", "strict vertical sync (FIFO present mode)", "on|off"),
    ("gpu", "NX_GPU", "graphics card: a part of its Vulkan name, an index, discrete or integrated", "auto|<name>"),
    ("button-icons", "NX_HUD_GLYPHS", "button icons on screen", "|".join(HUD)),
    ("sdl-pad", "NX_SDL_PAD", "controllers through SDL (PlayStation, Switch, Xbox)", "on|off"),
    ("discord", "NX_DISCORD", "Discord Rich Presence", "on|off"),
    ("mute", "NX_MUTE", "start without sound", "on|off"),
]
OPT_BY_NAME = {o[0]: o for o in OPTIONS}


def option_value(cfg, name):
    var = OPT_BY_NAME[name][1]
    raw = config_get(cfg, var)
    low = raw.lower()
    if name == "fullscreen":
        return "on" if raw == "1" else "off"
    if name in ("strict-vsync", "mute"):
        return "on" if raw and raw != "0" else "off"
    if name == "sdl-pad":
        return "off" if raw.startswith("0") else "on"
    if name == "discord":
        return "off" if raw == "0" else "on"
    if name == "fps":
        try:
            return "30" if 0 < int(raw) <= 30 else "60"
        except ValueError:
            return "60"
    if name == "gpu":
        return raw or "auto"
    if name == "button-icons":
        low = {"auto": "", "playstation": "ps", "teclado": "keyboard", "0": "off"}.get(low, low)
        return low if low in HUD[1:] else ("auto" if not low else low)
    return raw or "default"


def option_config_value(name, value):
    """What goes into config.txt ('' removes the line), or raise Fail."""
    v = value.strip()
    low = v.lower()
    onoff = low in ("on", "1", "yes", "true", "off", "0", "no", "false")
    on = low in ("on", "1", "yes", "true")
    if name in ("fullscreen", "strict-vsync", "mute"):
        if not onoff:
            raise Fail("%s: on or off" % name)
        return "1" if on else ""
    if name in ("sdl-pad", "discord"):
        if not onoff:
            raise Fail("%s: on or off" % name)
        return "" if on else "0"
    if name in ("window-size", "render-res"):
        if low in ("", "default", "auto"):
            return ""
        if not size_ok(low):
            raise Fail("%s: default or WIDTHxHEIGHT (at least 320x180)" % name)
        return low
    if name == "fps":
        if low not in ("60", "30"):
            raise Fail("fps: 60 or 30")
        return "30" if low == "30" else ""
    if name == "gpu":
        return "" if low in ("", "auto") else v
    if name == "button-icons":
        low = {"playstation": "ps", "teclado": "keyboard", "0": "off"}.get(low, low)
        if low not in HUD:
            raise Fail("button-icons: " + ", ".join(HUD))
        return "" if low == "auto" else low
    raise Fail("unknown option '%s' (%s)" % (name, ", ".join(o[0] for o in OPTIONS)))


def exe_reads_config(ed):
    """The binary has the text of the log line of the config.txt reader (apply_linux_platform.py)."""
    p = exe_path(ed)
    if not os.path.isfile(p):
        return True   # nothing to warn about
    needle = b"already in the environment (kept)"
    tail = b""
    with open(p, "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                return False
            if needle in tail + chunk:
                return True
            tail = chunk[-len(needle):]


def vulkaninfo():
    p = shutil.which("vulkaninfo")
    if p:
        return p
    tools = os.environ.get("NX_TOOLS_DIR") or os.path.join(os.path.expanduser("~"), ".local", "tsuki-tools")
    for pat in ("*/bin/vulkaninfo", "*/*/bin/vulkaninfo", "*/*/x86_64/bin/vulkaninfo", "*/x86_64/bin/vulkaninfo"):
        hits = sorted(glob.glob(os.path.join(tools, pat)))
        if hits and os.access(hits[0], os.X_OK):
            return hits[0]
    return ""


def list_gpus():
    """Names of the graphics cards Vulkan sees (the software ones are left out), [] if it cannot say."""
    exe = vulkaninfo()
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "--summary"], capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    names, cur, kind = [], "", ""
    for line in out.splitlines() + ["GPU0:"]:
        m = re.match(r"\s*(deviceName|deviceType)\s*=\s*(.*)", line)
        if m:
            if m.group(1) == "deviceName":
                cur = m.group(2).strip()
            else:
                kind = m.group(2).strip()
            continue
        if re.match(r"\s*GPU\d+:", line):
            if cur and "CPU" not in kind and "llvmpipe" not in cur.lower() and cur not in names:
                names.append(cur)
            cur, kind = "", ""
    return names


def cmd_options(a, shown, tsv):
    verb = a.verb
    if verb == "gpus":
        for n in list_gpus():
            if tsv:
                tsv_out(tsv, "gpu", n)
            else:
                print(n)
        return
    eds = list(EDITIONS) if a.edition_all else [shown]
    if verb == "show":
        cfg = Config(shown)
        if tsv:
            tsv_out(tsv, "file", "config.txt", rel(cfg.path), int(cfg.exists), shown)
            if running_editions():
                tsv_out(tsv, "note", "The game is open: changes apply the next time it starts.")
            if not exe_reads_config(shown):
                tsv_out(tsv, "warn", "This build does not read config.txt yet: apply tools/patches/apply_linux_platform.py and recompile.")
            for name, var, hlp, accepts in OPTIONS:
                tsv_out(tsv, "option", name, option_value(cfg, name), "set" if config_get(cfg, var) else "default", var, hlp, accepts)
            return
        print("File: %s%s" % (rel(cfg.path), "" if cfg.exists else " (not created yet: the defaults apply)"))
        if not exe_reads_config(shown):
            print("WARNING: this build does not read config.txt yet: apply tools/patches/apply_linux_platform.py and recompile.")
        for name, var, hlp, accepts in OPTIONS:
            v = option_value(cfg, name)
            print("  %-13s %-12s %s%s [%s]" % (name, v, "" if config_get(cfg, var) else "(default) ", hlp, accepts))
        return
    for ed in eds:
        if not has_data(ed):
            if len(eds) == 1:
                raise Fail("there is no data folder for %s yet: run 'launcher.sh install' first" % ed)
            continue
        cfg = Config(ed)
        if verb == "set":
            if len(a.args) < 2:
                raise Fail("usage: options set <%s> <value>" % "|".join(o[0] for o in OPTIONS))
            name = a.args[0].lower()
            if name not in OPT_BY_NAME:
                raise Fail("unknown option '%s' (%s)" % (name, ", ".join(o[0] for o in OPTIONS)))
            config_set(cfg, OPT_BY_NAME[name][1], option_config_value(name, " ".join(a.args[1:])))
        elif verb == "reset":
            full = not a.args
            names = [o[0] for o in OPTIONS] if full else [a.args[0].lower()]
            for n in names:
                if n not in OPT_BY_NAME:
                    raise Fail("unknown option '%s' (%s)" % (n, ", ".join(o[0] for o in OPTIONS)))
                config_set(cfg, OPT_BY_NAME[n][1], "")
            if full:   # a full reset also drops the options of old versions
                for old in ("NX_FSR", "NX_FSR_SHARP"):
                    config_set(cfg, old, "")
        else:
            raise Fail("unknown verb '%s' for options (show, set, reset, gpus)" % verb)
        active = any(config_key(x) for x in cfg.lines)
        if not cfg.exists and not active:
            print("%s: nothing to write (all defaults, no config.txt)." % ed)
            continue
        write_text(cfg.path, cfg.lines, cfg.crlf)
        print("Saved: " + rel(cfg.path))
    n = after_write_note()
    if n:
        print(n)


# ---------------------------------------------------------------------------------------------------- main
def main():
    global ROOT
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)   # "| head" is not an error
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--edition", default="japan")
    ap.add_argument("--tsv", action="store_true")
    ap.add_argument("--add", action="store_true")
    ap.add_argument("--move", action="store_true")
    ap.add_argument("--share", action="store_true")
    ap.add_argument("area", choices=("input", "title", "options"))
    ap.add_argument("verb")
    ap.add_argument("args", nargs="*")
    a = ap.parse_intermixed_args()
    ROOT = os.path.abspath(a.root)
    a.edition_all = a.edition == "both"
    shown = "japan" if a.edition_all else a.edition
    if shown not in EDITIONS:
        print("ERROR: unknown edition '%s' (japan, global, both)" % a.edition, file=sys.stderr)
        return 1
    try:
        {"input": cmd_input, "title": cmd_title, "options": cmd_options}[a.area](a, shown, a.tsv)
    except Fail as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
