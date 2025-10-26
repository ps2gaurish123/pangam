# -*- coding: utf-8 -*-
"""
gim_aim_trigger.py — Vision Aiming Demo (windowed-safe)

Features
- Active Window Only (default ON): no moves/clicks unless CS2 is foreground.
- First-shot policy: the first shot on a NEW target is a near "miss" with 2–5 px jitter;
  subsequent shots are precise.
- Smooth mouse movement: eased micro-steps for buttery motion.
- Recoil handling: every 4th shot temporarily flips aim mode (Head <-> Body) for that shot.
- 4s wait before first shot on a new target (per detected anchor).
- Model autoload: loads .pt models from C:\csvisionbot1\modelstest. Prefers nc==4 if present.
- Logging:
  * Text log: C:\csvisionbot1\logs\aim_trigger_*.log
  * CSV detection log: C:\csvisionbot1\logs\dets_*.csv  (class, conf, bbox, picked flag)
  * CSV shots log:      C:\csvisionbot1\logs\shots_*.csv (kind, aim point, jitter, recoil, rc)

Dependencies (Python 3.11 recommended):
  pip install numpy==1.26.4 pywin32==306 opencv-python mss ultralytics
  python -m pywin32_postinstall -install
"""

import os, time, math, random, threading, ctypes, logging, csv, atexit
import tkinter as tk
from ctypes import wintypes
from datetime import datetime

import numpy as np
import cv2
import mss
import win32gui
import win32api
from ultralytics import YOLO

# ------------------------- constants -------------------------
MODELS_DIR = r"C:\csvisionbot1\modelstest"
CS2_WINDOW_TITLES = ["Counter-Strike 2", "cs2"]

# ------------------------- logging -------------------------
def make_logger():
    logs_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(logs_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(logs_dir, f"aim_trigger_{ts}.log")

    logger = logging.getLogger("aimtrig")
    logger.setLevel(logging.INFO)

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(ch)

    logger.info(f"[BOOT] Logging to {log_path}")
    logger.log_path = log_path
    logger.logs_dir = logs_dir
    logger.ts = ts
    return logger

LOGGER = make_logger()

# CSV helpers
DETS_CSV = None
SHOTS_CSV = None

# global stats counters
TOTAL_ENEMY_DETS = 0
TOTAL_SHOTS = 0
NEAR_SHOTS = 0
PRECISE_SHOTS = 0
TARGETS_ENGAGED = 0

def _init_csvs():
    global DETS_CSV, SHOTS_CSV
    dets_path = os.path.join(LOGGER.logs_dir, f"dets_{LOGGER.ts}.csv")
    shots_path = os.path.join(LOGGER.logs_dir, f"shots_{LOGGER.ts}.csv")
    DETS_CSV = open(dets_path, "w", newline="", encoding="utf-8")
    SHOTS_CSV = open(shots_path, "w", newline="", encoding="utf-8")
    csv.writer(DETS_CSV).writerow(
        ["ts", "frame", "picked", "class", "conf", "x1", "y1", "x2", "y2"]
    )
    csv.writer(SHOTS_CSV).writerow(
        ["ts", "shot_counter", "kind", "class", "aim_x", "aim_y",
         "jitter_x", "jitter_y", "recoil_swap", "rc"]
    )
    DETS_CSV.flush(); SHOTS_CSV.flush()
    LOGGER.info(f"[CSV] dets -> {dets_path}")
    LOGGER.info(f"[CSV] shots -> {shots_path}")

def _log_det(frame_id:int, picked:bool, cname:str, conf:float, x1:int, y1:int, x2:int, y2:int):
    if DETS_CSV:
        csv.writer(DETS_CSV).writerow([time.time(), frame_id, int(picked), cname,
                                       round(conf if conf is not None else -1.0, 4),
                                       int(x1), int(y1), int(x2), int(y2)])
        DETS_CSV.flush()

def _log_shot(shot_counter:int, kind:str, cname:str, aim_x:float, aim_y:float,
              jitter_x:int=0, jitter_y:int=0, recoil_swap:bool=False, rc:int=0):
    if SHOTS_CSV:
        csv.writer(SHOTS_CSV).writerow([time.time(), shot_counter, kind, cname,
                                        int(aim_x), int(aim_y), int(jitter_x), int(jitter_y),
                                        int(recoil_swap), int(rc)])
        SHOTS_CSV.flush()

# robust helper to extract a single max confidence value from various types
def max_conf_value(confs):
    if confs is None:
        return None
    # PyTorch Tensor-like
    try:
        if hasattr(confs, "cpu") and hasattr(confs, "numpy"):
            try:
                arr = confs.cpu().numpy()
                if getattr(arr, "size", 0) == 0:
                    return None
                return float(arr.max())
            except Exception:
                pass
        # Numpy / array-like
        arr = np.array(confs)
        if arr.size == 0:
            return None
        return float(arr.max())
    except Exception:
        try:
            # Iterable fallback
            vals = [float(x) for x in confs]
            return float(max(vals)) if vals else None
        except Exception:
            try:
                return float(confs)
            except Exception:
                return None
def _close_csvs():
    # log final aggregated stats before closing CSVs
    try:
        LOGGER.info(f"[FINAL_STATS] total_enemy_detections={TOTAL_ENEMY_DETS} total_shots={TOTAL_SHOTS} near_shots={NEAR_SHOTS} precise_shots={PRECISE_SHOTS} targets_engaged={TARGETS_ENGAGED}")
    except Exception:
        pass
    for f in (DETS_CSV, SHOTS_CSV):
        try:
            if f: f.close()
        except: pass
atexit.register(_close_csvs)

# ------------------------- SendInput (ctypes) -------------------------
# Avoid wintypes.ULONG_PTR (missing on some Py versions); use pointer to ulong instead.
PUL = ctypes.POINTER(ctypes.c_ulong)

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", PUL)
    ]

