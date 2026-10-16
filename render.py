"""Disegno del widget con Pillow: stile tabellone aeroportuale (split-flap + Bahnschrift).

Si disegna in coordinate logiche (px a 96 dpi) su una tela supercampionata, poi si
riduce con LANCZOS: bordi, archi e testo vengono antialiasati.
"""
from __future__ import annotations

import math
import os
import time

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONTS = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
BAHN = os.path.join(FONTS, "bahnschrift.ttf")
SEGOE = os.path.join(FONTS, "seguisb.ttf")

C = dict(
    top=(25, 29, 40), bottom=(12, 14, 20), border=(46, 52, 70),
    panel=(27, 31, 42), fg=(240, 242, 248), dim=(128, 136, 156), faint=(62, 68, 86),
    tile=(30, 34, 45), tile_top=(38, 43, 57), split=(9, 10, 14),
    amber=(255, 184, 46), cyan=(86, 196, 255),
    green=(56, 214, 156), yellow=(251, 191, 36), red=(250, 108, 108), gray=(150, 162, 184),
)
STATUS = {"green": C["green"], "yellow": C["yellow"], "red": C["red"], "gray": C["gray"]}
FLAP_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
KEY = (1, 0, 1)  # colore reso trasparente dalla finestra (angoli arrotondati)
LANG = "en"  # "en" (default) o "it"


def tr(it: str, en: str) -> str:
    return en if LANG == "en" else it


def mix(a, b, t):
    """t=0 -> a, t=1 -> b"""
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


# --------------------------------------------------------------------------- flap
class Flaps:
    """Ricorda cosa mostra ogni gruppo di tessere e anima i cambi di valore."""
    DUR, STAGGER, RATE = 0.6, 0.07, 22

    def __init__(self):
        self.state: dict[str, tuple[str, float]] = {}
        self.animating = False

    def get(self, key: str, target: str, now: float) -> list[tuple[str, float | None]]:
        st = self.state.get(key)
        if st is None or st[0] != target:
            st = self.state[key] = (target, now)
        out = []
        for i, ch in enumerate(target):
            el = now - st[1] - i * self.STAGGER
            if ch != " " and el < self.DUR:
                self.animating = True
                k = int(now * self.RATE + i * 5)
                out.append((FLAP_CHARS[k % len(FLAP_CHARS)], (now * self.RATE) % 1))
            else:
                out.append((ch, None))
        return out


