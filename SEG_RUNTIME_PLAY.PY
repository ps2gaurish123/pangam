import os, sys, time, argparse, random
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import torch
import numpy as np
import cv2
import mss
import threading
import datetime
import queue
import tkinter as tk
from tkinter import scrolledtext
try:
    from PIL import Image, ImageTk
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False

PROJ = "C:/csvisionbot1"
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core.sendinput_union import key_down as _key_down, key_up as _key_up, tap as _tap, mouse_move
from core.focus_win import focus_window_by_title_sub
from core.capture import Capture

# --- Key logging wrappers ---
class KeyLogger:
    current = None  # type: KeyLogger | None

    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.f = open(path, "a", buffering=1)
        self.write(f"# start {datetime.datetime.now().isoformat()}\n")

    def write(self, s):
        try:
            self.f.write(s)
        except Exception:
            pass

    def log(self, kind, key, extra=""):
        ts = time.time()
        self.write(f"{ts:.3f}\t{kind}\t{key}{(' '+str(extra)) if extra else ''}\n")

    def close(self):
        try:
            self.write(f"# end {datetime.datetime.now().isoformat()}\n")
            self.f.close()
        except Exception:
            pass

def key_down(k):
    if KeyLogger.current:
        KeyLogger.current.log("down", k)
    _key_down(k)

def key_up(k):
    if KeyLogger.current:
        KeyLogger.current.log("up", k)
    _key_up(k)

def tap(k, secs):
    if KeyLogger.current:
        KeyLogger.current.log("tap", k, secs)
    _tap(k, secs)

# --- YOLO ---
def _try_glob(paths):
    import glob, os
    out = []
    for p in paths:
        out += glob.glob(p, recursive=True)
    out = sorted(out, key=lambda p: os.path.getmtime(p), reverse=True)
    return out

def resolve_weights(hint):
    import os
    if hint and os.path.isfile(hint): return hint
    c = _try_glob([
        "C:/csvisionbot1/models/*.pt",
        "C:/Users/Administrator/runs/segment/**/weights/best.pt",
        "C:/Users/Administrator/runs/segment/**/weights/last.pt",
        "C:/csvisionbot1/runs/segment/**/weights/best.pt",
        "C:/csvisionbot1/runs/segment/**/weights/last.pt",
    ])
    if c: return c[0]
    raise FileNotFoundError("YOLO weights not found")

def load_model(weights_path):
    from ultralytics import YOLO
    wp = resolve_weights(weights_path)
    print(f"[seg] Using weights: {wp}")
    model = YOLO(wp)
    try:
        model.fuse()
    except Exception:
        pass
    return model

def grab_cs2_frame(mss_ctx, mon):
    img = np.array(mss_ctx.grab(mon))
    return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

def overlay_mask_color(dst, mask, color, alpha):
    """Blend `color` into `dst` where `mask` is True using the given alpha."""
    if mask is None:
        return
    if mask.dtype != np.bool_:
        mask_bool = mask > 0
    else:
        mask_bool = mask
    if not np.any(mask_bool):
        return
    alpha = float(max(0.0, min(1.0, alpha)))
    if alpha == 0.0:
        return
    inv_alpha = 1.0 - alpha
    color_arr = np.array(color, dtype=np.float32)
    region = dst[mask_bool].astype(np.float32, copy=False)
    blended = inv_alpha * region + alpha * color_arr
    dst[mask_bool] = blended.astype(dst.dtype)

SCAN_HOLD_MIN = 0.18
SCAN_HOLD_MAX = 0.45
SCAN_INTER_ATTEMPT_COOLDOWN_MIN = 0.15
SCAN_INTER_ATTEMPT_COOLDOWN_MAX = 0.25
SCAN_INTER_EPISODE_COOLDOWN_MIN = 0.80
SCAN_INTER_EPISODE_COOLDOWN_MAX = 1.20
SCAN_ATTEMPTS_PER_EPISODE = 3

MICRO_NUDGE_MIN_INTERVAL = 0.25
OPPOSITE_NUDGE_LOCKOUT = 0.35

STUCK_TIMEOUT_SEC = 1.2
TURN_CHUNKS = 5
TURN_CHUNK_DEG = 10

VISION_RECOVER_CONSEC_FRAMES = 2
WALL_HYSTERESIS_PCT = 0.15

LOOP_DEBOUNCE_SEC = 5.0
CORNER_DEBOUNCE_SEC_MIN = 0.6
CORNER_DEBOUNCE_SEC_MAX = 0.8


class MotionController:
    """Manages WASD key state and mouse nudges."""

    def __init__(self):
        self.state = {"W": False, "A": False, "S": False, "D": False}
        self.pulses = {}

    def _set(self, key, value):
        if value and not self.state[key]:
            key_down(key)
            self.state[key] = True
        elif not value and self.state[key]:
            key_up(key)
            self.state[key] = False

    def forward(self, on=True):
        self._set("W", on)
        if on:
            self._set("S", False)

    def back(self, on=True):
        self._set("S", on)
        if on:
            self._set("W", False)

    def strafe_left(self, on=True):
        self._set("A", on)
        if on:
            self._set("D", False)

    def strafe_right(self, on=True):
        self._set("D", on)
        if on:
            self._set("A", False)

    def stop_all(self):
        for k in ("W", "A", "S", "D"):
            self._set(k, False)

    def is_forward(self):
        return self.state["W"]

    def micro_turn(self, direction, amount):
        """direction: -1 for left, +1 for right."""
        amount = int(max(1, abs(amount)))
        mouse_move(amount * direction, 0)

    def turn(self, direction, amount):
        direction = -1 if direction < 0 else 1
        amount = int(max(1, abs(amount)))
        mouse_move(direction * amount, 0)

    def scan(self, hold_time):
        tap("E", hold_time)

    def tap_key(self, key, hold_time):
        tap(key, hold_time)

    def mouse_step(self, dx, dy):
        mouse_move(int(dx), int(dy))

    def pulse_strafe(self, direction, duration):
        key = "D" if direction > 0 else "A"
        if direction > 0:
            self.strafe_right(True)
        else:
            self.strafe_left(True)
        self.pulses[key] = time.time() + duration

    def update(self):
        if not self.pulses:
            return
        now = time.time()
        expired = [k for k, t in self.pulses.items() if now >= t]
        for key in expired:
            if key == "A":
                self.strafe_left(False)
            elif key == "D":
                self.strafe_right(False)
            elif key == "W":
                self.forward(False)
            elif key == "S":
                self.back(False)
            self.pulses.pop(key, None)

