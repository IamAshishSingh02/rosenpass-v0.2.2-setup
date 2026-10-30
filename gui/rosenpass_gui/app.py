"""Tkinter UI and state machine for the rosenpass GUI."""

from __future__ import annotations

import base64
import queue
import shutil
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import keyserver
from . import profile as profile_mod
from . import runner
from . import wireguard
from .exchange import ExchangeProcess

IDLE = "IDLE"
EXCHANGING = "EXCHANGING"
KEYS_EXCHANGED = "KEYS EXCHANGED"
WG_UP = "WG UP"
ERROR = "ERROR"

STATE_COLORS = {
    IDLE: "#8e8e93",
    EXCHANGING: "#ff9500",
    KEYS_EXCHANGED: "#34c759",
    WG_UP: "#0071e3",
    ERROR: "#ff3b30",
}

POLL_MS = 200
WG_POLL_MS = 10_000

# -- design tokens (Apple-ish light palette, flat/modern) ---------------------
BG = "#f5f5f7"        # window background
CARD = "#ffffff"      # card background
FG = "#1d1d1f"        # primary text
MUTED = "#86868b"     # secondary text
FIELD = "#f5f5f7"     # entry field background
BORDER = "#d2d2d7"
ACCENT = "#0071e3"    # primary action blue
ACCENT_DARK = "#0051a2"
DISABLED_BG = "#c7e2f9"

LOG_BG = "#1d1d2b"
LOG_FG = "#e8e8ed"

UI_FONT = ("Helvetica", 12)
UI_FONT_BOLD = ("Helvetica", 12, "bold")
TITLE_FONT = ("Helvetica", 22, "bold")
SUB_FONT = ("Helvetica", 11)
SECTION_FONT = ("Helvetica", 10, "bold")
MONO_FONT = ("Menlo", 11)


