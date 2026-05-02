"""
TOM-STT Desktop Recorder v1.1
recorder.py

Fungsi: Merekam audio sistem (Zoom, Meet, Teams, dll) via WASAPI Loopback,
        lalu menyimpan sebagai MP3 64kbps mono ke folder Downloads user.

Dependensi (install sebelum jalankan):
  pip install soundcard lameenc requests numpy

Tidak butuh ffmpeg, tidak butuh DLL tambahan — semua pure Python.

Build ke .exe (jalankan di CMD):
  pip install pyinstaller
  pyinstaller --onefile --windowed --name "TomSTT-Recorder" recorder.py

Hasil .exe ada di folder: dist/
"""

import tkinter as tk
from tkinter import filedialog, messagebox   # keduanya top-level — hindari hang
import threading
import time
import os
import datetime
import webbrowser
import urllib.parse

import lameenc
import numpy as np
import requests
import soundcard as sc


# =============================================================================
# KONFIGURASI
# =============================================================================

SERVER_URL    = "https://tom-stt.com"
SAMPLE_RATE   = 16000
CHANNELS      = 1
POLL_INTERVAL = 2
MP3_BITRATE   = "64k"

# ── Firebase Firestore REST (untuk auth token tanpa Streamlit server) ─────────
# Isi dengan nilai dari Firebase Console → Project Settings → General
# FIREBASE_PROJECT_ID  : Project ID (misal: "tomstt-abc12")
# FIREBASE_WEB_API_KEY : Web API Key (public key — aman disimpan di .exe)
FIREBASE_PROJECT_ID  = "tom-stt-db"
FIREBASE_WEB_API_KEY = "AIzaSyANtUuW_DIJuk2NjiAUZz_0wDx46907Uuk"

DEV_MOCK_AUTH = False   # ganti False untuk production


# =============================================================================
# WARNA
# =============================================================================

C_BG      = "#111827"
C_CARD    = "#1F2937"
C_BORDER  = "#374151"
C_ACCENT  = "#EF4444"
C_ACCENT2 = "#C0392B"
C_GREEN   = "#10B981"
C_YELLOW  = "#F59E0B"
C_WHITE   = "#F9FAFB"
C_GRAY    = "#9CA3AF"
C_DIM     = "#6B7280"
C_ORANGE  = "#F97316"


# =============================================================================
# HELPER: Token extraction
# =============================================================================

def extract_token(raw: str) -> str | None:
    s = raw.strip()
    if not s:
        return None
    if s.startswith("http"):
        parsed     = urllib.parse.urlparse(s)
        params     = urllib.parse.parse_qs(parsed.query)
        token_list = params.get("t", [])
        return token_list[0].strip() if token_list else None
    return s if len(s) >= 4 else None


# =============================================================================
# HELPER: Auth polling
# =============================================================================

def check_token(token: str) -> dict | None:
    """
    Validasi token ke Firestore REST API langsung — tanpa perlu Streamlit server.

    Production flow:
      1. app.py generate_recorder_token() simpan ke Firestore recorder_tokens/{TOKEN}
      2. .exe polling GET Firestore REST → parse field → return user info

    Firestore REST response contoh:
      {"fields": {"nama": {"stringValue": "Tommy"}, "status": {"stringValue": "active"}, ...}}
    """
    if DEV_MOCK_AUTH:
        time.sleep(0.5)
        return {"status": "active", "nama": "Dev User", "paket": "TESTING"}

    if not token or not FIREBASE_PROJECT_ID or not FIREBASE_WEB_API_KEY:
        return None

    try:
        url = (
            f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}"
            f"/databases/(default)/documents/recorder_tokens/{token.upper()}"
            f"?key={FIREBASE_WEB_API_KEY}"
        )
        r = requests.get(url, timeout=8)

        if r.status_code != 200:
            return None

        doc    = r.json()
        fields = doc.get("fields", {})

        # Cek status
        status = fields.get("status", {}).get("stringValue", "")
        if status != "active":
            return None

        # Cek expiry
        expires_str = fields.get("expires_at", {}).get("timestampValue", "")
        if expires_str:
            from datetime import datetime, timezone
            try:
                expires_at = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) > expires_at:
                    return None   # sudah expired
            except Exception:
                pass

        nama  = fields.get("nama",  {}).get("stringValue", "User")
        paket = fields.get("paket", {}).get("stringValue", "-")

        return {"status": "active", "nama": nama, "paket": paket}

    except Exception:
        return None