def log_once(nav_state, key, message, log, ttl=0.6):
    entries = nav_state.setdefault("one_time_logs", {})
    now = time.time()
    last = entries.get(key, 0.0)
    if now - last >= ttl:
        log(message)
        entries[key] = now

class ScanArbiter:
    def __init__(self):
        self.rng = random.Random()
        self.episode_active = False
        self.episode_id = 0
        self.side = None
        self.attempt_idx = 0
        self.last_attempt_time = -1e9
        self.last_episode_end = -1e9
        self.inter_attempt_cd = SCAN_INTER_ATTEMPT_COOLDOWN_MIN
        self.inter_episode_cd = SCAN_INTER_EPISODE_COOLDOWN_MIN
        self.last_hold = 0.0

    def _rand(self, lo, hi):
        return self.rng.uniform(lo, hi)

    def begin_episode(self, preferred_side, now):
        if self.episode_active:
            return
        self.episode_active = True
        self.episode_id += 1
        self.side = preferred_side
        self.attempt_idx = 0
        self.last_attempt_time = -1e9
        self.inter_attempt_cd = self._rand(SCAN_INTER_ATTEMPT_COOLDOWN_MIN, SCAN_INTER_ATTEMPT_COOLDOWN_MAX)
        self.inter_episode_cd = self._rand(SCAN_INTER_EPISODE_COOLDOWN_MIN, SCAN_INTER_EPISODE_COOLDOWN_MAX)
        self.last_hold = 0.0

    def can_start_episode(self, now):
        if self.episode_active:
            return True
        return (now - self.last_episode_end) >= self.inter_episode_cd

    def ensure_episode(self, preferred_side, now, force=False):
        if not self.episode_active:
            if not self.can_start_episode(now) and not force:
                return False
            self.begin_episode(preferred_side, now)
        else:
            if preferred_side and preferred_side != self.side:
                # do not alternate mid-episode
                pass
        return True

    def attempt(self, now):
        if not self.episode_active:
            return None
        if self.attempt_idx >= SCAN_ATTEMPTS_PER_EPISODE:
            return None
        if now - self.last_attempt_time < self.inter_attempt_cd:
            return None
        self.attempt_idx += 1
        self.last_attempt_time = now
        hold = self._rand(SCAN_HOLD_MIN, SCAN_HOLD_MAX)
        self.last_hold = hold
        self.inter_attempt_cd = self._rand(SCAN_INTER_ATTEMPT_COOLDOWN_MIN, SCAN_INTER_ATTEMPT_COOLDOWN_MAX)
        return {
            "episode_id": self.episode_id,
            "attempt_idx": self.attempt_idx,
            "side": self.side,
            "hold": hold,
            "cooldown_active": False,
        }

    def cooldown_active(self, now):
        if self.episode_active:
            if self.attempt_idx >= SCAN_ATTEMPTS_PER_EPISODE:
                return False
            return (now - self.last_attempt_time) < self.inter_attempt_cd
        return (now - self.last_episode_end) < self.inter_episode_cd

    def mark_complete(self, now, success):
        self.episode_active = False
        self.side = None
        self.attempt_idx = 0
        self.last_episode_end = now
        if success:
            # shorten episode cooldown when successful
            self.inter_episode_cd = self._rand(SCAN_INTER_EPISODE_COOLDOWN_MIN * 0.5, SCAN_INTER_EPISODE_COOLDOWN_MIN)

    def mark_failure(self, now):
        self.mark_complete(now, success=False)

    def attempts_exhausted(self):
        return self.episode_active and self.attempt_idx >= SCAN_ATTEMPTS_PER_EPISODE

    def active_side(self):
        return self.side

def init_micro_state(nav_state):
    if "micro_state" in nav_state:
        return nav_state["micro_state"]
    nav_state["micro_state"] = {
        "last_time": {"left": -1e9, "right": -1e9},
        "lock_until": {"left": -1e9, "right": -1e9},
    }
    return nav_state["micro_state"]

def request_micro(nav_state, direction, now, log, label):
    state = init_micro_state(nav_state)
    side = "left" if direction < 0 else "right"
    opp = "right" if side == "left" else "left"
    if now - state["last_time"][side] < MICRO_NUDGE_MIN_INTERVAL:
        log_once(nav_state, f"micro_min_{side}", f"[RATE] Blocked {label}: min interval", log)
        return False
    if now < state["lock_until"][side]:
        log_once(nav_state, f"micro_lock_{side}", f"[RATE] Blocked {label}: lockout", log)
        return False
    state["last_time"][side] = now
    state["lock_until"][opp] = max(state["lock_until"][opp], now + OPPOSITE_NUDGE_LOCKOUT)
    return True

def apply_micro_turn(nav_state, direction, controller, args, log, label):
    now = time.time()
    if not request_micro(nav_state, direction, now, log, label):
        return False
    controller.micro_turn(-1 if direction < 0 else 1, args.micro_turn_dx)
    return True

def select_scan_side(mode, signals, nav_state):
    last = nav_state.get("last_scan_side", "E")
    if mode == "left_wall":
        if signals.get("left_wall_present"):
            side = "E"
        elif signals.get("right_wall_present"):
            side = "Q"
        else:
            side = last
    else:
        left_obs = signals.get("left_obs", 1.0)
        right_obs = signals.get("right_obs", 1.0)
        center_err = signals.get("heading_to_center")
        delta = 0.02
        if left_obs + delta < right_obs:
            side = "Q"
        elif right_obs + delta < left_obs:
            side = "E"
        elif center_err is not None:
            side = "E" if center_err > 0 else "Q"
        else:
            side = last
    nav_state["last_scan_side"] = side
    return side