# --------------------------------------------------------------------------- painter
class Painter:
    def __init__(self, w: float, h: float, scale: float):
        self.s = scale
        self.w, self.h = w, h
        self.img = Image.new("RGBA", (self.S(w), self.S(h)), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)

    _fonts: dict = {}

    def S(self, v: float) -> int:
        return round(v * self.s)

    def font(self, size: float, weight: str = "Regular", path: str = BAHN):
        key = (path, self.S(size), weight)
        f = self._fonts.get(key)
        if f is None:
            f = ImageFont.truetype(path, self.S(size))
            if path == BAHN:
                f.set_variation_by_name(weight)
            self._fonts[key] = f
        return f

    def rrect(self, x0, y0, x1, y1, r, fill=None, outline=None, width=1.0):
        self.d.rounded_rectangle([self.S(x0), self.S(y0), self.S(x1), self.S(y1)], self.S(r),
                                 fill=fill, outline=outline, width=max(1, self.S(width)))

    def rect(self, x0, y0, x1, y1, fill):
        self.d.rectangle([self.S(x0), self.S(y0), self.S(x1), self.S(y1)], fill=fill)

    def circle(self, x, y, r, fill=None, outline=None, width=1.0):
        self.d.ellipse([self.S(x - r), self.S(y - r), self.S(x + r), self.S(y + r)],
                       fill=fill, outline=outline, width=max(1, self.S(width)))

    def line(self, pts, fill, width=1.0):
        self.d.line([(self.S(x), self.S(y)) for x, y in pts], fill=fill, width=max(1, self.S(width)))

    def poly(self, pts, fill):
        self.d.polygon([(self.S(x), self.S(y)) for x, y in pts], fill=fill)

    def text(self, x, y, s, size, fill, weight="Regular", anchor="la"):
        self.d.text((self.S(x), self.S(y)), s, font=self.font(size, weight), fill=fill, anchor=anchor)

    def tw(self, s, size, weight="Regular") -> float:
        return self.d.textlength(s, font=self.font(size, weight)) / self.s

    def label(self, x, y, s, size=7.5, fill=C["dim"], weight="SemiBold", track=1.4, anchor="l"):
        """Etichetta maiuscola spaziata (stile segnaletica)."""
        s = s.upper()
        widths = [self.tw(ch, size, weight) for ch in s]
        total = sum(widths) + track * (len(s) - 1)
        cx = x - total if anchor == "r" else x - total / 2 if anchor == "m" else x
        for ch, w in zip(s, widths):
            self.text(cx, y, ch, size, fill, weight)
            cx += w + track
        return total

    def flap(self, x, y, chars, cw, ch, size, fg, gap=3.0, r=3.0, anchor="l"):
        """Tessere split-flap. chars: lista (carattere, fase animazione|None)."""
        n = len(chars)
        total = n * cw + (n - 1) * gap
        if anchor == "r":
            x -= total
        for i, (c, ph) in enumerate(chars):
            tx = x + i * (cw + gap)
            self.rrect(tx, y, tx + cw, y + ch, r, fill=C["tile"])
            self.rrect(tx, y, tx + cw, y + ch / 2, r, fill=C["tile_top"])
            self.rect(tx, y + ch / 2 - r, tx + cw, y + ch / 2, C["tile_top"])
            # fessura centrale (sotto al carattere, come una paletta vera vista di fronte)
            self.rect(tx, y + ch / 2 - 0.6, tx + cw, y + ch / 2 + 0.6, C["split"])
            col = fg if ph is None else mix(fg, C["dim"], 0.35)
            if c.strip():
                self.text(tx + cw / 2, y + ch / 2 + 0.5, c, size, col, "Bold SemiCondensed", "mm")
                self.rect(tx + 1, y + ch / 2 - 0.25, tx + cw - 1, y + ch / 2 + 0.25,
                          mix(C["split"], C["tile"], 0.3))
            # cerniere
            self.rect(tx - 0.6, y + ch / 2 - 2, tx + 0.8, y + ch / 2 + 2, C["split"])
            self.rect(tx + cw - 0.8, y + ch / 2 - 2, tx + cw + 0.6, y + ch / 2 + 2, C["split"])
            if ph is not None:
                # la paletta che cade: banda scura che scende sulla metà superiore
                band = y + (ch / 2) * ph
                if band > y + 1.5:
                    self.rect(tx + 0.5, max(y + 1, band - 2.5), tx + cw - 0.5, band,
                              mix(C["tile"], C["split"], 0.6))
        return total

    def chip(self, x, y, s, color, size=8.5, anchor="l"):
        w = self.tw(s, size, "SemiBold") + 12
        if anchor == "r":
            x -= w
        self.rrect(x, y, x + w, y + size + 8, (size + 8) / 2, fill=mix(C["panel"], color, 0.18))
        self.text(x + 6, y + (size + 8) / 2 + 0.3, s, size, color, "SemiBold", "lm")
        return w

    def plane(self, x, y, angle, size, fill):
        half = [(10, 0), (6, -1.5), (1.5, -1.5), (-3.5, -8.5), (-6, -8.5), (-3, -1.5),
                (-7.5, -1.5), (-9.5, -4.6), (-11, -4.6), (-10, -0.9), (-10.5, 0)]
        pts = half + [(px, -py) for px, py in reversed(half[1:-1])]
        ca, sa = math.cos(angle), math.sin(angle)
        k = size / 20
        self.poly([(x + (px * ca - py * sa) * k, y + (px * sa + py * ca) * k) for px, py in pts], fill)

    def button(self, state, name, x, y, size=17):
        """Pulsante icona; registra l'area cliccabile in state["buttons"] (coordinate logiche)."""
        hover = state.get("hover") == name
        if hover:
            self.rrect(x, y, x + size, y + size, 5, fill=mix(C["panel"], C["fg"], 0.12))
        col = C["fg"] if hover else C["dim"]
        cx, cy, k = x + size / 2, y + size / 2, size * 0.22
        if name == "close":
            self.line([(cx - k, cy - k), (cx + k, cy + k)], col, 1.5)
            self.line([(cx - k, cy + k), (cx + k, cy - k)], col, 1.5)
        elif name == "collapse":   # due barre: diventa una striscia
            self.line([(cx - k * 1.2, cy - k * 0.5), (cx + k * 1.2, cy - k * 0.5)], col, 1.5)
            self.line([(cx - k * 1.2, cy + k * 0.5), (cx + k * 1.2, cy + k * 0.5)], col, 1.5)
        else:                      # expand: riquadro
            self.rrect(cx - k * 1.15, cy - k, cx + k * 1.15, cy + k, 1.5, outline=col, width=1.4)
        state.setdefault("buttons", []).append((name, x, y, x + size, y + size))

    def ring(self, cx, cy, r, width, frac, color):
        box = [self.S(cx - r), self.S(cy - r), self.S(cx + r), self.S(cy + r)]
        self.d.ellipse(box, outline=C["faint"], width=self.S(width))
        if frac and frac > 0.002:
            frac = min(frac, 1.0)
            self.d.arc(box, -90, -90 + 360 * frac, fill=color, width=self.S(width))
            for a in (-90, -90 + 360 * frac):  # estremi arrotondati
                rr = r - width / 2
                self.circle(cx + rr * math.cos(math.radians(a)), cy + rr * math.sin(math.radians(a)),
                            width / 2, fill=color)

    def finish(self, out_scale: float) -> Image.Image:
        img = self.img.resize((round(self.w * out_scale), round(self.h * out_scale)), Image.LANCZOS)
        bg = Image.new("RGBA", img.size, KEY + (255,))
        bg.alpha_composite(img)
        return bg.convert("RGB")


