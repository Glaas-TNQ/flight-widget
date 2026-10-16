"""Dati volo da Flightradar24 (endpoint pubblici non ufficiali, nessuna chiave).

- flight/list.json  -> istanze del volo (orari programmati/stimati/reali, stato, aereo)
- airport.json      -> tabellone partenze/arrivi: terminal, gate, nastro bagagli
- clickhandler      -> posizione live (quota, velocità) quando il volo è in aria
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
API = "https://api.flightradar24.com/common/v1"
LIVE = "https://data-live.flightradar24.com/clickhandler/"

_session = requests.Session()
_session.headers.update({"User-Agent": UA, "Accept": "application/json"})


class FlightNotFound(Exception):
    pass


@dataclass
class Airport:
    iata: str = "?"
    name: str = ""
    city: str = ""
    tz_offset: int = 0          # secondi rispetto a UTC
    terminal: str | None = None
    gate: str | None = None
    baggage: str | None = None

    def local(self, ts: int | None) -> str:
        if not ts:
            return "--:--"
        return datetime.fromtimestamp(ts + self.tz_offset, tz=timezone.utc).strftime("%H:%M")

    def local_date(self, ts: int | None) -> str:
        if not ts:
            return ""
        return datetime.fromtimestamp(ts + self.tz_offset, tz=timezone.utc).strftime("%d/%m")


@dataclass
class Flight:
    number: str
    fr_id: str | None
    status_text: str
    status_color: str           # gray / green / yellow / red
    live: bool
    origin: Airport
    dest: Airport
    sched_dep: int | None
    sched_arr: int | None
    est_dep: int | None
    est_arr: int | None
    real_dep: int | None
    real_arr: int | None
    aircraft: str | None = None
    registration: str | None = None
    altitude: int | None = None     # piedi
    speed: int | None = None        # nodi
    fetched_at: float = field(default_factory=time.time)

    @property
    def best_dep(self) -> int | None:
        return self.real_dep or self.est_dep or self.sched_dep

    @property
    def best_arr(self) -> int | None:
        return self.real_arr or self.est_arr or self.sched_arr

    @property
    def dep_delay_min(self) -> int | None:
        if self.sched_dep and self.best_dep:
            return round((self.best_dep - self.sched_dep) / 60)
        return None

    @property
    def arr_delay_min(self) -> int | None:
        if self.sched_arr and self.best_arr:
            return round((self.best_arr - self.sched_arr) / 60)
        return None

    @property
    def phase(self) -> str:
        """ground / air / landed / cancelled / diverted"""
        t = self.status_text.lower()
        if "cancel" in t:
            return "cancelled"
        if "divert" in t:
            return "diverted"
        if self.real_arr or t.startswith("landed"):
            return "landed"
        if self.real_dep or self.live:
            return "air"
        return "ground"

    @property
    def progress(self) -> float | None:
        if self.phase != "air":
            return 1.0 if self.phase == "landed" else None
        a, b = self.real_dep or self.best_dep, self.best_arr
        if not a or not b or b <= a:
            return None
        return max(0.0, min(1.0, (time.time() - a) / (b - a)))


def _get(url: str, params: dict | None = None, timeout: float = 20) -> dict:
    last = None
    for attempt in range(3):
        try:
            r = _session.get(url, params=params, timeout=timeout)
            if r.status_code == 200 and r.text.lstrip().startswith("{"):
                return r.json()
            last = RuntimeError(f"HTTP {r.status_code}")
        except (requests.RequestException, ValueError) as e:
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise ConnectionError(f"Flightradar24 not responding ({last})")


def normalize(code: str) -> str:
    return "".join(code.upper().split())


def _airport(node: dict | None) -> Airport:
    node = node or {}
    code = node.get("code") or {}
    tz = node.get("timezone") or {}
    pos = node.get("position") or {}
    info = node.get("info") or {}
    return Airport(
        iata=code.get("iata") or "?",
        name=node.get("name") or "",
        city=((pos.get("region") or {}).get("city")) or "",
        tz_offset=tz.get("offset") or 0,
        terminal=info.get("terminal"), gate=info.get("gate"), baggage=info.get("baggage"),
    )


def _pick(rows: list[dict]) -> dict:
    """Sceglie l'istanza del volo che interessa adesso."""
    now = time.time()

    def dep(r):
        t = r["time"]
        return (t["real"]["departure"] or t["estimated"]["departure"]
                or t["scheduled"]["departure"] or 0)

    def arr(r):
        t = r["time"]
        return (t["real"]["arrival"] or t["estimated"]["arrival"]
                or t["scheduled"]["arrival"] or 0)

    live = [r for r in rows if (r.get("status") or {}).get("live")]
    if live:
        return live[0]
    # atterrato da meno di 90 minuti: lo mostro ancora (bagagli, gate di arrivo)
    recent = [r for r in rows if r["time"]["real"]["arrival"]
              and now - r["time"]["real"]["arrival"] < 90 * 60]
    if recent:
        return max(recent, key=arr)
    # prossimo volo non ancora atterrato
    upcoming = [r for r in rows if not r["time"]["real"]["arrival"]
                and arr(r) > now - 3 * 3600]
    if upcoming:
        return min(upcoming, key=dep)
    return max(rows, key=dep)


