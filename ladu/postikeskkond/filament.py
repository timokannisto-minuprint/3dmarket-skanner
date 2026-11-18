# -*- coding: utf-8 -*-
"""
filament.py – Postikeskkonna vahekaart 🧵 Filament, v108: andmed elavad laoserveris ladu.3dmarket.ee.

Postikeskkond on nüüd ainult klient: kõik /api/filament/* päringud suunatakse laoserverisse (reaalajas, kohalikku
koopiat ei hoita). index.html ei pidanud muutuma. Kohapeale jäävad ainult:
  - tootepildid näidatakse kohalikust kaustast (<filament_kaust>/pildid, kiirem); puuduvad pildid laaditakse taustal
    ka laoserverisse, et ladu.3dmarket.ee veebileht neid näitaks;
  - andmefailide üleslaadimine: kataloog.csv, skanner/*.json ja arved/*.csv saadetakse laoserverisse, kui need
    Dropboxis muutuvad (Claude uuendab neid seal).
Esimesel käivitusel, kui laoserver on tühi, viiakse siinsed filament_* tabelid sinna üle (üks kord).

Seaded failis ladu.json (postikeskkond.py kõrval): {"url": "https://ladu.3dmarket.ee", "voti": "..."}.
Vanad filament_* tabelid jäävad tellimused.db-sse alles (varukoopiaks), aga neid enam ei kasutata ega sünkroonita.
Telefoniskänner käib nüüd otse laoserverisse (https://ladu.3dmarket.ee), kohtvõrgu skännerit (filament_tel.py) pole vaja.
"""
import json, os, socket, sqlite3, sys, threading, time

from flask import Blueprint, Response, request, send_file, abort

bp = Blueprint("filament", __name__)
_db = _ok = _now = None
_K = {}
IS_WIN = sys.platform.startswith("win")
HERE = os.path.dirname(os.path.abspath(__file__))
TABELID = ("filament_liikumine", "filament_kood", "filament_tellimus", "filament_seade", "filament_tundmatu")
FAILID = ("kataloog.csv", "skanner/tellimine.json", "skanner/hinnad_tana.json", "skanner/toonerid.json")
OLEK = {"failid_aeg": 0, "failid_viga": "", "import": "", "laaditud": {}}
_LUKK = threading.Lock()


def init(app, db, ok, now, sync_tables, K=None):
    global _db, _ok, _now, _K
    _db, _ok, _now, _K = db, ok, now, (K or {})
    for t in TABELID:   # v108: filamendi andmeid enam arvutite vahel ei sünkroonita (elavad laoserveris)
        sync_tables.pop(t, None)
    app.register_blueprint(bp)
    if os.path.basename(sys.argv[0] or "").lower().startswith("postikeskkond"):
        t = threading.Timer(3, _taust)
        t.daemon = True
        t.start()


def schema(c):   # vanad tabelid jäävad alles (varukoopia); uusi andmeid siia ei kirjutata
    c.execute("""CREATE TABLE IF NOT EXISTS filament_liikumine (
        id INTEGER PRIMARY KEY AUTOINCREMENT, sku TEXT, kood TEXT, kogus INTEGER, tyyp TEXT,
        markus TEXT, aeg TEXT, allikas TEXT, arvuti TEXT)""")


# ---------------------------------------------------------------- seaded
def kaust():
    k = (_K.get("filament_kaust", "") or "").strip()
    if k:
        return os.path.abspath(os.path.expanduser(k))
    return r"E:\DropBox\Dropbox\3dmarket claude" if IS_WIN else os.path.expanduser("~/Dropbox/3dmarket claude")


def seaded():
    """ladu.json loetakse iga kord uuesti (muutmise järel ei pea programmi taaskäivitama)."""
    try:
        with open(os.path.join(HERE, "ladu.json"), encoding="utf-8") as fh:
            j = json.load(fh)
    except (OSError, ValueError):
        j = {}
    url = (j.get("url") or _K.get("ladu_url", "") or "https://ladu.3dmarket.ee").strip().rstrip("/")
    voti = (j.get("voti") or _K.get("ladu_voti", "") or "").strip()
    return url, voti


def _arvuti():
    return socket.gethostname().split(".")[0]


def _paised(voti):
    return {"X-Ladu-Voti": voti, "X-Arvuti": _arvuti(), "User-Agent": "Postikeskkond"}


def _kysi(meetod, tee, timeout=25, **kw):
    import requests
    url, voti = seaded()
    if not voti:
        raise RuntimeError("Laoserveri võti puudub (fail ladu.json Postikeskkonna kaustas).")
    h = _paised(voti)
    h.update(kw.pop("headers", {}))
    return requests.request(meetod, url + tee, headers=h, timeout=timeout, **kw)


# ---------------------------------------------------------------- vahendaja
@bp.get("/api/filament/pilt")
def filament_pilt():
    """Toote pilt kohalikust kataloogi kaustast (ainult pildid/ alamkaustast)."""
    rel = request.args.get("p", "")
    base = os.path.abspath(os.path.join(kaust(), "pildid"))
    f = os.path.abspath(os.path.join(kaust(), rel))
    if not f.startswith(base + os.sep) or not os.path.isfile(f):
        abort(404)
    return send_file(f, max_age=86400)


