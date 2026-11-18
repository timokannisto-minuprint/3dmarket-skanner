# -*- coding: utf-8 -*-
"""
ladu.3dmarket.ee – 3dmarketi laoserver (filamendid, hiljem ka muud poe tooted).

Üks andmebaas (SQLite, fail andmed/ladu.db) ja üks API kõigile:
  - Postikeskkond (Timo arvutis) näitab 🧵 Filament vahekaarti, aga andmed elavad siin. Tema filament.py suunab
    kõik /api/filament/* päringud siia (päis X-Ladu-Voti).
  - Telefoni kaamera-skänner: https://ladu.3dmarket.ee/#k=<telefoni võti> (päis X-Voti), töötab igal pool, mitte
    ainult kohtvõrgus. HTTPS-i teeb veebimajutuse server.
  - Hiljem uus pood ja SimplBooksi arved.

Keskkonnamuutujad (PM2 ecosystem.config.js):
  LADU_VOTI        Postikeskkonna võti (pikk juhuslik)
  TEL_VOTI         telefoni võti (QR-koodis)
  LADU_ANDMED      andmekaust (vaikimisi ./andmed): ladu.db, kataloog.csv, skanner/*.json, arved/*.csv, pildid/
  LADU_URL         avalik aadress (vaikimisi https://ladu.3dmarket.ee)
  SMARTPRINT_USER / SMARTPRINT_PASS   tarnija B2B portaal (Teel -> Näita arveid)
  PORT             (vaikimisi 3000), ELKDATA_APP_IP = kuulamise aadress (veebimajutus annab ise)
"""
import datetime as dt, hashlib, json, os, secrets, sqlite3, sys, traceback

from flask import Flask, jsonify, request, send_from_directory, abort

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import filament  # noqa: E402

VERSION = "1 (18.11.2026)"
ANDMED = os.path.abspath(os.environ.get("LADU_ANDMED") or os.path.join(HERE, "andmed"))
DB_PATH = os.path.join(ANDMED, "ladu.db")
LADU_URL = (os.environ.get("LADU_URL") or "https://ladu.3dmarket.ee").rstrip("/")
LADU_VOTI = os.environ.get("LADU_VOTI", "")
TEL_VOTI = os.environ.get("TEL_VOTI", "")
FIL_TABELID = ("filament_liikumine", "filament_kood", "filament_tellimus", "filament_seade", "filament_tundmatu")
_NOW_TS = "((julianday('now') - 2440587.5) * 86400.0)"
# failid, mida Postikeskkond (või Claude) võib üles laadida
LUBATUD_FAILID = ("kataloog.csv", "skanner/tellimine.json", "skanner/hinnad_tana.json", "skanner/toonerid.json")

os.makedirs(os.path.join(ANDMED, "skanner"), exist_ok=True)
os.makedirs(os.path.join(ANDMED, "arved"), exist_ok=True)

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024


# ---------------------------------------------------------------- andmebaas
class _Yhendus(sqlite3.Connection):
    """with db() as c: ... -> commit/rollback ja sulgemine."""
    def __exit__(self, *a):
        try:
            return super().__exit__(*a)
        finally:
            self.close()