class App:
    def __init__(self, root: tk.Tk, initial_profile: str | None = None):
        self.root = root
        self.profile = None
        self.exchange: ExchangeProcess | None = None
        self.state = IDLE
        self.wg_poll_job = None
        self._last_wg_status = None

        root.title("rosenpass")
        screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
        width = min(1120, max(760, screen_w - 80))
        height = min(880, max(560, screen_h - 100))
        root.geometry(f"{width}x{height}")
        root.minsize(760, 560)
        self._build_ui()
        self._set_state(IDLE)

        self.log(
            "rosenpass GUI — create a profile, generate keys, exchange the two "
            "public keys with your peer, then start the exchange."
        )
        self._startup_diagnostics()

        if initial_profile:
            self._load_profile(initial_profile)

        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ UI

    def _setup_style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")  # only theme that allows full recoloring
        self.root.configure(bg=BG)

        style.configure(".", background=BG, foreground=FG, font=UI_FONT)
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED,
                        font=SUB_FONT)
        style.configure("CardMuted.TLabel", background=CARD, foreground=MUTED,
                        font=SUB_FONT)
        style.configure("Title.TLabel", background=BG, foreground=FG,
                        font=TITLE_FONT)

        # cards: white, hairline border
        style.configure("Card.TLabelframe", background=CARD,
                        bordercolor=BORDER, relief="flat", borderwidth=1)
        style.configure("Card.TLabelframe.Label", background=CARD,
                        foreground=MUTED, font=SECTION_FONT)

        # inputs
        for w in ("TEntry", "TCombobox", "TSpinbox"):
            style.configure(w, fieldbackground=FIELD, background=FIELD,
                            foreground=FG, bordercolor=BORDER,
                            lightcolor=BORDER, darkcolor=BORDER,
                            insertcolor=FG, padding=6)
            style.map(w,
                      bordercolor=[("focus", ACCENT)],
                      lightcolor=[("focus", ACCENT)],
                      darkcolor=[("focus", ACCENT)])

        # buttons
        style.configure("TButton", background="#e5e5ea", foreground=FG,
                        font=UI_FONT, borderwidth=0, padding=(14, 8),
                        relief="flat")
        style.map("TButton",
                  background=[("disabled", "#f0f0f2"), ("pressed", "#d2d2d7")],
                  foreground=[("disabled", MUTED)])
        style.configure("Accent.TButton", background=ACCENT, foreground="#fff",
                        font=UI_FONT_BOLD, borderwidth=0, padding=(16, 8))
        style.map("Accent.TButton",
                  background=[("disabled", DISABLED_BG),
                              ("pressed", ACCENT_DARK)],
                  foreground=[("disabled", "#ffffff")])
        style.configure("Danger.TButton", background="#ffe5e0",
                        foreground="#c93400", font=UI_FONT, borderwidth=0,
                        padding=(14, 8))
        style.map("Danger.TButton",
                  background=[("disabled", "#f5ecea"), ("pressed", "#ffd0c7")],
                  foreground=[("disabled", MUTED)])

    def _build_ui(self):
        self._setup_style()
        pad = dict(padx=6, pady=4)
        main = ttk.Frame(self.root)
        main.pack(fill="both", expand=True, padx=18, pady=(14, 16))

        # -- header
        header = ttk.Frame(main)
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(header, text="Rosenpass", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="post-quantum preshared keys for WireGuard",
                  style="Muted.TLabel").pack(side="left", padx=(14, 0), pady=(8, 0))

        # -- profile card
        pf = ttk.LabelFrame(main, text="  PROFILE  ", style="Card.TLabelframe")
        pf.pack(fill="x", **pad)
        inner = ttk.Frame(pf, style="Card.TFrame")
        inner.pack(fill="x", padx=14, pady=12)
        for text, col in (("Machine name", 0), ("Number", 2), ("Role", 4),
                          ("Load", 6)):
            ttk.Label(inner, text=text, style="CardMuted.TLabel").grid(
                row=0, column=col, sticky="sw", **pad)
        self.name_var = tk.StringVar()
        ttk.Entry(inner, textvariable=self.name_var, width=18).grid(
            row=1, column=0, sticky="we", **pad)
        self.number_var = tk.IntVar(value=1)
        spin = ttk.Spinbox(
            inner, from_=1, to=253, width=5, textvariable=self.number_var,
            command=self._on_number_changed)
        spin.grid(row=1, column=1, **pad)
        spin.bind("<KeyRelease>", lambda _e: self._on_number_changed())
        self.role_var = tk.StringVar(value=profile_mod.ROLE_AUTO)
        ttk.Combobox(
            inner, textvariable=self.role_var, width=12, state="readonly",
            values=[profile_mod.ROLE_AUTO, profile_mod.ROLE_INITIATOR,
                    profile_mod.ROLE_RESPONDER]).grid(row=1, column=3, **pad)
        self.load_var = tk.StringVar()
        self.load_box = ttk.Combobox(
            inner, textvariable=self.load_var, width=14, state="readonly",
            values=profile_mod.Profile.list_profiles())
        self.load_box.grid(row=1, column=5, **pad)
        ttk.Button(inner, text="Load", command=self._on_load_clicked).grid(
            row=1, column=7, **pad)
        inner.columnconfigure(0, weight=2)
        inner.columnconfigure(3, weight=1)
        inner.columnconfigure(5, weight=1)

        # -- peer card
        pr = ttk.LabelFrame(main, text="  PEER  ", style="Card.TLabelframe")
        pr.pack(fill="x", **pad)
        inner = ttk.Frame(pr, style="Card.TFrame")
        inner.pack(fill="x", padx=14, pady=12)
        for text, col in (("Peer name", 0), ("Endpoint host", 2), ("Port", 4),
                          ("Peer WG IP", 6)):
            ttk.Label(inner, text=text, style="CardMuted.TLabel").grid(
                row=0, column=col, sticky="sw", **pad)
        self.peer_name_var = tk.StringVar()
        ttk.Entry(inner, textvariable=self.peer_name_var, width=18).grid(
            row=1, column=0, sticky="we", **pad)
        self.host_var = tk.StringVar()
        ttk.Entry(inner, textvariable=self.host_var, width=22).grid(
            row=1, column=2, sticky="we", **pad)
        self.port_var = tk.IntVar(value=10002)
        ttk.Entry(inner, textvariable=self.port_var, width=7).grid(
            row=1, column=4, sticky="w", **pad)
        self.peer_ip_var = tk.StringVar()
        ttk.Entry(inner, textvariable=self.peer_ip_var, width=14).grid(
            row=1, column=6, sticky="w", **pad)

        # peer key rows
        for label_txt, row, var, import_fn, pick_fn in (
            ("Peer rosenpass key", 2, "peer_rp_var",
             self._import_peer_rp_key, self._pick_peer_rp_key),
            ("Peer WireGuard key", 3, "peer_wg_var",
             self._import_peer_wg_key, self._pick_peer_wg_key),
        ):
            ttk.Label(inner, text=label_txt, style="CardMuted.TLabel").grid(
                row=row, column=0, sticky="e", **pad)
            var_obj = tk.StringVar()
            setattr(self, var, var_obj)
            ttk.Entry(inner, textvariable=var_obj).grid(
                row=row, column=1, columnspan=5, sticky="we", **pad)
            btns = ttk.Frame(inner, style="Card.TFrame")
            btns.grid(row=row, column=6, columnspan=2, sticky="w", **pad)
            ttk.Button(btns, text="Paste", command=import_fn).pack(
                side="left", padx=2)
            ttk.Button(btns, text="Choose file…", command=pick_fn).pack(
                side="left", padx=2)
        inner.columnconfigure(0, weight=1)
        inner.columnconfigure(1, weight=2)
        inner.columnconfigure(2, weight=2)
        inner.columnconfigure(6, weight=1)

        # -- status strip: colored dot + pill badge
        st = ttk.Frame(main)
        st.pack(fill="x", **pad)
        self.dot = tk.Label(st, text="●", font=("Helvetica", 20), bg=BG,
                            fg=STATE_COLORS[IDLE])
        self.dot.pack(side="left", padx=(4, 6))
        self.state_label = tk.Label(
            st, text=IDLE, font=("Helvetica", 14, "bold"),
            bg=STATE_COLORS[IDLE], fg="#ffffff", padx=14, pady=4)
        self.state_label.pack(side="left")
        self.detail_label = ttk.Label(st, text="", style="Muted.TLabel")
        self.detail_label.pack(side="left", padx=(12, 0))

        # -- action bar
        bt = ttk.Frame(main)
        bt.pack(fill="x", **pad)
        self.buttons = {}
        self.action_buttons = []
        for text, cmd, style_name in (
            ("Generate keys", self.on_generate_keys, "TButton"),
            ("Start exchange", self.on_start_exchange, "Accent.TButton"),
            ("Stop", self.on_stop, "Danger.TButton"),
            ("Enable WireGuard", self.on_enable_wg, "TButton"),
            ("Apply refreshed key", self.on_apply_refreshed_key, "TButton"),
            ("Disable WireGuard", self.on_disable_wg, "TButton"),
        ):
            b = ttk.Button(bt, text=text, style=style_name, command=cmd)
            self.action_buttons.append(b)
            self.buttons[text] = b
        self._action_columns = None
        bt.bind("<Configure>", lambda _event: self._layout_action_buttons(bt))
        self.root.after_idle(lambda: self._layout_action_buttons(bt))

        # -- key server card: upload/fetch public keys via the deployed link
        ks = ttk.LabelFrame(main, text="  KEY SERVER  ", style="Card.TLabelframe")
        ks.pack(fill="x", **pad)
        ksin = ttk.Frame(ks, style="Card.TFrame")
        ksin.pack(fill="x", padx=14, pady=12)
        ttk.Label(ksin, text="Deployed link", style="CardMuted.TLabel").grid(
            row=0, column=0, sticky="sw", **pad)
        self.server_var = tk.StringVar()
        ttk.Entry(ksin, textvariable=self.server_var).grid(
            row=1, column=0, sticky="we", **pad)
        self._ks_upload_btn = ttk.Button(
            ksin, text="Upload my keys", command=self.on_upload_keys)
        self._ks_upload_btn.grid(row=1, column=1, **pad)
        self._ks_fetch_btn = ttk.Button(
            ksin, text="Fetch peer keys", command=self.on_fetch_peer_keys)
        self._ks_fetch_btn.grid(row=1, column=2, **pad)
        ksin.columnconfigure(0, weight=1)
        self._ks_queue: queue.Queue = queue.Queue()
        self._ks_busy = False

        # -- log console (dark, monospace, colored levels)
        lf = ttk.LabelFrame(main, text="  LOG  ", style="Card.TLabelframe")
        lf.pack(fill="both", expand=True, **pad)
        self.log_text = tk.Text(
            lf, height=12, wrap="word", state="disabled", bg=LOG_BG, fg=LOG_FG,
            insertbackground=LOG_FG, font=MONO_FONT, relief="flat",
            padx=12, pady=10, borderwidth=0, highlightthickness=0)
        scroll = ttk.Scrollbar(lf, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.tag_configure("ok", foreground="#5ac8a0")
        self.log_text.tag_configure("err", foreground="#ff8a80")
        self.log_text.tag_configure("warn", foreground="#ffd60a")
        self.log_text.pack(side="left", fill="both", expand=True,
                           padx=(4, 0), pady=(8, 10))
        scroll.pack(side="right", fill="y", pady=(8, 10), padx=(0, 4))

    def _layout_action_buttons(self, frame):
        """Keep the action bar usable when the window is resized narrower."""
        available = frame.winfo_width()
        if available <= 1:
            return
        one_row_width = sum(button.winfo_reqwidth() for button in self.action_buttons)
        one_row_width += 8 * len(self.action_buttons) + 12
        columns = len(self.action_buttons) if available >= one_row_width else 3
        if columns == self._action_columns:
            return
        self._action_columns = columns
        for button in self.action_buttons:
            button.grid_forget()
        for col in range(len(self.action_buttons)):
            frame.columnconfigure(col, weight=0, uniform="")
        for col in range(columns):
            frame.columnconfigure(col, weight=1, uniform="action")
        for index, button in enumerate(self.action_buttons):
            button.grid(row=index // columns, column=index % columns,
                        sticky="ew", padx=4, pady=3)

    def _startup_diagnostics(self):
        binary = profile_mod.default_rosenpass_binary()
        if not binary.is_file():
            self.log(f"rosenpass binary not found at {binary}")
            self.log("Build it with:")
            if runner.IS_WINDOWS:
                self.log("  (install Rust MSVC + Visual Studio Build Tools + cmake)")
                self.log("  cargo build --release")
            else:
                self.log("  brew install cmake")
                self.log("  cargo build --release -p rosenpass")
        else:
            self.log(f"Using rosenpass binary: {binary}")

    # ------------------------------------------------------------- logging

    def log(self, line: str, tag: str = "info"):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line.rstrip("\n") + "\n", tag)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # --------------------------------------------------------------- state

    def _set_state(self, state: str, detail: str = ""):
        self.state = state
        color = STATE_COLORS[state]
        self.dot.configure(fg=color)
        self.state_label.configure(text=state, bg=color)
        self.detail_label.configure(text=detail)
        self._update_buttons()

    def _update_buttons(self):
        p = self.profile
        has_profile = p is not None
        state = self.state
        self.buttons["Generate keys"].configure(
            state="normal" if state == IDLE else "disabled"
        )
        self.buttons["Start exchange"].configure(
            state="normal"
            if has_profile and state == IDLE and p.has_rosenpass_keys()
            and p.has_peer_rosenpass_key()
            else "disabled"
        )
        self.buttons["Stop"].configure(
            state="normal"
            if state in (EXCHANGING, WG_UP) or (
                state == KEYS_EXCHANGED and self.exchange and self.exchange.is_alive()
            )
            else "disabled"
        )
        self.buttons["Enable WireGuard"].configure(
            state="normal"
            if has_profile and state == KEYS_EXCHANGED and p.has_wg_keypair()
            and p.has_peer_wg_key()
            else "disabled"
        )
        self.buttons["Apply refreshed key"].configure(
            state="normal" if state == WG_UP else "disabled"
        )
        self.buttons["Disable WireGuard"].configure(
            state="normal" if state == WG_UP else "disabled"
        )

    # ------------------------------------------------------------- actions

    def _collect_fields(self):
        """Pull the current field values into the profile object."""
        name = profile_mod.sanitize_name(self.name_var.get())
        number = int(self.number_var.get())
        old = self.profile
        if old is None or old.name != name or old.dir != profile_mod.profile_dir(name):
            self.profile = profile_mod.Profile(name, number)
        else:
            self.profile = old
            self.profile.number_changed(number)
        p = self.profile
        p.endpoint_host = self.host_var.get().strip()
        p.endpoint_port = int(self.port_var.get())
        p.peer_name = self.peer_name_var.get().strip()
        p.peer_wg_ip = self.peer_ip_var.get().strip() or profile_mod.derive(number)["peer_wg_ip"]
        p.role = self.role_var.get()
        p.rosenpass_binary = str(profile_mod.default_rosenpass_binary())
        p.server_url = self.server_var.get().strip()
        return p

    def on_generate_keys(self):
        try:
            p = self._collect_fields()
            if p.has_rosenpass_keys() and not messagebox.askyesno(
                "Overwrite keys?",
                "This profile already has rosenpass keys. Generating new ones "
                "changes this machine's identity — the peer must re-import "
                "your public key. Continue?",
            ):
                return
            p.generate_rosenpass_keys()
        except Exception as err:  # noqa: BLE001 - surfaced to the user
            messagebox.showerror("Key generation failed", str(err))
            return
        p.save()  # persist before WireGuard keygen so a failure still keeps state
        try:
            wireguard.gen_keypair(p)
        except Exception as err:  # noqa: BLE001
            messagebox.showerror("WireGuard keygen failed", str(err))
            return
        self.load_box.configure(values=profile_mod.Profile.list_profiles())
        self.log(f"Generated keys in {p.dir}")
        self.log(f"Share {p.path('rosenpass.public-key').name} and "
                 f"{p.path('wg.pub').name} with your peer, and import theirs.")
        self._set_state(IDLE)
        self._update_buttons()

    def on_start_exchange(self):
        if self.profile is None:
            return
        try:
            p = self._collect_fields()
            cmd = p.exchange_cmd()
        except Exception as err:  # noqa: BLE001
            messagebox.showerror("Cannot start exchange", str(err))
            return
        if not p.has_rosenpass_keys() or not p.has_peer_rosenpass_key():
            messagebox.showerror(
                "Cannot start exchange",
                "Generate your keys and import the peer's rosenpass public key first.",
            )
            return
        role = "initiator" if p.is_initiator() else "responder"
        self.log(f"Starting exchange as {role}: {' '.join(cmd)}")
        self.exchange = ExchangeProcess(cmd)
        self.exchange.start()
        self._set_state(EXCHANGING, f"role={role}")
        self.root.after(POLL_MS, self._poll_exchange)

    def _poll_exchange(self):
        ex = self.exchange
        if ex is None:
            return
        for event in ex.drain():
            kind = event[0]
            if kind == "stderr":
                self.log(f"[stderr] {event[1]}", "err")
            elif kind == "stdout":
                tag = "ok" if "exchanged" in event[1] else "info"
                self.log(event[1], tag)
            elif kind == "output-key":
                _, psk_path, why = event
                self._on_output_key(psk_path, why)
            elif kind == "exited":
                if self.state == EXCHANGING:
                    tail = ex.stderr_tail().strip().splitlines()[-10:]
                    self.log("rosenpass process exited unexpectedly.")
                    for line in tail:
                        self.log(f"  {line}")
                    messagebox.showerror(
                        "rosenpass exited",
                        "The exchange process died. See the log for details.",
                    )
                    self.exchange = None
                    self._set_state(IDLE)  # reset so the user can retry
                else:
                    self.exchange = None
        if ex.is_alive():
            self.root.after(POLL_MS, self._poll_exchange)
        else:
            self.exchange = None

    def _on_output_key(self, psk_path, why: str):
        p = self.profile
        self.log(f"PSK {'refreshed' if why == 'stale' else 'produced'} "
                 f"({why}) → {psk_path}", "ok")
        try:
            psk = profile_mod.read_psk(psk_path)
        except ValueError as err:
            self.log(f"ERROR reading PSK: {err}", "err")
            return
        if p.path("psk.out") != psk_path:
            shutil.copy(psk_path, p.path("psk.out"))
        if self.state == EXCHANGING:
            self._set_state(KEYS_EXCHANGED, "PSK ready — enable WireGuard")
        else:
            self._set_state(self.state, f"last refresh: {why}")

    def on_stop(self):
        if self.exchange:
            self.log("Stopping rosenpass exchange…")
            self.exchange.stop()
            self.exchange = None
            if self.state == WG_UP:
                self.log(
                    "WARNING: the tunnel stays up with the last PSK, but "
                    "rosenpass will no longer refresh it. Disable WireGuard "
                    "when done.", "warn"
                )
        if self.state in (EXCHANGING, KEYS_EXCHANGED):
            self._set_state(IDLE)
        self._update_buttons()

    # ------------------------------------------------------------- key server

    def _ks_set_busy(self, busy: bool):
        self._ks_busy = busy
        self._ks_upload_btn.configure(state="disabled" if busy else "normal")
        self._ks_fetch_btn.configure(state="disabled" if busy else "normal")

    def _ks_worker(self, fn, ok_msg: str, on_ok=None):
        try:
            detail = fn()
            self._ks_queue.put(("ok", ok_msg, detail, on_ok))
        except Exception as err:  # noqa: BLE001 - surfaced to the user
            self._ks_queue.put(("err", str(err), "", None))

    def _poll_keyserver(self):
        if not self._ks_busy:
            return
        try:
            kind, msg, detail, on_ok = self._ks_queue.get_nowait()
        except queue.Empty:
            self.root.after(POLL_MS, self._poll_keyserver)
            return
        self._ks_set_busy(False)
        self._ks_job = None
        if kind == "ok":
            self.log(msg, "ok")
            if detail:
                self.log(detail) if isinstance(detail, str) else None
            if on_ok:
                on_ok(detail)
        else:
            self.log(f"key server error: {msg}", "err")
            messagebox.showerror("Key server", msg)

    def on_upload_keys(self):
        base = self.server_var.get().strip()
        if not base:
            messagebox.showerror("Key server", "Enter the deployed link first.")
            return
        p = self.profile
        if p is None or not p.has_rosenpass_keys() or not p.has_wg_keypair():
            messagebox.showerror("Key server", "Generate keys first.")
            return
        self._ks_set_busy(True)
        self._ks_job = self.root.after(POLL_MS, self._poll_keyserver)
        self.log(f"Uploading public keys to {base} (as '{p.name}')…")
        threading.Thread(
            target=self._ks_worker,
            daemon=True,
            args=(
                lambda: keyserver.upload_keys(p, base),
                f"Uploaded keys for '{p.name}' — peer can now fetch them.",
            ),
        ).start()

    def on_fetch_peer_keys(self):
        base = self.server_var.get().strip()
        if not base:
            messagebox.showerror("Key server", "Enter the deployed link first.")
            return
        p = self.profile
        if p is None:
            messagebox.showerror("No profile", "Generate keys first.")
            return
        peer = profile_mod.sanitize_name(self.peer_name_var.get())
        if not peer:
            messagebox.showerror("Key server", "Enter the peer name first (it is the name they uploaded with).")
            return
        self._ks_set_busy(True)
        self._ks_job = self.root.after(POLL_MS, self._poll_keyserver)
        self.log(f"Fetching keys for peer '{peer}' from {base}…")

        def do_fetch():
            rp_bytes, wg_text = keyserver.fetch_peer_keys(base, peer)
            p.path("peer.rosenpass.public-key").write_bytes(rp_bytes)
            p.path("peer.wg.pub").write_text(wg_text + "\n")
            preview = base64.b64encode(rp_bytes).decode()[:self.RP_KEY_PREVIEW_CHARS]
            return f"peer '{peer}' keys imported.", preview, wg_text

        def done(detail):
            _msg, preview, wg_text = detail
            self.peer_rp_var.set(preview + "…")
            self.peer_wg_var.set(wg_text)
            self._update_buttons()

        threading.Thread(
            target=self._ks_worker,
            daemon=True,
            args=(do_fetch, f"Peer '{peer}' keys imported.", done),
        ).start()

    # ------------------------------------------------------------- wireguard

    def on_enable_wg(self):
        p = self.profile
        missing = wireguard.require_tools()
        if missing:
            messagebox.showerror(
                "WireGuard tools missing",
                f"Missing: {', '.join(missing)}\n"
                f"{wireguard.install_hint()}",
            )
            return
        try:
            psk = profile_mod.read_psk(p.path("psk.out"))
            conf = wireguard.write_conf(p, psk)
        except Exception as err:  # noqa: BLE001
            messagebox.showerror("Cannot enable WireGuard", str(err))
            return
        self.log(f"Bringing up {conf} (administrator prompt)…")
        try:
            wireguard.wg_quick_up(conf)
        except wireguard.WireGuardError as err:
            self.log(f"wg-quick up failed: {err}")
            messagebox.showerror("wg-quick up failed", str(err))
            return
        iface = wireguard.resolve_iface(p)
        self.log(f"WireGuard up on interface {iface or '?'}")
        self._set_state(WG_UP, f"iface={iface or '?'}")
        self._poll_wg()

    def _poll_wg(self):
        if self.state != WG_UP:
            return
        try:
            status = wireguard.show()
        except wireguard.WireGuardError as err:
            self.log(f"wg show failed (will retry): {err}")
        else:
            if status != self._last_wg_status:
                iface = wireguard.resolve_iface(self.profile)
                self.log(f"[wg show — iface {iface or '?'}]\n{status}")
                self._last_wg_status = status
        self.wg_poll_job = self.root.after(WG_POLL_MS, self._poll_wg)

    def on_apply_refreshed_key(self):
        p = self.profile
        iface = wireguard.resolve_iface(p)
        if not iface:
            messagebox.showerror("WireGuard", "Tunnel is not up.")
            return
        self.log(f"Applying refreshed PSK to {iface}…")
        try:
            wireguard.apply_psk(p, iface)
        except wireguard.WireGuardError as err:
            self.log(f"wg set failed: {err}")
            messagebox.showerror("wg set failed", str(err))
            return
        self.log("PSK applied to live tunnel.")

    def on_disable_wg(self):
        p = self.profile
        try:
            wireguard.wg_quick_down(p.conf_path)
        except wireguard.WireGuardError as err:
            self.log(f"wg-quick down failed: {err}")
            messagebox.showerror("wg-quick down failed", str(err))
            return
        if self.wg_poll_job:
            self.root.after_cancel(self.wg_poll_job)
            self.wg_poll_job = None
        self.log("WireGuard tunnel down.")
        self._set_state(KEYS_EXCHANGED if p.has_psk() else IDLE)

    # --------------------------------------------------------- key importing

    RP_KEY_PREVIEW_CHARS = 48

    def _import_peer_rp_key(self):
        """Field holds base64 of the peer's whole raw public-key file."""
        if self.profile is None:
            messagebox.showerror("No profile", "Generate keys first.")
            return
        try:
            self.profile.import_peer_rosenpass_key_from_b64(self.peer_rp_var.get())
        except ValueError as err:
            messagebox.showerror("Invalid peer rosenpass key", str(err))
            return
        self.log("Imported peer rosenpass public key.")
        self._update_buttons()

    def _import_peer_wg_key(self):
        if self.profile is None:
            messagebox.showerror("No profile", "Generate keys first.")
            return
        try:
            key = profile_mod.validate_b64_32(self.peer_wg_var.get())
        except ValueError as err:
            messagebox.showerror("Invalid peer WireGuard key", str(err))
            return
        self.profile.path("peer.wg.pub").write_text(key + "\n")
        self.log("Imported peer WireGuard public key.")
        self._update_buttons()

    def _pick_peer_rp_key(self):
        src = filedialog.askopenfilename(title="Select peer rosenpass public key")
        if not src:
            return
        if self.profile is None:
            messagebox.showerror("No profile", "Generate keys first.")
            return
        try:
            self.profile.import_peer_rosenpass_key_from_file(src)
        except (OSError, ValueError) as err:
            messagebox.showerror("Import failed", str(err))
            return
        # show a preview — the raw file is a ~512 KB blob, too big to display
        self.peer_rp_var.set(
            self._rp_key_preview(src) + "…"
        )
        self.log(f"Imported peer rosenpass public key from {src}.")
        self._update_buttons()

    @staticmethod
    def _rp_key_preview(src) -> str:
        raw = Path(src).read_bytes()
        b64 = base64.b64encode(raw).decode()
        return b64[:App.RP_KEY_PREVIEW_CHARS]

    def _pick_peer_wg_key(self):
        src = filedialog.askopenfilename(title="Select peer WireGuard public key")
        if not src:
            return
        try:
            self.peer_wg_var.set(Path(src).read_text().strip())
        except OSError as err:
            messagebox.showerror("Import failed", str(err))
            return
        self._import_peer_wg_key()

    # ------------------------------------------------------------ profiles

    def _on_number_changed(self):
        try:
            n = int(self.number_var.get())
        except (tk.TclError, ValueError):
            return
        self.port_var.set(10000 + (2 if n == 1 else 1))
        self.peer_ip_var.set(profile_mod.derive(n)["peer_wg_ip"])

    def _on_load_clicked(self):
        name = self.load_var.get()
        if name:
            self._load_profile(name)

    def _load_profile(self, name: str):
        if self.exchange:
            # never orphan a running daemon by swapping profiles under it
            self.log("Stopping running exchange before loading profile…")
            self.on_stop()
        try:
            p = profile_mod.Profile.load(name)
        except Exception as err:  # noqa: BLE001
            messagebox.showerror("Load failed", str(err))
            return
        self.profile = p
        self.name_var.set(p.name)
        self.number_var.set(p.number)
        self.host_var.set(p.endpoint_host)
        self.port_var.set(p.endpoint_port)
        self.peer_name_var.set(p.peer_name)
        self.peer_ip_var.set(p.peer_wg_ip)
        self.role_var.set(p.role)
        self.server_var.set(p.server_url)
        self.log(f"Loaded profile '{p.name}' from {p.dir}")
        self._set_state(IDLE)
        self._update_buttons()

    # -------------------------------------------------------------- closing

    def _on_close(self):
        if self.exchange and self.exchange.is_alive():
            self.exchange.stop()
        if self.wg_poll_job:
            self.root.after_cancel(self.wg_poll_job)
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()
