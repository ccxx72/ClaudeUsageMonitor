#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
Claude.ai usage monitor - versione PC (Python + Tkinter)
Claude.ai usage monitor - desktop version (Python + Tkinter)

IT: Mostra l'uso del piano Claude.ai (sessione 5h + settimanale) in una
    piccola finestra, con icona nella system tray che riporta solo la %.
EN: Shows Claude.ai plan usage (5h session + weekly window) in a small
    window, with a system-tray icon displaying just the percentage.

IT: Quando la finestra viene ridotta a icona, sparisce e resta SOLO
    l'icona nella tray con la percentuale (sessione 5h) disegnata sopra.
    Il colore cambia col livello: bianco <70%, arancione 70-89%, rosso >=90%.
EN: When the window is minimized it is hidden and ONLY the tray icon
    remains, with the (5h session) percentage drawn on it. Colour follows
    the level: white <70%, orange 70-89%, red >=90%.

DOVE PRENDERE IL sessionKey / HOW TO GET THE sessionKey:
    claude.ai (logged in) -> F12 -> Application -> Cookies ->
    https://claude.ai -> copy the value of "sessionKey" (starts with
    sk-ant-sid01-...). You can paste the bare value, "sessionKey=...",
    or even the whole Cookie line: the program isolates the right part.

ATTENZIONE / WARNING:
    IT: API interna non ufficiale. Il sessionKey e' una credenziale di
        login: tienilo privato. Scade e va rinnovato.
    EN: Unofficial internal API. The sessionKey is a login credential:
        keep it private. It expires and must be refreshed.