# =============================================================================
# HELPER: Save MP3
# =============================================================================

def save_as_mp3(audio_data: np.ndarray, output_path: str):
    if audio_data.ndim > 1:
        audio_data = audio_data[:, 0]
    audio_data = np.clip(audio_data, -1.0, 1.0)
    pcm_int16  = (audio_data * 32767).astype(np.int16)

    encoder = lameenc.Encoder()
    encoder.set_bit_rate(64)
    encoder.set_in_sample_rate(SAMPLE_RATE)
    encoder.set_channels(1)
    encoder.set_quality(2)

    mp3_bytes  = encoder.encode(pcm_int16.tobytes())
    mp3_bytes += encoder.flush()

    with open(output_path, "wb") as f:
        f.write(mp3_bytes)


# =============================================================================
# RECORDING ENGINE — dual source: WASAPI Loopback + Microphone
# =============================================================================

class RecordingEngine:
    """
    Rekam dua sumber audio secara paralel:
      - WASAPI Loopback : suara peserta lain (speaker output)
      - Microphone      : suara kamu sendiri (mic input)
    Keduanya di-mix menjadi satu array mono setelah stop().

    Mic failure bersifat NON-FATAL — jika mic tidak tersedia,
    rekaman tetap jalan dengan loopback saja.
    """

    # Bobot mix — mic sedikit lebih keras agar suara sendiri jelas
    LOOPBACK_VOL = 0.6
    MIC_VOL      = 0.75

    def __init__(self):
        self._lb_chunks  : list[np.ndarray]        = []
        self._mic_chunks : list[np.ndarray]        = []
        self._recording  : bool                    = False
        self._lb_thread  : threading.Thread | None = None
        self._mic_thread : threading.Thread | None = None
        self.last_error  : str                     = ""
        self.mic_active  : bool                    = False
        self.mic_muted   : bool                    = False   # ← toggle mute
        self.mic_name    : str                     = ""

    @property
    def is_recording(self) -> bool:
        return self._recording

    def toggle_mute(self):
        """Toggle mic mute — aman dipanggil dari main thread kapanpun."""
        self.mic_muted = not self.mic_muted

    def start(self):
        self._lb_chunks  = []
        self._mic_chunks = []
        self._recording  = True
        self.last_error  = ""
        self.mic_active  = False
        self.mic_muted   = True    # default MUTE — user aktifkan manual saat perlu bicara
        self.mic_name    = ""

        self._lb_thread = threading.Thread(
            target=self._loopback_loop, daemon=True, name="LoopbackLoop"
        )
        self._mic_thread = threading.Thread(
            target=self._mic_loop, daemon=True, name="MicLoop"
        )
        self._lb_thread.start()
        self._mic_thread.start()

    def stop(self) -> np.ndarray | None:
        self._recording = False
        if self._lb_thread:
            self._lb_thread.join(timeout=8)
        if self._mic_thread:
            self._mic_thread.join(timeout=4)   # mic timeout lebih pendek
        return self._mix()

    def _mix(self) -> np.ndarray | None:
        """
        Mix loopback + mic menjadi satu array mono.
        Jika salah satu tidak ada, pakai yang tersedia.
        Panjang disesuaikan ke yang lebih pendek agar sync.
        """
        lb  = np.concatenate(self._lb_chunks,  axis=0) if self._lb_chunks  else None
        mic = np.concatenate(self._mic_chunks, axis=0) if self._mic_chunks else None

        if lb is None and mic is None:
            return None
        if lb is None:
            return np.clip(mic * self.MIC_VOL, -1.0, 1.0)
        if mic is None:
            return np.clip(lb * self.LOOPBACK_VOL, -1.0, 1.0)

        # Trim ke panjang terpendek agar bisa dijumlah
        n   = min(len(lb), len(mic))
        lb  = lb[:n]
        mic = mic[:n]

        mixed = lb * self.LOOPBACK_VOL + mic * self.MIC_VOL
        return np.clip(mixed, -1.0, 1.0)

    def _loopback_loop(self):
        """Rekam WASAPI loopback — suara yang keluar dari speaker."""
        try:
            speaker    = sc.default_speaker()
            loopback   = sc.get_microphone(
                id=str(speaker.name), include_loopback=True
            )
            chunk_size = int(SAMPLE_RATE * 0.5)

            with loopback.recorder(samplerate=SAMPLE_RATE, channels=CHANNELS) as rec:
                while self._recording:
                    data = rec.record(numframes=chunk_size)
                    if data.ndim > 1:
                        data = data[:, 0]
                    self._lb_chunks.append(data.copy())

        except Exception as e:
            self._recording = False          # loopback gagal = fatal
            self.last_error = str(e)

    def _mic_loop(self):
        """
        Rekam microphone default — suara kamu sendiri.
        Failure di sini NON-FATAL: loopback tetap berjalan.
        Saat mic_muted=True, tulis silence (zeros) agar sync tetap terjaga.
        """
        try:
            mic        = sc.default_microphone()
            self.mic_name   = mic.name
            chunk_size = int(SAMPLE_RATE * 0.5)

            with mic.recorder(samplerate=SAMPLE_RATE, channels=CHANNELS) as rec:
                self.mic_active = True
                while self._recording:
                    data = rec.record(numframes=chunk_size)
                    if data.ndim > 1:
                        data = data[:, 0]
                    # Saat muted: simpan zeros agar panjang array tetap sync
                    if self.mic_muted:
                        data = np.zeros(len(data), dtype=np.float32)
                    self._mic_chunks.append(data.copy())

        except Exception:
            self.mic_active = False