_bg_cache: dict = {}


def card_background(p: Painter, accent) -> None:
    """Sfondo a gradiente con angoli arrotondati, bagliore del colore di stato e bordo."""
    key = (p.img.size, accent)
    bg = _bg_cache.get(key)
    if bg is None:
        W, H = p.img.size
        grad = Image.linear_gradient("L").resize((W, H))
        body = Image.composite(Image.new("RGBA", (W, H), C["bottom"] + (255,)),
                               Image.new("RGBA", (W, H), C["top"] + (255,)), grad)
        # bagliore morbido in alto a destra
        glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        r = W * 0.45
        gd.ellipse([W - r * 1.3, -r * 0.9, W + r * 0.7, r * 0.6], fill=accent + (38,))
        glow = glow.filter(ImageFilter.GaussianBlur(W * 0.09))
        body.alpha_composite(glow)
        mask = Image.new("L", (W, H), 0)
        rad = p.S(16)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, W - 1, H - 1], rad, fill=255)
        bg = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        bg.paste(body, (0, 0), mask)
        ImageDraw.Draw(bg).rounded_rectangle([0, 0, W - 1, H - 1], rad, outline=C["border"],
                                             width=max(1, p.S(1)))
        # filo luminoso sul bordo superiore
        line = Image.new("RGBA", (W, max(1, p.S(1.5))), accent + (255,))
        fade = Image.new("L", (W, line.height), 0)
        fd = ImageDraw.Draw(fade)
        for i in range(W):
            t = 1 - abs(i - W / 2) / (W / 2)
            fd.line([(i, 0), (i, line.height)], fill=round(200 * t ** 1.6))
        bg.paste(line, (0, 0), fade)
        _bg_cache[key] = bg
    p.img.alpha_composite(bg)


# --------------------------------------------------------------------------- helpers
def fmt_short(sec: float) -> str:
    m = int(max(0, sec) // 60)
    h, m = divmod(m, 60)
    if h >= 48:
        return f"{h // 24}" + tr("g", "d")
    return f"{h}h{m:02d}" if h else f"{m}'"


def delay_info(m):
    if m is None:
        return None, C["dim"]
    if m <= 5:
        return (tr("in orario", "on time") if m > -5 else tr(f"{-m} min prima", f"{-m} min early")), C["green"]
    h, mm = divmod(m, 60)
    txt = f"+{h}h {mm:02d}m" if h else f"+{m} min"
    return txt, C["yellow"] if m < 30 else C["red"]


def bezier(p0, p1, p2, t):
    u = 1 - t
    return (u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
            u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1])


