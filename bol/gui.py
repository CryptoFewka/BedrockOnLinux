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

    # Minecraft-launcher palette: deep slate + signature green.
    BG, CARD, CARD2 = "#15171c", "#1e2129", "#272b35"
    FG, SUB = "#f1f3f5", "#8b93a7"
    GREEN, GREEN_H = "#4c9f4f", "#58b85b"
    GOLD, RED, FIELD = "#e0b341", "#e06c5b", "#272b35"

    ctk.set_appearance_mode("dark")
    try:
        root = ctk.CTk(className=PRETTY)
    except Exception as e:
        _desktop_error(
            f"No usable X11/XWayland display ({e}). Enable XWayland or use "
            "the command line.")
        return
    root.title(PRETTY)
    root.geometry("900x600")
    root.minsize(820, 560)
    root.configure(fg_color=BG)

    def font(size=13, weight="normal"):
        return ctk.CTkFont(size=size, weight=weight)

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

    na = NativeAuth()
    ui = {"versions": [], "labels": [], "busy": False, "details": False,
          "launch_active": False}

    def mkbtn(parent, text, cmd, kind="ghost", **kw):
        base = {
            "play":    dict(fg_color=GREEN, hover_color=GREEN_H, text_color="white"),
            "primary": dict(fg_color=GREEN, hover_color=GREEN_H, text_color="white"),
            "ghost":   dict(fg_color=CARD2, hover_color="#333a47", text_color=FG),
            "flat":    dict(fg_color="transparent", hover_color=CARD2, text_color=SUB),
        }[kind]
        opts = dict(corner_radius=10, font=font(13), command=cmd)
        opts.update(base)
        opts.update(kw)
        return ctk.CTkButton(parent, text=text, **opts)

    top = ctk.CTkFrame(root, fg_color="transparent")
    top.pack(fill="x", padx=22, pady=(16, 6))
    ll = logo_label(top, 34, BG)
    if ll:
        ll.pack(side="left", padx=(2, 12))
    ctk.CTkLabel(top, text="BedrockOnLinux", font=font(17, "bold"),
                 text_color=FG).pack(side="left")

    acct = ctk.CTkFrame(top, fg_color=CARD, corner_radius=12)
    acct.pack(side="right")
    acct_dot = ctk.CTkLabel(acct, text="●", text_color=SUB, font=font(13), width=12)
    acct_dot.pack(side="left", padx=(12, 2), pady=6)
    acct_txt = tk.StringVar(value="Not signed in")
    ctk.CTkLabel(acct, textvariable=acct_txt, text_color=FG,
                 font=font(13)).pack(side="left", padx=(2, 8))
    acct_btn = mkbtn(acct, "Sign in", lambda: acct_click(), kind="ghost",
                     width=86, height=30, font=font(12, "bold"))
    acct_btn.pack(side="left", padx=(0, 8), pady=8)

    hero = ctk.CTkFrame(root, fg_color=CARD, corner_radius=18)
    hero.pack(fill="both", expand=True, padx=22, pady=6)
    hw = ctk.CTkFrame(hero, fg_color="transparent")
    hw.place(relx=0.5, rely=0.46, anchor="center")
    hl = logo_label(hw, 120, CARD)
    if hl:
        hl.pack()
    ctk.CTkLabel(hw, text="Minecraft Bedrock", font=font(27, "bold"),
                 text_color=FG).pack(pady=(14, 2))
    ctk.CTkLabel(hw, text="Bedrock Edition for Linux", font=font(13),
                 text_color=SUB).pack()

    status = ctk.CTkFrame(root, fg_color="transparent")
    status.pack(fill="x", padx=26, pady=(4, 0))
    status_txt = tk.StringVar(value="Ready to play.")
    status_lbl = ctk.CTkLabel(status, textvariable=status_txt, text_color=SUB,
                              font=font(12), anchor="w")
    status_lbl.pack(fill="x")
    prog = ctk.CTkProgressBar(status, height=8, corner_radius=4,
                              progress_color=GREEN, fg_color=CARD2)
    prog.set(0)

    bar = ctk.CTkFrame(root, fg_color="transparent")
    bar.pack(fill="x", padx=22, pady=(8, 16))

    vbox = ctk.CTkFrame(bar, fg_color="transparent")
    vbox.pack(side="left")
    ctk.CTkLabel(vbox, text="VERSION", text_color=SUB, font=font(11, "bold"),
                 anchor="w").pack(anchor="w", pady=(0, 3))
    mc_var = tk.StringVar(value="")

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
        close_picker()

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
        win.configure(fg_color=CARD2)
        win.attributes("-topmost", True)
        x, y = ver_field.winfo_rootx(), ver_field.winfo_rooty()
        win.geometry(f"{ver_field.winfo_width()}x{min(340, 34 * len(labels) + 16)}"
                     f"+{x}+{y + ver_field.winfo_height() + 4}")
        sf = ctk.CTkScrollableFrame(win, fg_color=CARD2, corner_radius=8)
        sf.pack(fill="both", expand=True, padx=2, pady=2)
        cur = mc_var.get()
        for lab in labels:
            ctk.CTkButton(sf, text=lab, anchor="w", height=30, corner_radius=6,
                          fg_color=GREEN if lab == cur else "transparent",
                          hover_color="#333a47",
                          text_color="white" if lab == cur else FG, font=font(12),
                          command=lambda l=lab: set_version(l)).pack(fill="x", pady=1)
        win.after(10, win.focus_force)
        win.bind("<FocusOut>", lambda e: close_picker())
        win.bind("<Escape>", lambda e: close_picker())

    ver_field = ctk.CTkFrame(vbox, fg_color=FIELD, corner_radius=10,
                             width=210, height=36)
    ver_field.pack(anchor="w")
    ver_field.pack_propagate(False)
    ver_lbl = ctk.CTkLabel(ver_field, textvariable=mc_var, text_color=FG,
                           font=font(13), anchor="w")
    ver_lbl.pack(side="left", fill="x", expand=True, padx=(12, 0))
    ver_arrow = ctk.CTkLabel(ver_field, text="▾", text_color=SUB, font=font(14))
    ver_arrow.pack(side="right", padx=(0, 12))

    def _ver_hover(on):
        ver_field.configure(fg_color="#30353f" if on else FIELD)
    for _w in (ver_field, ver_lbl, ver_arrow):
        _w.bind("<Enter>", lambda e: _ver_hover(True))
        _w.bind("<Leave>", lambda e: _ver_hover(False))
        _w.bind("<Button-1>", lambda e: open_picker())

    play_btn = mkbtn(bar, "▶   PLAY", lambda: do_play(), kind="play",
                     width=158, height=48, corner_radius=12, font=font(15, "bold"))
    play_btn.pack(side="right")
    mkbtn(bar, "⚙", lambda: open_settings(), kind="ghost", width=48, height=48,
          corner_radius=12, font=font(18)).pack(side="right", padx=(0, 10))
    det_btn = mkbtn(bar, "Details", lambda: toggle_details(), kind="flat",
                    width=82, height=48)
    det_btn.pack(side="right", padx=(0, 6))

    detwrap = ctk.CTkFrame(root, fg_color=CARD, corner_radius=12)
    logbox = tk.Text(detwrap, height=9, bg="#0d0f13", fg="#7fd97f", bd=0,
                     font=("monospace", 10), highlightthickness=0,
                     padx=12, pady=10, insertbackground=FG)
    logbox.pack(fill="both", expand=True, padx=5, pady=5)
    for _tg, (_lbl, _a1, _a2, _lc, _mc) in _LEVELS.items():
        _nm = _lbl.strip()
        logbox.tag_configure("L_" + _nm, foreground=_lc,
                             font=("monospace", 10, "bold"))
        logbox.tag_configure("M_" + _nm, foreground=_mc)

    def toggle_details():
        ui["details"] = not ui["details"]
        if ui["details"]:
            detwrap.pack(fill="both", padx=22, pady=(0, 16))
            det_btn.configure(text_color=FG)
        else:
            detwrap.pack_forget()
            det_btn.configure(text_color=SUB)

    def set_status(t, color=SUB):
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
            status_lbl.configure(text_color=FG)
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
            set_status(line[2:].strip(), RED)
            return
        txt = _friendly(line)
        if txt:
            steady = False
            if isinstance(txt, tuple):
                txt, steady = txt
            if steady:
                set_status(txt, GREEN if "running" in txt.lower() else SUB)
                end_progress()
            else:
                set_status(txt, FG)
                bar_busy()
    log._LOG_SINK = lambda m: root.after(0, glog, m)

    def acct_state(ph):
        if ph == "in":
            acct_dot.configure(text_color=GREEN)
            acct_txt.set("Signed in")
            acct_btn.configure(text="Sign out")
            acct_btn._mode = "out"
        elif ph == "auth":
            acct_dot.configure(text_color=GOLD)
            acct_txt.set("Sign-in pending…")
            acct_btn._mode = "out"
        else:
            acct_dot.configure(text_color=SUB)
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
        d = ctk.CTkToplevel(root)
        d.title("Sign in to Microsoft")
        d.configure(fg_color=CARD)
        d.transient(root)
        d.resizable(False, False)
        d.after(120, d.lift)
        ctk.CTkLabel(d, text="Sign in to your Microsoft account",
                     font=font(15, "bold"), text_color=FG).pack(
                         anchor="w", padx=26, pady=(24, 0))
        ctk.CTkLabel(d, text="Open the link and enter this code:",
                     text_color=SUB).pack(anchor="w", padx=26, pady=(6, 12))
        cf = ctk.CTkFrame(d, fg_color=FIELD, corner_radius=10)
        cf.pack(fill="x", padx=26)
        ctk.CTkLabel(cf, text=code, text_color=GOLD,
                     font=ctk.CTkFont(family="monospace", size=22,
                                      weight="bold")).pack(padx=18, pady=12)
        row = ctk.CTkFrame(d, fg_color="transparent")
        row.pack(fill="x", padx=26, pady=(14, 24))
        mkbtn(row, "Open link", lambda: subprocess.Popen(
            ["xdg-open", url], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL), kind="primary", width=120,
            height=38).pack(side="left")
        mkbtn(row, "Copy code", lambda: (root.clipboard_clear(),
              root.clipboard_append(code)), kind="ghost", width=120,
              height=38).pack(side="left", padx=10)

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

    def busy(on):
        ui["busy"] = on
        play_btn.configure(state="disabled" if on else "normal")

    def do_play():
        if ui["busy"]:
            return
        busy(True)
        set_status("Preparing…", FG)
        bar_busy()

        def work():
            try:
                ver = selected_version()
                do_setup(mc_ver=ver, progress=set_progress)
                set_status("Starting Minecraft…", FG)
                ui["launch_active"] = True
                launch()
                set_status("Minecraft closed.", SUB)
            except Exception as e:
                message = str(e) or type(e).__name__
                log._LOG_SINK(f"xx {message}")
                set_status("Minecraft could not start.", RED)
                root.after(0, lambda text=message: messagebox.showerror(
                    "Minecraft could not start", text[:2000], parent=root))
            finally:
                ui["launch_active"] = False
                end_progress()
                root.after(0, lambda: busy(False))
        # Never let closing Tk kill the marker-owning PLAY worker before its
        # launch finally block can record/clear the completed GPU session.
        threading.Thread(target=work, daemon=False).start()

    def open_settings():
        d = ctk.CTkToplevel(root)
        d.title("Settings")
        d.configure(fg_color=CARD)
        d.transient(root)
        d.resizable(False, False)
        d.after(120, d.lift)
        wrap = ctk.CTkFrame(d, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=24, pady=22)
        ctk.CTkLabel(wrap, text="Settings", font=font(16, "bold"),
                     text_color=FG).pack(anchor="w", pady=(0, 14))

        beta_v = tk.BooleanVar(value=load_settings().get("show_betas", False))

        def save_beta():
            s2 = load_settings()
            s2["show_betas"] = beta_v.get()
            save_settings(s2)
            threading.Thread(target=refresh_versions, daemon=True).start()
        ctk.CTkSwitch(wrap, text="Show beta / preview versions", variable=beta_v,
                      command=save_beta, progress_color=GREEN,
                      font=font(13)).pack(anchor="w", pady=7)

        diag_v = tk.BooleanVar(value=load_settings().get("diagnostics", False))

        def save_diag():
            s2 = load_settings()
            s2["diagnostics"] = diag_v.get()
            save_settings(s2)
        ctk.CTkSwitch(wrap, text="Advanced diagnostics (verbose logs — for bug "
                      "reports)", variable=diag_v, command=save_diag,
                      progress_color=GREEN, font=font(13)).pack(anchor="w", pady=7)

        confine_v = tk.BooleanVar(
            value=load_settings().get("confine_cursor", False))

        def save_confine():
            s2 = load_settings()
            s2["confine_cursor"] = confine_v.get()
            save_settings(s2)
        ctk.CTkSwitch(wrap, text="Keep the mouse inside the window (fixes the "
                      "cursor escaping in windowed mode)", variable=confine_v,
                      command=save_confine, progress_color=GREEN,
                      font=font(13)).pack(anchor="w", pady=7)

        ctk.CTkLabel(wrap, text="Custom Environment Variables",
                     text_color=SUB, font=font(11, "bold"),
                     anchor="w").pack(anchor="w", pady=(4, 3))

        def save_custom_env(_event=None):
            s2 = load_settings()
            s2["custom_env"] = env_entry.get()
            save_settings(s2)

        env_entry = ctk.CTkEntry(
            wrap,
            placeholder_text="e.g., PROTON_USE_WINED3D=1 KEY=VALUE",
            fg_color=FIELD, border_color=FIELD, text_color=FG,
            placeholder_text_color=SUB, corner_radius=10, height=36,
            font=font(13))
        env_entry.pack(fill="x", pady=(0, 7))
        saved_env = load_settings().get("custom_env") or ""
        if saved_env:
            env_entry.insert(0, saved_env)
        env_entry.bind("<KeyRelease>", save_custom_env)
        env_entry.bind("<FocusOut>", save_custom_env)

        ctk.CTkLabel(wrap, text="Gamescope",
                     text_color=SUB, font=font(11, "bold"),
                     anchor="w").pack(anchor="w", pady=(4, 3))

        def save_gamescope(_event=None):
            s2 = load_settings()
            s2["gamescope"] = gamescope_entry.get()
            save_settings(s2)

        gamescope_entry = ctk.CTkEntry(
            wrap,
            placeholder_text="1 for auto, or e.g. -w 1920 -h 1080 -f",
            fg_color=FIELD, border_color=FIELD, text_color=FG,
            placeholder_text_color=SUB, corner_radius=10, height=36,
            font=font(13))
        gamescope_entry.pack(fill="x", pady=(0, 7))
        saved_gamescope = load_settings().get("gamescope") or ""
        if saved_gamescope:
            gamescope_entry.insert(0, saved_gamescope)
        gamescope_entry.bind("<KeyRelease>", save_gamescope)
        gamescope_entry.bind("<FocusOut>", save_gamescope)

        ctk.CTkFrame(wrap, fg_color=CARD2, height=1).pack(fill="x", pady=14)

        imp_status = tk.StringVar(value="")

        def do_import():
            from tkinter import filedialog, messagebox
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
                                    messagebox.showinfo("Import", msg, parent=d)))
            threading.Thread(target=work, daemon=True).start()

        def do_inject():
            from tkinter import filedialog, messagebox
            if not _mc_running():
                messagebox.showwarning(
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
                                    messagebox.showinfo("DLL injector", msg,
                                                        parent=d)))
            threading.Thread(target=work, daemon=True).start()

        for label, fn in (
            ("Import content (.mcpack / .mcworld / .mcaddon)…", do_import),
            ("Inject a client DLL…", do_inject),
            ("Open Minecraft folder", lambda: subprocess.Popen(
                ["xdg-open", str(_mojang_dir())], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)),
            ("Open logs folder", lambda: subprocess.Popen(
                ["xdg-open", str(LOGS)], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)),
            ("Repair (reset Wine prefix)", lambda: threading.Thread(
                target=reset_prefix, daemon=True).start()),
            ("Force stop Minecraft", kill_wine),
        ):
            mkbtn(wrap, label, fn, kind="ghost", anchor="w",
                  height=38).pack(fill="x", pady=3)
        ctk.CTkLabel(wrap, textvariable=imp_status, text_color=GOLD,
                     font=font(11)).pack(anchor="w", pady=(2, 0))
        ctk.CTkLabel(wrap, text=f"{PRETTY} {VERSION}", text_color=SUB,
                     font=font(11)).pack(anchor="w", pady=(12, 0))

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
            status_lbl.configure(text_color=FG)
        root.after(0, ap)

    def restart_prompt():
        d = ctk.CTkToplevel(root)
        d.title("Update installed")
        d.configure(fg_color=CARD)
        d.transient(root)
        d.resizable(False, False)
        d.after(120, d.lift)
        ctk.CTkLabel(d, text="Update installed", font=font(14, "bold"),
                     text_color=FG).pack(anchor="w", padx=24, pady=(22, 0))
        ctk.CTkLabel(d, text="Restart now to run the new version?",
                     text_color=SUB).pack(anchor="w", padx=24, pady=(4, 16))
        row = ctk.CTkFrame(d, fg_color="transparent")
        row.pack(fill="x", padx=24, pady=(0, 22))
        mkbtn(row, "Restart now", relaunch_app, kind="primary", width=130,
              height=38, font=font(13, "bold")).pack(side="right")
        mkbtn(row, "Later", d.destroy, kind="ghost", width=90,
              height=38).pack(side="right", padx=(0, 8))

    def run_update(rel, banner):
        banner.destroy()
        set_status(f"Updating to v{rel['version']}…", FG)
        bar_busy()

        def done(state, msg):
            end_progress()
            set_status(msg, GREEN if state == "ok"
                       else (RED if state == "error" else SUB))
            if state == "ok":
                restart_prompt()

        def work():
            state, msg = self_update(rel, progress=update_progress)
            root.after(0, lambda: done(state, msg))
        threading.Thread(target=work, daemon=True).start()

    def show_update_banner(rel):
        bn = ctk.CTkFrame(root, fg_color="#26331f", corner_radius=10)
        ctk.CTkLabel(bn, text=f"⟳   Update available — v{rel['version']}   "
                     f"(you have {VERSION})", text_color="#cfe8c2",
                     font=font(12, "bold")).pack(side="left", padx=18, pady=8)
        mkbtn(bn, "Later", bn.destroy, kind="flat", width=64, height=30,
              text_color="#9fb89a", hover_color="#33421f").pack(
                  side="right", padx=(0, 14), pady=7)
        mkbtn(bn, "Update now", lambda: run_update(rel, bn), kind="primary",
              width=112, height=30, font=font(12, "bold")).pack(
                  side="right", padx=(0, 6), pady=7)
        bn.pack(fill="x", padx=22, pady=(0, 4), after=top)

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
                "that it closed. To abort it, use Settings → Force stop "
                "Minecraft.",
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
    root.mainloop()