def log_scan_event(log, reason, attempt, signals, nav_state, cooldown_active, status, recovery_stage="none", stuck_timer=0.0):
    if attempt:
        hold_ms = int(attempt["hold"] * 1000)
        attempt_idx = attempt["attempt_idx"]
        episode_id = attempt["episode_id"]
        side = attempt["side"]
    else:
        hold_ms = 0
        attempt_idx = 0
        arb = nav_state.get("scan_arbiter")
        episode_id = arb.episode_id if arb else -1
        side = arb.active_side() if arb and arb.active_side() else "?"
    mask_valid = signals.get("mask_valid", False)
    left_conf = signals.get("left_obs", 0.0)
    right_conf = signals.get("right_obs", 0.0)
    center_err = signals.get("heading_to_center")
    msg = (f"[SCAN] reason={reason} ep={episode_id} side={side} hold_ms={hold_ms} "
           f"attempt={attempt_idx}/{SCAN_ATTEMPTS_PER_EPISODE} cooldown={cooldown_active} "
           f"mask_valid={mask_valid} left_conf={left_conf:.2f} right_conf={right_conf:.2f} "
           f"center_err={'n/a' if center_err is None else f'{center_err:.1f}'} "
           f"status={status} recovery_stage={recovery_stage} stuck_timer={stuck_timer:.2f}")
    log(msg)

def trigger_scan(reason, mode, signals, controller, nav_state, log, recovery_stage="none", stuck_timer=0.0,
                 force_side=None, force_start=False):
    arbiter = nav_state.get("scan_arbiter")
    if arbiter is None:
        arbiter = ScanArbiter()
        nav_state["scan_arbiter"] = arbiter
    now = time.time()
    desired_side = force_side or select_scan_side(mode, signals, nav_state)
    if not arbiter.ensure_episode(desired_side, now, force=force_start):
        log_scan_event(log, reason, None, signals, nav_state, cooldown_active=True, status="episode-cooldown",
                       recovery_stage=recovery_stage, stuck_timer=stuck_timer)
        return False
    attempt = arbiter.attempt(now)
    if attempt is None:
        status = "attempt-cooldown"
        cooldown = arbiter.cooldown_active(now)
        if arbiter.attempts_exhausted():
            status = "exhausted"
        log_scan_event(log, reason, None, signals, nav_state, cooldown_active=cooldown, status=status,
                       recovery_stage=recovery_stage, stuck_timer=stuck_timer)
        return False
    controller.tap_key(attempt["side"], attempt["hold"])
    log_scan_event(log, reason, attempt, signals, nav_state, cooldown_active=False, status="triggered",
                   recovery_stage=recovery_stage, stuck_timer=stuck_timer)
    return True

def handle_scan_feedback(nav_state, mask_valid):
    arbiter = nav_state.get("scan_arbiter")
    if not arbiter:
        return
    if arbiter.episode_active and mask_valid:
        arbiter.mark_complete(time.time(), success=True)

def update_vision_state(nav_state, mask_valid):
    if mask_valid:
        nav_state["vision_good_frames"] = nav_state.get("vision_good_frames", 0) + 1
    else:
        nav_state["vision_good_frames"] = 0
        nav_state["vision_block"] = True
    if nav_state.get("vision_block", True):
        if nav_state["vision_good_frames"] >= VISION_RECOVER_CONSEC_FRAMES:
            nav_state["vision_block"] = False
    else:
        if not mask_valid:
            nav_state["vision_block"] = True
    return not nav_state.get("vision_block", True)

def update_loop_state(signals, nav_state, log):
    now = time.time()
    if signals.get("at_loop_start"):
        block_until = nav_state.get("loop_block_until", 0.0)
        if now < block_until:
            return
        start = nav_state.get("loop_candidate_since")
        if start is None:
            nav_state["loop_candidate_since"] = now
        elif now - start >= 0.6:
            similarity = nav_state.get("loop_similarity", 0.0)
            log(f"[LOOP] closure similarity={similarity:.2f}")
            nav_state["loop_block_until"] = now + LOOP_DEBOUNCE_SEC
            nav_state["loop_candidate_since"] = None
    else:
        nav_state["loop_candidate_since"] = None


# --- band stats & centerline ---
def band_mask(mask, band_h):
    h, w = mask.shape
    y0 = max(0, h - band_h)
    out = np.zeros_like(mask)
    out[y0:h, :] = mask[y0:h, :]
    return out, y0