# --------------------------------------------------------------------------- views
def render(state: dict, scale: float) -> tuple[Image.Image, bool]:
    """state: flight, code, error, fetching, next_fetch, compact, flaps, status_it, now, highlight.
    Ritorna (immagine, serve_animare)."""
    flaps: Flaps = state["flaps"]
    flaps.animating = False
    state["buttons"] = []
    f = state.get("flight")
    if state.get("compact") and f:
        p = compact_view(state, scale)
    elif f:
        p = full_view(state, scale)
    else:
        p = empty_view(state, scale)
    pulse = bool(state.get("highlight"))
    return p.finish(scale), flaps.animating or pulse


def _accent(f):
    if f is None:
        return C["cyan"]
    if f.phase == "cancelled":
        return C["red"]
    return STATUS.get(f.status_color, C["gray"])


def _border_flash(p: Painter, state):
    if state.get("highlight"):
        a = 0.5 + 0.5 * math.sin(state["now"] * 8)
        p.rrect(0.5, 0.5, p.w - 0.5, p.h - 0.5, 16, outline=mix(C["border"], C["amber"], a), width=2)


def empty_view(state, scale):
    W, H = 360, 118
    p = Painter(W, H, 2 * scale)
    card_background(p, C["cyan"] if not state.get("error") else C["red"])
    code = state.get("code") or "------"
    p.label(18, 18, tr("Volo", "Flight"))
    p.flap(18, 32, state["flaps"].get("num", code, state["now"]), 20, 30, 20, C["fg"])
    p.button(state, "close", W - 18 - 17, 9)
    msg = state.get("error") or tr("Carico i dati del volo…", "Loading flight data…")
    p.text(18, 82, msg[:58], 10, C["red"] if state.get("error") else C["dim"])
    _border_flash(p, state)
    return p


