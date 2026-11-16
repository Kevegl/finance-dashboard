"""Kleines Statusfenster des Startprogramms (lokaler Betrieb): zeigt, dass das Dashboard läuft, auch wenn der
Browser geschlossen ist – mit «Im Browser öffnen» und «Beenden». Nur Standardbibliothek (tkinter).

Fehlt tkinter (z.B. Linux ohne python3-tk) oder gibt es keinen Bildschirm, läuft das Startprogramm wie bisher
ohne Fenster weiter. Mit FD_NO_GUI=1 lässt sich das Fenster abschalten.
"""
import threading
import webbrowser

import tkinter as tk
from tkinter import messagebox, ttk

TEXT = {
    "de": {
        "starting": "Startet …", "running": "Läuft auf diesem Computer", "updating": "Wird aktualisiert …",
        "restarting": "Startet neu …", "stopping": "Wird beendet …",
        "open": "Im Browser öffnen", "quit": "Beenden",
        "hint": "Du kannst dieses Fenster minimieren. Schliessen beendet das Dashboard.",
        "confirm_title": "Dashboard beenden?",
        "confirm": "Das Finanz-Dashboard wird beendet. Deine Daten sind gespeichert.",
        "reset": "Passwort zurücksetzen …",
        "reset_title": "Anmeldung zurücksetzen?",
        "reset_confirm": "Deine Daten bleiben unverändert erhalten. Nur die Anmeldung wird zurückgesetzt: Passwort, "
                         "Zwei-Faktor-Anmeldung und alle Sitzungen werden gelöscht.\n\nDanach öffnet sich der Browser, "
                         "und du legst dort ein neues Passwort fest.",
    },
    "en": {
        "starting": "Starting …", "running": "Running on this computer", "updating": "Updating …",
        "restarting": "Restarting …", "stopping": "Closing …",
        "open": "Open in browser", "quit": "Quit",
        "hint": "You can minimise this window. Closing it quits the dashboard.",
        "confirm_title": "Quit the dashboard?",
        "confirm": "The finance dashboard will be closed. Your data is saved.",
        "reset": "Reset password …",
        "reset_title": "Reset sign-in?",
        "reset_confirm": "Your data stays untouched. Only the sign-in is reset: password, two-factor "
                         "authentication and all sessions are deleted.\n\nThe browser then opens and you set a "
                         "new password there.",
    },
}
COLOR = {"running": "#1f9d55", "stopping": "#8a8a8a"}   # sonst orange (startet, aktualisiert)


def app_icon(size=32):
    """Logo des Dashboards (Linie nach oben auf dunklem Grund) als Fenstersymbol."""
    img = tk.PhotoImage(width=size, height=size)
    img.put("#111111", to=(0, 0, size, size))
    pts = [(4, 17), (9, 12), (13, 15), (20, 7)]          # wie favicon.svg (24er-Raster)
    s = size / 24
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        steps = int(max(abs(x1 - x0), abs(y1 - y0)) * s * 3) + 1
        for i in range(steps + 1):
            x = (x0 + (x1 - x0) * i / steps) * s
            y = (y0 + (y1 - y0) * i / steps) * s
            r = max(1, round(s))
            img.put("#ffffff", to=(int(x - r), int(y - r), int(x + r), int(y + r)))
    return img


class StatusWindow:
    def __init__(self, launcher):
        self.l = launcher
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Finanz-Dashboard")
        self.root.resizable(False, False)
        try:
            self._icon = app_icon()
            self.root.iconphoto(True, self._icon)
        except tk.TclError:
            pass
        frm = ttk.Frame(self.root, padding=(18, 14, 18, 16))
        frm.grid(sticky="nsew")
        ttk.Label(frm, text="Finanz-Dashboard", font=("TkDefaultFont", 13, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
        line = ttk.Frame(frm)
        line.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.dot = tk.Canvas(line, width=12, height=12, highlightthickness=0, bd=0)
        self.dot.grid(row=0, column=0, padx=(0, 6))
        self.dot_id = self.dot.create_oval(1, 1, 11, 11, fill="#d97706", outline="")
        self.status = ttk.Label(line)
        self.status.grid(row=0, column=1, sticky="w")
        ttk.Label(frm, text=launcher.url, foreground="#666666").grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.hint = ttk.Label(frm, wraplength=300, foreground="#666666", justify="left")
        self.hint.grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 12))
        self.btn_open = ttk.Button(frm, command=lambda: webbrowser.open(launcher.url))
        self.btn_open.grid(row=4, column=0, sticky="w")
        self.btn_quit = ttk.Button(frm, command=self.ask_quit)
        self.btn_quit.grid(row=4, column=1, sticky="e", padx=(12, 0))
        self.btn_reset = tk.Label(frm, cursor="hand2", foreground="#666666", font=("TkDefaultFont", 9, "underline"))
        self.btn_reset.grid(row=5, column=0, columnspan=2, sticky="w", pady=(12, 0))
        self.btn_reset.bind("<Button-1>", lambda _e: self.ask_reset())
        frm.columnconfigure(1, weight=1)
        self.root.protocol("WM_DELETE_WINDOW", self.ask_quit)
        self.shown = None
        self.update_texts()
        self.root.deiconify()
        self.root.lift()

    def t(self, key):
        return TEXT.get(self.l.lang or "de", TEXT["de"])[key]

    def update_texts(self):
        state = "stopping" if self.l.stopping else self.l.status
        if (state, self.l.lang) == self.shown:
            return
        self.shown = (state, self.l.lang)
        self.status.configure(text=self.t(state if state in TEXT["de"] else "starting"))
        self.dot.itemconfigure(self.dot_id, fill=COLOR.get(state, "#d97706"))
        self.hint.configure(text=self.t("hint"))
        self.btn_open.configure(text=self.t("open"), state="normal" if state == "running" else "disabled")
        self.btn_reset.configure(text=self.t("reset"))
        self.btn_quit.configure(text=self.t("quit"), state="disabled" if self.l.stopping else "normal")

    def ask_quit(self):
        if self.l.stopping:
            return
        if messagebox.askokcancel(self.t("confirm_title"), self.t("confirm"), parent=self.root):
            threading.Thread(target=self.l.request_stop, daemon=True).start()
            self.update_texts()

    def ask_reset(self):
        if self.l.stopping or self.l.status != "running":
            return
        if messagebox.askokcancel(self.t("reset_title"), self.t("reset_confirm"), parent=self.root):
            threading.Thread(target=self.l.reset_login, daemon=True).start()

    def run(self, worker):
        """Läuft im Hauptthread, bis die Überwachung (worker) endet."""
        ticks = [0]

        def poll():
            ticks[0] += 1
            if ticks[0] % 30 == 0 and self.l.status == "running":      # Sprache alle ~10 s nachführen
                threading.Thread(target=self.l.fetch_lang, daemon=True).start()
            if self.l.interrupted and not self.l.stopping:          # Strg+C im Konsolenfenster
                threading.Thread(target=self.l.request_stop, daemon=True).start()
            if not worker.is_alive():
                self.root.destroy()
                return
            self.update_texts()
            self.root.after(300, poll)
        self.root.after(300, poll)
        self.root.mainloop()
