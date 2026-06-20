#!/usr/bin/env python3
# HyperSpace AGI — Installer GUI
# Richiede: pip install customtkinter requests
# Avvio:    python hyperspace-installer.pyw
# Build exe: pyinstaller --onefile --windowed --name HyperSpaceInstaller hyperspace-installer.pyw

import customtkinter as ctk
import threading, subprocess, os, sys, json, shutil, webbrowser, time
import tkinter as tk
from tkinter import filedialog, messagebox
try:
    import requests
except ImportError:
    requests = None

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

APP_TITLE  = "HyperSpace AGI — Installer"
REPO_URL   = "https://github.com/opodark/hyperspace-agi-1.02"
REPO_CLONE = "https://github.com/opodark/hyperspace-agi-1.02.git"
DASH_URL   = "http://localhost:8085"

COLOR_BG      = "#0f0f0f"
COLOR_SURFACE = "#1a1a1a"
COLOR_CARD    = "#222222"
COLOR_PRIMARY = "#00b4c8"
COLOR_SUCCESS = "#22c55e"
COLOR_WARN    = "#f59e0b"
COLOR_ERROR   = "#ef4444"
COLOR_TEXT    = "#e5e5e5"
COLOR_MUTED   = "#888888"

# ─── utility ────────────────────────────────────────────────────────────────