def db():
    c = sqlite3.connect(DB_PATH, timeout=30, factory=_Yhendus)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with db() as c:
        c.execute("PRAGMA journal_mode=WAL")
        filament.schema(c)
        c.execute("CREATE TABLE IF NOT EXISTS sync_kustutatud (tabel TEXT, guid TEXT, ts REAL, PRIMARY KEY(tabel, guid))")
        for t in FIL_TABELID:   # samad guid/sync_ts veerud ja triggerid mis Postikeskkonnas (andmed tulevad sealt üle)
            for col in ("guid TEXT", "sync_ts REAL"):
                try:
                    c.execute(f"ALTER TABLE {t} ADD COLUMN {col}")
                except sqlite3.OperationalError:
                    pass
            c.execute(f"CREATE INDEX IF NOT EXISTS {t}_guid ON {t}(guid)")
            c.execute(f"""CREATE TRIGGER IF NOT EXISTS {t}_s_ins AFTER INSERT ON {t} WHEN NEW.guid IS NULL OR NEW.sync_ts IS NULL
                BEGIN UPDATE {t} SET guid=coalesce(NEW.guid, lower(hex(randomblob(16)))), sync_ts=coalesce(NEW.sync_ts, {_NOW_TS}) WHERE id=NEW.id; END""")
            c.execute(f"""CREATE TRIGGER IF NOT EXISTS {t}_s_upd AFTER UPDATE ON {t} WHEN NEW.sync_ts IS OLD.sync_ts
                BEGIN UPDATE {t} SET sync_ts={_NOW_TS} WHERE id=NEW.id; END""")
            c.execute(f"""CREATE TRIGGER IF NOT EXISTS {t}_s_del AFTER DELETE ON {t} WHEN OLD.guid IS NOT NULL
                BEGIN INSERT OR REPLACE INTO sync_kustutatud(tabel, guid, ts) VALUES('{t}', OLD.guid, {_NOW_TS}); END""")
        c.execute("""CREATE TABLE IF NOT EXISTS ladu_logi (id INTEGER PRIMARY KEY AUTOINCREMENT, aeg TEXT, mis TEXT)""")


def ok(**kw):
    return jsonify({"ok": True, **kw})


def now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


filament.init(app, db, ok, now, {}, {"filament_kaust": ANDMED})
if TEL_VOTI:
    filament.TELEFON.update(skanner=f"{LADU_URL}/#k={TEL_VOTI}", juhend=f"{LADU_URL}/#k={TEL_VOTI}", ip=LADU_URL)


# ---------------------------------------------------------------- ligipääs
AVALIK = {"/", "/zxing.min.js", "/manifest.json", "/api/tervis"}
TELEFONI_TEED = {"/api/nimed", "/api/kanne", "/api/tagasi", "/api/tere"}


def _vordle(a, b):
    return bool(a) and bool(b) and secrets.compare_digest(a, b)


@app.before_request
def _luba():
    p = request.path
    if p in AVALIK:
        return
    if p in TELEFONI_TEED:
        if _vordle(request.headers.get("X-Voti", ""), TEL_VOTI) or _vordle(request.headers.get("X-Ladu-Voti", ""), LADU_VOTI):
            return
        abort(403, "Vale või puuduv võti. Ava Postikeskkonnas 🧵 Filament → 📱 Telefon ja skänni QR-kood uuesti.")
    if not _vordle(request.headers.get("X-Ladu-Voti", ""), LADU_VOTI):
        abort(403, "Ligipääs keelatud.")


@app.after_request
def _paised(r):
    r.headers.setdefault("Cache-Control", "no-store")
    r.headers["X-Content-Type-Options"] = "nosniff"
    return r


@app.errorhandler(Exception)
def _viga(e):
    code = getattr(e, "code", 500)
    code = code if isinstance(code, int) else 500
    if code >= 500:
        traceback.print_exc()
    msg = getattr(e, "description", None) if code < 500 and hasattr(e, "description") else str(e)
    return jsonify({"ok": False, "viga": msg or str(e)}), code


# ---------------------------------------------------------------- telefoni skänner (sama leht mis Postikeskkonna v107)
@app.get("/")
def tel_leht():
    return send_from_directory(os.path.join(HERE, "static"), "skanner.html", max_age=0)


@app.get("/zxing.min.js")
def tel_zxing():
    return send_from_directory(os.path.join(HERE, "static"), "zxing.min.js", mimetype="application/javascript", max_age=86400)


@app.get("/api/tervis")
def tervis():
    return ok(versioon=VERSION)


@app.get("/api/tere")
def tel_tere():
    return ok(arvuti="ladu.3dmarket.ee")