def full_view(state, scale):
    f = state["flight"]
    now = state["now"]
    o, d = f.origin, f.dest
    acc = _accent(f)
    W, H = 360, 372
    p = Painter(W, H, 2 * scale)
    card_background(p, acc)
    M = 18

    # --- intestazione: numero volo su tessere + pillola di stato
    p.label(M, 15, tr("Volo", "Flight"))
    p.button(state, "close", W - M - 17 + 4, 6)
    p.button(state, "collapse", W - M - 17 * 2 - 2 + 4, 6)
    p.flap(M, 28, state["flaps"].get("num", f.number, now), 19, 28, 19, C["fg"], gap=2.5)
    st = state["status_it"](f.status_text)
    sw = p.tw(st, 9.5, "SemiBold") + 28
    p.rrect(W - M - sw, 31, W - M, 53, 11, fill=mix(C["panel"], acc, 0.2))
    dot_a = 0.3 if state.get("pulse") and int(now) % 2 else 1
    p.circle(W - M - sw + 12, 42, 3.4, fill=mix(C["panel"], acc, dot_a))
    p.text(W - M - sw + 21, 42.5, st, 9.5, acc, "SemiBold", "lm")

    # --- rotta ad arco
    y = 76
    p.text(M - 1, y, o.iata, 34, C["fg"], "Bold", "la")
    p.text(W - M + 1, y, d.iata, 34, C["fg"], "Bold", "ra")
    p.text(M, y + 41, (o.city or o.name)[:20], 9.5, C["dim"])
    p.text(W - M, y + 41, (d.city or d.name)[:20], 9.5, C["dim"], anchor="ra")
    x0 = M + p.tw(o.iata, 34, "Bold") + 14
    x1 = W - M - p.tw(d.iata, 34, "Bold") - 14
    by = y + 24
    P0, P1, P2 = (x0, by), ((x0 + x1) / 2, by - 30), (x1, by)
    prog = f.progress if f.phase in ("air", "landed") else 0.0
    prog = prog or 0.0
    n = 48
    pts = [bezier(P0, P1, P2, i / n) for i in range(n + 1)]
    for i in range(0, n, 2):  # tratteggio
        p.line([pts[i], pts[i + 1]], C["faint"], 1.4)
    done = [bezier(P0, P1, P2, prog * i / n) for i in range(n + 1)]
    if prog > 0:
        p.line(done, C["cyan"], 2)
    p.circle(*P0, 3.2, fill=C["top"], outline=C["cyan"] if prog > 0 else C["dim"], width=1.6)
    p.circle(*P2, 3.2, fill=C["top"], outline=C["cyan"] if prog >= 1 else C["dim"], width=1.6)
    t = min(max(prog, 0.0), 1.0)
    px, py = bezier(P0, P1, P2, min(t, 0.92) if f.phase != "ground" else 0.06)
    tx, ty = bezier(P0, P1, P2, min(1, t + 0.01)), bezier(P0, P1, P2, max(0, t - 0.01))
    ang = math.atan2(tx[1] - ty[1], tx[0] - ty[0]) if f.phase == "air" else (
        0 if f.phase == "landed" else math.atan2(P1[1] - P0[1], P1[0] - P0[0]) * 0.55)
    if f.phase == "air":
        p.circle(px, py, 9, fill=mix(C["top"], C["cyan"], 0.18))
    p.plane(px, py, ang, 21, C["fg"] if f.phase != "cancelled" else C["red"])

    # --- orari
    y = 140
    cw = (W - 2 * M - 10) / 2
    for side in (0, 1):
        x = M + side * (cw + 10)
        p.rrect(x, y, x + cw, y + 82, 10, fill=C["panel"])
    _time_block(p, f, now, M + 12, y, cw - 24, "dep")
    _time_block(p, f, now, M + cw + 10 + 12, y, cw - 24, "arr")

    # --- gate / bagagli + anello conto alla rovescia
    y = 238
    ring_r = 33
    rcx, rcy = W - M - ring_r - 2, y + 38
    if f.phase in ("ground", "cancelled"):
        p.label(M, y, "Gate")
        gate = (o.gate or "---")
        p.flap(M, y + 14, state["flaps"].get("gate", gate.upper(), now), 26, 40, 27,
               C["amber"] if o.gate else C["faint"], gap=3)
        gx = M + max(3, len(gate)) * 29 + 14
        p.label(gx, y, "Terminal")
        term = o.terminal or "-"
        p.flap(gx, y + 14, state["flaps"].get("term", term.upper(), now), 20, 40, 21,
               C["fg"] if o.terminal else C["faint"], gap=3)
    elif f.phase == "air":
        p.label(M, y, tr("Quota", "Altitude"))
        alt = (f"{f.altitude:,}" if LANG == "en" else f"{f.altitude:,}".replace(",", ".")) if f.altitude else "—"
        p.text(M, y + 13, alt, 24, C["fg"], "SemiBold")
        p.text(M + p.tw(alt, 24, "SemiBold") + 4, y + 23, "ft", 10, C["dim"])
        p.label(M + 120, y, tr("Velocità", "Speed"))
        spd = str(round(f.speed * 1.852)) if f.speed else "—"
        p.text(M + 120, y + 13, spd, 24, C["fg"], "SemiBold")
        p.text(M + 120 + p.tw(spd, 24, "SemiBold") + 4, y + 23, "km/h", 10, C["dim"])
        if d.gate or d.terminal:
            p.text(M, y + 50, tr("Arrivo", "Arrival") + f"  T{d.terminal or '-'}  ·  gate {d.gate or '-'}", 9.5, C["dim"])
    else:  # landed / diverted
        p.label(M, y, tr("Nastro", "Belt"))
        belt = d.baggage or "--"
        p.flap(M, y + 14, state["flaps"].get("belt", belt.upper(), now), 26, 40, 27,
               C["amber"] if d.baggage else C["faint"], gap=3)
        gx = M + max(2, len(belt)) * 29 + 14
        p.label(gx, y, tr("Gate arr.", "Arr. gate"))
        g = d.gate or "-"
        p.flap(gx, y + 14, state["flaps"].get("agate", g.upper(), now), 20, 40, 21,
               C["fg"] if d.gate else C["faint"], gap=3)

    big, small, frac, rcol = _ring_info(f, now)
    p.ring(rcx, rcy, ring_r, 5, frac, rcol)
    p.text(rcx, rcy - 3, big, 15 if len(big) <= 5 else 12, C["fg"], "Bold", "mm")
    p.label(rcx, rcy + 9, small, 6.2, C["dim"], track=0.8, anchor="m")

    # --- piede
    _countdown_line(p, f, now, M, 318, W)
    _footer(p, state, f, M, H - 24, W)
    _border_flash(p, state)
    return p