Dipendenze / Dependencies (tray):  pip install pystray pillow
(opzionale / optional: requests; altrimenti usa urllib della stdlib)
Avvio / Run:  python claude_usage_monitor.py
=====================================================================
"""

import json
import os
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path

import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox

# IT: networking - usa "requests" se disponibile, altrimenti urllib (stdlib).
# EN: networking - use "requests" if available, otherwise urllib (stdlib).
try:
    import requests  # type: ignore
    _HAS_REQUESTS = True
except Exception:
    import urllib.request
    import urllib.error
    _HAS_REQUESTS = False

# IT: tray - pystray + Pillow sono opzionali; senza, la tray e' disabilitata.
# EN: tray - pystray + Pillow are optional; without them the tray is disabled.
try:
    import pystray  # type: ignore
    from PIL import Image, ImageDraw, ImageFont  # type: ignore
    _HAS_TRAY = True
except Exception:
    _HAS_TRAY = False

# ===================== CONFIG =====================
FETCH_INTERVAL_MS = 120_000   # IT: ricarica dati ogni 2 min / EN: reload data every 2 min
TICK_INTERVAL_MS  = 30_000    # IT: aggiorna countdown ogni 30 s / EN: refresh countdown every 30 s
HTTP_TIMEOUT      = 15        # IT: timeout richieste (s) / EN: request timeout (s)
CONFIG_PATH = Path.home() / ".claude_usage_monitor.json"  # IT/EN: file di config / config file

# IT: quale percentuale mostrare nell'icona tray: "session" (5h) o "weekly".
# EN: which percentage to show in the tray icon: "session" (5h) or "weekly".
TRAY_METRIC = "session"

# ---- Colori / Colours (tema scuro come lo sketch ESP32) ----
COL_BG     = "#101418"   # IT: sfondo / EN: background
COL_ACCENT = "#D97757"   # IT: accento (arancione Claude) / EN: accent (Claude orange)
COL_TEXT   = "#FFFFFF"   # IT: testo principale / EN: primary text
COL_DIM    = "#8A929C"   # IT: testo secondario / EN: secondary text

# IT: User-Agent "da browser" per la richiesta HTTPS.
# EN: browser-like User-Agent for the HTTPS request.
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")


# ===================== Stato / State =====================
class State:
    """IT: contenitore dello stato condiviso dell'app.
       EN: container for the shared application state."""
    def __init__(self):
        self.cookie = ""           # IT: "sessionKey=..." / EN: the "sessionKey=..." cookie
        self.org_id = ""           # IT: UUID organizzazione scelta / EN: chosen org UUID
        self.session_pct = None    # IT/EN: % sessione 5h / 5h session %
        self.weekly_pct = None     # IT/EN: % settimanale / weekly %
        self.session_reset = None  # IT/EN: datetime reset sessione / session reset datetime
        self.weekly_reset = None   # IT/EN: datetime reset settimanale / weekly reset datetime
        self.data_ok = False       # IT: dati validi? / EN: data valid?
        self.err_msg = ""          # IT: ultimo errore / EN: last error
        self.last_update = None    # IT: ora ultimo aggiornamento / EN: last update time
        self.fetching = False      # IT: fetch in corso? / EN: fetch running?

S = State()


# ===================== Config (load/save) =====================
def load_config():
    """IT: carica il sessionKey dal file di config.
       EN: load the sessionKey from the config file."""
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            S.cookie = data.get("cookie", "") or ""
    except Exception as e:
        print("Config non leggibile / unreadable config:", e)


def save_config():
    """IT: salva il sessionKey nel file di config (permessi 600 su POSIX).
       EN: save the sessionKey to the config file (mode 600 on POSIX)."""
    try:
        CONFIG_PATH.write_text(
            json.dumps({"cookie": S.cookie}, ensure_ascii=False),
            encoding="utf-8",
        )
        # IT: restringe i permessi dove supportato / EN: tighten permissions where supported
        try:
            os.chmod(CONFIG_PATH, stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            pass
    except Exception as e:
        print("Config non scrivibile / unwritable config:", e)


# ===================== Util tempo / Time helpers =====================
def iso_to_dt(s):
    """IT: converte una stringa ISO8601 (UTC) in datetime; None se invalida.
       EN: convert an ISO8601 (UTC) string to datetime; None if invalid."""
    if not s:
        return None
    try:
        # IT: 'Z' -> '+00:00' per fromisoformat / EN: 'Z' -> '+00:00' for fromisoformat
        s2 = s.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def fmt_countdown(dt):
    """IT: tempo mancante a 'dt' come 'Ng Nh' o 'Nh Nm'.
       EN: time left until 'dt' formatted as 'Nd Nh' or 'Nh Nm'."""
    if dt is None:
        return "--"
    now = datetime.now(timezone.utc)
    s = int((dt - now).total_seconds())
    if s < 0:
        s = 0
    d = s // 86400
    h = (s % 86400) // 3600
    m = (s % 3600) // 60
    if d > 0:
        return f"{d}g {h}h"   # IT: g=giorni / EN: g stands for days (giorni)
    return f"{h}h {m}m"


# ===================== sessionKey: pulizia incollato / paste cleanup =====================
def clean_session_key(v):
    """IT: estrae 'sessionKey=...' da qualsiasi cosa l'utente incolli
           (valore grezzo, sessionKey=..., o un'intera riga Cookie).
       EN: extract 'sessionKey=...' from whatever the user pastes
           (raw value, sessionKey=..., or a whole Cookie line)."""
    v = v.strip()
    # IT: rimuove eventuali virgolette esterne / EN: strip surrounding quotes
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1]
    i = v.find("sessionKey=")
    if i >= 0:
        # IT: isola fino al ';' / EN: isolate up to the ';'
        end = v.find(";", i)
        if end < 0:
            end = len(v)
        v = v[i:end]
    else:
        # IT: era solo il valore grezzo / EN: it was just the raw value
        v = "sessionKey=" + v
    return v.strip()


# ===================== HTTP GET verso claude.ai =====================
def claude_get(url):
    """IT: GET HTTPS verso claude.ai con i cookie/header giusti.
           Ritorna (status_code, body); -1 in caso di errore di rete.
       EN: HTTPS GET to claude.ai with the proper cookies/headers.
           Returns (status_code, body); -1 on a network error."""
    headers = {
        "Cookie": S.cookie,
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
        "Referer": "https://claude.ai",
        "Origin": "https://claude.ai",
    }
    if _HAS_REQUESTS:
        # IT: percorso con la libreria requests / EN: requests-library path
        try:
            r = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
            return r.status_code, r.text
        except Exception as e:
            return -1, str(e)
    else:
        # IT: fallback con urllib della stdlib / EN: stdlib urllib fallback
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                body = resp.read().decode("utf-8", "replace")
                return getattr(resp, "status", 200) or 200, body
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:
                body = ""
            return e.code, body
        except Exception as e:
            return -1, str(e)


# ===================== Fetch dati / Data fetch (runs in a thread) =====================
def fetch_claude():
    """IT: recupera org + utilizzo e aggiorna lo stato globale S.
       EN: fetch org + usage and update the global state S."""
    if len(S.cookie) < 20:
        S.err_msg = "Manca sessionKey"  # IT: chiave assente / EN: key missing
        S.data_ok = False
        return

    # ---- Passo 1 / Step 1: scegli l'org (preferisci raven / Team) ----
    # IT: la prima richiesta scopre le organizzazioni dell'account.
    # EN: the first request discovers the account's organizations.
    if not S.org_id:
        code, payload = claude_get("https://claude.ai/api/organizations")
        if code != 200:
            S.err_msg = ("Sessione scaduta" if code in (401, 403)
                         else (f"HTTP {code}" if code > 0 else "Rete assente"))
            return
        try:
            orgs = json.loads(payload)
        except Exception:
            S.err_msg = "JSON org"
            return

        chosen, fallback = "", ""
        for org in orgs if isinstance(orgs, list) else []:
            uuid = org.get("uuid")
            if not uuid:
                continue
            if not fallback:
                fallback = uuid  # IT: prima org come ripiego / EN: first org as fallback
            # IT: preferisci l'org dell'abbonamento ("raven" = piano a pagamento).
            # EN: prefer the subscription org ("raven" = paid plan).
            is_raven = org.get("raven_type") is not None
            if not is_raven:
                caps = org.get("capabilities") or []
                if isinstance(caps, list) and "raven" in caps:
                    is_raven = True
            if is_raven:
                chosen = uuid
                break
        S.org_id = chosen or fallback
        if not S.org_id:
            S.err_msg = "No org"
            return
        print("Org scelta / chosen org:", S.org_id)

    # ---- Passo 2 / Step 2: utilizzo / usage ----
    url = f"https://claude.ai/api/organizations/{S.org_id}/usage"
    code, payload = claude_get(url)
    if code != 200:
        if code in (401, 403):
            # IT: chiave scaduta -> azzera org per riprovare / EN: expired key -> reset org to retry
            S.err_msg = "Sessione scaduta"
            S.org_id = ""
        else:
            S.err_msg = f"HTTP {code}" if code > 0 else "Rete assente"
        return

    try:
        doc = json.loads(payload)
    except Exception:
        S.err_msg = "JSON usage"
        return

    # IT: finestra 5h ("five_hour") / EN: 5h window ("five_hour")
    fh = doc.get("five_hour")
    if isinstance(fh, dict):
        S.session_pct = float(fh.get("utilization") or 0.0)
        S.session_reset = iso_to_dt(fh.get("resets_at") or "")
    else:
        # IT: nessuna finestra attiva = 0% usato / EN: no active window = 0% used
        S.session_pct = 0.0
        S.session_reset = None

    # IT: finestra settimanale ("seven_day") / EN: weekly window ("seven_day")
    sd = doc.get("seven_day")
    if isinstance(sd, dict):
        S.weekly_pct = float(sd.get("utilization") or 0.0)
        S.weekly_reset = iso_to_dt(sd.get("resets_at") or "")
    else:
        S.weekly_pct = 0.0
        S.weekly_reset = None

    S.data_ok = True
    S.err_msg = ""
    S.last_update = datetime.now()


# ===================== Tray helpers =====================
def _tray_font(size):
    """IT: cerca un font bold di sistema, con fallback al default.
       EN: look for a bold system font, falling back to the default."""
    for name in ("segoeuib.ttf", "seguisb.ttf", "arialbd.ttf",
                 "DejaVuSans-Bold.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _tray_color(val):
    """IT: colore della cifra in base al livello d'uso.
       EN: digit colour based on the usage level."""
    if val >= 90:
        return (235, 80, 80, 255)     # IT: rosso / EN: red
    if val >= 70:
        return (217, 119, 87, 255)    # IT: accento arancione / EN: orange accent
    return (255, 255, 255, 255)       # IT: bianco / EN: white


# ===================== UI =====================
class App:
    """IT: finestra principale Tkinter + gestione tray.
       EN: main Tkinter window + tray management."""

    def __init__(self, root):
        self.root = root
        self.tray_icon = None
        root.title("Claude usage")
        root.configure(bg=COL_BG)
        root.resizable(False, False)

        # IT: area di disegno (replica il display dell'ESP32).
        # EN: drawing area (mirrors the ESP32 display).
        self.W, self.H = 380, 270
        self.canvas = tk.Canvas(root, width=self.W, height=self.H, bg=COL_BG,
                                highlightthickness=0)
        self.canvas.pack(side="top", fill="both")

        # ---- Font ----
        self.f_title  = tkfont.Font(family="Helvetica", size=22, weight="bold")
        self.f_small  = tkfont.Font(family="Helvetica", size=11, weight="bold")
        self.f_big    = tkfont.Font(family="Helvetica", size=58, weight="bold")
        self.f_pct    = tkfont.Font(family="Helvetica", size=22, weight="bold")
        self.f_cd     = tkfont.Font(family="Helvetica", size=18, weight="bold")
        self.f_status = tkfont.Font(family="Helvetica", size=9)

        # ---- Barra pulsanti / Button bar ----
        bar = tk.Frame(root, bg=COL_BG)
        bar.pack(side="bottom", fill="x", padx=10, pady=(0, 10))

        # IT: pulsante "Aggiorna" (refresh manuale) / EN: "Refresh" button (manual refresh)
        self.btn_refresh = tk.Button(
            bar, text="Aggiorna", command=self.on_refresh,
            bg=COL_ACCENT, fg=COL_BG, activebackground=COL_ACCENT,
            activeforeground=COL_BG, relief="flat", bd=0,
            font=self.f_small, padx=14, pady=6, cursor="hand2",
        )
        self.btn_refresh.pack(side="left")

        # IT: pulsante per impostare/rinnovare la chiave / EN: button to set/renew the key
        self.btn_key = tk.Button(
            bar, text="Imposta chiave", command=self.on_set_key,
            bg="#222a31", fg=COL_TEXT, activebackground="#2c353d",
            activeforeground=COL_TEXT, relief="flat", bd=0,
            font=self.f_small, padx=14, pady=6, cursor="hand2",
        )
        self.btn_key.pack(side="left", padx=(8, 0))

        # IT: la X chiude del tutto l'app / EN: the X closes the whole app
        root.protocol("WM_DELETE_WINDOW", self.quit_app)

        # IT: crea l'icona tray (se le librerie ci sono) / EN: create the tray icon (if libs present)
        self.setup_tray()
        if _HAS_TRAY and self.tray_icon is not None:
            # IT: minimizza -> nascondi finestra (resta solo l'icona tray).
            # EN: minimize -> hide window (only the tray icon remains).
            root.bind("<Unmap>", self.on_unmap)

        self.draw()
        self.schedule_fetch()
        self.schedule_tick()

    # ---------- disegno finestra / window drawing ----------
    def draw(self):
        """IT: ridisegna l'intero contenuto della finestra.
           EN: redraw the entire window content."""
        c = self.canvas
        c.delete("all")
        W = self.W

        # IT: intestazione / EN: header
        c.create_text(14, 10, anchor="nw", text="CLAUDE",
                      fill=COL_ACCENT, font=self.f_title)
        c.create_text(W - 14, 16, anchor="ne", text="uso piano",
                      fill=COL_DIM, font=self.f_small)

        # IT: nessun dato valido -> messaggio centrale / EN: no valid data -> centered message
        if not S.data_ok:
            msg = S.err_msg or "In attesa dati..."
            c.create_text(W // 2, self.H // 2 - 10, text=msg,
                          fill=COL_DIM, font=self.f_pct)
            self.draw_status()
            self.update_tray()
            return

        # ---- SESSIONE (5h) / 5h SESSION ----
        c.create_text(14, 56, anchor="nw", text="SESSIONE (5h)",
                      fill=COL_DIM, font=self.f_small)
        ps = (str(int(round(S.session_pct)))
              if S.session_pct is not None else "--")
        c.create_text(14, 70, anchor="nw", text=ps,
                      fill=COL_TEXT, font=self.f_big)
        w = self.f_big.measure(ps)  # IT: larghezza per posizionare "%" / EN: width to place "%"
        c.create_text(14 + w + 6, 100, anchor="nw", text="%",
                      fill=COL_TEXT, font=self.f_pct)

        # IT: countdown al reset della sessione / EN: countdown to session reset
        c.create_text(W - 14, 74, anchor="ne", text="ripristino tra",
                      fill=COL_ACCENT, font=self.f_small)
        c.create_text(W - 14, 90, anchor="ne",
                      text=fmt_countdown(S.session_reset),
                      fill=COL_ACCENT, font=self.f_cd)

        # IT: barra di avanzamento sessione / EN: session progress bar
        x0, y0, x1, y1 = 14, 158, W - 14, 172
        c.create_rectangle(x0, y0, x1, y1, outline=COL_DIM)
        if S.session_pct is not None and S.session_pct > 0:
            bw = int((x1 - x0 - 2) * min(S.session_pct, 100) / 100.0)
            if bw > 0:
                c.create_rectangle(x0 + 1, y0 + 1, x0 + 1 + bw, y1 - 1,
                                   fill=COL_ACCENT, outline="")

        # ---- SETTIMANALE / WEEKLY ----
        c.create_text(14, 184, anchor="nw", text="SETTIMANALE",
                      fill=COL_DIM, font=self.f_small)
        if S.weekly_pct is not None:
            ws = (f"{int(round(S.weekly_pct))}%   reset "
                  f"{fmt_countdown(S.weekly_reset)}")
        else:
            ws = "--"
        c.create_text(14, 200, anchor="nw", text=ws,
                      fill=COL_TEXT, font=self.f_pct)

        # IT: barra di avanzamento settimanale / EN: weekly progress bar
        x0, y0, x1, y1 = 14, 232, W - 14, 244
        c.create_rectangle(x0, y0, x1, y1, outline=COL_DIM)
        if S.weekly_pct is not None and S.weekly_pct > 0:
            bw = int((x1 - x0 - 2) * min(S.weekly_pct, 100) / 100.0)
            if bw > 0:
                c.create_rectangle(x0 + 1, y0 + 1, x0 + 1 + bw, y1 - 1,
                                   fill=COL_DIM, outline="")

        self.draw_status()
        self.update_tray()  # IT: tiene allineata l'icona tray / EN: keeps the tray icon in sync

    def draw_status(self):
        """IT: riga di stato in basso (ultimo aggiornamento / errore).
           EN: bottom status line (last update / error)."""
        c = self.canvas
        if S.fetching:
            txt = "aggiornamento..."
        elif S.last_update:
            txt = "ultimo aggiornamento " + S.last_update.strftime("%H:%M:%S")
        elif S.err_msg:
            txt = S.err_msg
        else:
            txt = ""
        c.create_text(14, self.H - 4, anchor="sw", text=txt,
                      fill=COL_DIM, font=self.f_status)

    # ---------- azioni / actions ----------
    def on_refresh(self):
        """IT: handler del pulsante Aggiorna / EN: Refresh button handler."""
        self.start_fetch()

    def start_fetch(self):
        """IT: avvia il fetch in un thread per non bloccare la UI.
           EN: start the fetch in a thread so the UI stays responsive."""
        if S.fetching:
            return
        S.fetching = True
        try:
            self.btn_refresh.config(state="disabled")
        except Exception:
            pass
        self.draw()

        def worker():
            try:
                fetch_claude()
            finally:
                S.fetching = False
                # IT: torna sul thread UI per ridisegnare / EN: hop back to the UI thread to redraw
                self.root.after(0, self.on_fetch_done)

        threading.Thread(target=worker, daemon=True).start()

    def on_fetch_done(self):
        """IT: chiamata sul thread UI a fetch concluso.
           EN: called on the UI thread once the fetch is done."""
        try:
            self.btn_refresh.config(state="normal")
        except Exception:
            pass
        self.draw()

    # ---------- scheduling / temporizzazione ----------
    def schedule_fetch(self):
        """IT: fetch periodico ogni FETCH_INTERVAL_MS.
           EN: periodic fetch every FETCH_INTERVAL_MS."""
        if len(S.cookie) >= 20:
            self.start_fetch()
        else:
            S.err_msg = "Manca sessionKey"
            self.draw()
        self.root.after(FETCH_INTERVAL_MS, self.schedule_fetch)

    def schedule_tick(self):
        """IT: ridisegno periodico per aggiornare i countdown (no rete).
           EN: periodic redraw to update the countdowns (no network)."""
        if not S.fetching:
            self.draw()
        self.root.after(TICK_INTERVAL_MS, self.schedule_tick)

    # ---------- system tray ----------
    def setup_tray(self):
        """IT: crea l'icona tray e la avvia in un thread dedicato.
           EN: create the tray icon and run it in a dedicated thread."""
        if not _HAS_TRAY:
            return
        try:
            menu = pystray.Menu(
                # IT: voce di default = doppio click sull'icona / EN: default item = double-click on the icon
                pystray.MenuItem("Mostra", self._tray_show, default=True),
                pystray.MenuItem("Aggiorna", self._tray_refresh),
                pystray.MenuItem("Esci", self._tray_quit),
            )
            self.tray_icon = pystray.Icon(
                "claude_usage", self.make_tray_image(),
                self.tray_title(), menu)
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
        except Exception as e:
            print("Tray non disponibile / tray unavailable:", e)
            self.tray_icon = None

    def _tray_value(self):
        """IT: percentuale da mostrare nell'icona (vedi TRAY_METRIC).
           EN: percentage to show in the icon (see TRAY_METRIC)."""
        return S.weekly_pct if TRAY_METRIC == "weekly" else S.session_pct

    def make_tray_image(self):
        """IT: genera l'immagine dell'icona con la % disegnata sopra.
           EN: build the icon image with the percentage drawn on it."""
        size = 64
        img = Image.new("RGBA", (size, size), (16, 20, 24, 255))
        d = ImageDraw.Draw(img)
        pct = self._tray_value()
        if S.data_ok and pct is not None:
            val = int(round(pct))
            text = str(val)
            col = _tray_color(val)
        else:
            text = "--"
            col = (138, 146, 156, 255)
        # IT: cifre piu' grandi se 1-2 caratteri / EN: bigger digits when 1-2 chars
        fsize = 54 if len(text) <= 2 else 40
        font = _tray_font(fsize)
        try:
            # IT: centra il testo usando il bounding box / EN: center the text using the bounding box
            bbox = d.textbbox((0, 0), text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            x = (size - tw) / 2 - bbox[0]
            y = (size - th) / 2 - bbox[1]
        except Exception:
            x, y = 8, 8
        d.text((x, y), text, font=font, fill=col)
        return img

    def tray_title(self):
        """IT: tooltip dell'icona (sessione + settimanale).
           EN: icon tooltip (session + weekly)."""
        if not S.data_ok:
            return "Claude usage - " + (S.err_msg or "in attesa")
        sp = (f"{int(round(S.session_pct))}%"
              if S.session_pct is not None else "--")
        wp = (f"{int(round(S.weekly_pct))}%"
              if S.weekly_pct is not None else "--")
        return f"Claude - Sessione {sp} / Settimana {wp}"

    def update_tray(self):
        """IT: aggiorna immagine + tooltip dell'icona tray.
           EN: refresh the tray icon image + tooltip."""
        if not self.tray_icon:
            return
        try:
            self.tray_icon.icon = self.make_tray_image()
            self.tray_icon.title = self.tray_title()
        except Exception:
            pass

    # IT: i callback della tray girano nel thread di pystray: rientrano
    #     nel thread UI con root.after(0, ...) perche' Tkinter non e' thread-safe.
    # EN: tray callbacks run in pystray's thread: they hop back to the UI
    #     thread via root.after(0, ...) because Tkinter is not thread-safe.
    def _tray_show(self, icon=None, item=None):
        self.root.after(0, self.restore_window)

    def _tray_refresh(self, icon=None, item=None):
        self.root.after(0, self.start_fetch)

    def _tray_quit(self, icon=None, item=None):
        self.root.after(0, self.quit_app)

    # ---------- finestra: minimizza / ripristina / chiudi ----------
    def on_unmap(self, event):
        """IT: alla minimizzazione, nasconde la finestra (resta la tray).
           EN: on minimize, hide the window (the tray remains)."""
        if event.widget is self.root and self.root.state() == "iconic":
            self.root.withdraw()

    def restore_window(self):
        """IT: riporta in primo piano la finestra dalla tray.
           EN: bring the window back to the foreground from the tray."""
        self.root.deiconify()
        try:
            self.root.state("normal")
        except Exception:
            pass
        self.root.lift()
        self.root.focus_force()

    def quit_app(self):
        """IT: ferma la tray e chiude l'applicazione.
           EN: stop the tray and close the application."""
        try:
            if self.tray_icon:
                self.tray_icon.stop()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    # ---------- dialog sessionKey ----------
    def on_set_key(self):
        """IT: finestra di dialogo per inserire/rinnovare/cancellare la chiave.
           EN: dialog to set/renew/clear the session key."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Imposta sessionKey")
        dlg.configure(bg=COL_BG)
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()  # IT: modale / EN: modal

        tk.Label(
            dlg, bg=COL_BG, fg=COL_DIM, font=self.f_small, justify="left",
            text=("Incolla il sessionKey (o l'intera riga Cookie).\n"
                  "claude.ai -> F12 -> Application -> Cookies -> claude.ai"),
        ).pack(anchor="w", padx=12, pady=(12, 6))

        # IT: campo multilinea, accetta l'intera riga Cookie / EN: multiline field, accepts a whole Cookie line
        txt = tk.Text(dlg, width=58, height=5, wrap="word",
                      bg="#1a2127", fg=COL_TEXT, insertbackground=COL_TEXT,
                      relief="flat", font=self.f_status)
        txt.pack(padx=12)
        txt.focus_set()

        row = tk.Frame(dlg, bg=COL_BG)
        row.pack(fill="x", padx=12, pady=12)

        def do_save():
            # IT: valida, pulisce e salva la chiave, poi aggiorna.
            # EN: validate, clean and save the key, then refresh.
            raw = txt.get("1.0", "end").strip()
            if len(raw) < 20:
                messagebox.showwarning(
                    "Valore non valido",
                    "La stringa sembra troppo corta.", parent=dlg)
                return
            S.cookie = clean_session_key(raw)
            if len(S.cookie) < 30:
                messagebox.showwarning(
                    "Valore non valido",
                    "Valore troppo corto, ignorato.", parent=dlg)
                return
            save_config()
            S.org_id = ""      # IT: forza la riscelta dell'org / EN: force re-selecting the org
            S.err_msg = ""
            S.data_ok = False
            dlg.destroy()
            self.start_fetch()

        def do_clear():
            # IT: cancella la chiave salvata / EN: clear the stored key
            S.cookie = ""
            save_config()
            S.org_id = ""
            S.data_ok = False
            S.err_msg = "Manca sessionKey"
            dlg.destroy()
            self.draw()

        tk.Button(row, text="Salva", command=do_save, bg=COL_ACCENT,
                  fg=COL_BG, relief="flat", bd=0, font=self.f_small,
                  padx=14, pady=6, cursor="hand2").pack(side="right")
        tk.Button(row, text="Cancella chiave", command=do_clear,
                  bg="#222a31", fg=COL_TEXT, relief="flat", bd=0,
                  font=self.f_small, padx=14, pady=6,
                  cursor="hand2").pack(side="right", padx=(0, 8))


def main():
    """IT: punto di ingresso / EN: entry point."""
    load_config()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