@app.get("/api/nimed")
def tel_nimed():
    k = filament.kataloog()
    nimed = {ean: t["nimi"] for ean, t in k["by_ean"].items()}
    with db() as c:
        for r in c.execute("SELECT kood, sku FROM filament_kood ORDER BY id"):
            t = k["by_sku"].get(r["sku"])
            if t:
                nimed[r["kood"]] = t["nimi"]
    return ok(nimed=nimed)


def _tel_guid(j):
    return "tel-" + "".join(ch for ch in str(j.get("g") or "") if ch.isalnum() or ch == "-")[:40]


def _aeg_kohalik(s):
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return filament._aeg()


@app.post("/api/kanne")
def tel_kanne():
    """Üks skänn telefonist. Korduv sama guid-iga saatmine on ohutu."""
    j = request.get_json(silent=True) or {}
    gid = _tel_guid(j)
    kood = filament._kood(j.get("kood"))
    tyyp = j.get("tyyp") if j.get("tyyp") in ("inventory", "receipt", "sale") else "inventory"
    try:
        kogus = max(1, min(999, int(j.get("kogus") or 1)))
    except (TypeError, ValueError):
        kogus = 1
    if len(gid) < 8 or not kood:
        abort(400, "Kood või id puudub.")
    with db() as c:
        r = c.execute("SELECT sku FROM filament_liikumine WHERE guid=?", (gid,)).fetchone()
        if r:
            t = filament.kataloog()["by_sku"].get(r["sku"], {})
            return ok(sku=r["sku"], nimi=t.get("nimi", r["sku"]), laos=filament._laoseis(c).get(r["sku"], 0), korduv=True)
        if c.execute("SELECT 1 FROM filament_tundmatu WHERE guid=?", (gid,)).fetchone():
            return ok(tundmatu=True, kood=kood, korduv=True)
        if c.execute("SELECT 1 FROM sync_kustutatud WHERE guid=? AND tabel IN ('filament_liikumine','filament_tundmatu')", (gid,)).fetchone():
            return ok(kustutatud=True, kood=kood)
        t = filament._leia(c, kood)
        aeg = _aeg_kohalik(j.get("aeg"))
        if not t:
            c.execute("INSERT INTO filament_tundmatu(kood, kogus, tyyp, aeg, guid) VALUES(?,?,?,?,?)", (kood, kogus, tyyp, aeg, gid))
            return ok(tundmatu=True, kood=kood)
        filament._lisa(c, t["sku"], kogus, tyyp, kood, "Telefon", aeg, "telefon", gid)
        return ok(sku=t["sku"], nimi=t["nimi"], laos=filament._laoseis(c).get(t["sku"], 0))


@app.post("/api/tagasi")
def tel_tagasi():
    gid = _tel_guid(request.get_json(silent=True) or {})
    with db() as c:
        n = c.execute("DELETE FROM filament_liikumine WHERE guid=?", (gid,)).rowcount
        n += c.execute("DELETE FROM filament_tundmatu WHERE guid=?", (gid,)).rowcount
    return ok(kustutatud=n)


# ---------------------------------------------------------------- andmefailid (kataloog, hinnad, arved) Postikeskkonnast
def _faili_tee(nimi):
    nimi = (nimi or "").replace("\\", "/").strip("/")
    ok_nimi = nimi in LUBATUD_FAILID or (nimi.startswith("arved/") and nimi.lower().endswith(".csv") and "/" not in nimi[6:]
                                         and ".." not in nimi and len(nimi) < 120)
    if not ok_nimi:
        abort(400, "Seda faili ei saa üles laadida.")
    return os.path.join(ANDMED, *nimi.split("/"))


def _sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 16), b""):
            h.update(b)
    return h.hexdigest()


@app.get("/api/ladu/failid")
def ladu_failid():
    out = {}
    for nimi in list(LUBATUD_FAILID) + ["arved/" + f for f in sorted(os.listdir(os.path.join(ANDMED, "arved")))]:
        p = os.path.join(ANDMED, *nimi.split("/"))
        if os.path.isfile(p):
            out[nimi] = {"sha": _sha(p), "suurus": os.path.getsize(p), "mtime": os.path.getmtime(p)}
    return ok(failid=out)


