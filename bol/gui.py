"""bol.gui — the desktop GUI (customtkinter: modern, rounded, self-contained)."""
# SPDX-License-Identifier: MIT

import base64
import os
import shutil
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

from .auth import (
    NativeAuth,
    msa_logout,
    msa_signed_in,
)
from .config import LOGS, PRETTY, VERSION
from .content import _mojang_dir, import_content
from .games import list_mc_versions
from .gamesetup import do_setup
from .inject import run_injector
from .launch import launch
from . import log
from .log import BolError, _LEVELS, warn
from .prefix import (
    _mc_running,
    kill_wine,
    prefix_operation_lock,
    reset_prefix,
)
from .update import check_for_update, self_update
from .util import load_settings, save_settings


def _desktop_error(message):
    warn(message)
    notifier = shutil.which("notify-send")
    if notifier:
        try:
            subprocess.run(
                [notifier, "--app-name", PRETTY, PRETTY, message],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=5, check=False)
        except (OSError, subprocess.SubprocessError):
            pass


# --------------------------------------------------------------------------
# Palette. A little deeper / more layered than a flat two-tone slate, so
# cards actually separate from the window instead of blending into it.
# --------------------------------------------------------------------------
class Theme:
    BG        = "#0f1115"   # window
    CARD      = "#181b22"   # primary panels
    CARD_2    = "#20242e"   # nested surfaces (fields, rows)
    CARD_3    = "#2a2f3b"   # hover / raised state
    BORDER    = "#2a2e38"

    FG        = "#f2f4f7"
    SUB       = "#8b93a7"
    MUTED     = "#5d6577"

    GREEN     = "#43a047"
    GREEN_HOV = "#4fc153"
    GREEN_DIM = "#1c2c1c"

    GOLD      = "#e3b34a"
    GOLD_DIM  = "#33291a"

    RED       = "#e2685a"
    RED_DIM   = "#341f1d"

    BLUE      = "#5b9bd9"