def run_cmd(cmd, cwd=None):
    """Esegui comando shell, ritorna (returncode, stdout+stderr)."""
    result = subprocess.run(
        cmd, shell=True, cwd=cwd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    return result.returncode, result.stdout

def check_tool(name):
    return shutil.which(name) is not None

def probe_ollama(url):
    if requests is None: return []
    try:
        r = requests.get(f"{url}/api/tags", timeout=3)
        if r.status_code == 200:
            return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        pass
    return []

def probe_lmstudio(url):
    if requests is None: return []
    try:
        r = requests.get(f"{url}/v1/models", timeout=3)
        if r.status_code == 200:
            return [m["id"] for m in r.json().get("data", [])]
    except Exception:
        pass
    return []


# ─── main app ───────────────────────────────────────────────────────────────

class InstallerApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("720x560")
        self.minsize(680, 500)
        self.resizable(True, True)
        self.configure(fg_color=COLOR_BG)

        # state
        self.step        = 0
        self.backend_var = ctk.StringVar(value="ollama")
        self.model_var   = ctk.StringVar(value="")
        self.install_dir = ctk.StringVar(value=os.path.join(os.path.expanduser("~"), "hyperspace-agi"))
        self.ollama_port = ctk.StringVar(value="11434")
        self.lms_port    = ctk.StringVar(value="1234")
        self.prereq      = {}   # {name: bool}

        self._build_layout()
        self._show_step(0)

    # ── layout shell ────────────────────────────────────────────────────────
    def _build_layout(self):
        # Header
        hdr = ctk.CTkFrame(self, fg_color=COLOR_SURFACE, corner_radius=0, height=64)
        hdr.pack(fill="x", side="top")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="⬡  HyperSpace AGI",
                     font=ctk.CTkFont(family="Consolas", size=20, weight="bold"),
                     text_color=COLOR_PRIMARY).pack(side="left", padx=24, pady=16)
        self.step_label = ctk.CTkLabel(hdr, text="Step 1 / 6",
                     font=ctk.CTkFont(size=12), text_color=COLOR_MUTED)
        self.step_label.pack(side="right", padx=24)

        # Progress bar
        self.progress = ctk.CTkProgressBar(self, height=3,
                         progress_color=COLOR_PRIMARY, fg_color="#2a2a2a")
        self.progress.pack(fill="x", side="top")
        self.progress.set(0)

        # Body (scrollable)
        self.body = ctk.CTkScrollableFrame(self, fg_color=COLOR_BG, corner_radius=0)
        self.body.pack(fill="both", expand=True, padx=0, pady=0)

        # Footer nav
        ftr = ctk.CTkFrame(self, fg_color=COLOR_SURFACE, corner_radius=0, height=60)
        ftr.pack(fill="x", side="bottom")
        ftr.pack_propagate(False)
        self.btn_back = ctk.CTkButton(ftr, text="← Indietro", width=120,
                         fg_color="transparent", border_color=COLOR_MUTED,
                         border_width=1, text_color=COLOR_MUTED,
                         hover_color="#2a2a2a", command=self._go_back)
        self.btn_back.pack(side="left", padx=20, pady=12)
        self.btn_next = ctk.CTkButton(ftr, text="Avanti →", width=140,
                         fg_color=COLOR_PRIMARY, hover_color="#009ab0",
                         text_color="#000", font=ctk.CTkFont(weight="bold"),
                         command=self._go_next)
        self.btn_next.pack(side="right", padx=20, pady=12)

    # ── navigation ──────────────────────────────────────────────────────────
    def _clear_body(self):
        for w in self.body.winfo_children():
            w.destroy()

    def _show_step(self, n):
        self.step = n
        self._clear_body()
        self.step_label.configure(text=f"Step {n+1} / 6")
        self.progress.set((n) / 5)
        steps = [
            self._step_welcome,
            self._step_backend,
            self._step_model,
            self._step_folder,
            self._step_install,
            self._step_done,
        ]
        steps[n]()
        self.btn_back.configure(state="normal" if n > 0 else "disabled")
        self.btn_next.configure(state="normal")
        if n == 4:   # install
            self.btn_next.configure(state="disabled")
        if n == 5:   # done
            self.btn_back.configure(state="disabled")
            self.btn_next.configure(text="Apri Dashboard", fg_color=COLOR_SUCCESS,
                                     hover_color="#16a34a", text_color="#000",
                                     command=lambda: webbrowser.open(DASH_URL))

    def _go_next(self):
        if self.step < 5:
            self._show_step(self.step + 1)

    def _go_back(self):
        if self.step > 0:
            self._show_step(self.step - 1)

    # ── card helper ─────────────────────────────────────────────────────────
    def _card(self, parent, title=None, pady=(0,12)):
        f = ctk.CTkFrame(parent, fg_color=COLOR_CARD, corner_radius=10)
        f.pack(fill="x", padx=24, pady=pady)
        if title:
            ctk.CTkLabel(f, text=title,
                         font=ctk.CTkFont(size=11, weight="bold"),
                         text_color=COLOR_MUTED).pack(anchor="w", padx=16, pady=(12,4))
        return f

    def _badge(self, parent, text, ok):
        color = COLOR_SUCCESS if ok else COLOR_ERROR
        icon  = "✅" if ok else "❌"
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=3)
        ctk.CTkLabel(row, text=icon, width=24, font=ctk.CTkFont(size=14)).pack(side="left")
        ctk.CTkLabel(row, text=text, font=ctk.CTkFont(size=13),
                     text_color=color if ok else COLOR_MUTED).pack(side="left", padx=6)

    # ════════════════════════════════════════════════════════════════════════
    # STEP 0 — BENVENUTO + CHECK PREREQUISITI
    # ════════════════════════════════════════════════════════════════════════
    def _step_welcome(self):
        p = self.body
        ctk.CTkLabel(p, text="Benvenuto in HyperSpace AGI",
                     font=ctk.CTkFont(size=22, weight="bold"),
                     text_color=COLOR_TEXT).pack(anchor="w", padx=24, pady=(28,4))
        ctk.CTkLabel(p, text="Questo wizard installerà e avvierà HyperSpace AGI sul tuo computer.\n"
                             "Verifica che i prerequisiti siano soddisfatti prima di continuare.",
                     font=ctk.CTkFont(size=13), text_color=COLOR_MUTED,
                     wraplength=620, justify="left").pack(anchor="w", padx=24, pady=(0,20))

        card = self._card(p, "Prerequisiti rilevati")
        checks = [
            ("docker",         "Docker Desktop"),
            ("git",            "Git"),
            ("docker compose", "Docker Compose"),
        ]
        for cmd, label in checks:
            ok = check_tool(cmd.split()[0])
            self.prereq[cmd] = ok
            self._badge(card, label, ok)

        # Docker compose v2 check
        code, _ = run_cmd("docker compose version")
        compose_ok = (code == 0)
        self.prereq["compose"] = compose_ok
        self._badge(card, "docker compose v2", compose_ok)
        ctk.CTkLabel(card, text="", height=8).pack()

        all_ok = all([self.prereq.get("docker"), compose_ok])
        if not all_ok:
            warn = self._card(p, pady=(0,16))
            ctk.CTkLabel(warn, text="⚠️  Docker Desktop non trovato o non avviato.\n"
                                    "Scaricalo da https://www.docker.com/products/docker-desktop e riavvia l'installer.",
                         font=ctk.CTkFont(size=12), text_color=COLOR_WARN,
                         wraplength=600, justify="left").pack(padx=16, pady=12)

    # ════════════════════════════════════════════════════════════════════════
    # STEP 1 — BACKEND
    # ════════════════════════════════════════════════════════════════════════
    def _step_backend(self):
        p = self.body
        ctk.CTkLabel(p, text="Scegli il backend di inferenza",
                     font=ctk.CTkFont(size=20, weight="bold"),
                     text_color=COLOR_TEXT).pack(anchor="w", padx=24, pady=(28,4))
        ctk.CTkLabel(p, text="HyperSpace funziona con Ollama o LM Studio.\n"
                             "Assicurati che il backend scelto sia in esecuzione prima di procedere.",
                     font=ctk.CTkFont(size=13), text_color=COLOR_MUTED,
                     wraplength=620, justify="left").pack(anchor="w", padx=24, pady=(0,20))

        for val, label, desc, default_port, port_var in [
            ("ollama",   "🦙  Ollama",    "Porta default: 11434", "11434", self.ollama_port),
            ("lmstudio", "🧪  LM Studio", "Porta default: 1234",  "1234",  self.lms_port),
        ]:
            card = self._card(p)
            row  = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=(10,4))
            ctk.CTkRadioButton(row, text=label, variable=self.backend_var, value=val,
                               font=ctk.CTkFont(size=15, weight="bold"),
                               radiobutton_width=18, radiobutton_height=18,
                               fg_color=COLOR_PRIMARY).pack(side="left")
            sub = ctk.CTkFrame(card, fg_color="transparent")
            sub.pack(fill="x", padx=44, pady=(0,12))
            ctk.CTkLabel(sub, text=desc, font=ctk.CTkFont(size=12),
                         text_color=COLOR_MUTED).pack(side="left")
            ctk.CTkLabel(sub, text="Porta:", font=ctk.CTkFont(size=12),
                         text_color=COLOR_MUTED).pack(side="left", padx=(16,4))
            ctk.CTkEntry(sub, textvariable=port_var, width=70,
                         font=ctk.CTkFont(family="Consolas", size=12)).pack(side="left")

        # test connessione
        test_card = self._card(p, pady=(4,16))
        self.conn_label = ctk.CTkLabel(test_card, text="Premi 'Testa connessione' per verificare",
                          font=ctk.CTkFont(size=12), text_color=COLOR_MUTED)
        self.conn_label.pack(padx=16, pady=(10,4), anchor="w")
        ctk.CTkButton(test_card, text="🔌  Testa connessione", width=200,
                      fg_color="transparent", border_color=COLOR_PRIMARY, border_width=1,
                      text_color=COLOR_PRIMARY, hover_color="#1a2a2a",
                      command=self._test_connection).pack(padx=16, pady=(0,12), anchor="w")

    def _test_connection(self):
        backend = self.backend_var.get()
        if backend == "ollama":
            url    = f"http://localhost:{self.ollama_port.get()}"
            models = probe_ollama(url)
        else:
            url    = f"http://localhost:{self.lms_port.get()}"
            models = probe_lmstudio(url)
        if models:
            self.conn_label.configure(
                text=f"✅  Connesso! {len(models)} modell{'o' if len(models)==1 else 'i'} trovat{'o' if len(models)==1 else 'i'}: {', '.join(models[:3])}{'...' if len(models)>3 else ''}",
                text_color=COLOR_SUCCESS)
        else:
            self.conn_label.configure(
                text=f"❌  Nessuna risposta da {url} — avvia {backend} e riprova.",
                text_color=COLOR_ERROR)

    # ════════════════════════════════════════════════════════════════════════
    # STEP 2 — MODELLO
    # ════════════════════════════════════════════════════════════════════════
    def _step_model(self):
        p = self.body
        ctk.CTkLabel(p, text="Seleziona il modello",
                     font=ctk.CTkFont(size=20, weight="bold"),
                     text_color=COLOR_TEXT).pack(anchor="w", padx=24, pady=(28,4))
        ctk.CTkLabel(p, text="Scegli il modello da usare come default per i task.",
                     font=ctk.CTkFont(size=13), text_color=COLOR_MUTED,
                     wraplength=620).pack(anchor="w", padx=24, pady=(0,20))

        card = self._card(p)
        self.model_combo = ctk.CTkComboBox(card, variable=self.model_var,
                           values=["Caricamento..."], width=480,
                           font=ctk.CTkFont(family="Consolas", size=13),
                           dropdown_font=ctk.CTkFont(family="Consolas", size=13),
                           button_color=COLOR_PRIMARY, border_color="#444")
        self.model_combo.pack(padx=16, pady=(14,6))
        self.model_status = ctk.CTkLabel(card, text="",
                            font=ctk.CTkFont(size=11), text_color=COLOR_MUTED)
        self.model_status.pack(padx=16, pady=(0,4), anchor="w")
        ctk.CTkButton(card, text="↺  Ricarica", width=120,
                      fg_color="transparent", border_color="#444", border_width=1,
                      text_color=COLOR_MUTED, hover_color="#2a2a2a",
                      command=self._load_models).pack(padx=16, pady=(0,14), anchor="w")

        note = self._card(p, pady=(0,16))
        ctk.CTkLabel(note,
            text="💡  Puoi anche digitare manualmente il nome nella casella sopra\n"
                 "     (es. hf.co/utente/modello:Q4_K_M)",
            font=ctk.CTkFont(size=12), text_color=COLOR_MUTED,
            justify="left").pack(padx=16, pady=12)

        self._load_models()

    def _load_models(self):
        self.model_status.configure(text="⏳ Caricamento...", text_color=COLOR_MUTED)
        backend = self.backend_var.get()
        if backend == "ollama":
            url    = f"http://localhost:{self.ollama_port.get()}"
            models = probe_ollama(url)
        else:
            url    = f"http://localhost:{self.lms_port.get()}"
            models = probe_lmstudio(url)
        if models:
            self.model_combo.configure(values=models)
            if not self.model_var.get() or self.model_var.get() not in models:
                self.model_var.set(models[0])
            self.model_status.configure(
                text=f"✅  {len(models)} modell{'o' if len(models)==1 else 'i'} trovati da {url}",
                text_color=COLOR_SUCCESS)
        else:
            self.model_combo.configure(values=["(nessun modello trovato)"])
            self.model_var.set("")
            self.model_status.configure(
                text=f"⚠️  Nessun modello da {url} — avvia il backend oppure digita manualmente.",
                text_color=COLOR_WARN)

    # ════════════════════════════════════════════════════════════════════════
    # STEP 3 — CARTELLA
    # ════════════════════════════════════════════════════════════════════════
    def _step_folder(self):
        p = self.body
        ctk.CTkLabel(p, text="Cartella di installazione",
                     font=ctk.CTkFont(size=20, weight="bold"),
                     text_color=COLOR_TEXT).pack(anchor="w", padx=24, pady=(28,4))
        ctk.CTkLabel(p, text="Scegli dove installare HyperSpace AGI.",
                     font=ctk.CTkFont(size=13), text_color=COLOR_MUTED).pack(anchor="w", padx=24, pady=(0,20))

        card = self._card(p)
        row  = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=14)
        self.dir_entry = ctk.CTkEntry(row, textvariable=self.install_dir,
                          font=ctk.CTkFont(family="Consolas", size=12),
                          width=460)
        self.dir_entry.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row, text="Sfoglia…", width=90,
                      fg_color="#333", hover_color="#444",
                      command=self._browse).pack(side="left", padx=(8,0))

        # Riepilogo config
        summary = self._card(p, title="Riepilogo configurazione", pady=(8,16))
        backend  = self.backend_var.get()
        port     = self.ollama_port.get() if backend == "ollama" else self.lms_port.get()
        model    = self.model_var.get() or "(non selezionato)"
        url_val  = f"http://host.docker.internal:{port}"
        for label, value in [
            ("Backend",    backend.upper()),
            ("URL",        url_val),
            ("Modello",    model),
        ]:
            r = ctk.CTkFrame(summary, fg_color="transparent")
            r.pack(fill="x", padx=16, pady=3)
            ctk.CTkLabel(r, text=label, width=80,
                         font=ctk.CTkFont(size=12), text_color=COLOR_MUTED).pack(side="left")
            ctk.CTkLabel(r, text=value,
                         font=ctk.CTkFont(family="Consolas", size=12),
                         text_color=COLOR_PRIMARY).pack(side="left")
        ctk.CTkLabel(summary, text="", height=6).pack()

    def _browse(self):
        d = filedialog.askdirectory(title="Scegli cartella di installazione")
        if d:
            self.install_dir.set(os.path.join(d, "hyperspace-agi"))

    # ════════════════════════════════════════════════════════════════════════
    # STEP 4 — INSTALLAZIONE
    # ════════════════════════════════════════════════════════════════════════
    def _step_install(self):
        p = self.body
        ctk.CTkLabel(p, text="Installazione",
                     font=ctk.CTkFont(size=20, weight="bold"),
                     text_color=COLOR_TEXT).pack(anchor="w", padx=24, pady=(28,4))

        card = self._card(p)
        self.install_log = ctk.CTkTextbox(card, height=280,
                           font=ctk.CTkFont(family="Consolas", size=11),
                           fg_color="#111", text_color="#aaffaa",
                           wrap="word")
        self.install_log.pack(fill="both", expand=True, padx=8, pady=8)

        self.install_bar = ctk.CTkProgressBar(card, mode="indeterminate",
                           progress_color=COLOR_PRIMARY, fg_color="#2a2a2a")
        self.install_bar.pack(fill="x", padx=8, pady=(0,8))
        self.install_bar.start()

        threading.Thread(target=self._do_install, daemon=True).start()

    def _log(self, msg, color=None):
        """Scrivi riga nel log testuale (thread-safe)."""
        def _append():
            self.install_log.configure(state="normal")
            self.install_log.insert("end", msg + "\n")
            self.install_log.see("end")
            self.install_log.configure(state="disabled")
        self.after(0, _append)

    def _do_install(self):
        dest    = self.install_dir.get()
        backend = self.backend_var.get()
        port    = self.ollama_port.get() if backend == "ollama" else self.lms_port.get()
        model   = self.model_var.get() or "phi3"
        ollama_url = f"http://host.docker.internal:{port}"

        self._log(f"[1/4] Cartella destinazione: {dest}")

        # 1 — clone / aggiorna
        if os.path.isdir(os.path.join(dest, ".git")):
            self._log("      Repository già presente — git pull...")
            code, out = run_cmd("git pull", cwd=dest)
        else:
            os.makedirs(dest, exist_ok=True)
            self._log(f"      git clone {REPO_CLONE}")
            code, out = run_cmd(f'git clone "{REPO_CLONE}" "{dest}"')
        self._log(out.strip() or "OK")
        if code != 0:
            self._log("❌  git clone fallito. Verifica git e la connessione.", COLOR_ERROR)
            self._install_done(False)
            return

        # 2 — scrivi .env
        self._log("\n[2/4] Scrittura .env...")
        env_src = os.path.join(dest, ".env.example")
        env_dst = os.path.join(dest, ".env")
        try:
            if os.path.isfile(env_src):
                with open(env_src, "r", encoding="utf-8") as f:
                    content = f.read()
            else:
                content = ""
            # sostituzioni
            import re
            def setvar(text, key, val):
                pattern = rf"^{key}=.*"
                replacement = f"{key}={val}"
                new = re.sub(pattern, replacement, text, flags=re.MULTILINE)
                if new == text:   # chiave non esisteva
                    new += f"\n{key}={val}"
                return new
            content = setvar(content, "OLLAMA_URL",        ollama_url)
            content = setvar(content, "OLLAMA_MODEL",      model)
            content = setvar(content, "INFERENCE_BACKEND", backend)
            with open(env_dst, "w", encoding="utf-8") as f:
                f.write(content)
            self._log(f"      .env scritto in {env_dst}")
            self._log(f"      OLLAMA_URL={ollama_url}")
            self._log(f"      OLLAMA_MODEL={model}")
            self._log(f"      INFERENCE_BACKEND={backend}")
        except Exception as e:
            self._log(f"❌  Errore scrittura .env: {e}")
            self._install_done(False)
            return

        # 3 — docker compose build
        self._log("\n[3/4] docker compose build (può richiedere qualche minuto)...")
        code, out = run_cmd("docker compose build", cwd=dest)
        self._log(out[-2000:].strip() if out else "OK")
        if code != 0:
            self._log("⚠️  Build con avvisi — provo comunque l'avvio.")

        # 4 — docker compose up
        self._log("\n[4/4] docker compose up -d...")
        code, out = run_cmd("docker compose up -d", cwd=dest)
        self._log(out.strip() or "OK")
        if code != 0:
            self._log("❌  Avvio container fallito.")
            self._log(out)
            self._install_done(False)
            return

        self._log("\n✅  Installazione completata!")
        self._log(f"   Dashboard → {DASH_URL}")
        self._install_done(True)

    def _install_done(self, success):
        def _ui():
            self.install_bar.stop()
            self.install_bar.configure(mode="determinate")
            self.install_bar.set(1 if success else 0)
            if success:
                self.btn_next.configure(state="normal")
                self._go_next()
        self.after(0, _ui)

    # ════════════════════════════════════════════════════════════════════════
    # STEP 5 — DONE
    # ════════════════════════════════════════════════════════════════════════
    def _step_done(self):
        p = self.body
        ctk.CTkLabel(p, text="🎉  HyperSpace AGI è attivo!",
                     font=ctk.CTkFont(size=22, weight="bold"),
                     text_color=COLOR_SUCCESS).pack(anchor="w", padx=24, pady=(40,8))
        ctk.CTkLabel(p,
            text=f"La dashboard è disponibile su:\n{DASH_URL}",
            font=ctk.CTkFont(size=14), text_color=COLOR_TEXT,
            justify="left").pack(anchor="w", padx=24, pady=(0,24))

        card = self._card(p, title="Prossimi passi")
        steps_txt = [
            "1. Apri la dashboard cliccando il pulsante qui sotto",
            "2. Vai nella sezione Tasks e scegli il modello dal menu",
            "3. Scrivi un prompt e premi ▶ Esegui",
            "4. Per fermare: docker compose down nella cartella di installazione",
        ]
        for s in steps_txt:
            ctk.CTkLabel(card, text=s, font=ctk.CTkFont(size=12),
                         text_color=COLOR_MUTED, justify="left").pack(
                         anchor="w", padx=16, pady=3)
        ctk.CTkLabel(card, text="", height=8).pack()

        self.progress.set(1)


# ─── entry point ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = InstallerApp()
    app.mainloop()
