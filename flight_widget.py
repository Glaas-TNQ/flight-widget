"""Widget desktop sempre in primo piano che segue un volo in tempo reale.

Uso:  pythonw flight_widget.py FR2670        (oppure senza argomenti: chiede il codice)

- trascina con il tasto sinistro per spostarlo; doppio clic = aggiorna subito
- tasto destro: cambia volo, compatto, trasparenza, primo piano, esci
- notifiche di Windows su gate, stato, ritardo e imbarco
"""
from __future__ import annotations

import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk

from PIL import ImageTk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fr24  # noqa: E402
import render  # noqa: E402

CONFIG = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "FlightWidget", "config.json")
KEY_HEX = "#%02x%02x%02x" % render.KEY
UI = dict(bg="#1b1f2a", line="#2e3446", fg="#f0f2f8", dim="#8088a0", field="#11141b",
          amber="#ffb82e", cyan="#56c4ff")


def load_cfg() -> dict:
    try:
        with open(CONFIG, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_cfg(cfg: dict) -> None:
    try:
        os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
        with open(CONFIG, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
    except OSError:
        pass


def toast(title: str, body: str) -> None:
    """Notifica nativa di Windows via PowerShell (nessuna dipendenza)."""
    esc = lambda s: (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                     .replace("'", "''"))
    ps = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$x = New-Object Windows.Data.Xml.Dom.XmlDocument
$x.LoadXml('<toast scenario="reminder"><visual><binding template="ToastGeneric"><text>{esc(title)}</text><text>{esc(body)}</text></binding></visual><actions><action content="OK" arguments="ok"/></actions><audio src="ms-winsoundevent:Notification.Default"/></toast>')
$t = New-Object Windows.UI.Notifications.ToastNotification $x
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}}\\WindowsPowerShell\\v1.0\\powershell.exe').Show($t)
"""
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                         creationflags=0x08000000)  # CREATE_NO_WINDOW
    except OSError:
        pass


STATUS_IT = [
    ("estimated dep", "Partenza stimata"), ("estimated", "Arrivo stimato"),
    ("scheduled", "Programmato"), ("delayed", "In ritardo"), ("landed", "Atterrato"),
    ("canceled", "Cancellato"), ("cancelled", "Cancellato"), ("diverted", "Dirottato"),
    ("departed", "Partito"), ("unknown", "Sconosciuto"),
]


def status_it(text: str) -> str:
    low = text.lower()
    for en, it in STATUS_IT:
        if low.startswith(en):
            return it + text[len(en):]
    return text


def fmt_dur(sec: float) -> str:
    sec = int(abs(sec))
    h, m = divmod(sec // 60, 60)
    return f"{h}h {m:02d}m" if h else f"{m} min"


class Widget:
    def __init__(self, code: str | None):
        self.cfg = load_cfg()
        self.code = fr24.normalize(code) if code else self.cfg.get("code")
        if code:
            self.cfg["code"] = self.code
            save_cfg(self.cfg)
        self.flight: fr24.Flight | None = None
        self.prev: fr24.Flight | None = None
        self.error: str | None = None
        self.results: queue.Queue = queue.Queue()
        self.fetching = False
        self.next_fetch = 0.0
        self.compact = self.cfg.get("compact", False)
        render.LANG = self.cfg.get("lang", "en")
        self.alerted: set[str] = set()
        self.flaps = render.Flaps()
        self.flash = 0.0
        self.photo = None
        self._dragging = False
        self._after = None
        self.buttons: list = []
        self.hover: str | None = None
        self._pressed: str | None = None
        self.hidden = False
        self.told_tray = False
        self.cmds: queue.Queue = queue.Queue()
        self.tray = None

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

        self.root = tk.Tk()
        self.root.title("Flight Widget")
        self.root.overrideredirect(True)
        self.root.configure(bg=KEY_HEX)
        self.root.attributes("-transparentcolor", KEY_HEX)
        self.root.attributes("-topmost", self.cfg.get("topmost", True))
        self.root.attributes("-alpha", self.cfg.get("alpha", 1.0))
        self.k = self.root.winfo_fpixels("1i") / 96  # fattore di scala DPI
        x, y = self.cfg.get("pos", [None, None])
        if x is None:
            x, y = self.root.winfo_screenwidth() - round(380 * self.k), round(40 * self.k)
        self.root.geometry(f"+{x}+{y}")

        self.view = tk.Label(self.root, bd=0, bg=KEY_HEX, highlightthickness=0)
        self.view.pack()
        self.view.bind("<ButtonPress-1>", self._drag_start)
        self.view.bind("<B1-Motion>", self._drag_move)
        self.view.bind("<ButtonRelease-1>", self._drag_end)
        self.view.bind("<Double-Button-1>", lambda e: self.refresh_now())
        self.view.bind("<Button-3>", self._menu)
        self.view.bind("<Motion>", self._motion)
        self.view.bind("<Leave>", lambda e: self._set_hover(None))

        self.topvar = tk.BooleanVar(value=self.cfg.get("topmost", True))
        self.build_menu()

        self.make_tray()
        if not self.code:
            self.root.after(100, self.ask_code)
        self.tick()

    def build_menu(self):
        """Menu del tasto destro, ricostruito quando cambia la lingua."""
        tr = render.tr
        mstyle = dict(tearoff=0, bg=UI["bg"], fg=UI["fg"], activebackground=UI["line"],
                      activeforeground=UI["amber"], bd=0, relief="flat",
                      font=("Bahnschrift", 10), selectcolor=UI["amber"])
        self.menu = tk.Menu(self.root, **mstyle)
        self.menu.add_command(label="  " + tr("Aggiorna ora", "Refresh now"), command=self.refresh_now)
        self.menu.add_command(label="  " + tr("Cambia volo…", "Change flight…"), command=self.ask_code)
        self.menu.add_separator()
        self.menu.add_command(label="  " + tr("Compatto / esteso", "Compact / full"), command=self.toggle_compact)
        self.menu.add_checkbutton(label="  " + tr("Sempre in primo piano", "Always on top"),
                                  variable=self.topvar, command=self.toggle_top)
        alpha = tk.Menu(self.menu, **mstyle)
        for a in (1.0, 0.97, 0.9, 0.8, 0.65):
            alpha.add_command(label=f"  {int(a * 100)}%", command=lambda a=a: self.set_alpha(a))
        self.menu.add_cascade(label="  " + tr("Trasparenza", "Opacity"), menu=alpha)
        self.menu.add_command(label="  " + tr("English", "Italiano"), command=self.toggle_lang)
        self.menu.add_command(label="  " + tr("Prova notifica", "Test notification"),
                              command=lambda: toast("Flight Widget", tr("Le notifiche funzionano ✈",
                                                                         "Notifications work ✈")))
        self.menu.add_separator()
        self.menu.add_command(label="  " + tr("Nascondi nell'area di notifica", "Hide to tray"),
                              command=self.hide)
        self.menu.add_command(label="  " + tr("Esci", "Quit"), command=self.quit)

    # ---------------------------------------------------------------- UI events
    def _hit(self, e) -> str | None:
        x, y = e.x / self.k, e.y / self.k
        for name, x0, y0, x1, y1 in self.buttons:
            if x0 - 3 <= x <= x1 + 3 and y0 - 3 <= y <= y1 + 3:
                return name
        return None

    def _set_hover(self, name):
        if name != self.hover:
            self.hover = name
            self.view.configure(cursor="hand2" if name else "")
            self.redraw()

    def _motion(self, e):
        if not self._dragging:
            self._set_hover(self._hit(e))

    def _drag_start(self, e):
        self._pressed = self._hit(e)
        if self._pressed:
            return
        self._dragging = True
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        if self._dragging:
            self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _drag_end(self, e):
        if self._pressed:
            name, self._pressed = self._pressed, None
            if self._hit(e) == name:
                {"close": self.hide, "collapse": self.toggle_compact,
                 "expand": self.toggle_compact}[name]()
            return
        self._dragging = False
        self.cfg["pos"] = [self.root.winfo_x(), self.root.winfo_y()]
        save_cfg(self.cfg)

    # ---------------------------------------------------------------- tray
    def make_tray(self):
        """Icona nell'area di notifica: il widget chiuso resta attivo e manda le notifiche."""
        try:
            import pystray
        except ImportError:
            return
        tr = render.tr
        menu = pystray.Menu(
            pystray.MenuItem(lambda _i: tr("Mostra widget", "Show widget"),
                             lambda: self.cmds.put("show"), default=True),
            pystray.MenuItem(lambda _i: tr("Aggiorna ora", "Refresh now"), lambda: self.cmds.put("refresh")),
            pystray.MenuItem(lambda _i: tr("Cambia volo…", "Change flight…"), lambda: self.cmds.put("ask")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda _i: tr("Esci", "Quit"), lambda: self.cmds.put("quit")),
        )
        try:
            self.tray = pystray.Icon("FlightWidget", render.tray_icon(), "Flight Widget", menu)
            self.tray.run_detached()
        except Exception:
            self.tray = None

    def tray_title(self):
        if not self.tray:
            return
        f = self.flight
        if f:
            o = f.origin
            bits = [f"{f.number} {o.iata}-{f.dest.iata}"]
            if f.phase == "ground" and o.gate:
                bits.append(f"gate {o.gate}")
            bits.append(status_it(f.status_text) if render.LANG == "it" else f.status_text)
            title = " · ".join(bits)
        else:
            title = f"Flight Widget · {self.code or ''}"
        try:
            self.tray.title = title[:120]
        except Exception:
            pass

    def hide(self):
        if not self.tray:  # senza icona di notifica non lo nascondo: non si potrebbe riaprire
            self.toggle_compact() if not self.compact else None
            return
        self.hidden = True
        self.hover = None
        self.root.withdraw()
        if not self.told_tray:
            self.told_tray = True
            toast("Flight Widget", render.tr(
                "Continuo a seguire il volo dall'area di notifica. Clic sull'icona dell'aereo per riaprirlo.",
                "Still tracking your flight from the system tray. Click the plane icon to reopen."))

    def show(self):
        self.hidden = False
        self.root.deiconify()
        self.root.attributes("-topmost", self.cfg.get("topmost", True))
        self.root.lift()
        self.redraw()

    def handle_cmds(self):
        while not self.cmds.empty():
            cmd = self.cmds.get()
            if cmd == "show":
                self.show()
            elif cmd == "refresh":
                self.refresh_now()
            elif cmd == "ask":
                self.show()
                self.ask_code()
            elif cmd == "quit":
                self.quit()
                return False
        return True

    def _menu(self, e):
        self.menu.tk_popup(e.x_root, e.y_root)

    def toggle_compact(self):
        self.compact = not self.compact
        self.cfg["compact"] = self.compact
        save_cfg(self.cfg)
        self.redraw()

    def toggle_lang(self):
        render.LANG = "en" if render.LANG == "it" else "it"
        self.cfg["lang"] = render.LANG
        save_cfg(self.cfg)
        self.build_menu()
        self.tray_title()
        if self.tray:
            try:
                self.tray.update_menu()
            except Exception:
                pass
        self.redraw()

    def toggle_top(self):
        self.cfg["topmost"] = self.topvar.get()
        self.root.attributes("-topmost", self.cfg["topmost"])
        save_cfg(self.cfg)

    def set_alpha(self, a):
        self.cfg["alpha"] = a
        self.root.attributes("-alpha", a)
        save_cfg(self.cfg)

    def ask_code(self):
        """Finestrella in stile per inserire il codice volo."""
        top = tk.Toplevel(self.root)
        top.overrideredirect(True)
        top.configure(bg=UI["bg"], highlightthickness=1, highlightbackground=UI["line"])
        top.attributes("-topmost", True)
        tk.Label(top, text=render.tr("CODICE VOLO", "FLIGHT CODE"), font=("Bahnschrift SemiBold", 9), fg=UI["dim"],
                 bg=UI["bg"]).pack(anchor="w", padx=18, pady=(16, 6))
        var = tk.StringVar(value=self.code or "")
        entry = tk.Entry(top, textvariable=var, font=("Bahnschrift SemiBold", 24), width=9,
                         justify="center", bg=UI["field"], fg=UI["amber"], relief="flat",
                         insertbackground=UI["amber"], highlightthickness=1,
                         selectbackground=UI["line"], selectforeground=UI["amber"],
                         highlightbackground=UI["line"], highlightcolor=UI["cyan"])
        entry.pack(padx=18, ipady=6)
        tk.Label(top, text=render.tr("Invio per confermare  ·  Esc per annullare", "Enter to confirm  ·  Esc to cancel"), font=("Bahnschrift", 8),
                 fg=UI["dim"], bg=UI["bg"]).pack(padx=18, pady=(8, 16))
        top.update_idletasks()
        x = self.root.winfo_x() + max(0, (self.root.winfo_width() - top.winfo_width()) // 2)
        top.geometry(f"+{x}+{self.root.winfo_y() + round(40 * self.k)}")

        def ok(_e=None):
            code = var.get().strip()
            top.destroy()
            if code:
                self.set_code(code)
            elif not self.code:
                self.quit()

        def cancel(_e=None):
            top.destroy()
            if not self.code:
                self.quit()

        entry.bind("<Return>", ok)
        entry.bind("<Escape>", cancel)
        top.after(50, lambda: (top.focus_force(), entry.focus_force(), entry.select_range(0, "end")))

    def set_code(self, code: str):
        self.code = fr24.normalize(code)
        self.cfg["code"] = self.code
        save_cfg(self.cfg)
        self.flight = self.prev = None
        self.alerted.clear()
        self.error = None
        self.refresh_now()

    def quit(self):
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass
        self.root.destroy()

    # ---------------------------------------------------------------- fetching
    def refresh_now(self):
        self.next_fetch = 0
        self.redraw()

    def _worker(self, code: str):
        try:
            self.results.put((code, fr24.fetch(code), None))
        except fr24.FlightNotFound as e:
            self.results.put((code, None, str(e)))
        except Exception as e:  # rete assente, Cloudflare, JSON cambiato...
            self.results.put((code, None, render.tr("Errore rete: ", "Network error: ") + str(e)))

    def interval(self) -> int:
        f = self.flight
        if not f:
            return 30
        dep = f.best_dep or 0
        if f.phase == "landed":
            return 180
        if f.phase == "air":
            return 45
        if dep - time.time() < 90 * 60:  # meno di 90 minuti al decollo
            return 30
        if dep - time.time() < 6 * 3600:
            return 60
        return 300

    def tick(self):
        if not self.handle_cmds():
            return
        now = time.time()
        while not self.results.empty():
            code, flight, err = self.results.get()
            self.fetching = False
            if code != self.code:
                continue
            if flight:
                self.prev, self.flight, self.error = self.flight, flight, None
                self.notify_changes()
                self.tray_title()
            else:
                self.error = err
            self.next_fetch = now + (self.interval() if not self.error else 30)
        if self.code and not self.fetching and now >= self.next_fetch:
            self.fetching = True
            threading.Thread(target=self._worker, args=(self.code,), daemon=True).start()
        self.redraw()

    def redraw(self):
        now = time.time()
        if self.hidden:  # nascosto: niente disegno, ma dati e notifiche continuano
            if self._after:
                self.root.after_cancel(self._after)
            self._after = self.root.after(250, self.tick)
            return
        f = self.flight
        pulse = bool(f and (f.phase == "air" or (
            f.phase == "ground" and f.best_dep and 0 < f.best_dep - now < 45 * 60)))
        state = dict(flight=f, code=self.code, error=self.error, fetching=self.fetching,
                     compact=self.compact, flaps=self.flaps, now=now,
                     status_it=status_it if render.LANG == "it" else (lambda s: s),
                     highlight=now < self.flash, pulse=pulse, hover=self.hover)
        animating = False
        try:
            img, animating = render.render(state, self.k)
            self.buttons = state.get("buttons", [])
            self.photo = ImageTk.PhotoImage(img)
            self.view.configure(image=self.photo)
            self._keep_on_screen(*img.size)
        except Exception:  # un frame sbagliato non deve fermare il widget
            import traceback
            traceback.print_exc()
        if self._after:
            self.root.after_cancel(self._after)
        delay = 50 if animating else 1000 - int((now % 1) * 1000)
        self._after = self.root.after(max(20, delay), self.tick)

    def _keep_on_screen(self, W, H):
        if self._dragging:
            return
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x, y = self.root.winfo_x(), self.root.winfo_y()
        nx, ny = max(0, min(x, sw - W - 4)), max(0, min(y, sh - H - 4))
        if (nx, ny) != (x, y):
            self.root.geometry(f"+{nx}+{ny}")

    # ---------------------------------------------------------------- notifiche
    def notify_changes(self):
        f, p = self.flight, self.prev
        o = f.origin
        tr = render.tr
        msgs = []
        if p and p.number == f.number and p.sched_dep == f.sched_dep:
            if f.origin.gate and f.origin.gate != p.origin.gate:
                msgs.append(("Gate " + (tr("assegnato", "assigned") if not p.origin.gate
                                        else tr("CAMBIATO", "CHANGED")),
                             f"{f.number}: gate {f.origin.gate}"
                             + (tr(" (era ", " (was ") + f"{p.origin.gate})" if p.origin.gate else "")
                             + (f" · Terminal {o.terminal}" if o.terminal else "")))
            if p.phase != f.phase:
                txt = {"air": tr("Decollato alle ", "Took off at ") + o.local(f.real_dep),
                       "landed": tr("Atterrato a ", "Landed in ") + f.dest.iata
                       + tr(" alle ", " at ") + f.dest.local(f.real_arr),
                       "cancelled": tr("VOLO CANCELLATO", "FLIGHT CANCELLED"),
                       "diverted": tr("Volo dirottato", "Flight diverted")}.get(f.phase)
                if txt:
                    msgs.append((f"{f.number} {o.iata}→{f.dest.iata}", txt))
            if (f.best_dep and p.best_dep and f.phase == "ground"
                    and abs(f.best_dep - p.best_dep) >= 5 * 60):
                diff = round((f.best_dep - p.best_dep) / 60)
                msgs.append((f"{f.number}: " + (tr("partenza posticipata", "departure delayed") if diff > 0
                                                else tr("partenza anticipata", "departure moved earlier")),
                             tr("Ora stimata ", "Now estimated at ") + o.local(f.best_dep)
                             + f" ({'+' if diff > 0 else ''}{diff} min)"))
            if f.dest.baggage and f.dest.baggage != p.dest.baggage:
                msgs.append((f"{f.number}: " + tr("bagagli", "baggage"),
                             tr("Nastro ", "Belt ") + f"{f.dest.baggage}" + tr(" a ", " at ") + f.dest.iata))
        # promemoria imbarco (una volta sola per soglia)
        if f.phase == "ground" and f.best_dep:
            left = (f.best_dep - time.time()) / 60
            for th, label in ((45, tr("Imbarco a breve", "Boarding soon")),
                              (25, tr("Chiusura gate vicina", "Gate closing soon"))):
                key = f"{f.sched_dep}-{th}"
                if left <= th and left > 0 and key not in self.alerted:
                    self.alerted.add(key)
                    gate = f" · gate {o.gate}" if o.gate else ""
                    msgs.append((f"{label} · {f.number}", tr("Decollo previsto ", "Take-off expected at ")
                                 + o.local(f.best_dep) + tr(" (tra ", " (in ") + f"{fmt_dur(left * 60)}){gate}"))
        for t, b in msgs:
            toast(t, b)
        if msgs:
            self.flash = time.time() + 6

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    Widget(sys.argv[1] if len(sys.argv) > 1 else None).run()