# =============================================================================
# GUI — Modern Dark Design
# =============================================================================

# Palet warna refined
C_BG      = "#0F172A"
C_SURFACE = "#1E293B"
C_RAISED  = "#263348"
C_BORDER  = "#334155"
C_ACCENT  = "#EF4444"
C_ACCENT2 = "#DC2626"
C_GREEN   = "#22C55E"
C_YELLOW  = "#F59E0B"
C_WHITE   = "#F1F5F9"
C_GRAY    = "#94A3B8"
C_DIM     = "#64748B"
C_ORANGE  = "#F97316"


class TomSTTRecorder(tk.Tk):

    WIN_W = 440
    WIN_H = 400

    def __init__(self):
        super().__init__()
        self.title("TOM'STT AI Recorder")
        self.geometry(f"{self.WIN_W}x{self.WIN_H}")
        self.resizable(False, False)
        self.configure(bg=C_BG)
        self._center_window()

        self.token       = ""
        self.user_info   = {}
        self._timer_secs = 0
        self._timer_job  = None
        self._poll_job   = None
        self._saved_path = ""
        self._blink_on   = True
        self.save_folder = os.path.join(os.path.expanduser("~"), "Downloads")
        self.engine      = RecordingEngine()

        self.scr_login = self._build_login()
        self.scr_ready = self._build_ready()
        self.scr_rec   = self._build_recording()
        self.scr_done  = self._build_done()
        self._show(self.scr_login)

    # ── Utilities ─────────────────────────────────────────────────────────────

    def _center_window(self):
        self.update_idletasks()
        x = (self.winfo_screenwidth()  - self.WIN_W) // 2
        y = (self.winfo_screenheight() - self.WIN_H) // 2
        self.geometry(f"{self.WIN_W}x{self.WIN_H}+{x}+{y}")

    def _show(self, screen: tk.Frame):
        for s in [self.scr_login, self.scr_ready, self.scr_rec, self.scr_done]:
            s.place_forget()
        screen.place(x=0, y=0, width=self.WIN_W, height=self.WIN_H)

    # ── Widget factory ────────────────────────────────────────────────────────

    def _lbl(self, parent, text="", fg=C_WHITE, font=("Segoe UI", 10), **kw):
        return tk.Label(parent, text=text, fg=fg, bg=parent["bg"], font=font, **kw)

    def _btn(self, parent, text, cmd, bg=C_ACCENT, fg=C_WHITE,
             font=("Segoe UI", 10, "bold"), width=20, pady=10):
        return tk.Button(
            parent, text=text, command=cmd,
            bg=bg, fg=fg, font=font,
            relief="flat", cursor="hand2",
            activebackground=C_ACCENT2, activeforeground=fg,
            width=width, pady=pady, bd=0
        )

    def _divider(self, parent):
        tk.Frame(parent, bg=C_BORDER, height=1).pack(fill="x")

    def _topbar(self, parent) -> tk.Frame:
        bar = tk.Frame(parent, bg=C_SURFACE, padx=20, pady=12)
        bar.pack(fill="x", side="top")
        self._divider(parent)
        brand = tk.Frame(bar, bg=C_SURFACE)
        brand.pack(side="left")
        tk.Label(brand, text="TOM'STT AI", fg=C_ACCENT, bg=C_SURFACE,
                 font=("Segoe UI", 11, "bold")).pack(side="left")
        tk.Label(brand, text=" Recorder", fg=C_DIM, bg=C_SURFACE,
                 font=("Segoe UI", 11)).pack(side="left")
        return bar

    # =========================================================================
    # SCREEN 1 — LOGIN
    # =========================================================================

    def _build_login(self) -> tk.Frame:
        f = tk.Frame(self, bg=C_BG)
        bar = self._topbar(f)

        body = tk.Frame(f, bg=C_BG)
        body.pack(expand=True, fill="both", padx=32)

        self._lbl(body, "Masukkan Link QR Code dari website",
                  fg=C_DIM, font=("Segoe UI", 9)).pack(anchor="w", pady=(18, 8))

        entry_outer = tk.Frame(body, bg=C_BORDER, padx=1, pady=1)
        entry_outer.pack(fill="x")
        entry_inner = tk.Frame(entry_outer, bg=C_SURFACE, padx=12)
        entry_inner.pack(fill="x")
        self.entry_token = tk.Entry(
            entry_inner, font=("Consolas", 10),
            bg=C_SURFACE, fg=C_WHITE,
            insertbackground=C_ACCENT,
            relief="flat", bd=0
        )
        self.entry_token.pack(fill="x", ipady=10)
        self.entry_token.bind("<Return>", lambda e: self._on_login())
        self.entry_token.focus_set()

        self.lbl_login_status = self._lbl(
            body, "Salin link dari halaman Desktop Recorder di tom-stt.com",
            fg=C_DIM, font=("Segoe UI", 8), wraplength=370, justify="left"
        )
        self.lbl_login_status.pack(anchor="w", pady=(8, 20))

        btn_row = tk.Frame(body, bg=C_BG)
        btn_row.pack(anchor="center")
        self._btn(btn_row, "  🔑   LOGIN  ", self._on_login,
                  width=14, pady=10).pack(side="left", padx=(0, 10))
        self._btn(btn_row, "  🌐   Buka Website  ", self._open_website,
                  bg=C_SURFACE, fg=C_GRAY, width=18, pady=10).pack(side="left")

        self._divider(f)
        tk.Label(f, text="tom-stt.com  ·  AI Speech-to-Text Platform",
                 fg=C_DIM, bg=C_BG, font=("Segoe UI", 8)).pack(pady=8)
        return f

    def _open_website(self):
        webbrowser.open(f"{SERVER_URL}/?tab=recorder")

    def _on_login(self):
        token = extract_token(self.entry_token.get())
        if not token:
            self.lbl_login_status.config(
                text="⚠  Link atau token tidak valid. Salin ulang dari website.",
                fg=C_YELLOW
            )
            return
        self.token = token
        self.lbl_login_status.config(text="⏳  Memverifikasi token...", fg=C_DIM)
        threading.Thread(target=self._poll_once, daemon=True).start()

    def _poll_once(self):
        result = check_token(self.token)
        if result:
            self.after(0, lambda: self._on_auth_success(result))
        else:
            self.after(0, lambda: self.lbl_login_status.config(
                text="⏳  Menunggu konfirmasi dari website...\n"
                     "Pastikan link sudah dibuka di browser dan Anda sudah login.",
                fg=C_DIM
            ))
            self._poll_job = self.after(
                POLL_INTERVAL * 1000,
                lambda: threading.Thread(target=self._poll_loop, daemon=True).start()
            )

    def _poll_loop(self):
        result = check_token(self.token)
        if result:
            self.after(0, lambda: self._on_auth_success(result))
        else:
            self._poll_job = self.after(
                POLL_INTERVAL * 1000,
                lambda: threading.Thread(target=self._poll_loop, daemon=True).start()
            )

    def _on_auth_success(self, data: dict):
        if self._poll_job:
            self.after_cancel(self._poll_job)
            self._poll_job = None
        self.user_info = data
        nama  = data.get("nama", "User").upper()
        paket = data.get("paket", "-")
        # Update label di kedua screen (masing-masing punya instance sendiri)
        self.lbl_ready_nama.config(text=f"✔  {nama}")
        self.lbl_ready_paket.config(text=f"Paket: {paket}")
        self.lbl_rec_nama.config(text=f"✔  {nama}")
        self.lbl_rec_paket.config(text=f"Paket: {paket}")
        self._update_folder_label()
        self._show(self.scr_ready)

    # =========================================================================
    # SCREEN 2 — READY
    # =========================================================================

    def _build_ready(self) -> tk.Frame:
        f = tk.Frame(self, bg=C_BG)

        # Topbar: 3 baris — brand | nama user | paket
        bar = tk.Frame(f, bg=C_SURFACE, padx=20, pady=10)
        bar.pack(fill="x", side="top")
        self._divider(f)
        row1 = tk.Frame(bar, bg=C_SURFACE)
        row1.pack(anchor="w")
        tk.Label(row1, text="TOM'STT AI", fg=C_ACCENT, bg=C_SURFACE,
                 font=("Segoe UI", 11, "bold")).pack(side="left")
        tk.Label(row1, text=" Recorder", fg=C_DIM, bg=C_SURFACE,
                 font=("Segoe UI", 11)).pack(side="left")
        self.lbl_ready_nama = tk.Label(bar, text="", fg=C_GREEN, bg=C_SURFACE,
                                        font=("Segoe UI", 9, "bold"))
        self.lbl_ready_nama.pack(anchor="w")
        self.lbl_ready_paket = tk.Label(bar, text="", fg=C_DIM, bg=C_SURFACE,
                                         font=("Segoe UI", 8))
        self.lbl_ready_paket.pack(anchor="w")

        center = tk.Frame(f, bg=C_BG)
        center.pack(expand=True)
        self._lbl(center, "00 : 00 : 00", fg=C_RAISED,
                  font=("Segoe UI", 40, "bold")).pack(pady=(0, 24))
        self._btn(center, "  ▶    MULAI REKAM  ", self._on_start,
                  width=24, pady=12).pack()

        self._divider(f)
        folder_bar = tk.Frame(f, bg=C_SURFACE, padx=16, pady=9)
        folder_bar.pack(fill="x", side="bottom")
        self.lbl_ready_folder = tk.Label(
            folder_bar, text="", fg=C_GRAY, bg=C_SURFACE,
            font=("Segoe UI", 8), anchor="w"
        )
        self.lbl_ready_folder.pack(side="left", fill="x", expand=True)
        tk.Button(
            folder_bar, text="📁  Ganti Folder",
            command=self._pick_folder,
            bg=C_RAISED, fg=C_GRAY,
            font=("Segoe UI", 8, "bold"),
            relief="flat", cursor="hand2",
            activebackground=C_BORDER,
            padx=12, pady=4, bd=0
        ).pack(side="right")
        return f

    def _update_folder_label(self):
        path  = self.save_folder
        parts = path.replace("\\", "/").split("/")
        short = (parts[0] + " / … / " + " / ".join(parts[-2:])) if len(parts) > 3 else path
        self.lbl_ready_folder.config(text=f"💾  {short}")

    def _pick_folder(self):
        import subprocess
        init = self.save_folder.replace("\\", "\\\\")
        ps_script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
            "$d.Description = 'Pilih folder penyimpanan rekaman'; "
            f"$d.SelectedPath = '{init}'; "
            "$d.ShowNewFolderButton = $true; "
            "if ($d.ShowDialog() -eq 'OK') { Write-Output $d.SelectedPath }"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                capture_output=True, text=True, timeout=120,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            chosen = result.stdout.strip()
            if chosen and os.path.isdir(chosen):
                self.save_folder = chosen
                self._update_folder_label()
        except Exception:
            pass

    def _on_start(self):
        self._timer_secs = 0
        self.engine.start()
        self.after(800, self._check_engine_started)

    def _check_engine_started(self):
        if not self.engine.is_recording and self.engine.last_error:
            self._show(self.scr_ready)
            messagebox.showerror(
                "Gagal Merekam",
                f"Tidak dapat mengakses audio sistem:\n\n{self.engine.last_error}\n\n"
                "Pastikan speaker tidak mute dan audio sedang aktif."
            )
            return
        self.after(600, self._update_mic_status)
        self._show(self.scr_rec)
        self._tick()

    # =========================================================================
    # SCREEN 3 — RECORDING
    # =========================================================================

    def _build_recording(self) -> tk.Frame:
        f = tk.Frame(self, bg=C_BG)

        # Topbar: 3 baris — brand | nama user | paket
        bar = tk.Frame(f, bg=C_SURFACE, padx=20, pady=10)
        bar.pack(fill="x", side="top")
        self._divider(f)
        row1 = tk.Frame(bar, bg=C_SURFACE)
        row1.pack(anchor="w")
        tk.Label(row1, text="TOM'STT AI", fg=C_ACCENT, bg=C_SURFACE,
                 font=("Segoe UI", 11, "bold")).pack(side="left")
        tk.Label(row1, text=" Recorder", fg=C_DIM, bg=C_SURFACE,
                 font=("Segoe UI", 11)).pack(side="left")
        self.lbl_rec_nama = tk.Label(bar, text="", fg=C_GREEN, bg=C_SURFACE,
                                      font=("Segoe UI", 9, "bold"))
        self.lbl_rec_nama.pack(anchor="w")
        self.lbl_rec_paket = tk.Label(bar, text="", fg=C_DIM, bg=C_SURFACE,
                                       font=("Segoe UI", 8))
        self.lbl_rec_paket.pack(anchor="w")

        center = tk.Frame(f, bg=C_BG)
        center.pack(expand=True)

        # REC badge
        badge = tk.Frame(center, bg=C_BG)
        badge.pack(pady=(0, 6))
        self.lbl_dot = tk.Label(badge, text="●", fg=C_ACCENT, bg=C_BG,
                                 font=("Segoe UI", 13))
        self.lbl_dot.pack(side="left", padx=(0, 8))
        tk.Label(badge, text="MEREKAM", fg=C_ACCENT, bg=C_BG,
                 font=("Segoe UI", 9, "bold")).pack(side="left")

        # Timer
        self.lbl_timer = tk.Label(center, text="00 : 00 : 00",
                                   fg=C_WHITE, bg=C_BG,
                                   font=("Segoe UI", 38, "bold"))
        self.lbl_timer.pack(pady=(2, 16))

        # Stop button
        self._btn(center, "  ■    STOP & SIMPAN  ", self._on_stop,
                  bg=C_ORANGE, width=24, pady=11).pack()

        # Status bar — 3 baris
        self._divider(f)
        status_bar = tk.Frame(f, bg=C_SURFACE, padx=16, pady=10)
        status_bar.pack(fill="x", side="bottom")

        # Baris 1: toggle mute + nama mic
        mic_row = tk.Frame(status_bar, bg=C_SURFACE)
        mic_row.pack(fill="x", pady=(0, 4))
        self.btn_mute = tk.Button(
            mic_row,
            text="🔇  Mic Anda: MUTE",
            command=self._toggle_mic,
            bg=C_RAISED, fg=C_YELLOW,
            font=("Segoe UI", 8, "bold"),
            relief="flat", cursor="hand2",
            activebackground=C_BORDER,
            padx=10, pady=3, bd=0,
            state="disabled"
        )
        self.btn_mute.pack(side="left", padx=(0, 10))
        self.lbl_mic_name = tk.Label(
            mic_row, text="Mendeteksi mic...",
            fg=C_DIM, bg=C_SURFACE,
            font=("Segoe UI", 8), anchor="w"
        )
        self.lbl_mic_name.pack(side="left", fill="x", expand=True)

        # Baris 2: hint
        tk.Label(
            status_bar,
            text="💡  Klik tombol mic di atas saat ingin suara Anda ikut terekam",
            fg=C_DIM, bg=C_SURFACE,
            font=("Segoe UI", 8), anchor="w"
        ).pack(fill="x", pady=(0, 3))

        # Baris 3: warning
        tk.Label(
            status_bar,
            text="⚠   Jangan tutup aplikasi ini saat merekam",
            fg=C_YELLOW, bg=C_SURFACE,
            font=("Segoe UI", 8), anchor="w"
        ).pack(fill="x")

        return f

    def _update_mic_status(self):
        if self.engine.mic_active:
            name  = self.engine.mic_name
            short = (name[:42] + "…") if len(name) > 42 else name
            self.lbl_mic_name.config(text=short, fg=C_DIM)
            self.btn_mute.config(
                text="🔇  Mic Anda: MUTE",
                bg=C_RAISED, fg=C_YELLOW,
                activebackground=C_BORDER,
                state="normal"
            )
        else:
            self.lbl_mic_name.config(text="Mic tidak terdeteksi", fg=C_YELLOW)
            self.btn_mute.config(
                text="🔇  Mic: N/A",
                bg=C_RAISED, fg=C_DIM,
                state="disabled"
            )

    def _toggle_mic(self):
        self.engine.toggle_mute()
        if self.engine.mic_muted:
            self.btn_mute.config(text="🔇  Mic Anda: MUTE",
                                  bg=C_RAISED, fg=C_YELLOW,
                                  activebackground=C_BORDER)
            self.lbl_mic_name.config(fg=C_DIM)
        else:
            self.btn_mute.config(text="🎙  Mic Anda: ON",
                                  bg=C_GREEN, fg=C_BG,
                                  activebackground="#16A34A")
            self.lbl_mic_name.config(fg=C_GRAY)

    def _tick(self):
        self._timer_secs += 1
        h = self._timer_secs // 3600
        m = (self._timer_secs % 3600) // 60
        s = self._timer_secs % 60
        self.lbl_timer.config(text=f"{h:02d} : {m:02d} : {s:02d}")
        self._blink_on = not self._blink_on
        self.lbl_dot.config(fg=C_ACCENT if self._blink_on else C_BG)
        self._timer_job = self.after(1000, self._tick)

    def _on_stop(self):
        if self._timer_job:
            self.after_cancel(self._timer_job)
            self._timer_job = None
        duration = self._timer_secs
        self.lbl_done_status.config(
            text="⏳  Mengonversi ke MP3, mohon tunggu...", fg=C_YELLOW
        )
        self.lbl_done_file.config(text="")
        self.lbl_done_meta.config(text="")
        self.btn_done_folder.config(state="disabled")
        self.btn_done_again.config(state="disabled")
        self._show(self.scr_done)
        threading.Thread(target=self._process, args=(duration,), daemon=True).start()

    def _process(self, duration: int):
        audio = self.engine.stop()
        if audio is None or len(audio) == 0:
            err = self.engine.last_error or "Tidak ada data audio yang terekam."
            self.after(0, lambda: self.lbl_done_status.config(
                text=f"❌  {err}", fg=C_ACCENT
            ))
            return
        ts       = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
        filename = f"Rekaman_TOMSTT_{ts}.mp3"
        out_path = os.path.join(self.save_folder, filename)
        try:
            save_as_mp3(audio, out_path)
            size_mb = os.path.getsize(out_path) / (1024 * 1024)
            h = duration // 3600
            m = (duration % 3600) // 60
            s = duration % 60
            self.after(0, lambda: self._on_save_ok(
                filename, f"{h:02d}:{m:02d}:{s:02d}", size_mb, out_path
            ))
        except Exception as e:
            err = str(e)
            self.after(0, lambda: self.lbl_done_status.config(
                text=f"❌  Gagal menyimpan: {err[:80]}", fg=C_ACCENT
            ))

    def _on_save_ok(self, filename, dur, size_mb, path):
        self._saved_path = path
        self.lbl_done_status.config(text="✔  File berhasil disimpan!", fg=C_GREEN)
        self.lbl_done_file.config(text=f"📄  {filename}")
        self.lbl_done_meta.config(text=f"Durasi: {dur}   ·   Ukuran: {size_mb:.1f} MB")
        self.btn_done_folder.config(state="normal")
        self.btn_done_again.config(state="normal")

    # =========================================================================
    # SCREEN 4 — DONE
    # =========================================================================

    def _build_done(self) -> tk.Frame:
        f = tk.Frame(self, bg=C_BG)
        self._topbar(f)

        center = tk.Frame(f, bg=C_BG)
        center.pack(expand=True)

        self.lbl_done_status = tk.Label(
            center, text="✔  File berhasil disimpan!",
            fg=C_GREEN, bg=C_BG,
            font=("Segoe UI", 14, "bold")
        )
        self.lbl_done_status.pack(pady=(0, 16))

        card = tk.Frame(center, bg=C_SURFACE, padx=28, pady=16)
        card.pack()
        tk.Frame(card, bg=C_ACCENT, height=2, width=60).pack(pady=(0, 12))
        self.lbl_done_file = tk.Label(card, text="📄  ...", fg=C_WHITE, bg=C_SURFACE,
                                       font=("Segoe UI", 10, "bold"))
        self.lbl_done_file.pack()
        self.lbl_done_meta = tk.Label(card, text="", fg=C_DIM, bg=C_SURFACE,
                                       font=("Segoe UI", 9))
        self.lbl_done_meta.pack(pady=(6, 0))

        btn_row = tk.Frame(center, bg=C_BG)
        btn_row.pack(pady=(20, 0))
        self.btn_done_folder = self._btn(
            btn_row, "  📂  Buka Folder  ", self._open_folder,
            bg=C_SURFACE, fg=C_GRAY, width=16, pady=9
        )
        self.btn_done_folder.pack(side="left", padx=(0, 10))
        self.btn_done_again = self._btn(
            btn_row, "  🔴  Rekam Lagi  ", self._rekam_lagi, width=14, pady=9
        )
        self.btn_done_again.pack(side="left")

        self._divider(f)
        tk.Label(
            f, text="💡  Upload file ini ke tom-stt.com untuk mendapatkan transkripnya",
            fg=C_DIM, bg=C_BG, font=("Segoe UI", 8)
        ).pack(pady=8)
        return f

    def _open_folder(self):
        os.startfile(os.path.dirname(self._saved_path))

    def _rekam_lagi(self):
        self.token     = ""
        self.user_info = {}
        self.entry_token.delete(0, tk.END)
        self.lbl_login_status.config(
            text="💡  Token bersifat sekali pakai.\n"
                 "Ambil QR Code baru dari tom-stt.com untuk merekam lagi.",
            fg=C_YELLOW
        )
        self._show(self.scr_login)


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    app = TomSTTRecorder()
    app.mainloop()