def _time_block(p, f, now, x, y, w, which):
    ap = f.origin if which == "dep" else f.dest
    right = which == "arr"
    anc = "ra" if right else "la"
    if which == "dep":
        sched, best = f.sched_dep, f.best_dep
        tag = tr("effettiva", "actual") if f.real_dep else tr("stimata", "estimated") if f.est_dep else ""
        delay = f.dep_delay_min
        guessed = False
    else:
        sched, best = f.sched_arr, f.best_arr
        tag = tr("effettivo", "actual") if f.real_arr else tr("stimato", "estimated") if f.est_arr else ""
        guessed = False
        if not f.est_arr and not f.real_arr and sched and f.dep_delay_min and f.dep_delay_min > 5:
            best, guessed, tag = sched + f.dep_delay_min * 60, True, tr("previsto", "expected")
        delay = round((best - sched) / 60) if best and sched else None
    title = (tr("Partenza", "Departure") if which == "dep" else tr("Arrivo", "Arrival")) + (f" · {tag}" if tag else "")
    day = ap.local_date(sched)
    if day and day != ap.local_date(int(now)):
        title += f" · {day}"
    p.label(x + (w if right else 0), y + 11, title, 7, anchor="r" if right else "l", track=1.1)
    dtxt, dcol = delay_info(delay)
    big = ap.local(best)
    late = delay is not None and delay > 5
    p.text(x + (w if right else 0), y + 24, ("~" if guessed else "") + big, 27,
           dcol if late and which == "dep" else C["fg"], "Bold", anc)
    bw = p.tw(("~" if guessed else "") + big, 27, "Bold")
    if sched and best and sched != best and abs(best - sched) >= 60:
        old = ap.local(sched)
        ow = p.tw(old, 10)
        ox = x + bw + 7 if not right else x + w - bw - 7 - ow
        p.text(ox, y + 41, old, 10, C["dim"], anchor="lm")
        p.line([(ox - 1, y + 41.5), (ox + ow + 1, y + 41.5)], C["dim"], 1)
    if dtxt:
        p.chip(x + (w if right else 0), y + 58, dtxt, dcol, 8, anchor="r" if right else "l")


def _ring_info(f, now):
    if f.phase == "cancelled":
        return "X", tr("cancellato", "cancelled"), 1.0, C["red"]
    if f.phase == "landed":
        return fmt_short(now - (f.real_arr or now)), tr("atterrato", "landed"), 1.0, C["green"]
    if f.phase == "diverted":
        return "!", tr("dirottato", "diverted"), 1.0, C["red"]
    if f.phase == "air":
        arr = f.best_arr
        return (fmt_short(arr - now) if arr else "—"), tr("all'arrivo", "to landing"), f.progress or 0, C["cyan"]
    dep = f.best_dep
    if not dep:
        return "—", "", 0, C["dim"]
    left = dep - now
    if left <= 0:
        return tr("ora", "now"), tr("decollo", "departure"), 1.0, C["amber"]
    frac = max(0.0, min(1.0, 1 - left / (3 * 3600)))
    col = C["red"] if left < 20 * 60 else C["amber"] if left < 45 * 60 else C["cyan"]
    return fmt_short(left), tr("al decollo", "to take-off"), frac, col