@app.put("/api/ladu/fail")
def ladu_fail():
    p = _faili_tee(request.args.get("nimi"))
    data = request.get_data()
    if not data:
        abort(400, "Fail on tühi.")
    tmp = p + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, p)
    _logi(f"fail {request.args.get('nimi')} {len(data)} B ({filament._arvuti()})")
    return ok(sha=_sha(p))


def _logi(mis):
    with db() as c:
        c.execute("INSERT INTO ladu_logi(aeg, mis) VALUES(?,?)", (filament._aeg(), mis))


# ---------------------------------------------------------------- ühekordne üleviimine Postikeskkonnast
@app.get("/api/ladu/olek")
def ladu_olek():
    with db() as c:
        n = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in FIL_TABELID}
        logi = [dict(r) for r in c.execute("SELECT aeg, mis FROM ladu_logi ORDER BY id DESC LIMIT 20")]
    k = filament.kataloog()
    return ok(versioon=VERSION, read=n, kataloog=len(k["tooted"]), kataloogi_viga=k["viga"], logi=logi)


@app.post("/api/ladu/import")
def ladu_import():
    """Postikeskkonna filament_* tabelid (kõik read guid-iga) + sync_kustutatud. Ainult tühja lattu, v.a. sunni=1."""
    j = request.get_json(silent=True) or {}
    tabelid = j.get("tabelid") or {}
    with db() as c:
        olemas = sum(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in FIL_TABELID)
        if olemas and not request.args.get("sunni"):
            abort(409, f"Laos on juba {olemas} rida, importi ei tehtud.")
        kokku = {}
        for t in FIL_TABELID:
            veerud = {r[1] for r in c.execute(f"PRAGMA table_info({t})")} - {"id"}
            n = 0
            for r in tabelid.get(t) or []:
                r = {k: v for k, v in r.items() if k in veerud}
                if not r.get("guid") or c.execute(f"SELECT 1 FROM {t} WHERE guid=?", (r["guid"],)).fetchone():
                    continue
                c.execute(f"INSERT INTO {t}({', '.join(r)}) VALUES({', '.join('?' * len(r))})", list(r.values()))
                n += 1
            kokku[t] = n
        for d in j.get("kustutatud") or []:
            if d.get("tabel") in FIL_TABELID and d.get("guid"):
                c.execute("INSERT OR IGNORE INTO sync_kustutatud(tabel, guid, ts) VALUES(?,?,?)", (d["tabel"], d["guid"], d.get("ts") or 0))
    _logi("import " + json.dumps(kokku))
    return ok(lisatud=kokku)


@app.get("/api/ladu/eksport")
def ladu_eksport():
    """Kogu laoandmebaas JSON-ina (varukoopia / tagasi Postikeskkonda)."""
    with db() as c:
        tab = {t: [{k: r[k] for k in r.keys() if k != "id"} for r in c.execute(f"SELECT * FROM {t} ORDER BY id")] for t in FIL_TABELID}
        dels = [dict(r) for r in c.execute("SELECT tabel, guid, ts FROM sync_kustutatud")]
    return ok(tabelid=tab, kustutatud=dels, aeg=filament._aeg())


init_db()

if __name__ == "__main__":
    host = os.environ.get("ELKDATA_APP_IP") or "127.0.0.1"
    port = int(os.environ.get("PORT") or 3000)
    if not LADU_VOTI:
        print("HOIATUS: LADU_VOTI puudub – Postikeskkond ei saa ühendust.")
    try:
        from waitress import serve
        print(f"ladu.3dmarket.ee v{VERSION} kuulab {host}:{port} (waitress), andmed {ANDMED}")
        serve(app, host=host, port=port, threads=8)
    except ImportError:
        print(f"ladu.3dmarket.ee v{VERSION} kuulab {host}:{port} (Flask), andmed {ANDMED}")
        app.run(host=host, port=port, threaded=True)