def _gate_info(flight_number: str, airport_iata: str, mode: str, around_ts: int | None) -> dict | None:
    """Cerca il volo nel tabellone dell'aeroporto per terminal/gate/nastro."""
    params = {
        "code": airport_iata.lower(),
        "plugin[]": "schedule",
        f"plugin-setting[schedule][mode]": mode,
        "page": 1,
        "limit": 100,
    }
    if around_ts:
        params["plugin-setting[schedule][timestamp]"] = int(around_ts) - 2 * 3600
    for page in (1, 2, 3):
        params["page"] = page
        data = _get(f"{API}/airport.json", params)
        sched = (((data.get("result") or {}).get("response") or {}).get("airport") or {})
        sched = ((sched.get("pluginData") or {}).get("schedule") or {}).get(mode) or {}
        for row in sched.get("data") or []:
            f = row.get("flight") or {}
            num = ((f.get("identification") or {}).get("number") or {})
            if flight_number in (num.get("default"), num.get("alternative")):
                side = "origin" if mode == "departures" else "destination"
                return ((f.get("airport") or {}).get(side) or {}).get("info")
        pg = sched.get("page") or {}
        if not pg or page >= (pg.get("total") or 1):
            break
    return None


def _live_position(fr_id: str) -> tuple[int | None, int | None]:
    try:
        data = _get(LIVE, {"version": "1.5", "flight": fr_id}, timeout=10)
    except ConnectionError:
        return None, None
    trail = data.get("trail") or []
    if not trail:
        return None, None
    return trail[0].get("alt"), trail[0].get("spd")


def fetch(code: str, want_gate: bool = True) -> Flight:
    code = normalize(code)
    data = _get(f"{API}/flight/list.json",
                {"query": code, "fetchBy": "flight", "page": 1, "limit": 25})
    rows = (((data.get("result") or {}).get("response") or {}).get("data")) or []
    if not rows:
        raise FlightNotFound(f"No flight found for {code}")
    r = _pick(rows)

    ident = r.get("identification") or {}
    st = r.get("status") or {}
    gen = ((st.get("generic") or {}).get("status")) or {}
    t = r.get("time") or {}
    ac = r.get("aircraft") or {}
    ap = r.get("airport") or {}

    f = Flight(
        number=((ident.get("number") or {}).get("default")) or code,
        fr_id=ident.get("id"),
        status_text=st.get("text") or "Sconosciuto",
        status_color=gen.get("color") or st.get("icon") or "gray",
        live=bool(st.get("live")),
        origin=_airport(ap.get("origin")),
        dest=_airport(ap.get("destination")),
        sched_dep=t.get("scheduled", {}).get("departure"),
        sched_arr=t.get("scheduled", {}).get("arrival"),
        est_dep=t.get("estimated", {}).get("departure"),
        est_arr=t.get("estimated", {}).get("arrival"),
        real_dep=t.get("real", {}).get("departure"),
        real_arr=t.get("real", {}).get("arrival"),
        aircraft=((ac.get("model") or {}).get("text")) or ((ac.get("model") or {}).get("code")),
        registration=ac.get("registration"),
    )

    if want_gate:
        try:
            if f.phase == "ground" and f.origin.iata != "?":
                info = _gate_info(f.number, f.origin.iata, "departures", f.sched_dep)
                if info:
                    f.origin.terminal, f.origin.gate, f.origin.baggage = (
                        info.get("terminal"), info.get("gate"), info.get("baggage"))
            if f.phase in ("air", "landed") and f.dest.iata != "?":
                info = _gate_info(f.number, f.dest.iata, "arrivals", f.sched_arr)
                if info:
                    f.dest.terminal, f.dest.gate, f.dest.baggage = (
                        info.get("terminal"), info.get("gate"), info.get("baggage"))
        except ConnectionError:
            pass  # il gate è un extra: senza, il widget funziona lo stesso

    if f.live and f.fr_id:
        f.altitude, f.speed = _live_position(f.fr_id)
    return f


if __name__ == "__main__":
    import sys
    fl = fetch(sys.argv[1] if len(sys.argv) > 1 else "FR2670")
    o, d = fl.origin, fl.dest
    print(f"{fl.number}  {o.iata} -> {d.iata}  [{fl.status_text}] fase={fl.phase}")
    print(f"  partenza  prog {o.local(fl.sched_dep)}  stim {o.local(fl.est_dep)}  reale {o.local(fl.real_dep)}"
          f"  ritardo {fl.dep_delay_min} min")
    print(f"  arrivo    prog {d.local(fl.sched_arr)}  stim {d.local(fl.est_arr)}  reale {d.local(fl.real_arr)}")
    print(f"  T{o.terminal} gate {o.gate}  | arrivo T{d.terminal} gate {d.gate} nastro {d.baggage}")
    print(f"  aereo {fl.aircraft} {fl.registration}  alt {fl.altitude} spd {fl.speed}  progress {fl.progress}")