def _countdown_line(p, f, now, M, y, W):
    o = f.origin
    if f.phase == "ground" and f.best_dep:
        left = (f.best_dep - now) / 60
        if 0 < left <= 45:
            msg, col = (tr("Imbarco in corso", "Boarding") if left > 25 else tr("Chiusura gate vicina", "Gate closing soon")), C["amber"]
        elif left > 0:
            h, m = divmod(int(left), 60)
            msg, col = tr("Decollo tra ", "Take-off in ") + (f"{h}h {m:02d}m" if h else f"{m} min"), C["fg"]
        else:
            msg, col = tr("Decollo imminente", "Take-off imminent"), C["amber"]
        if o.gate:
            msg += f"  ·  gate {o.gate}"
    elif f.phase == "air":
        msg, col = tr("In volo verso ", "Flying to ") + (f.dest.city or f.dest.iata), C["cyan"]
    elif f.phase == "landed":
        msg, col = tr("Atterrato a ", "Landed in ") + f"{f.dest.city or f.dest.iata}" + tr(" alle ", " at ") + f.dest.local(f.real_arr), C["green"]
    elif f.phase == "cancelled":
        msg, col = tr("Volo cancellato", "Flight cancelled"), C["red"]
    else:
        msg, col = tr("Volo dirottato", "Flight diverted"), C["red"]
    p.text(M, y, msg, 11.5, col, "SemiBold")


def _footer(p, state, f, M, y, W):
    p.line([(M, y - 8), (W - M, y - 8)], C["faint"], 0.8)
    ac = " · ".join(x for x in (f.aircraft, f.registration) if x) if f else ""
    p.text(M, y + 1, ac[:38], 8.5, C["dim"])
    now = state["now"]
    if state.get("error"):
        txt, col = tr("rete assente, riprovo", "offline, retrying"), C["red"]
    elif state.get("fetching"):
        txt, col = tr("aggiorno…", "updating…"), C["amber"]
    else:
        ago = int(now - f.fetched_at)
        txt, col = f"live · {ago}s" + tr(" fa", " ago"), C["green"]
    tw = p.tw(txt, 8.5)
    p.text(W - M, y + 1, txt, 8.5, C["dim"], anchor="ra")
    p.circle(W - M - tw - 7, y + 6.5, 2.6, fill=col)


def compact_view(state, scale):
    f = state["flight"]
    now = state["now"]
    o, d = f.origin, f.dest
    acc = _accent(f)
    W, H = 330, 66
    p = Painter(W, H, 2 * scale)
    card_background(p, acc)
    p.rrect(7, 14, 10, H - 14, 1.5, fill=acc)
    p.text(18, 12, f.number, 16, C["fg"], "Bold")
    p.text(18, 36, f"{o.iata}  ›  {d.iata}", 10, C["dim"], "SemiBold")
    if f.phase in ("ground", "cancelled"):
        val, lab, on = (o.gate or "---"), "Gate", bool(o.gate)
    elif f.phase == "air":
        val, lab, on = None, None, False
    else:
        val, lab, on = (d.baggage or "--"), tr("Nastro", "Belt"), bool(d.baggage)
    if val:
        p.label(118, 9, lab, 6.5)
        p.flap(118, 21, state["flaps"].get("cgate", val.upper(), now), 19, 30, 20,
               C["amber"] if on else C["faint"], gap=2.5)
    else:
        p.label(118, 9, tr("Quota", "Altitude"), 6.5)
        alt = f"{f.altitude:,}".replace(",", ".") + " ft" if f.altitude else tr("in volo", "airborne")
        p.text(118, 24, alt, 15, C["cyan"], "SemiBold")
    big, small, frac, rcol = _ring_info(f, now)
    R = W - 38
    p.text(R, 13, big, 18, rcol if rcol != C["cyan"] else C["fg"], "Bold", "ra")
    p.label(R, 38, small, 6.5, anchor="r")
    p.line([(R + 9, 12), (R + 9, H - 14)], C["faint"], 0.8)
    p.button(state, "close", W - 25, 9, 16)
    p.button(state, "expand", W - 25, 31, 16)
    if frac:
        p.rrect(18, H - 7, R, H - 5, 1, fill=C["faint"])
        p.rrect(18, H - 7, 18 + (R - 18) * min(frac, 1), H - 5, 1, fill=rcol)
    _border_flash(p, state)
    return p


def tray_icon() -> Image.Image:
    """Icona per l'area di notifica: aereo ambra su tessera scura."""
    p = Painter(32, 32, 4)
    p.rrect(0, 0, 32, 32, 7, fill=C["tile"])
    p.rect(0, 15.6, 32, 16.4, C["split"])
    p.plane(16.5, 16, -0.75, 27, C["amber"])
    return p.img.resize((64, 64), Image.LANCZOS)