def band_coverage(mask_band):
    h, w = mask_band.shape
    thr = 0.02
    left  = np.mean(mask_band[:, : w//3]) / 255.0
    mid   = np.mean(mask_band[:, w//3 : 2*w//3]) / 255.0
    right = np.mean(mask_band[:, 2*w//3 :]) / 255.0
    return left, mid, right, (left>thr), (mid>thr), (right>thr)

def band_centerline_x(mask_band):
    """Estimate centerline in the band: per-row (leftmost,rightmost) → row center; average across rows."""
    h, w = mask_band.shape
    ys = np.arange(h, dtype=np.int32)
    rows = []
    for y in ys:
        row = mask_band[y]
        xs = np.flatnonzero(row)
        if xs.size:
            rows.append((xs[0], xs[-1]))
    if not rows:
        return None, None
    centers = [(l+r)*0.5 for (l,r) in rows]
    widths  = [ (r-l) for (l,r) in rows ]
    return float(np.mean(centers)), float(np.mean(widths))

def apply_wasd_for_center(mid_ok, err_px, eps_px, l_ok, r_ok, blocked):
    """
    Forward if mid is open; strafe toward reducing |err|.
    If blocked (no mid & no sides), back off with S.
    """
    # default: release everything
    # (we keep W/A/D stateful via key_down/up below)
    if blocked:
        key_up("W"); key_down("S")
        key_up("A"); key_up("D")
        return

    key_up("S")
    if mid_ok:
        key_down("W")
        if err_px is not None and abs(err_px) > eps_px:
            if err_px > 0:   # center is to the right → move right to re-center
                key_down("D"); key_up("A")
            else:            # center is to the left → move left
                key_down("A"); key_up("D")
        else:
            key_up("A"); key_up("D")
    else:
        # no mid; choose side that likely reduces error, else any available
        key_up("W")
        if l_ok and not r_ok:
            key_down("A"); key_up("D")
        elif r_ok and not l_ok:
            key_down("D"); key_up("A")
        elif l_ok and r_ok:
            # pick side based on error if we have it
            if err_px is None or abs(err_px) < 1.0:
                key_down("A"); key_up("D")
            elif err_px > 0:
                key_down("D"); key_up("A")
            else:
                key_down("A"); key_up("D")
        else:
            # fully blocked handled above, but keep safe
            key_down("S"); key_up("A"); key_up("D")

def _bool_signature(mask, size=32):
    resized = cv2.resize((mask > 0).astype(np.uint8), (size, size), interpolation=cv2.INTER_AREA)
    return resized.astype(bool)

def compute_shared_signals(frame, walk_mask, obs_mask, args, nav_state, center_err):
    H, W = walk_mask.shape
    obs_mask = obs_mask if obs_mask is not None else np.zeros_like(walk_mask)
    walk_band, _ = band_mask(walk_mask, args.band_h)
    obs_band, _ = band_mask(obs_mask, args.band_h)

    left_walk, mid_walk, right_walk, l_ok, m_ok, r_ok = band_coverage(walk_band)
    left_obs = float(np.mean(obs_band[:, : W//3]) / 255.0) if obs_band.size else 0.0
    mid_obs = float(np.mean(obs_band[:, W//3 : 2*W//3]) / 255.0) if obs_band.size else 0.0
    right_obs = float(np.mean(obs_band[:, 2*W//3 :]) / 255.0) if obs_band.size else 0.0

    front_rows = slice(max(0, obs_band.shape[0] - args.front_band_px), obs_band.shape[0])
    front_mid = float(np.mean(obs_band[front_rows, W//3 : 2*W//3]) / 255.0) if obs_band.size else 0.0
    front_left = float(np.mean(obs_band[front_rows, : W//3]) / 255.0) if obs_band.size else 0.0
    front_right = float(np.mean(obs_band[front_rows, 2*W//3 :]) / 255.0) if obs_band.size else 0.0

    wall_thr = args.wall_presence_thresh
    wall_hyst = max(args.wall_presence_hyst, wall_thr * WALL_HYSTERESIS_PCT)
    wall_thr = args.wall_presence_thresh
    prev_walls = nav_state.setdefault("wall_presence", {"left": False, "right": False})
    if left_obs >= wall_thr:
        left_wall_present = True
    elif left_obs <= max(0.0, wall_thr - wall_hyst):
        left_wall_present = False
    else:
        left_wall_present = prev_walls["left"]

    if right_obs >= wall_thr:
        right_wall_present = True
    elif right_obs <= max(0.0, wall_thr - wall_hyst):
        right_wall_present = False
    else:
        right_wall_present = prev_walls["right"]
    prev_walls["left"] = left_wall_present
    prev_walls["right"] = right_wall_present

    fb_thr = args.front_block_thresh
    fb_hyst = args.front_block_hyst
    prev_front = nav_state.setdefault("front_blocked", False)
    if front_mid >= fb_thr:
        front_blocked = True
    elif front_mid <= max(0.0, fb_thr - fb_hyst):
        front_blocked = False
    else:
        front_blocked = prev_front
    nav_state["front_blocked"] = front_blocked

    gap_thr = args.gap_open_thresh
    left_gap_open = (left_walk >= gap_thr) and (left_obs < max(0.02, wall_thr * 0.4))
    right_gap_open = (right_walk >= gap_thr) and (right_obs < max(0.02, wall_thr * 0.4))

    protrusion_min = args.protrusion_min
    protrusion_max = args.protrusion_max
    left_protrusion = left_wall_present and (protrusion_min <= front_left <= protrusion_max)
    right_protrusion = right_wall_present and (protrusion_min <= front_right <= protrusion_max)

    prev_mid = nav_state.setdefault("prev_mid_walk", mid_walk)
    abrupt_end = left_wall_present and (mid_walk < args.abrupt_end_mid_thresh) and ((prev_mid - mid_walk) > args.abrupt_end_delta)
    nav_state["prev_mid_walk"] = 0.7 * prev_mid + 0.3 * mid_walk

    now = time.time()
    last_progress = nav_state.setdefault("last_progress", now)
    if (mid_walk >= args.progress_mid_min and not front_blocked) or (mid_walk >= args.progress_mid_min * 1.2):
        nav_state["last_progress"] = now
        last_progress = now
    progress_ok = (now - last_progress) <= STUCK_TIMEOUT_SEC

    walk_conf = max(left_walk, mid_walk, right_walk)
    mask_valid = (walk_conf >= max(args.progress_mid_min * 0.6, 0.03)) or left_wall_present or right_wall_present
    if not mask_valid and center_err is not None:
        mask_valid = True
    signals_mask_valid = mask_valid

    signature = _bool_signature(walk_band)
    if "start_signature" not in nav_state:
        nav_state["start_signature"] = signature
        loop_match = False
    else:
        base = nav_state["start_signature"]
        overlap = np.mean(signature == base) if base.size else 0.0
        loop_match = overlap >= args.loop_match_thresh
        nav_state["loop_similarity"] = overlap

    return {
        "front_blocked": front_blocked,
        "left_wall_present": left_wall_present,
        "right_wall_present": right_wall_present,
        "ahead_abrupt_end": abrupt_end,
        "left_protrusion_short": left_protrusion,
        "right_protrusion_short": right_protrusion,
        "left_gap_open": left_gap_open,
        "right_gap_open": right_gap_open,
        "heading_to_center": 0.0 if center_err is None else float(center_err),
        "center_valid": center_err is not None,
        "progress_ok": progress_ok,
        "at_loop_start": loop_match,
        "left_walk": left_walk,
        "mid_walk": mid_walk,
        "right_walk": right_walk,
        "left_obs": left_obs,
        "mid_obs": mid_obs,
        "right_obs": right_obs,
        "mask_valid": signals_mask_valid,
    }

def maybe_log_action(nav_state, action, log, throttle=0.5):
    if not action:
        return
    last = nav_state.get("last_action", "")
    last_t = nav_state.get("last_action_time", 0.0)
    now = time.time()
    if action != last or (now - last_t) >= throttle:
        log(f"[ACT] {action}")
        nav_state["last_action"] = action
        nav_state["last_action_time"] = now

def get_recovery_state(nav_state):
    rec = nav_state.get("recovery_state")
    if rec is None:
        rec = {
            "active": False,
            "stage": "idle",
            "side": "E",
            "mode": "left_wall",
            "chunk_idx": 0,
            "flip_done": False,
            "last_action_time": 0.0,
            "started_at": 0.0,
            "reason": "",
        }
        nav_state["recovery_state"] = rec
    return rec

def start_recovery(nav_state, mode, side, reason, log):
    rec = get_recovery_state(nav_state)
    if rec["active"]:
        return
    now = time.time()
    arb = nav_state.get("scan_arbiter")
    if arb and arb.episode_active:
        arb.mark_failure(now)
    rec.update({
        "active": True,
        "stage": "scan",
        "side": side,
        "mode": mode,
        "chunk_idx": 0,
        "flip_done": False,
        "last_action_time": 0.0,
        "started_at": now,
        "reason": reason,
    })
    log(f"[RECOVER] start reason={reason} side={side} mode={mode}")

def stop_recovery(nav_state, log, status):
    rec = get_recovery_state(nav_state)
    if not rec["active"]:
        return
    duration = time.time() - rec.get("started_at", time.time())
    log(f"[RECOVER] complete status={status} duration={duration:.2f}s")
    rec["active"] = False
    rec["stage"] = "idle"

def advance_recovery(signals, controller, args, nav_state, log, stuck_timer):
    rec = get_recovery_state(nav_state)
    if not rec["active"]:
        return False
    mask_valid = signals.get("mask_valid", False)
    if mask_valid:
        stop_recovery(nav_state, log, status="mask-restored")
        arb = nav_state.get("scan_arbiter")
        if arb and arb.episode_active:
            arb.mark_complete(time.time(), success=True)
        return False

    now = time.time()
    stage = rec["stage"]
    side = rec["side"]
    mode = rec["mode"]

    if not args.auto_recover and stage != "halt":
        controller.stop_all()
        log_once(nav_state, "recover_manual_wait", "[RECOVER] manual override required; auto recovery disabled", log, ttl=3.0)
        rec["stage"] = "halt"

    if stage == "scan":
        triggered = trigger_scan("recovery", mode, signals, controller, nav_state, log,
                                 recovery_stage="scan", stuck_timer=stuck_timer,
                                 force_side=side, force_start=True)
        arb = nav_state.get("scan_arbiter")
        if arb and arb.attempts_exhausted():
            arb.mark_failure(now)
            rec["stage"] = "turn"
            rec["chunk_idx"] = 0
            rec["last_action_time"] = 0.0
            log(f"[RECOVER] escalate to turn chunks side={side}")
        return True

    if stage == "turn":
        if now - rec.get("last_action_time", 0.0) < 0.25:
            return True
        controller.stop_all()
        direction = 1 if side == "E" else -1
        controller.turn(direction, args.turn_dx)
        rec["chunk_idx"] += 1
        rec["last_action_time"] = now
        log(f"[RECOVER] stage=chunk side={side} idx={rec['chunk_idx']}/{TURN_CHUNKS} stuck_timer={stuck_timer:.2f}")
        if rec["chunk_idx"] >= TURN_CHUNKS:
            if rec["flip_done"]:
                rec["stage"] = "halt"
            else:
                rec["stage"] = "flip"
        return True

    if stage == "flip":
        rec["flip_done"] = True
        rec["side"] = "Q" if side == "E" else "E"
        rec["stage"] = "turn"
        rec["chunk_idx"] = 0
        rec["last_action_time"] = 0.0
        log(f"[RECOVER] flip side -> {rec['side']}")
        return True

    if stage == "halt":
        controller.stop_all()
        log_once(nav_state, "recover_halt", "[RECOVER] emergency halt engaged; awaiting operator", log, ttl=2.0)
        return True

    return False

def execute_mode_a(signals, controller, args, nav_state, log, vision_ok, stuck_timer):
    now = time.time()
    controller.back(False)
    controller.strafe_left(False)
    controller.strafe_right(False)

    if not vision_ok:
        controller.forward(False)
        return "vision-gated"

    front_blocked = signals["front_blocked"]
    left_wall = signals["left_wall_present"]
    right_wall = signals["right_wall_present"]
    ahead_end = signals["ahead_abrupt_end"]
    left_gap = signals["left_gap_open"]
    left_protrusion = signals["left_protrusion_short"]

    corner_block_until = nav_state.get("corner_block_until", 0.0)
    if front_blocked and left_wall:
        controller.forward(False)
        if now >= corner_block_until:
            triggered = trigger_scan("corner-trap", "left_wall", signals, controller, nav_state, log,
                                     stuck_timer=stuck_timer)
            if triggered:
                nav_state["corner_block_until"] = now + random.uniform(CORNER_DEBOUNCE_SEC_MIN, CORNER_DEBOUNCE_SEC_MAX)
                nav_state["last_turn_side"] = "right"
                return "corner-trap-scan"
        return "corner-trap-hold"

    if left_wall and ahead_end:
        triggered = trigger_scan("abrupt-end", "left_wall", signals, controller, nav_state, log,
                                 stuck_timer=stuck_timer)
        if triggered:
            return "abrupt-end-scan"
        return "abrupt-end-wait"

    if left_protrusion:
        controller.forward(True)
        controller.pulse_strafe(1, 0.18)
        if apply_micro_turn(nav_state, -1, controller, args, log, "left-protrusion"):
            return "left-protrusion-bypass"
        return "left-protrusion-blocked"

    if left_wall and not front_blocked:
        controller.forward(True)
        if apply_micro_turn(nav_state, -1, controller, args, log, "wall-follow"):
            return "wall-follow"
        controller.strafe_left(False)
        return "wall-follow-hold"

    if (not left_wall) and (not front_blocked):
        controller.forward(True)
        if apply_micro_turn(nav_state, -1, controller, args, log, "wall-lost"):
            return "left-wall-lost-nudge"
        return "left-wall-lost-blocked"

    if left_gap:
        controller.forward(False)
        if getattr(args, "wall_policy", "follow_wall") == "follow_wall":
            if apply_micro_turn(nav_state, -1, controller, args, log, "gap-ignore"):
                return "gap-left-ignore"
            return "gap-left-ignore-blocked"
        controller.turn(-1, args.turn_dx)
        controller.strafe_left(True)
        controller.forward(True)
        nav_state["left_turns"] = nav_state.get("left_turns", 0) + 1
        nav_state["last_turn_side"] = "left"
        return "gap-left-enter"

    if front_blocked:
        controller.forward(False)
        triggered = trigger_scan("front-blocked", "left_wall", signals, controller, nav_state, log,
                                 stuck_timer=stuck_timer)
        if triggered:
            nav_state["last_turn_side"] = "right"
            return "front-block-scan"
        return "front-block-wait"

    controller.forward(True)
    if apply_micro_turn(nav_state, -1, controller, args, log, "default-cruise"):
        return "default-cruise"
    return "default-cruise-hold"

def execute_mode_b(signals, controller, args, nav_state, log, vision_ok, stuck_timer):
    controller.back(False)
    heading = signals["heading_to_center"]
    center_valid = signals["center_valid"]
    front_blocked = signals["front_blocked"]
    left_gap = signals["left_gap_open"]
    right_gap = signals["right_gap_open"]
    left_wall = signals["left_wall_present"]
    right_wall = signals["right_wall_present"]
    left_protrusion = signals["left_protrusion_short"]
    right_protrusion = signals["right_protrusion_short"]

    if not vision_ok:
        controller.forward(False)
        return "vision-gated"

    small_band = args.center_eps
    outer_band = max(args.center_outer_band, small_band + 5.0)

    if front_blocked:
        controller.forward(False)
        if left_gap and not right_gap:
            controller.turn(-1, args.turn_dx)
            controller.strafe_left(True)
            controller.forward(True)
            nav_state["last_turn_side"] = "left"
            return "front-block-left-gap"
        if right_gap and not left_gap:
            controller.turn(1, args.turn_dx)
            controller.strafe_right(True)
            controller.forward(True)
            nav_state["last_turn_side"] = "right"
            return "front-block-right-gap"
        if left_gap and right_gap:
            prefer = nav_state.get("merge_bias")
            now_side = "left" if (heading is not None and heading < 0) else "right"
            if prefer is None or (time.time() - nav_state.get("last_merge_time", 0.0)) > args.merge_memory:
                prefer = now_side
            if prefer == "left":
                controller.turn(-1, args.turn_dx)
                controller.strafe_left(True)
                controller.forward(True)
                nav_state["merge_bias"] = "right"
                nav_state["last_merge_time"] = time.time()
                nav_state["last_turn_side"] = "left"
                return "front-block-both-left"
            controller.turn(1, args.turn_dx)
            controller.strafe_right(True)
            controller.forward(True)
            nav_state["merge_bias"] = "left"
            nav_state["last_merge_time"] = time.time()
            nav_state["last_turn_side"] = "right"
            return "front-block-both-right"
        triggered = trigger_scan("center-front-blocked", "center_path", signals, controller, nav_state, log,
                                 stuck_timer=stuck_timer)
        if triggered:
            return "front-block-scan"
        return "front-block-wait"

    if signals["ahead_abrupt_end"]:
        triggered = trigger_scan("center-abrupt-end", "center_path", signals, controller, nav_state, log,
                                 stuck_timer=stuck_timer)
        if triggered:
            return "abrupt-end-scan"
        return "abrupt-end-wait"

    if left_protrusion and (heading is None or heading <= 0):
        controller.forward(True)
        controller.pulse_strafe(1, 0.18)
        if apply_micro_turn(nav_state, -1, controller, args, log, "center-left-protrusion"):
            return "left-protrusion-bypass"
        return "left-protrusion-blocked"

    if right_protrusion and (heading is None or heading >= 0):
        controller.forward(True)
        controller.pulse_strafe(-1, 0.18)
        if apply_micro_turn(nav_state, 1, controller, args, log, "center-right-protrusion"):
            return "right-protrusion-bypass"
        return "right-protrusion-blocked"

    controller.forward(True)
    controller.strafe_left(False)
    controller.strafe_right(False)

    if not center_valid:
        if heading is not None and abs(heading) > small_band:
            direction = 1 if heading > 0 else -1
            if apply_micro_turn(nav_state, direction, controller, args, log, "center-no-line"):
                return "no-center-correct"
        return "no-center-default"

    if abs(heading) <= small_band:
        return "lane-keep-straight"

    if heading < -small_band:
        if abs(heading) > outer_band:
            controller.turn(1, args.turn_dx)
            return "lane-hard-correct-right"
        controller.strafe_left(True)
        if apply_micro_turn(nav_state, -1, controller, args, log, "lane-correct-right"):
            return "lane-correct-right"
        return "lane-correct-right-blocked"

    if heading > small_band:
        if abs(heading) > outer_band:
            controller.turn(-1, args.turn_dx)
            return "lane-hard-correct-left"
        controller.strafe_right(True)
        if apply_micro_turn(nav_state, 1, controller, args, log, "lane-correct-left"):
            return "lane-correct-left"
        return "lane-correct-left-blocked"

    return "lane-keep"

def release_all():
    for k in ("W","A","S","D"):
        key_up(k)

def run_nav_loop(args, stop_event=None, frame_cb=None, log_cb=None):
    def log(msg):
        if log_cb:
            try:
                log_cb(msg)
            except Exception:
                pass
        else:
            print(msg)

    if not focus_window_by_title_sub(args.title_sub):
        log(f"[WARN] Could not focus window containing '{args.title_sub}'. Will retry.")

    model = load_model(args.weights)
    cap = Capture(args.title_sub)
    dt = 1.0 / max(15, args.fps)

    log_dir = os.path.join(PROJ, "logs")
    os.makedirs(log_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(log_dir, f"seg_runtime_play_keys_{ts}.log")
    KeyLogger.current = KeyLogger(log_path)
    log(f"[LOG] Key presses logging to {log_path}")
    if frame_cb and not _HAS_PIL:
        log("[HUD] Pillow not installed; HUD image cannot be shown inside UI. Showing placeholder text instead.")

    window_missing_logged = False
    mask_missing_logged = False

    controller = MotionController()
    nav_state = {
        "scan_arbiter": ScanArbiter(),
        "corner_block_until": 0.0,
        "loop_block_until": 0.0,
        "loop_candidate_since": None,
        "left_turns": 0,
    }

    try:
        last_focus_t = 0.0
        while True:
            controller.update()
            if stop_event is not None and stop_event.is_set():
                log("[STOP] Stop requested from UI")
                break
            try:
                import win32api
                if (win32api.GetAsyncKeyState(0x30) & 0x8000) or (win32api.GetAsyncKeyState(0x60) & 0x8000):
                    log("[STOP] Zero key held; stopping loop")
                    break
            except Exception:
                pass

            loop_start_time = time.time()
            if loop_start_time - last_focus_t > 2.0:
                focus_window_by_title_sub(args.title_sub)
                last_focus_t = loop_start_time

            bgra = cap.grab_frame_mss()
            if bgra is None:
                if not window_missing_logged:
                    log("[WAIT] Game window not found or minimized; retrying...")
                    window_missing_logged = True
                controller.stop_all()
                time.sleep(0.1)
                continue
            window_missing_logged = False

            frame = cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)
            dev = "cuda" if torch.cuda.is_available() else "cpu"
            res = model.predict(source=frame, verbose=False, imgsz=960, conf=0.25, device=dev)

            masks = None
            cls = None
            if res and res[0].masks is not None and getattr(res[0].masks, "data", None) is not None and getattr(res[0].masks, "data").shape[0] > 0:
                masks = res[0].masks.data.cpu().numpy()
                cls = res[0].boxes.cls.cpu().numpy().astype(int)
                mask_missing_logged = False
            else:
                if not mask_missing_logged:
                    log("[WARN] Model returned no masks; scanning to reacquire")
                    mask_missing_logged = True

            Hf, Wf = frame.shape[:2]
            walk_mask = np.zeros((Hf, Wf), dtype=np.uint8)
            obs_mask = np.zeros((Hf, Wf), dtype=np.uint8)
            if masks is not None and cls is not None:
                for mi, ci in zip(masks, cls):
                    mask_uint8 = (mi > 0.5).astype(np.uint8) * 255
                    if mask_uint8.shape != (Hf, Wf):
                        mask_uint8 = cv2.resize(mask_uint8, (Wf, Hf), interpolation=cv2.INTER_NEAREST)
                    if ci == 0:
                        walk_mask = np.maximum(walk_mask, mask_uint8)
                    elif ci in (1, 2):
                        obs_mask = np.maximum(obs_mask, mask_uint8)

            band, _ = band_mask(walk_mask, args.band_h)
            left, mid, right, _, _, _ = band_coverage(band)
            cx, _ = band_centerline_x(band)
            err = None
            if cx is not None:
                err = cx - (band.shape[1] * 0.5)

            signals = compute_shared_signals(frame, walk_mask, obs_mask, args, nav_state, err)
            handle_scan_feedback(nav_state, signals["mask_valid"])
            vision_ok = update_vision_state(nav_state, signals["mask_valid"])
            update_loop_state(signals, nav_state, log)

            now = time.time()
            if signals["progress_ok"]:
                nav_state["progress_bad_since"] = None
            else:
                if nav_state.get("progress_bad_since") is None:
                    nav_state["progress_bad_since"] = now
            stuck_timer = 0.0
            if nav_state.get("progress_bad_since") is not None:
                stuck_timer = now - nav_state["progress_bad_since"]

            arb = nav_state.get("scan_arbiter")
            if arb and arb.episode_active and arb.attempts_exhausted():
                side = arb.active_side() or select_scan_side(args.mode, signals, nav_state)
                start_recovery(nav_state, args.mode, side, reason="scan-exhausted", log=log)

            if (not signals["progress_ok"]) and stuck_timer >= STUCK_TIMEOUT_SEC:
                side = select_scan_side(args.mode, signals, nav_state)
                start_recovery(nav_state, args.mode, side, reason="stuck-progress", log=log)

            rec = get_recovery_state(nav_state)
            action = None
            if rec["active"]:
                if advance_recovery(signals, controller, args, nav_state, log, stuck_timer):
                    action = f"recovery-{rec['stage']}"
                else:
                    rec = get_recovery_state(nav_state)
                    if rec["active"]:
                        action = f"recovery-{rec['stage']}"

            if action is None:
                if not vision_ok:
                    controller.forward(False)
                    trigger_scan("vision-gated", args.mode, signals, controller, nav_state, log,
                                 stuck_timer=stuck_timer)
                    action = "vision-gated"
                else:
                    if args.mode == "left_wall":
                        action = execute_mode_a(signals, controller, args, nav_state, log, vision_ok, stuck_timer)
                    else:
                        action = execute_mode_b(signals, controller, args, nav_state, log, vision_ok, stuck_timer)

            maybe_log_action(nav_state, action, log)

            if not args.no_preview and args.hud_mode != "off":
                vis = frame.copy()
                H, W = vis.shape[:2]
                alpha = float(max(0.0, min(1.0, args.hud_alpha)))
                if args.hud_mode == "full":
                    if obs_mask is not None:
                        overlay_mask_color(vis, obs_mask, (0, 0, 200), alpha)
                    if walk_mask is not None:
                        overlay_mask_color(vis, walk_mask, (0, 200, 0), alpha)
                y_top = H - args.band_h
                y_bot = H - 1
                cv2.rectangle(vis, (0, y_top), (W-1, H-1), (255,255,255), 1)
                if cx is not None:
                    cx_i = int(cx)
                    cv2.line(vis, (cx_i, y_top), (cx_i, y_bot), (0,255,0), 2)
                    cv2.line(vis, (W//2, y_bot), (cx_i, y_top), (255,255,0), 1)
                cv2.line(vis, (W//2, y_top), (W//2, y_bot), (0,0,255), 1)
                cv2.putText(vis, f"L:{left:.2f} M:{mid:.2f} R:{right:.2f} err:{0 if err is None else int(err)}px",
                            (20,40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0,255,0), 2)

                if frame_cb:
                    frame_cb(vis)
                else:
                    cv2.imshow("seg_runtime_play", vis)
                    if cv2.waitKey(1) & 0xFF == 27:
                        log("[STOP] ESC pressed")
                        break

            elapsed = time.time() - loop_start_time
            if elapsed < dt:
                time.sleep(dt - elapsed)

    except KeyboardInterrupt:
        log("[STOP] KeyboardInterrupt")
    finally:
        if 'controller' in locals() and isinstance(controller, MotionController):
            controller.stop_all()
        release_all()
        if KeyLogger.current:
            KeyLogger.current.close()
            KeyLogger.current = None
        if not frame_cb:
            try:
                cv2.destroyAllWindows()
            except Exception:
                pass
        log("[INFO] Navigation loop exited")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", default="C:/Users/Administrator/runs/segment/train2/weights/best.pt")
    ap.add_argument("--title_sub", default="Counter-Strike 2")
    ap.add_argument("--no_preview", action="store_true")
    ap.add_argument("--headless", action="store_true", help="Run without Tk UI (CLI mode)")
    ap.add_argument("--hud_mode", choices=["off", "basic", "full"], default="full",
                    help="HUD overlay: off (no HUD), basic (guides only), full (masks + guides)")
    ap.add_argument("--hud_alpha", type=float, default=0.6,
                    help="Alpha for HUD mask overlays (0..1)")
    ap.add_argument("--band_h", type=int, default=220)
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--center_eps", type=float, default=20.0, help="deadband (px) around centerline")
    ap.add_argument("--yaw_kp", type=float, default=0.02, help="px→mouse dx gain")
    ap.add_argument("--yaw_cap", type=int, default=6, help="max mouse dx per frame")
    ap.add_argument("--mode", choices=["left_wall", "center_path"], default="center_path",
                    help="Navigation mode: left_wall (wall follower) or center_path (lane keeping)")
    ap.add_argument("--wall_policy", choices=["follow_wall", "enter_openings"], default="follow_wall",
                    help="Left-wall hug policy when encountering openings")
    ap.add_argument("--auto_recover", action="store_true",
                    help="Enable automatic stuck recovery fallback")
    ap.add_argument("--front_block_thresh", type=float, default=0.18,
                    help="Obstacle coverage threshold to treat forward as blocked")
    ap.add_argument("--front_block_hyst", type=float, default=0.05,
                    help="Hysteresis for front blocked detection")
    ap.add_argument("--wall_presence_thresh", type=float, default=0.12,
                    help="Obstacle coverage threshold to treat side wall as present")
    ap.add_argument("--wall_presence_hyst", type=float, default=0.04,
                    help="Hysteresis for wall presence detection")
    ap.add_argument("--gap_open_thresh", type=float, default=0.15,
                    help="Walkable coverage threshold to call a side gap open")
    ap.add_argument("--protrusion_min", type=float, default=0.03,
                    help="Minimum obstacle fraction to treat as protrusion")
    ap.add_argument("--protrusion_max", type=float, default=0.18,
                    help="Maximum obstacle fraction to treat as protrusion")
    ap.add_argument("--abrupt_end_mid_thresh", type=float, default=0.05,
                    help="Mid-walk coverage below which we suspect an abrupt end")
    ap.add_argument("--abrupt_end_delta", type=float, default=0.07,
                    help="Drop in mid coverage to classify abrupt end")
    ap.add_argument("--front_band_px", type=int, default=120,
                    help="Height in pixels (within band) used for forward blocking tests")
    ap.add_argument("--progress_mid_min", type=float, default=0.06,
                    help="Minimum mid-walk coverage considered forward progress")
    ap.add_argument("--loop_match_thresh", type=float, default=0.9,
                    help="Similarity threshold for loop-start detection signature")
    ap.add_argument("--center_outer_band", type=float, default=35.0,
                    help="Outer band for aggressive lane corrections in mode B")
    ap.add_argument("--merge_memory", type=float, default=1.0,
                    help="Seconds to remember last merge choice in mode B")
    ap.add_argument("--turn_dx", type=int, default=8,
                    help="Mouse dx for committed turns")
    ap.add_argument("--micro_turn_dx", type=int, default=3,
                    help="Mouse dx for micro nudges")
    args = ap.parse_args()
    if not args.headless:
        return launch_ui(args)

    run_nav_loop(args)

def launch_ui(args):
    root = tk.Tk()
    root.title("Seg Runtime Play")
    panel = tk.Label(root, text="HUD not streaming yet")
    panel.pack(side="top", fill="both", expand=True)

    log_text = scrolledtext.ScrolledText(root, height=8, state="disabled", font=("Consolas", 9))
    log_text.pack(side="top", fill="both", padx=6, pady=6)

    status = tk.StringVar(value="IDLE")
    mode_var = tk.StringVar(value=args.mode)
    fallback_var = tk.BooleanVar(value=args.auto_recover)
    action_var = tk.StringVar(value="Last action: --")
    ctrls = tk.Frame(root); ctrls.pack(side="bottom", fill="x")
    tk.Label(ctrls, textvariable=status).pack(side="left", padx=8)
    mode_frame = tk.Frame(ctrls)
    mode_frame.pack(side="left", padx=8)
    tk.Label(mode_frame, text="Mode:").pack(side="left")
    tk.Radiobutton(mode_frame, text="Left Wall", variable=mode_var, value="left_wall").pack(side="left")
    tk.Radiobutton(mode_frame, text="Center Path", variable=mode_var, value="center_path").pack(side="left")
    tk.Label(ctrls, textvariable=action_var).pack(side="left", padx=8)
    tk.Checkbutton(ctrls, text="Auto Recover", variable=fallback_var).pack(side="left", padx=8)

    stop_event = threading.Event()
    worker = {"t": None}

    img_queue = queue.Queue(maxsize=1)
    log_queue = queue.Queue()

    def enqueue_frame(img_bgr):
        try:
            while True:
                img_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            img_queue.put_nowait(img_bgr.copy())
        except queue.Full:
            pass

    def enqueue_log(message):
        stamp = time.strftime("%H:%M:%S")
        log_queue.put(f"[{stamp}] {message}")

    def process_queues():
        try:
            while True:
                msg = log_queue.get_nowait()
                log_text.configure(state="normal")
                log_text.insert("end", msg + "\n")
                log_text.see("end")
                log_text.configure(state="disabled")
                if msg.startswith("[ACT]") or msg.startswith("[ESCAPE]") or msg.startswith("[SCAN]") or msg.startswith("[RECOVER]"):
                    action_var.set(f"Last action: {msg}")
        except queue.Empty:
            pass

        try:
            img = img_queue.get_nowait()
            if _HAS_PIL:
                rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                im = Image.fromarray(rgb)
                imgtk = ImageTk.PhotoImage(image=im)
                panel.imgtk = imgtk
                panel.configure(image=imgtk, text="")
            else:
                panel.configure(text="Install Pillow (pip install pillow) to see HUD in UI")
        except queue.Empty:
            pass

        root.after(80, process_queues)

    process_queues()

    def on_start():
        if worker["t"] and worker["t"].is_alive():
            return
        status.set("RUNNING")
        stop_event.clear()
        args.mode = mode_var.get()
        args.auto_recover = fallback_var.get()
        worker["t"] = threading.Thread(target=run_nav_loop, args=(args, stop_event, enqueue_frame, enqueue_log), daemon=True)
        worker["t"].start()
        enqueue_log("[INFO] Navigation thread started")

    def on_stop():
        if not (worker["t"] and worker["t"].is_alive()):
            return
        status.set("STOPPING")
        enqueue_log("[INFO] Stop requested")
        stop_event.set()
        release_all()

    tk.Button(ctrls, text="Start", command=on_start).pack(side="left", padx=6)
    tk.Button(ctrls, text="Stop", command=on_stop).pack(side="left", padx=6)

    def poll_worker():
        alive = worker["t"].is_alive() if worker["t"] else False
        if not alive and status.get() != "IDLE":
            status.set("IDLE")
        if not alive and worker["t"]:
            worker["t"] = None
        root.after(200, poll_worker)

    poll_worker()

    root.mainloop()
    return 0

if __name__ == "__main__":
    main()