class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]

class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]

SendInput = ctypes.windll.user32.SendInput
MOUSEEVENTF_MOVE      = 0x0001
MOUSEEVENTF_LEFTDOWN  = 0x0002
MOUSEEVENTF_LEFTUP    = 0x0004

def sendinput_move_relative(dx: int, dy: int):
    inp = INPUT()
    inp.type = 0
    inp.union.mi = MOUSEINPUT(dx, dy, 0, MOUSEEVENTF_MOVE, 0, None)
    return SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

def sendinput_left_click():
    down = INPUT(); down.type = 0
    down.union.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, None)
    up = INPUT(); up.type = 0
    up.union.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, None)
    n1 = SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
    time.sleep(0.010)
    n2 = SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))
    rc = int(n1 + n2)
    # Fallback if SendInput didn't inject clicks (rc == 0)
    if rc == 0:
        try:
            # win32api.mouse_event is a simple fallback (dx,dy,data,extra)
            win32api.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            time.sleep(0.010)
            win32api.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            rc = 1
            LOGGER.info("[FALLBACK_CLICK] used win32api.mouse_event")
        except Exception as e:
            LOGGER.info(f"[FALLBACK_FAIL] click fallback failed: {e}")
    return rc

# Global flag for held mouse state
# MOUSE_HELD = False

# ------------------------- window helpers -------------------------
def find_cs2_hwnd():
    found = []
    def enum_handler(hwnd, acc):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if any(t.lower() in title.lower() for t in CS2_WINDOW_TITLES):
                acc.append(hwnd)
    win32gui.EnumWindows(enum_handler, found)
    return found[0] if found else None

def client_rect_to_screen(hwnd):
    cr = win32gui.GetClientRect(hwnd)
    left, top = win32gui.ClientToScreen(hwnd, (0, 0))
    width, height = cr[2] - cr[0], cr[3] - cr[1]
    return (left, top, width, height)