def gui():
    if os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("DISPLAY"):
        _desktop_error(
            "The launcher needs XWayland. Install or enable XWayland, then "
            "open BedrockOnLinux again; command-line tools remain available.")
        return
    from .deps import ensure_gui_deps
    ensure_gui_deps()
    try:
        import tkinter as tk
        from tkinter import messagebox
        import customtkinter as ctk
    except Exception as e:
        _desktop_error(
            f"GUI toolkit unavailable ({e}). Install python3-tk and "
            "customtkinter, or use the command line.")
        return

    T = Theme
    ctk.set_appearance_mode("dark")
    try:
        root = ctk.CTk(className=PRETTY)
    except Exception as e:
        _desktop_error(
            f"No usable X11/XWayland display ({e}). Enable XWayland or use "
            "the command line.")
        return
    root.title(PRETTY)
    root.geometry("980x650")
    root.minsize(860, 580)
    root.configure(fg_color=T.BG)

    def font(size=13, weight="normal", family=None):
        return ctk.CTkFont(size=size, weight=weight, family=family)

    MONO = "monospace"

    # ---------------------------------------------------------------- icon --
    icon_img = None
    here = Path(__file__).resolve().parent
    for p in (here.parent / "data/icon.png",
              here / "data/icon.png",
              Path("/app/share/icons/hicolor/256x256/apps/") /
              "io.github.wyze3306.BedrockOnLinux.png",
              Path("/usr/lib/bedrock-on-linux/data/icon.png"),
              Path("/usr/share/icons/hicolor/256x256/apps/bedrock-on-linux.png")):
        if p.exists():
            try:
                icon_img = tk.PhotoImage(file=str(p))
                root.iconphoto(True, icon_img)
                root._icon = icon_img
                break
            except Exception:
                pass
    if icon_img is None:
        try:
            with zipfile.ZipFile(Path(sys.argv[0])) as archive:
                encoded = base64.b64encode(archive.read("data/icon.png"))
            icon_img = tk.PhotoImage(data=encoded)
            root.iconphoto(True, icon_img)
            root._icon = icon_img
        except (OSError, KeyError, zipfile.BadZipFile, tk.TclError):
            pass

    def logo_label(parent, px, bg):
        """A scaled logo image in a tk.Label (its bg must match the parent)."""
        if icon_img is None:
            return None
        try:
            im = icon_img.subsample(max(1, icon_img.width() // px))
            lab = tk.Label(parent, image=im, bg=bg, bd=0)
            lab.image = im
            return lab
        except Exception:
            return None

    # ------------------------------------------------------------- helpers --
    def mkbtn(parent, text, cmd, kind="ghost", **kw):
        base = {
            "play":    dict(fg_color=T.GREEN, hover_color=T.GREEN_HOV,
                             text_color="white"),
            "primary": dict(fg_color=T.GREEN, hover_color=T.GREEN_HOV,
                             text_color="white"),
            "danger":  dict(fg_color=T.RED, hover_color="#ea7c70",
                             text_color="white"),
            "ghost":   dict(fg_color=T.CARD_2, hover_color=T.CARD_3,
                             text_color=T.FG),
            "flat":    dict(fg_color="transparent", hover_color=T.CARD_2,
                             text_color=T.SUB),
        }[kind]
        opts = dict(corner_radius=10, font=font(13), command=cmd)
        opts.update(base)
        opts.update(kw)
        return ctk.CTkButton(parent, text=text, **opts)

    def center_over_root(win, w, h):
        root.update_idletasks()
        x = root.winfo_rootx() + (root.winfo_width() - w) // 2
        y = root.winfo_rooty() + (root.winfo_height() - h) // 2
        win.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")

    def dialog(title, w, h):
        """A CTkToplevel with consistent chrome: centered, dark, Esc-to-close."""
        d = ctk.CTkToplevel(root)
        d.title(title)
        d.configure(fg_color=T.CARD)
        d.transient(root)
        d.resizable(False, False)
        center_over_root(d, w, h)
        d.after(120, d.lift)
        d.bind("<Escape>", lambda e: d.destroy())
        return d

    class Tooltip:
        """Small delayed hover label for icon-only buttons."""

        def __init__(self, widget, text):
            self.widget, self.text, self.win, self._job = widget, text, None, None
            widget.bind("<Enter>", self._schedule, add="+")
            widget.bind("<Leave>", self._hide, add="+")

        def _schedule(self, _e=None):
            self._job = root.after(500, self._show)

        def _show(self):
            if self.win is not None:
                return
            self.win = tk.Toplevel(root)
            self.win.overrideredirect(True)
            self.win.attributes("-topmost", True)
            x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
            lab = tk.Label(self.win, text=self.text, bg=T.CARD_3, fg=T.FG,
                            font=("sans-serif", 10), padx=8, pady=4, bd=0)
            lab.pack()
            self.win.update_idletasks()
            self.win.geometry(
                f"+{x - self.win.winfo_width() // 2}+{y}")

        def _hide(self, _e=None):
            if self._job:
                root.after_cancel(self._job)
                self._job = None
            if self.win is not None:
                self.win.destroy()
                self.win = None

    na = NativeAuth()
    ui = {"versions": [], "labels": [], "busy": False, "details": False,
          "launch_active": False}

    # ==================================================================
    # Top bar: brand + account
    # ==================================================================
    top = ctk.CTkFrame(root, fg_color="transparent")
    top.pack(fill="x", padx=22, pady=(18, 8))

    brand = ctk.CTkFrame(top, fg_color="transparent")
    brand.pack(side="left")
    ll = logo_label(brand, 30, T.BG)
    if ll:
        ll.pack(side="left", padx=(0, 10))
    ctk.CTkLabel(brand, text="BedrockOnLinux", font=font(18, "bold"),
                 text_color=T.FG).pack(side="left")
    ctk.CTkLabel(brand, text=f"v{VERSION}", font=font(11), text_color=T.MUTED
                 ).pack(side="left", padx=(8, 0), pady=(4, 0))

    acct = ctk.CTkFrame(top, fg_color=T.CARD, corner_radius=14)
    acct.pack(side="right")
    acct_dot = ctk.CTkLabel(acct, text="●", text_color=T.SUB, font=font(12),
                             width=10)
    acct_dot.pack(side="left", padx=(14, 4), pady=8)
    acct_txt = tk.StringVar(value="Not signed in")
    ctk.CTkLabel(acct, textvariable=acct_txt, text_color=T.FG,
                 font=font(13)).pack(side="left", padx=(0, 10))
    acct_btn = mkbtn(acct, "Sign in", lambda: acct_click(), kind="ghost",
                      width=88, height=30, font=font(12, "bold"))
    acct_btn.pack(side="left", padx=(0, 8), pady=8)

    # ==================================================================
    # View area — swaps between the Hero and the (now built-in) Settings
    # panel, instead of Settings opening a separate window.
    # ==================================================================
    view_area = ctk.CTkFrame(root, fg_color="transparent")
    view_area.pack(fill="both", expand=True, padx=22, pady=6)

    hero = ctk.CTkFrame(view_area, fg_color=T.CARD, corner_radius=18,
                         border_width=1, border_color=T.BORDER)
    hero.pack(fill="both", expand=True)
    hw = ctk.CTkFrame(hero, fg_color="transparent")
    hw.place(relx=0.5, rely=0.44, anchor="center")
    hl = logo_label(hw, 118, T.CARD)
    if hl:
        hl.pack()
    ctk.CTkLabel(hw, text="Minecraft Bedrock", font=font(28, "bold"),
                 text_color=T.FG).pack(pady=(16, 2))
    ctk.CTkLabel(hw, text="Bedrock Edition for Linux", font=font(13),
                 text_color=T.SUB).pack()

    selected_chip = ctk.CTkLabel(
        hw, text="", font=font(12, "bold"), text_color=T.GREEN,
        fg_color=T.GREEN_DIM, corner_radius=8)
    selected_chip.pack(pady=(14, 0))

    # ==================================================================
    # Status + progress
    # ==================================================================
    status = ctk.CTkFrame(root, fg_color="transparent")
    status.pack(fill="x", padx=26, pady=(4, 0))
    status_txt = tk.StringVar(value="Ready to play.")
    status_lbl = ctk.CTkLabel(status, textvariable=status_txt, text_color=T.SUB,
                               font=font(12), anchor="w")
    status_lbl.pack(fill="x")
    prog = ctk.CTkProgressBar(status, height=8, corner_radius=4,
                               progress_color=T.GREEN, fg_color=T.CARD_2)
    prog.set(0)

    # ==================================================================
    # Control dock: version picker · details · settings · play
    # ==================================================================
    dock = ctk.CTkFrame(root, fg_color=T.CARD, corner_radius=16,
                         border_width=1, border_color=T.BORDER)
    dock.pack(fill="x", padx=22, pady=(10, 16))
    bar = ctk.CTkFrame(dock, fg_color="transparent")
    bar.pack(fill="x", padx=16, pady=14)

    vbox = ctk.CTkFrame(bar, fg_color="transparent")
    vbox.pack(side="left")
    ctk.CTkLabel(vbox, text="VERSION", text_color=T.MUTED, font=font(11, "bold"),
                 anchor="w").pack(anchor="w", pady=(0, 4))
    mc_var = tk.StringVar(value="")

    # ---- version picker (searchable dropdown) ------------------------
    _pick = {"win": None}

    def close_picker():
        w = _pick["win"]
        _pick["win"] = None
        if w is not None:
            try:
                w.destroy()
            except Exception:
                pass

    def set_version(label):
        mc_var.set(label or "")
        _update_selected_chip()
        close_picker()

    def _update_selected_chip():
        lab = mc_var.get()
        if not lab:
            selected_chip.configure(text="")
            return
        is_beta = "beta" in lab
        selected_chip.configure(
            text=f"  {lab.split('  ')[0]}"
                 f"{'  ·  BETA' if is_beta else ''}  ",
            text_color=T.GOLD if is_beta else T.GREEN,
            fg_color=T.GOLD_DIM if is_beta else T.GREEN_DIM)

    def open_picker():
        if _pick["win"] is not None:
            close_picker()
            return
        labels = ui.get("labels") or []
        if not labels:
            return
        win = ctk.CTkToplevel(root)
        _pick["win"] = win
        win.overrideredirect(True)
        win.configure(fg_color=T.CARD_2)
        win.attributes("-topmost", True)
        x, y = ver_field.winfo_rootx(), ver_field.winfo_rooty()
        h = min(360, 40 + 32 * min(len(labels), 8))
        win.geometry(f"{max(240, ver_field.winfo_width())}x{h}"
                     f"+{x}+{y + ver_field.winfo_height() + 4}")

        search = ctk.CTkEntry(win, placeholder_text="Filter versions…",
                               fg_color=T.CARD_3, border_width=0,
                               text_color=T.FG, corner_radius=8, height=30,
                               font=font(12))
        search.pack(fill="x", padx=6, pady=(6, 4))
        search.focus_set()

        sf = ctk.CTkScrollableFrame(win, fg_color=T.CARD_2, corner_radius=8)
        sf.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        cur = mc_var.get()

        def rebuild(_e=None):
            for child in sf.winfo_children():
                child.destroy()
            q = search.get().strip().lower()
            shown = [lab for lab in labels if q in lab.lower()] if q else labels
            if not shown:
                ctk.CTkLabel(sf, text="No matches", text_color=T.MUTED,
                             font=font(12)).pack(pady=10)
                return
            for lab in shown:
                is_beta = "beta" in lab
                row = ctk.CTkButton(
                    sf, text=lab, anchor="w", height=30, corner_radius=6,
                    fg_color=T.GREEN if lab == cur else "transparent",
                    hover_color=T.CARD_3,
                    text_color="white" if lab == cur else
                    (T.GOLD if is_beta else T.FG),
                    font=font(12), command=lambda l=lab: set_version(l))
                row.pack(fill="x", pady=1)
                
        def on_enter(_e=None):
            q = search.get().strip().lower()
            shown = [lab for lab in labels if q in lab.lower()] if q else labels
            if shown:
                set_version(shown[0])
                return "break"
            return "break"

        search.bind("<KeyRelease>", rebuild)
        search.bind("<Return>", on_enter)
        search.bind("<Escape>", lambda e: close_picker())
        rebuild()
        win.bind("<FocusOut>", lambda e: win.after(80, close_picker))
        win.bind("<Escape>", lambda e: close_picker())

    ver_field = ctk.CTkFrame(vbox, fg_color=T.CARD_2, corner_radius=10,
                              width=220, height=38)
    ver_field.pack(anchor="w")
    ver_field.pack_propagate(False)
    ver_lbl = ctk.CTkLabel(ver_field, textvariable=mc_var, text_color=T.FG,
                            font=font(13), anchor="w")
    ver_lbl.pack(side="left", fill="x", expand=True, padx=(12, 0))
    ver_arrow = ctk.CTkLabel(ver_field, text="▾", text_color=T.SUB, font=font(14))
    ver_arrow.pack(side="right", padx=(0, 12))

    def _ver_hover(on):
        ver_field.configure(fg_color=T.CARD_3 if on else T.CARD_2)
    for _w in (ver_field, ver_lbl, ver_arrow):
        _w.bind("<Enter>", lambda e: _ver_hover(True))
        _w.bind("<Leave>", lambda e: _ver_hover(False))
        _w.bind("<Button-1>", lambda e: open_picker())

    play_btn = mkbtn(bar, "▶   PLAY", lambda: do_play(), kind="play",
                      width=168, height=52, corner_radius=12,
                      font=font(16, "bold"))
    play_btn.pack(side="right")

    settings_btn = mkbtn(bar, "⚙", lambda: toggle_settings(), kind="ghost",
                          width=48, height=52, corner_radius=12, font=font(18))
    settings_btn.pack(side="right", padx=(0, 10))
    Tooltip(settings_btn, "Settings")

    det_btn = mkbtn(bar, "Details", lambda: toggle_details(), kind="flat",
                     width=86, height=52)
    det_btn.pack(side="right", padx=(0, 6))
    Tooltip(det_btn, "Show install / run log")

    # ==================================================================
    # Details / log panel
    # ==================================================================
    detwrap = ctk.CTkFrame(dock, fg_color=T.CARD_2, corner_radius=12)
    log_head = ctk.CTkFrame(detwrap, fg_color="transparent")
    log_head.pack(fill="x", padx=10, pady=(8, 0))
    ctk.CTkLabel(log_head, text="ACTIVITY LOG", text_color=T.MUTED,
                 font=font(10, "bold")).pack(side="left")

    logbox = tk.Text(detwrap, height=10, bg="#0b0d11", fg="#7fd97f", bd=0,
                      font=(MONO, 10), highlightthickness=0,
                      padx=12, pady=10, insertbackground=T.FG, wrap="word")

    def clear_log():
        logbox.delete("1.0", "end")

    def copy_log():
        root.clipboard_clear()
        root.clipboard_append(logbox.get("1.0", "end-1c"))
        copy_log_btn.configure(text="Copied ✓")
        root.after(1200, lambda: copy_log_btn.configure(text="Copy"))

    copy_log_btn = mkbtn(log_head, "Copy", copy_log, kind="flat",
                          width=56, height=24, font=font(11))
    copy_log_btn.pack(side="right")
    mkbtn(log_head, "Clear", clear_log, kind="flat",
          width=52, height=24, font=font(11)).pack(side="right", padx=(0, 4))

    logbox.pack(fill="both", expand=True, padx=10, pady=(6, 10))
    for _tg, (_lbl, _a1, _a2, _lc, _mc) in _LEVELS.items():
        _nm = _lbl.strip()
        logbox.tag_configure("L_" + _nm, foreground=_lc,
                              font=(MONO, 10, "bold"))
        logbox.tag_configure("M_" + _nm, foreground=_mc)

    def toggle_details():
        ui["details"] = not ui["details"]
        if ui["details"]:
            detwrap.pack(fill="both", padx=14, pady=(0, 14))
            det_btn.configure(text_color=T.FG)
        else:
            detwrap.pack_forget()
            det_btn.configure(text_color=T.SUB)

    # ==================================================================
    # Status / progress helpers
    # ==================================================================
    def set_status(t, color=T.SUB):
        root.after(0, lambda: (status_txt.set(t),
                                status_lbl.configure(text_color=color)))

    def _show_bar():
        if not prog.winfo_ismapped():
            prog.pack(fill="x", pady=(8, 2))

    def bar_busy():
        def ap():
            _show_bar()
            prog.configure(mode="indeterminate")
            prog.start()
        root.after(0, ap)

    def set_progress(g, t):
        def ap():
            _show_bar()
            prog.stop()
            prog.configure(mode="determinate")
            prog.set(g / max(1, t))
            status_txt.set(f"Downloading Minecraft…  {int(100 * g / max(1, t))}%")
            status_lbl.configure(text_color=T.FG)
        root.after(0, ap)

    def end_progress():
        def ap():
            prog.stop()
            prog.pack_forget()
        root.after(0, ap)

    def _friendly(line):
        m = line
        for tag in ("::", "OK", "!!", "xx"):
            if m.startswith(tag):
                m = m[len(tag):].strip()
                break
        low = m.lower()
        if "downloading minecraft" in low:
            return None        # handled by the % progress bar
        if ("building winegdk" in low or "cloning winegdk" in low
                or "updating winegdk" in low):
            return ("Setting up the game engine — first run, "
                    "this can take a while…")
        if "installing minecraft" in low or "reinstalling minecraft" in low:
            return "Installing Minecraft…"
        if "preparing gdk-proton" in low or "extracting" in low:
            return "Preparing the engine…"
        if "pre-auth" in low or "signing in" in low:
            return "Signing in to Xbox Live…"
        if "minecraft is running" in low:
            return ("Minecraft is running — close the game to come back here.",
                    True)
        if "starting minecraft" in low or "launching minecraft" in low:
            return "Starting Minecraft…"
        if "game closed" in low:
            return ("Minecraft closed.", True)
        return None

    def glog(line):
        lvl = _LEVELS.get(line[:2])
        if lvl:
            nm = lvl[0].strip()
            logbox.insert("end", lvl[0] + "  ", "L_" + nm)
            logbox.insert("end", line[2:].strip() + "\n", "M_" + nm)
        else:
            logbox.insert("end", line + "\n")
        logbox.see("end")
        if not ui["busy"]:
            return
        if line.startswith("xx"):
            set_status(line[2:].strip(), T.RED)
            return
        txt = _friendly(line)
        if txt:
            steady = False
            if isinstance(txt, tuple):
                txt, steady = txt
            if steady:
                set_status(txt, T.GREEN if "running" in txt.lower() else T.SUB)
                end_progress()
            else:
                set_status(txt, T.FG)
                bar_busy()
    log._LOG_SINK = lambda m: root.after(0, glog, m)

    # ==================================================================
    # Account
    # ==================================================================
    def acct_state(ph):
        if ph == "in":
            acct_dot.configure(text_color=T.GREEN)
            acct_txt.set("Signed in")
            acct_btn.configure(text="Sign out")
            acct_btn._mode = "out"
        elif ph == "auth":
            acct_dot.configure(text_color=T.GOLD)
            acct_txt.set("Sign-in pending…")
            acct_btn._mode = "out"
        else:
            acct_dot.configure(text_color=T.SUB)
            acct_txt.set("Not signed in")
            acct_btn.configure(text="Sign in")
            acct_btn._mode = "in"

    def acct_click():
        if getattr(acct_btn, "_mode", "in") == "out":
            na.stop()
            try:
                # PLAY holds this same non-blocking lock through the complete
                # game session, so Sign out can never invalidate credentials
                # halfway through launch or while Minecraft is using them.
                with prefix_operation_lock("sign out of Microsoft"):
                    msa_logout()
            except BolError as exc:
                warn(str(exc))
                acct_state("in" if msa_signed_in() else "out")
                return
            acct_state("out")
        else:
            threading.Thread(target=lambda: na.start(on_auth, on_online),
                              daemon=True).start()

    def on_auth(url, code):
        root.after(0, lambda: (acct_state("auth"), code_dialog(url, code)))

    def on_online():
        root.after(0, lambda: acct_state("in"))

    def code_dialog(url, code):
        d = dialog("Sign in to Microsoft", 380, 220)
        ctk.CTkLabel(d, text="Sign in to your Microsoft account",
                     font=font(15, "bold"), text_color=T.FG).pack(
                         anchor="w", padx=26, pady=(24, 0))
        ctk.CTkLabel(d, text="Open the link and enter this code:",
                     text_color=T.SUB).pack(anchor="w", padx=26, pady=(6, 12))
        cf = ctk.CTkFrame(d, fg_color=T.CARD_2, corner_radius=10)
        cf.pack(fill="x", padx=26)
        ctk.CTkLabel(cf, text=code, text_color=T.GOLD,
                      font=ctk.CTkFont(family=MONO, size=22,
                                       weight="bold")).pack(padx=18, pady=12)
        row = ctk.CTkFrame(d, fg_color="transparent")
        row.pack(fill="x", padx=26, pady=(14, 24))
        mkbtn(row, "Open link", lambda: subprocess.Popen(
            ["xdg-open", url], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL), kind="primary", width=120,
            height=38).pack(side="left")

        copy_btn = None

        def copy_code():
            root.clipboard_clear()
            root.clipboard_append(code)
            copy_btn.configure(text="Copied ✓")
            root.after(1200, lambda: copy_btn.configure(text="Copy code"))

        copy_btn = mkbtn(row, "Copy code", copy_code, kind="ghost", width=120,
                          height=38)
        copy_btn.pack(side="left", padx=10)

    # ==================================================================
    # Versions
    # ==================================================================
    def refresh_versions():
        beta = load_settings().get("show_betas", False)
        try:
            ui["versions"] = list_mc_versions(beta)
        except Exception as e:
            log._LOG_SINK(f"xx versions: {e}")
            return
        labels = [v["tag"] + ("  · beta" if v["beta"] else "")
                  for v in ui["versions"]]

        def ap():
            ui["labels"] = labels
            cur = (load_settings().get("mc_version") or "")
            pick = next((x for x in labels
                         if x.split("  ")[0] == cur
                         or x.split("  ")[0].startswith(cur + ".")),
                        labels[0] if labels else "")
            if labels:
                mc_var.set(pick)
                _update_selected_chip()
        root.after(0, ap)

    def selected_version():
        if not ui["versions"] or not mc_var.get():
            return None
        labels = [v["tag"] + ("  · beta" if v["beta"] else "")
                  for v in ui["versions"]]
        try:
            return ui["versions"][labels.index(mc_var.get())]
        except ValueError:
            return None

    # ==================================================================
    # Play
    # ==================================================================
    def busy(on):
        ui["busy"] = on
        play_btn.configure(state="disabled" if on else "normal")

    def do_play():
        if ui["busy"]:
            return
        busy(True)
        set_status("Preparing…", T.FG)
        bar_busy()

        def work():
            try:
                ver = selected_version()
                do_setup(mc_ver=ver, progress=set_progress)
                set_status("Starting Minecraft…", T.FG)
                ui["launch_active"] = True
                launch()
                set_status("Minecraft closed.", T.SUB)
            except Exception as e:
                message = str(e) or type(e).__name__
                log._LOG_SINK(f"xx {message}")
                set_status("Minecraft could not start.", T.RED)
                root.after(0, lambda text=message: messagebox.showerror(
                    "Minecraft could not start", text[:2000], parent=root))
            finally:
                ui["launch_active"] = False
                end_progress()
                root.after(0, lambda: busy(False))
        # Never let closing Tk kill the marker-owning PLAY worker before its
        # launch finally block can record/clear the completed GPU session.
        threading.Thread(target=work, daemon=False).start()

    # ==================================================================
    # Settings (tabbed) — built in, lives in view_area next to the hero
    # ==================================================================
    settings_view = ctk.CTkFrame(view_area, fg_color=T.CARD, corner_radius=18,
                                  border_width=1, border_color=T.BORDER)

    def toggle_settings():
        if settings_view.winfo_ismapped():
            settings_view.pack_forget()
            hero.pack(fill="both", expand=True)
            settings_btn.configure(fg_color=T.CARD_2)
        else:
            hero.pack_forget()
            settings_view.pack(fill="both", expand=True)
            settings_btn.configure(fg_color=T.CARD_3)

    def _build_settings():
        d = root  # dialog filedialogs/messageboxes parent to the main window
        outer = ctk.CTkFrame(settings_view, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=20, pady=18)

        head = ctk.CTkFrame(outer, fg_color="transparent")
        head.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(head, text="Settings", font=font(16, "bold"),
                     text_color=T.FG).pack(side="left")
        mkbtn(head, "← Back", toggle_settings, kind="flat", width=76,
              height=28, font=font(12)).pack(side="right")

        tabs = ctk.CTkTabview(
            outer, fg_color=T.CARD_2, segmented_button_fg_color=T.CARD_2,
            segmented_button_selected_color=T.GREEN,
            segmented_button_selected_hover_color=T.GREEN_HOV,
            segmented_button_unselected_color=T.CARD_2,
            text_color=T.FG, corner_radius=12)
        tabs.pack(fill="both", expand=True)
        tab_general = tabs.add("General")
        tab_advanced = tabs.add("Advanced")
        tab_tools = tabs.add("Tools")

        # ---- General --------------------------------------------------
        beta_v = tk.BooleanVar(value=load_settings().get("show_betas", False))

        def save_beta():
            s2 = load_settings()
            s2["show_betas"] = beta_v.get()
            save_settings(s2)
            threading.Thread(target=refresh_versions, daemon=True).start()
        ctk.CTkSwitch(tab_general, text="Show beta / preview versions",
                      variable=beta_v, command=save_beta,
                      progress_color=T.GREEN, font=font(13)
                      ).pack(anchor="w", pady=8, padx=4)

        confine_v = tk.BooleanVar(
            value=load_settings().get("confine_cursor", False))

        def save_confine():
            s2 = load_settings()
            s2["confine_cursor"] = confine_v.get()
            save_settings(s2)
        ctk.CTkSwitch(tab_general, text="Keep the mouse inside the window\n"
                      "(fixes the cursor escaping in windowed mode)",
                      variable=confine_v, command=save_confine,
                      progress_color=T.GREEN, font=font(13)
                      ).pack(anchor="w", pady=8, padx=4)

        # ---- Advanced ---------------------------------------------------
        diag_v = tk.BooleanVar(value=load_settings().get("diagnostics", False))

        def save_diag():
            s2 = load_settings()
            s2["diagnostics"] = diag_v.get()
            save_settings(s2)
        ctk.CTkSwitch(tab_advanced, text="Advanced diagnostics\n"
                      "(verbose logs — for bug reports)",
                      variable=diag_v, command=save_diag,
                      progress_color=T.GREEN, font=font(13)
                      ).pack(anchor="w", pady=(4, 12), padx=4)

        ctk.CTkLabel(tab_advanced, text="Custom environment variables",
                     text_color=T.SUB, font=font(11, "bold"),
                     anchor="w").pack(anchor="w", pady=(0, 4), padx=4)

        def save_custom_env(_event=None):
            s2 = load_settings()
            s2["custom_env"] = env_entry.get()
            save_settings(s2)

        env_entry = ctk.CTkEntry(
            tab_advanced,
            placeholder_text="e.g., PROTON_USE_WINED3D=1 KEY=VALUE",
            fg_color=T.CARD_3, border_width=0, text_color=T.FG,
            placeholder_text_color=T.MUTED, corner_radius=10, height=36,
            font=font(13))
        env_entry.pack(fill="x", pady=(0, 12), padx=4)
        saved_env = load_settings().get("custom_env") or ""
        if saved_env:
            env_entry.insert(0, saved_env)
        env_entry.bind("<KeyRelease>", save_custom_env)
        env_entry.bind("<FocusOut>", save_custom_env)
        env_entry.bind("<Return>", lambda e: "break")
        
        ctk.CTkLabel(tab_advanced, text="Gamescope arguments",
                     text_color=T.SUB, font=font(11, "bold"),
                     anchor="w").pack(anchor="w", pady=(0, 4), padx=4)

        def save_gamescope(_event=None):
            s2 = load_settings()
            s2["gamescope"] = gamescope_entry.get()
            save_settings(s2)

        gamescope_entry = ctk.CTkEntry(
            tab_advanced,
            placeholder_text="1 for auto, or e.g. -w 1920 -h 1080 -f",
            fg_color=T.CARD_3, border_width=0, text_color=T.FG,
            placeholder_text_color=T.MUTED, corner_radius=10, height=36,
            font=font(13))
        gamescope_entry.pack(fill="x", pady=(0, 4), padx=4)
        saved_gamescope = load_settings().get("gamescope") or ""
        if saved_gamescope:
            gamescope_entry.insert(0, saved_gamescope)
        gamescope_entry.bind("<KeyRelease>", save_gamescope)
        gamescope_entry.bind("<FocusOut>", save_gamescope)
        gamescope_entry.bind("<Return>", lambda e: "break")
        
        # ---- Tools --------------------------------------------------
        imp_status = tk.StringVar(value="")

        def do_import():
            from tkinter import filedialog, messagebox as mb
            files = filedialog.askopenfilenames(
                parent=d, title="Import Minecraft content",
                filetypes=[("Minecraft content",
                            "*.mcpack *.mcaddon *.mcworld *.mctemplate *.mcskin"),
                           ("All files", "*.*")])
            if not files:
                return
            imp_status.set("Importing…")

            def work():
                done, errs = [], []
                for f in files:
                    try:
                        done += import_content(f)
                    except BolError as e:
                        errs.append(str(e))
                    except Exception as e:        # noqa: BLE001
                        errs.append(f"{Path(f).name}: {e}")
                msg = (f"Imported {len(done)} item(s)."
                       if done else "Nothing imported.")
                if errs:
                    msg += "\n\nProblems:\n• " + "\n• ".join(errs)
                if _mc_running():
                    msg += ("\n\nMinecraft is running — restart it to see the "
                            "new content.")
                d.after(0, lambda: (imp_status.set(""),
                                    mb.showinfo("Import", msg, parent=d)))
            threading.Thread(target=work, daemon=True).start()

        def do_inject():
            from tkinter import filedialog, messagebox as mb
            if not _mc_running():
                mb.showwarning(
                    "DLL injector",
                    "Start Minecraft first and wait for the main menu, then "
                    "inject.", parent=d)
                return
            last = load_settings().get("injector_dll") or ""
            dll = filedialog.askopenfilename(
                parent=d, title="Choose a client .dll to inject",
                initialdir=(str(Path(last).parent) if last else None),
                initialfile=(Path(last).name if last else None),
                filetypes=[("Client DLL", "*.dll"), ("All files", "*.*")])
            if not dll:
                return
            imp_status.set("Injecting…")

            def work():
                try:
                    name = run_injector(dll)
                    s2 = load_settings()
                    s2["injector_dll"] = dll
                    save_settings(s2)
                    msg = (f"Injected {name} into Minecraft. ✓\n\n"
                           "(Native / AppImage only — not inside the Flatpak "
                           "sandbox.)")
                except Exception as e:                # noqa: BLE001
                    msg = f"Couldn't inject:\n{e}"
                d.after(0, lambda: (imp_status.set(""),
                                    mb.showinfo("DLL injector", msg,
                                                parent=d)))
            threading.Thread(target=work, daemon=True).start()

        for label, fn, kind in (
            ("Import content (.mcpack / .mcworld / .mcaddon)…",
             do_import, "ghost"),
            ("Inject a client DLL…", do_inject, "ghost"),
            ("Open Minecraft folder", lambda: subprocess.Popen(
                ["xdg-open", str(_mojang_dir())], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL), "ghost"),
            ("Open logs folder", lambda: subprocess.Popen(
                ["xdg-open", str(LOGS)], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL), "ghost"),
            ("Repair (reset Wine prefix)", lambda: threading.Thread(
                target=reset_prefix, daemon=True).start(), "ghost"),
            ("Force stop Minecraft", kill_wine, "danger"),
        ):
            mkbtn(tab_tools, label, fn, kind=kind, anchor="w",
                  height=38).pack(fill="x", pady=3, padx=4)

        ctk.CTkLabel(tab_tools, textvariable=imp_status, text_color=T.GOLD,
                     font=font(11)).pack(anchor="w", pady=(6, 0), padx=4)

        ctk.CTkLabel(outer, text=f"{PRETTY} {VERSION}", text_color=T.MUTED,
                     font=font(11)).pack(anchor="w", pady=(10, 0))

    _build_settings()

    # ==================================================================
    # Self-update
    # ==================================================================
    def relaunch_app():
        na.stop()
        try:
            if os.environ.get("APPIMAGE"):
                os.execv(os.environ["APPIMAGE"], [os.environ["APPIMAGE"], "gui"])
            tgt = os.path.realpath(sys.argv[0] or __file__)
            os.execv(sys.executable, [sys.executable, tgt, "gui"])
        except Exception:
            root.destroy()

    def update_progress(got, total):
        def ap():
            _show_bar()
            prog.stop()
            prog.configure(mode="determinate")
            prog.set(got / max(1, total))
            status_txt.set(f"Downloading update…  {int(100 * got / max(1, total))}%")
            status_lbl.configure(text_color=T.FG)
        root.after(0, ap)

    def restart_prompt():
        d = dialog("Update installed", 340, 150)
        ctk.CTkLabel(d, text="Update installed", font=font(14, "bold"),
                     text_color=T.FG).pack(anchor="w", padx=24, pady=(22, 0))
        ctk.CTkLabel(d, text="Restart now to run the new version?",
                     text_color=T.SUB).pack(anchor="w", padx=24, pady=(4, 16))
        row = ctk.CTkFrame(d, fg_color="transparent")
        row.pack(fill="x", padx=24, pady=(0, 22))
        mkbtn(row, "Restart now", relaunch_app, kind="primary", width=130,
              height=38, font=font(13, "bold")).pack(side="right")
        mkbtn(row, "Later", d.destroy, kind="ghost", width=90,
              height=38).pack(side="right", padx=(0, 8))

    def run_update(rel, banner):
        banner.destroy()
        set_status(f"Updating to v{rel['version']}…", T.FG)
        bar_busy()

        def done(state, msg):
            end_progress()
            set_status(msg, T.GREEN if state == "ok"
                       else (T.RED if state == "error" else T.SUB))
            if state == "ok":
                restart_prompt()

        def work():
            state, msg = self_update(rel, progress=update_progress)
            root.after(0, lambda: done(state, msg))
        threading.Thread(target=work, daemon=True).start()

    def show_update_banner(rel):
        bn = ctk.CTkFrame(root, fg_color=T.GREEN_DIM, corner_radius=12,
                           border_width=1, border_color=T.GREEN)
        ctk.CTkLabel(bn, text=f"⟳   Update available — v{rel['version']}   "
                     f"(you have {VERSION})", text_color="#cfe8c2",
                     font=font(12, "bold")).pack(side="left", padx=18, pady=9)
        mkbtn(bn, "Later", bn.destroy, kind="flat", width=64, height=30,
              text_color="#9fb89a", hover_color="#33421f").pack(
                  side="right", padx=(0, 14), pady=7)
        mkbtn(bn, "Update now", lambda: run_update(rel, bn), kind="primary",
              width=112, height=30, font=font(12, "bold")).pack(
                  side="right", padx=(0, 6), pady=7)
        bn.pack(fill="x", padx=22, pady=(0, 6), after=top)

    def update_check():
        rel = check_for_update()
        if rel:
            root.after(0, lambda: show_update_banner(rel))

    acct_state("in" if msa_signed_in() else "out")
    threading.Thread(target=refresh_versions, daemon=True).start()
    threading.Thread(target=update_check, daemon=True).start()

    def on_close():
        if ui.get("launch_active"):
            messagebox.showwarning(
                "Minecraft is running",
                "Close Minecraft first and wait for the launcher to report "
                "that it closed. To abort it, use Settings → Tools → Force "
                "stop Minecraft.",
                parent=root,
            )
            return
        if ui.get("busy"):
            messagebox.showwarning(
                "Operation in progress",
                "Wait for the current preparation task to finish before "
                "closing the launcher.",
                parent=root,
            )
            return
        na.stop()
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", on_close)
    def on_enter_pressed(e):
        focus = root.focus_get()
        if isinstance(focus, (tk.Entry, tk.Text)):
            return "break"
        do_play()

    root.bind("<Return>", on_enter_pressed)
    root.mainloop()