@bp.route("/api/filament", defaults={"tee": ""}, methods=["GET", "POST", "PUT", "DELETE"])
@bp.route("/api/filament/<path:tee>", methods=["GET", "POST", "PUT", "DELETE"])
def filament_vahenda(tee):
    if tee in ("kataloog", "tellimine", "impordi") or (tee == "" and request.args.get("uuenda")):
        _laadi_failid(sunni=tee == "impordi")
    timeout = 150 if tee.startswith("tarnija_") else 25
    try:
        r = _kysi(request.method, "/api/filament" + ("/" + tee if tee else ""), timeout=timeout,
                  params=request.args, data=request.get_data() or None,
                  headers={"Content-Type": request.headers.get("Content-Type", "application/json")})
    except Exception as e:
        return Response(json.dumps({"ok": False, "viga": f"Laoserver ({seaded()[0]}) pole kättesaadav: {e}"}, ensure_ascii=False),
                        502, content_type="application/json")
    return Response(r.content, r.status_code, content_type=r.headers.get("Content-Type", "application/json"))


@bp.get("/api/ladu/olek")
def ladu_olek():
    """Laoserveri olek + selle arvuti üleslaadimised (diagnostika)."""
    try:
        j = _kysi("GET", "/api/ladu/olek").json()
    except Exception as e:
        j = {"ok": False, "viga": str(e)}
    j["klient"] = dict(OLEK, url=seaded()[0])
    return Response(json.dumps(j, ensure_ascii=False), 200, content_type="application/json")


# ---------------------------------------------------------------- andmefailid -> laoserver
def _sha(p):
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 16), b""):
            h.update(b)
    return h.hexdigest()


def _kohalikud_failid():
    out = list(FAILID)
    d = os.path.join(kaust(), "arved")
    try:
        out += ["arved/" + f for f in os.listdir(d)
                if f.lower().endswith(".csv") and not any(x in f.lower() for x in ("ostuajalugu", "hinnalogi"))]
    except OSError:
        pass
    return out


def _laadi_failid(sunni=False):
    """Saadab muutunud kataloogi/hinnad/arved laoserverisse (kõige rohkem kord 30 s jooksul)."""
    if not sunni and time.time() - OLEK["failid_aeg"] < 30:
        return
    if not _LUKK.acquire(blocking=False):
        return
    try:
        OLEK["failid_aeg"] = time.time()
        server = _kysi("GET", "/api/ladu/failid").json().get("failid") or {}
        for nimi in _kohalikud_failid():
            p = os.path.join(kaust(), *nimi.split("/"))
            if not os.path.isfile(p):
                continue
            m = os.path.getmtime(p)
            if not sunni and OLEK["laaditud"].get(nimi) == m:
                continue
            sha = _sha(p)
            if (server.get(nimi) or {}).get("sha") != sha:
                with open(p, "rb") as fh:
                    r = _kysi("PUT", "/api/ladu/fail", timeout=120, params={"nimi": nimi}, data=fh.read(),
                              headers={"Content-Type": "application/octet-stream"})
                if not r.ok:
                    raise RuntimeError(f"{nimi}: HTTP {r.status_code} {r.text[:200]}")
            OLEK["laaditud"][nimi] = m
        OLEK["failid_viga"] = ""
    except Exception as e:
        OLEK["failid_viga"] = str(e)
        print("  🧵 Laoserverisse failide saatmine ebaõnnestus:", e)
    finally:
        _LUKK.release()


# ---------------------------------------------------------------- üks kord: vanad andmed laoserverisse
def _import():
    try:
        olek = _kysi("GET", "/api/ladu/olek").json()
    except Exception as e:
        OLEK["import"] = f"laoserver pole kättesaadav: {e}"
        return
    if not olek.get("ok") or any((olek.get("read") or {}).values()):
        OLEK["import"] = "pole vaja (laoserveris on andmed juba olemas)"
        return
    with _db() as c:
        olemas = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        tab = {}
        for t in TABELID:
            if t in olemas:
                tab[t] = [{k: r[k] for k in r.keys() if k != "id"} for r in c.execute(f"SELECT * FROM {t} ORDER BY id")]
        dels = []
        if "sync_kustutatud" in olemas:
            dels = [dict(r) for r in c.execute("SELECT tabel, guid, ts FROM sync_kustutatud WHERE tabel LIKE 'filament_%'")]
    if not tab.get("filament_liikumine"):
        OLEK["import"] = "pole vaja (siin arvutis filamendi andmeid pole)"
        return
    r = _kysi("POST", "/api/ladu/import", timeout=120, json={"tabelid": tab, "kustutatud": dels})
    OLEK["import"] = f"tehtud: {r.text[:300]}"
    print("  🧵 Filamendi andmed viidi laoserverisse:", r.text[:300])


PILDI_LAIENDID = (".jpg", ".jpeg", ".png", ".webp")


def _laadi_pildid():
    """Saadab laoserverisse pildid, mida seal veel pole (või mille suurus erineb)."""
    base = os.path.join(kaust(), "pildid")
    server = _kysi("GET", "/api/ladu/pildid", timeout=60).json().get("pildid") or {}
    n = 0
    for root, _, files in os.walk(base):
        for f in files:
            p = os.path.join(root, f)
            rel = os.path.relpath(p, kaust()).replace(os.sep, "/")
            if not f.lower().endswith(PILDI_LAIENDID) or server.get(rel) == os.path.getsize(p):
                continue
            with open(p, "rb") as fh:
                r = _kysi("PUT", "/api/ladu/fail", timeout=120, params={"nimi": rel}, data=fh.read(),
                          headers={"Content-Type": "application/octet-stream"})
            if r.ok:
                n += 1
    OLEK["pildid"] = f"{n} uut pilti saadetud ({time.strftime('%H:%M')})"


def _taust():
    try:
        _import()
    except Exception as e:
        OLEK["import"] = f"viga: {e}"
        print("  🧵 Laoserverisse üleviimine ebaõnnestus:", e)
    _laadi_failid(sunni=True)
    while True:   # pildid: kohe ja siis kord tunnis
        try:
            _laadi_pildid()
        except Exception as e:
            OLEK["pildid"] = f"viga: {e}"
        time.sleep(3600)