# ------------------------- config & UI -------------------------
class Config:
    def __init__(self):
        self.screen_width = win32api.GetSystemMetrics(0)
        self.screen_height = win32api.GetSystemMetrics(1)

        self.capture_width = 160
        self.capture_height = 200
        self.center_x = self.screen_width // 2
        self.center_y = self.screen_height // 2
        self.crosshairX = self.capture_width // 2
        self.crosshairY = self.capture_height // 2
        self.region = {"top": 0, "left": 0, "width": self.capture_width, "height": self.capture_height + 80}

        # runtime toggles
        self.Running = True
        self.AimEnabled = True
        self.TriggerEnabled = True
        self.ActiveWindowOnly = True  # default ON

        # movement/timing
        self.Sensitivity = 1.0
        self.delay = 0.007
        self.MovementCoefficientX = 0.80
        self.MovementCoefficientY = 0.65
        self.radius = 70
        self.trigger_delay = 0.02
        self.smooth_sleep = 0.0015  # reduced for snappier yet smoother micro-steps (was 0.0025)

        # model state
        self.models = []
        self.active_model_index = 0

        # team & aim
        self.team = "OFF"       # 'OFF','CT','T'
        self.aim_mode = "head"  # 'head','body'

        # jitter / detection policy
        self.JITTER_MIN = 2
        self.JITTER_MAX = 5
        self.WAIT_BEFORE_SHOOT = 1.0
        self.TARGET_RETAIN_THRESH = 50.0

        # Consecutive detections required to shoot (new)
        self.ConsecutiveDetectionsToShoot = 1

        # whether to apply small jitter before the first (near) shot
        self.ApplyJitter = True
        # move faster when True (fewer steps / shorter sleeps)
        self.FastMove = False
        # if True, after 7 shots on the same anchor do a precise re-center + precise shot
        self.AggressiveAfter7 = False
        # auto-detect whether local player is CT or T from model detections
        self.AutoDetectTeam = False

        # CSV logging policy
        self.LogDetectionsVerbose = False   # if True, log all detections each frame
        self.DetectionLogStride = 15        # otherwise log all detections every N frames

        # hwnd
        self.cs2_hwnd = None

    def recompute_center_from_cs2(self):
        self.cs2_hwnd = find_cs2_hwnd()
        if self.cs2_hwnd:
            try:
                left, top, w, h = client_rect_to_screen(self.cs2_hwnd)
                self.center_x = left + w // 2
                self.center_y = top + h // 2
                return True
            except Exception as e:
                LOGGER.info(f"[WARN] recompute_center_from_cs2 failed: {e}")
        self.center_x = self.screen_width // 2
        self.center_y = self.screen_height // 2
        self.cs2_hwnd = None
        return False

    def update_region(self):
        self.region["left"] = int(self.center_x - self.capture_width // 2)
        self.region["top"] = int(self.center_y - self.capture_height // 2)
        self.region["width"] = int(self.capture_width)
        self.region["height"] = int(self.capture_height + 80)

config = Config()

def load_models(models_dir: str):
    models = []
    if not os.path.isdir(models_dir):
        return models
    for fname in sorted(os.listdir(models_dir)):
        if not fname.lower().endswith(".pt"):
            continue
        path = os.path.join(models_dir, fname)
        try:
            m = YOLO(path)
            names = m.names if hasattr(m, "names") else {}
            nc = len(names) if isinstance(names, dict) else getattr(m, "nc", 0)
            label = f"{os.path.basename(path)} | nc={nc}"
            if isinstance(names, dict) and names:
                label += " | " + ",".join([f"{k}:{v}" for k, v in names.items()])
            models.append({"path": path, "model": m, "names": names if isinstance(names, dict) else {}, "nc": nc, "label": label})
        except Exception as e:
            LOGGER.info(f"[WARN] model load failed: {path} — {e}")
    return models

def choose_default_model(lst):
    for i, m in enumerate(lst):
        if m.get("nc") == 4:
            return i
    return 0 if lst else -1

def CreateUI():
    root = tk.Tk()
    root.title("gaurish")
    root.geometry('380x760')
    root.attributes('-topmost', True)

    found = config.recompute_center_from_cs2()
    config.update_region()
    LOGGER.info(f"[INFO] Capture origin set to {'CS2 client center' if found else 'screen center'} at ({config.center_x},{config.center_y})")

    def refresh_region():
        found_local = config.recompute_center_from_cs2()
        config.update_region()
        LOGGER.info(f"[INFO] Recenter: {'CS2 client' if found_local else 'screen'} -> region {config.region}")

    def quitProgram():
        config.Running = False
        try: root.quit()
        except: pass

    # handlers (sliders/toggles)
    def set_sens(v): config.Sensitivity = float(v)
    def set_delay(v): config.delay = float(v)
    def set_cx(v): config.MovementCoefficientX = float(v)
    def set_cy(v): config.MovementCoefficientY = float(v)
    def set_radius(v): config.radius = int(float(v))
    def set_offx(v):
        dx = int(float(v)); config.crosshairX = config.capture_width // 2 + dx
        config.center_x += dx; config.update_region()
    def set_offy(v):
        dy = int(float(v)); config.crosshairY = config.capture_height // 2 + dy
        config.center_y += dy; config.update_region()
    def set_trig_delay(v): config.trigger_delay = float(v)

    def toggle_aim():
        config.AimEnabled = not config.AimEnabled; aim_var.set(f"Aim: {config.AimEnabled}")
    def toggle_trig():
        config.TriggerEnabled = not config.TriggerEnabled; trig_var.set(f"Trigger: {config.TriggerEnabled}")

    # models
    tk.Label(root, text="Models (auto)").pack(pady=(6,0))
    config.models = load_models(MODELS_DIR)
    if not config.models:
        tk.Label(root, text="No models in C:\\csvisionbot1\\modelstest").pack()
    else:
        config.active_model_index = choose_default_model(config.models)
        choices = [m["label"] for m in config.models]
        mvar = tk.StringVar(root, value=(choices[config.active_model_index]))
        def on_model_change(*_):
            sel = mvar.get()
            for i, m in enumerate(config.models):
                if m["label"] == sel:
                    config.active_model_index = i
                    active_var.set(f"Active: {os.path.basename(m['path'])}")
                    LOGGER.info(f"[MODEL] Active -> {m['label']}")
                    break
        tk.OptionMenu(root, mvar, *choices, command=lambda _: on_model_change()).pack()
        active_var = tk.StringVar(root, value=f"Active: {os.path.basename(config.models[config.active_model_index]['path'])}")
        tk.Label(root, textvariable=active_var).pack()

    # team filter
    tk.Label(root, text="Team Filter").pack(pady=(8,0))
    tvar = tk.StringVar(root, value=config.team)
    def on_team(*_):
        config.team = tvar.get(); LOGGER.info(f"[TEAM] Filter -> {config.team}")
    tk.OptionMenu(root, tvar, "OFF", "CT", "T", command=lambda _: on_team()).pack()

    # Auto-detect team toggle
    auto_team_var = tk.BooleanVar(value=config.AutoDetectTeam)
    def on_auto_team():
        config.AutoDetectTeam = auto_team_var.get()
        LOGGER.info(f"[AUTO_TEAM] AutoDetectTeam -> {config.AutoDetectTeam}")
    tk.Checkbutton(root, text="Auto-detect my team from model", variable=auto_team_var, command=on_auto_team).pack(anchor="w", padx=12, pady=(4,6))

    # Fast move toggle
    fast_var = tk.BooleanVar(value=config.FastMove)
    def on_fast_move():
        config.FastMove = fast_var.get()
        LOGGER.info(f"[CFG] FastMove -> {config.FastMove}")
    tk.Checkbutton(root, text="Fast Move to Target", variable=fast_var, command=on_fast_move).pack(anchor="w", padx=12, pady=(2,4))

    # Aggressive after 7 shots toggle
    aggr_var = tk.BooleanVar(value=config.AggressiveAfter7)
    def on_aggr_change():
        config.AggressiveAfter7 = aggr_var.get()
        LOGGER.info(f"[CFG] AggressiveAfter7 -> {config.AggressiveAfter7}")
    tk.Checkbutton(root, text="Aggressive: after 7 shots -> precise recenter+shoot", variable=aggr_var, command=on_aggr_change).pack(anchor="w", padx=12, pady=(2,4))

    # NEW: consecutive detections dropdown
    tk.Label(root, text="Consecutive Detections to Shoot").pack(pady=(6,0))
    cd_var = tk.StringVar(root, value=str(config.ConsecutiveDetectionsToShoot))
    def on_consec_change(*_):
        try:
            config.ConsecutiveDetectionsToShoot = max(1, int(cd_var.get()))
            LOGGER.info(f"[CFG] ConsecutiveDetectionsToShoot -> {config.ConsecutiveDetectionsToShoot}")
        except Exception:
            pass
    tk.OptionMenu(root, cd_var, "1", "2", "3", "4", "5", "6", command=lambda _: on_consec_change()).pack()

    # NEW: Jitter toggle (radio buttons) — if ON, apply small jitter immediately before the shot
    tk.Label(root, text="Jitter Before First Shot").pack(pady=(6,0))
    jitter_var = tk.StringVar(root, value=("On" if config.ApplyJitter else "Off"))
    def on_jitter_change(*_):
        config.ApplyJitter = (jitter_var.get() == "On")
        LOGGER.info(f"[CFG] ApplyJitter -> {config.ApplyJitter}")
    tk.Radiobutton(root, text="On", value="On", variable=jitter_var, command=on_jitter_change).pack(anchor="w", padx=12)
    tk.Radiobutton(root, text="Off", value="Off", variable=jitter_var, command=on_jitter_change).pack(anchor="w", padx=12)

    # toggles
    aim_var = tk.StringVar(root, value=f"Aim: {config.AimEnabled}")
    trig_var = tk.StringVar(root, value=f"Trigger: {config.TriggerEnabled}")
    tk.Button(root, text="Toggle Aim", command=toggle_aim).pack(pady=(8,0)); tk.Label(root, textvariable=aim_var).pack()
    tk.Button(root, text="Toggle Trigger", command=toggle_trig).pack(pady=(6,0)); tk.Label(root, textvariable=trig_var).pack()

    # aim level
    tk.Label(root, text="Aim Level").pack(pady=(8,0))
    aim_mode_var = tk.StringVar(root, value=config.aim_mode)
    def on_aimmode(*_):
        config.aim_mode = aim_mode_var.get(); LOGGER.info(f"[AIMMODE] -> {config.aim_mode}")
    tk.Radiobutton(root, text="Head", value="head", variable=aim_mode_var, command=on_aimmode).pack(anchor="w", padx=12)
    tk.Radiobutton(root, text="Body", value="body", variable=aim_mode_var, command=on_aimmode).pack(anchor="w", padx=12)

    # Active window gate
    aw_var = tk.BooleanVar(value=config.ActiveWindowOnly)
    def on_aw():
        config.ActiveWindowOnly = aw_var.get(); LOGGER.info(f"[FOREGROUND] ActiveWindowOnly -> {config.ActiveWindowOnly}")
    tk.Checkbutton(root, text="Active Window Only", variable=aw_var, command=on_aw).pack(pady=(8,6), anchor="w", padx=12)

    # CSV logging toggles
    log_all_var = tk.BooleanVar(value=config.LogDetectionsVerbose)
    def on_log_all():
        config.LogDetectionsVerbose = log_all_var.get()
        LOGGER.info(f"[CSV] LogDetectionsVerbose -> {config.LogDetectionsVerbose}")
    tk.Checkbutton(root, text="Log ALL detections each frame", variable=log_all_var, command=on_log_all).pack(anchor="w", padx=12)

    def set_stride(v):
        config.DetectionLogStride = max(1, int(float(v)))
    tk.Label(root, text="Detection Log Stride (frames)").pack()
    s_stride = tk.Scale(root, from_=1, to=60, resolution=1, orient=tk.HORIZONTAL, command=set_stride); s_stride.pack(); s_stride.set(config.DetectionLogStride)

    # sliders
    def add_slider(label, frm, to, step, cb, init):
        tk.Label(root, text=label).pack()
        s = tk.Scale(root, from_=frm, to=to, resolution=step, orient=tk.HORIZONTAL, command=cb); s.pack(); s.set(init); return s
    add_slider("Ingame Sensitivity", 0.1, 10, 0.01, set_sens, config.Sensitivity)
    add_slider("Delay after move (s)", 0.003, 0.05, 0.001, set_delay, config.delay)
    add_slider("Movement Coef X", 0.5, 5, 0.01, set_cx, config.MovementCoefficientX)
    add_slider("Movement Coef Y", 0.5, 5, 0.01, set_cy, config.MovementCoefficientY)
    add_slider("FOV Radius", 10, max(12, config.capture_width // 2), 1, set_radius, config.radius)
    add_slider("Offset Center X", -250, 250, 1, set_offx, 0)
    add_slider("Offset Center Y", -250, 250, 1, set_offy, 0)
    add_slider("Trigger Delay (s)", 0.0, 0.12, 0.005, set_trig_delay, config.trigger_delay)

    tk.Button(root, text="Recenter on CS2", command=refresh_region).pack(pady=6)
    tk.Button(root, text="Quit", command=quitProgram).pack(pady=8)

    root.mainloop()

# ------------------------- target selection -------------------------
TEAM_BY_CLASS = {"CT": {"CT", "CT_HEAD"}, "T": {"T", "T_HEAD"}}

def is_teammate(cname: str, team: str) -> bool:
    if team in ("CT", "T"):
        return cname in TEAM_BY_CLASS.get(team, set())
    return False

def class_is_enemy(cname: str, team: str):
    if team == "OFF":
        return True
    return not is_teammate(cname, team)

def aim_point_for_box(x1, y1, x2, y2, cname: str, aim_mode: str):
    h = (y2 - y1)
    cx = (x1 + x2) * 0.5
    if "HEAD" in cname:
        cy = (y1 + y2) * 0.5 if aim_mode=="head" else (y1 + y2) * 0.5 + 0.35*h
        return cx, cy
    cy = y1 + (0.20*h if aim_mode=="head" else 0.55*h)
    return cx, cy

def pick_target(boxes_xyxy, classes, names_dict, team, aim_mode):
    """
    Return (moveX, moveY, dist, cname, aim_x, aim_y, x1, y1, x2, y2, total_dets, enemy_dets) or None.
    Distances are relative to the center of the capture frame (config.crosshairX/Y).
    """
    total = len(boxes_xyxy)
    if total == 0:
        return None

    has_head_enemy = False
    has_body_enemy = False
    enemy_dets = 0

    cls_names = []
    for i in range(total):
        cname = names_dict.get(int(classes[i]), str(int(classes[i])))
        cls_names.append(cname)
        if class_is_enemy(cname, team):
            enemy_dets += 1
            if "HEAD" in cname:
                has_head_enemy = True
            else:
                has_body_enemy = True

    # prefer head boxes when aim_mode=="head" and heads exist;
    # if aim_mode=="body" but only heads detected, prefer heads so we still hit target
    prefer_heads = (aim_mode == "head" and has_head_enemy) or (aim_mode == "body" and not has_body_enemy and has_head_enemy)

    best_idx = None
    best_dist = float("inf")
    best_moveX = best_moveY = 0.0
    best_aim_x = best_aim_y = 0.0
    best_cname = None
    best_bbox = (0, 0, 0, 0)

    for i in range(total):
        x1, y1, x2, y2 = boxes_xyxy[i].tolist()
        cname = cls_names[i]

        # Skip teammates / non-enemies
        if not class_is_enemy(cname, team):
            continue

        # Respect head preference
        if prefer_heads and "HEAD" not in cname:
            continue
        if (not prefer_heads) and ("HEAD" in cname):
            continue

        aim_x, aim_y = aim_point_for_box(x1, y1, x2, y2, cname, aim_mode)
        moveX = aim_x - config.crosshairX
        moveY = aim_y - config.crosshairY
        dist = math.hypot(moveX, moveY)

        if dist < best_dist:
            best_dist = dist
            best_idx = i
            best_moveX = moveX
            best_moveY = moveY
            best_aim_x = aim_x
            best_aim_y = aim_y
            best_cname = cname
            best_bbox = (x1, y1, x2, y2)

    if best_idx is None:
        return None

    # include bbox coords so caller can recompute aim for head/body preference later
    return best_moveX, best_moveY, best_dist, best_cname, best_aim_x, best_aim_y, best_bbox[0], best_bbox[1], best_bbox[2], best_bbox[3], total, enemy_dets

# ------------------------- smooth movement -------------------------
def smooth_move(dx: int, dy: int):
    """Move in eased micro-steps for smoothness."""
    dist = math.hypot(dx, dy)
    steps = max(3, min(15, int(dist / 6)))  # dynamic step count
    prev_x = prev_y = 0
    for i in range(1, steps+1):
        t = i / steps
        s = 1 - pow(1 - t, 3)  # ease-out cubic
        tx = int(round(dx * s))
        ty = int(round(dy * s))
        stepx = tx - prev_x
        stepy = ty - prev_y
        if stepx != 0 or stepy != 0:
            sendinput_move_relative(stepx, stepy)
        prev_x, prev_y = tx, ty
        time.sleep(config.smooth_sleep)

def smooth_move_fast(dx: int, dy: int):
    """Faster, more aggressive eased micro-steps (fewer steps, shorter sleeps)."""
    dist = math.hypot(dx, dy)
    # fewer, coarser steps for speed; ensure at least 1 step
    steps = max(1, min(6, int(max(1.0, dist) / 30)))
    prev_x = prev_y = 0
    local_sleep = max(0.0003, config.smooth_sleep * 0.35)
    for i in range(1, steps+1):
        t = i / steps
        s = 1 - pow(1 - t, 2)  # faster ease
        tx = int(round(dx * s))
        ty = int(round(dy * s))
        stepx = tx - prev_x
        stepy = ty - prev_y
        if stepx != 0 or stepy != 0:
            sendinput_move_relative(stepx, stepy)
        prev_x, prev_y = tx, ty
        time.sleep(local_sleep)

def move(dx: int, dy: int):
    """Wrapper: choose fast or normal smooth movement based on config.FastMove."""
    if dx == 0 and dy == 0:
        return
    if config.FastMove:
        smooth_move_fast(dx, dy)
    else:
        smooth_move(dx, dy)

# ------------------------- main -------------------------
def random_jitter():
    r = random.uniform(config.JITTER_MIN, config.JITTER_MAX)
    theta = random.uniform(0.0, 2.0*math.pi)
    jx = int(round(r*math.cos(theta))); jy = int(round(r*math.sin(theta)))
    if jx==0 and jy==0:
        jx = config.JITTER_MIN
    return jx, jy

# New helper: infer CT/T from current detections
def infer_team_from_frame(boxes_xyxy, classes, names_dict):
    """
    Simple heuristic:
    - If a detected bbox center is very near the capture crosshair, use that detection's team.
    - Otherwise count CT vs T detections and pick majority if non-zero.
    Returns 'CT' or 'T' or None.
    """
    try:
        ct_count = 0
        t_count = 0
        # crosshair is in capture coords
        cx = config.crosshairX
        cy = config.crosshairY
        for i in range(len(boxes_xyxy)):
            x1, y1, x2, y2 = boxes_xyxy[i].tolist()
            cname = names_dict.get(int(classes[i]), str(int(classes[i])))
            # determine team token
            cname_up = cname.upper() if isinstance(cname, str) else str(cname).upper()
            team = None
            if "CT" in cname_up:
                team = "CT"
                ct_count += 1
            elif "T" in cname_up:
                team = "T"
                t_count += 1
            # if a bbox center is very close to crosshair, prefer that
            bcx = (x1 + x2) * 0.5
            bcy = (y1 + y2) * 0.5
            if team is not None:
                if math.hypot(bcx - cx, bcy - cy) <= max(8, min(config.capture_width, config.capture_height) * 0.12):
                    return team
        # majority
        if ct_count > t_count and ct_count > 0:
            return "CT"
        if t_count > ct_count and t_count > 0:
            return "T"
    except Exception:
        pass
    return None

def main():
    ui_thread = threading.Thread(target=CreateUI, daemon=True); ui_thread.start()
    _init_csvs()

    if not config.models:
        config.models = load_models(MODELS_DIR)
        if not config.models:
            LOGGER.info(f"[FATAL] no models in {MODELS_DIR}")
            return
        if not (0 <= config.active_model_index < len(config.models)):
            config.active_model_index = choose_default_model(config.models)

    LOGGER.info("[INFO] Models loaded:")
    for m in config.models:
        LOGGER.info(" - " + m["label"])

    screen = mss.mss()
    time.sleep(0.5)
    config.recompute_center_from_cs2()

    # State for first-shot miss & detection timing
    current_anchor = None  # (ax, ay)
    current_target_cname = None
    first_seen_ts = 0.0
    first_shot_done = False
    shot_counter = 0
    # recenter after a random number of shots between 8 and 10
    shots_since_recenter = 0
    recenter_after_shots = random.randint(8, 10)
    # per-anchor 3-shot burst counter
    shots_in_burst = 0
    frame_id = 0
    consec_detects = 0

    while config.Running:
        time.sleep(0.001)
        if not config.AimEnabled:
            time.sleep(0.03)
            frame_id += 1
            continue

        # Foreground gating
        if config.ActiveWindowOnly:
            try:
                fg = win32gui.GetForegroundWindow()
            except Exception:
                fg = None
            if config.cs2_hwnd is None:
                config.recompute_center_from_cs2()
            if fg is None or config.cs2_hwnd is None or fg != config.cs2_hwnd:
                time.sleep(0.03)
                frame_id += 1
                continue

        # Grab frame
        entry = config.models[config.active_model_index]
        model, names = entry["model"], entry["names"]
        frame = np.array(screen.grab(config.region))
        frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

        # Inference
        try:
            r0 = model.predict(source=frame, conf=0.45, verbose=False, max_det=15)[0]
        except Exception as e:
            LOGGER.info(f"[WARN] inference error: {e}")
            frame_id += 1
            continue

        boxes = r0.boxes.xyxy
        clses = r0.boxes.cls if hasattr(r0.boxes, "cls") else np.zeros((len(boxes),))
        confs = r0.boxes.conf if hasattr(r0.boxes, "conf") else None

        # Auto-detect team from detections if enabled
        if config.AutoDetectTeam and len(boxes) > 0:
            detected = infer_team_from_frame(boxes, clses, names)
            if detected:
                # set config.team so selection logic and logs use detected value
                if detected != config.team:
                    LOGGER.info(f"[AUTO_TEAM] inferred team -> {detected}")
                config.team = detected

        # Recoil handling: every 4th shot temporarily swap aim
        effective_mode = config.aim_mode
        recoil_swap = False
        if shot_counter > 0 and shot_counter % 4 == 0:
            effective_mode = ("body" if config.aim_mode == "head" else "head")
            recoil_swap = True

        # capture team at this moment to avoid a race with the UI thread changing config.team
        team_local = config.team

        picked = pick_target(boxes, clses, names, team_local, effective_mode)

        # Lightweight CSV detection logging
        log_all = config.LogDetectionsVerbose or (frame_id % max(1, int(config.DetectionLogStride)) == 0)
        if log_all and len(boxes) > 0:
            for i in range(len(boxes)):
                x1, y1, x2, y2 = boxes[i].tolist()
                cname = names.get(int(clses[i]), str(int(clses[i])))
                conf = float(confs[i]) if confs is not None else None
                _log_det(frame_id, False, cname, conf, x1, y1, x2, y2)

        if picked is None:
            # nothing picked
            consec_detects = 0
            frame_id += 1
            continue

        # unpack including bbox coords returned by pick_target
        moveX, moveY, dist, cname, aim_x, aim_y, bx1, by1, bx2, by2, total_dets, enemy_dets = picked

        # If team filter is active ensure we don't follow teammates.
        # Use the captured team_local used for selection to remain consistent.
        if not class_is_enemy(cname, team_local):
            # release held mouse if we skip due to teammate
            consec_detects = 0
            frame_id += 1
            continue

        # Mark the chosen target (a small 2x2 box at aim point)
        _log_det(frame_id, True, cname,
                 max_conf_value(confs),
                 int(aim_x-1), int(aim_y-1), int(aim_x+1), int(aim_y+1))

        # Anchor & consecutive detection tracking:
        now = time.time()
        if current_target_cname is None:
            # first-ever detection
            current_anchor = (aim_x, aim_y)
            current_target_cname = cname
            first_seen_ts = now
            first_shot_done = False
            consec_detects = 1
            # reset per-target shot counters so new target can be engaged immediately
            shots_since_recenter = 0
            recenter_after_shots = random.randint(8, 10)
            shots_in_burst = 0
        else:
            # if different class or moved beyond retain threshold -> reset anchor/count
            dx_a = aim_x - (current_anchor[0] if current_anchor else aim_x)
            dy_a = aim_y - (current_anchor[1] if current_anchor else aim_y)
            if cname != current_target_cname or math.hypot(dx_a, dy_a) > config.TARGET_RETAIN_THRESH:
                current_anchor = (aim_x, aim_y)
                current_target_cname = cname
                first_seen_ts = now
                first_shot_done = False
                consec_detects = 1
                # reset recenter counters on target change
                shots_since_recenter = 0
                recenter_after_shots = random.randint(8, 10)
                shots_in_burst = 0
                LOGGER.info(f"[TARGET] new anchor=({int(aim_x)},{int(aim_y)}) class={cname} t0={first_seen_ts:.2f}")
            else:
                # same target & close -> increment consecutive detection counter
                consec_detects += 1

        # Always move toward the aim point first (follow enemy).
        # Move the mouse by the full required delta so we actually reach the aim point
        dx_full = int(round(moveX))
        dy_full = int(round(moveY))
        if dx_full != 0 or dy_full != 0:
            move(dx_full, dy_full)
            LOGGER.info(f"[MOVE_SMOOTH] full_dx={dx_full} full_dy={dy_full} dist={dist:.1f} mode={effective_mode} fast={config.FastMove}")
        time.sleep(config.delay)

        # Re-evaluate time after movement, then decide whether shooting is allowed.
        now = time.time()
        # Defensive allowed check: immediate allow if consecutive threshold is 1
        allowed = ((now - first_seen_ts) >= config.WAIT_BEFORE_SHOOT) or (consec_detects >= config.ConsecutiveDetectionsToShoot) or (config.ConsecutiveDetectionsToShoot <= 1)
        LOGGER.info(f"[TRIGGER_CHECK] allowed={allowed} elapsed={now-first_seen_ts:.2f}s consec={consec_detects}/{config.ConsecutiveDetectionsToShoot} WAIT={config.WAIT_BEFORE_SHOOT} TriggerEnabled={config.TriggerEnabled}")

        # Trigger policy: shoot only after the movement above has completed
        if config.TriggerEnabled and allowed:
            # determine simple team label for logs (CT/T/UNK)
            if "CT" in cname:
                target_team = "CT"
            elif "T" in cname:
                target_team = "T"
            else:
                target_team = "UNK"

            # 3-shot burst behavior: perform up to 3 taps per anchor, then move to precise aim point
            try:
                # fire next burst shot(s) if we haven't completed the 3-shot burst yet
                if shots_in_burst < 3:
                    # perform one tap (we do a single tap per loop iteration to remain responsive)
                    if config.trigger_delay > 0:
                        time.sleep(config.trigger_delay)
                    # optional small jitter on first shot of a burst
                    if shots_in_burst == 0 and config.ApplyJitter:
                        jx, jy = random_jitter()
                        move(jx, jy)
                    else:
                        jx, jy = 0, 0
                    rc = sendinput_left_click()
                    shot_counter += 1
                    shots_since_recenter += 1
                    shots_in_burst += 1
                    LOGGER.info(f"[BURST_SHOT] idx={shots_in_burst}/3 team={target_team} class={cname} aim=({int(aim_x)},{int(aim_y)}) jitter=({jx},{jy}) rc={rc}")
                    _log_shot(shot_counter, "BURST", cname, aim_x, aim_y, jx, jy, recoil_swap, rc)
                # if we've completed a burst, move to precise aim point (head/body) then reset burst counter
                if shots_in_burst >= 3:
                    try:
                        # compute precise aim using the selected aim_mode (head/body)
                        rc_aim_x, rc_aim_y = aim_point_for_box(bx1, by1, bx2, by2, cname, config.aim_mode)
                        rc_dx = int(round(rc_aim_x - config.crosshairX))
                        rc_dy = int(round(rc_aim_y - config.crosshairY))
                        # move to precise point immediately, then hold for a short random gap
                        if rc_dx != 0 or rc_dy != 0:
                            move(rc_dx, rc_dy)
                            LOGGER.info(f"[BURST_RECENTER] moved to precise aim ({int(rc_aim_x)},{int(rc_aim_y)}) mode={config.aim_mode} fast={config.FastMove}")
                        # hold that position for 0.3–0.5s before continuing the next burst cycle
                        gap = random.uniform(0.3, 0.5)
                        LOGGER.info(f"[BURST_GAP] holding for {gap:.3f}s before next burst")
                        time.sleep(gap)
                        # reset burst counter to start a new 3-shot cycle for this anchor
                        shots_in_burst = 0
                    except Exception as e:
                        LOGGER.info(f"[WARN] burst recenter failed: {e}")
                # regular recenter after random threshold (if configured)
                if shots_since_recenter >= recenter_after_shots:
                    try:
                        rc_aim_x, rc_aim_y = aim_point_for_box(bx1, by1, bx2, by2, cname, config.aim_mode)
                        rc_dx = int(round(rc_aim_x - config.crosshairX))
                        rc_dy = int(round(rc_aim_y - config.crosshairY))
                        if rc_dx != 0 or rc_dy != 0:
                            move(rc_dx, rc_dy)
                            LOGGER.info(f"[RECENTER] after {shots_since_recenter} shots -> moved to ({int(rc_aim_x)},{int(rc_aim_y)}) using aim_mode={config.aim_mode} fast={config.FastMove}")
                        shots_since_recenter = 0
                        recenter_after_shots = random.randint(8, 10)
                    except Exception as e:
                        LOGGER.info(f"[WARN] recenter failed: {e}")
            except Exception as e:
                LOGGER.info(f"[WARN] burst shot failed: {e}")
        else:
            # Not allowed to shoot this frame — nothing to do.
            pass

        # If target changed away from current_target_cname and mouse was held, release
        # (handled earlier on teammate/none switch). Continue loop.
        frame_id += 1

    LOGGER.info("[EXIT] Clean shutdown")

if __name__ == "__main__":
    main()
