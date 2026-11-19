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
  POOD_VOTI        e-poe võti (GET /api/pood/laoseis, POST /api/pood/myyk)
  LADU_ANDMED      andmekaust (vaikimisi ./andmed): ladu.db, kataloog.csv, skanner/*.json, arved/*.csv, pildid/
  LADU_PAROOL_RASI veebi sisselogimise parooli räsi (python app.py --parool)
  LADU_SALADUS     sessiooniküpsise allkirjavõti (vaikimisi tuletatakse LADU_VOTI-st)
  LADU_URL         avalik aadress (vaikimisi https://ladu.3dmarket.ee)
  SMARTPRINT_USER / SMARTPRINT_PASS   tarnija B2B portaal (Teel -> Näita arveid)
  PORT             (vaikimisi 3000), ELKDATA_APP_IP = kuulamise aadress (veebimajutus annab ise)
"""
import datetime as dt, hashlib, json, os, secrets, sqlite3, sys, threading, time, traceback

from flask import Flask, jsonify, request, send_from_directory, abort, session, redirect, Response

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import filament  # noqa: E402

VERSION = "1 (18.11.2026)"
ANDMED = os.path.abspath(os.environ.get("LADU_ANDMED") or os.path.join(HERE, "andmed"))
DB_PATH = os.path.join(ANDMED, "ladu.db")
LADU_URL = (os.environ.get("LADU_URL") or "https://ladu.3dmarket.ee").rstrip("/")
LADU_VOTI = os.environ.get("LADU_VOTI", "")
TEL_VOTI = os.environ.get("TEL_VOTI", "")
POOD_VOTI = os.environ.get("POOD_VOTI", "")   # e-pood: ainult laoseisu lugemine
FIL_TABELID = ("filament_liikumine", "filament_kood", "filament_tellimus", "filament_seade", "filament_tundmatu")
_NOW_TS = "((julianday('now') - 2440587.5) * 86400.0)"
# failid, mida Postikeskkond (või Claude) võib üles laadida
LUBATUD_FAILID = ("kataloog.csv", "skanner/tellimine.json", "skanner/hinnad_tana.json", "skanner/toonerid.json")

os.makedirs(os.path.join(ANDMED, "skanner"), exist_ok=True)
os.makedirs(os.path.join(ANDMED, "arved"), exist_ok=True)

app = Flask(__name__, static_folder=None)
app.config.update(
    MAX_CONTENT_LENGTH=32 * 1024 * 1024,
    SECRET_KEY=os.environ.get("LADU_SALADUS") or hashlib.sha256(("ladu-sessioon:" + LADU_VOTI).encode()).hexdigest(),
    SESSION_COOKIE_NAME="ladu", SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=LADU_URL.startswith("https"), PERMANENT_SESSION_LIFETIME=dt.timedelta(days=30))
PAROOL_RASI = os.environ.get("LADU_PAROOL_RASI", "")


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
    filament.TELEFON.update(skanner=f"{LADU_URL}/skanner#k={TEL_VOTI}", juhend=f"{LADU_URL}/skanner#k={TEL_VOTI}", ip=LADU_URL)


# ---------------------------------------------------------------- ligipääs
AVALIK = {"/", "/skanner", "/login", "/logout", "/zxing.min.js", "/api/tervis"}
TELEFONI_TEED = {"/api/nimed", "/api/kanne", "/api/tagasi", "/api/tere"}


def _vordle(a, b):
    return bool(a) and bool(b) and secrets.compare_digest(a, b)


@app.before_request
def _luba():
    p = request.path
    if p in AVALIK:
        return
    if (p == "/api/pood/laoseis" and request.method == "GET" or p == "/api/pood/myyk" and request.method == "POST") and _vordle(request.headers.get("X-Pood-Voti", ""), POOD_VOTI):
        return
    if session.get("sees"):   # veebis sisse loginud (laohalduse leht)
        return
    if p == "/seaded":
        return redirect("/login")
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


# ---------------------------------------------------------------- veebi laohaldus + sisselogimine
_KATSED = {}   # ip -> [aeg, ...] ebaõnnestunud sisselogimised
_KATSED_LUKK = threading.Lock()

LOGIN_HTML = """<!doctype html><html lang="et"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>3dmarket ladu</title><style>
:root{--bg:#f4f6f5;--fg:#17201d;--card:#fff;--line:#d9dfdc;--accent:#0f6b5c;--bad:#b3261e}
@media (prefers-color-scheme:dark){:root{--bg:#0b1110;--fg:#eef3f1;--card:#141c1a;--line:#26302e;--accent:#3fb59e;--bad:#ff7a66}}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--fg);font:16px/1.4 -apple-system,system-ui,"Segoe UI",sans-serif;padding:16px;box-sizing:border-box}
form{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:28px;width:100%;max-width:340px;box-sizing:border-box}
h1{font-size:20px;margin:0 0 4px}p{margin:0 0 18px;opacity:.7;font-size:14px}
input{width:100%;box-sizing:border-box;padding:12px;border:1px solid var(--line);border-radius:10px;background:transparent;color:inherit;font:inherit;margin-bottom:12px}
button{width:100%;padding:12px;border:0;border-radius:10px;background:var(--accent);color:#fff;font:600 16px inherit;cursor:pointer}
.v{color:var(--bad);font-size:14px;margin:0 0 12px}</style></head><body>
<form method="post" action="/login"><h1>3dmarket ladu</h1><p>Filamentide laohaldus</p>%VIGA%
<input type="password" name="parool" placeholder="Parool" autofocus autocomplete="current-password" required>
<button>Logi sisse</button></form></body></html>"""


def _ip():
    return (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()


SEADED_HTML = LOGIN_HTML.split("</style>")[0] + """
form{max-width:420px}label{display:block;font-size:13px;font-weight:600;margin:0 0 4px}.ok{color:var(--accent);font-size:14px;margin:0 0 12px}
a{color:var(--accent)}.n{font-size:13px;opacity:.7;margin:14px 0 0}</style></head><body>
<form method="post" action="/seaded" autocomplete="off"><h1>⚙ Seaded</h1><p>Tarnija ühendus (Smartprint24 B2B). Seda kasutavad Teel-vaate arved ja tarnija kontroll, ka Postikeskkonna kaudu.</p>%TEADE%
<label for="k">Smartprinti kasutaja</label><input id="k" name="kasutaja" value="%KASUTAJA%" autocomplete="off" required>
<label for="p">Smartprinti parool</label><input id="p" type="password" name="parool" placeholder="%PH%" autocomplete="new-password">
<button>Salvesta ja proovi ühendust</button>
<p class="n">Parool hoitakse ainult laoserveris (ei lähe brauserisse tagasi). Tühjaks jäetud parooliväli jätab vana parooli alles.<br><a href="/">← Tagasi lattu</a></p></form></body></html>"""


def _tarnija_fail():
    return os.path.join(ANDMED, "tarnija.json")


def _tarnija():
    try:
        with open(_tarnija_fail(), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


@app.route("/seaded", methods=["GET", "POST"])
def seaded():
    import html as _h
    t, teade = _tarnija(), ""
    if request.method == "POST":
        k = request.form.get("kasutaja", "").strip()
        p = request.form.get("parool", "")
        t["smartprint_kasutaja"] = k
        if p.strip():
            t["smartprint_parool"] = p.strip()
        f = _tarnija_fail()
        with open(f + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(t, fh, ensure_ascii=False)
        os.chmod(f + ".tmp", 0o600)
        os.replace(f + ".tmp", f)
        _logi("tarnija seaded muudetud (veeb)")
        try:
            filament.sp_sessioon()
            teade = '<div class="ok">✓ Salvestatud. Ühendus Smartprintiga töötab.</div>'
        except Exception as e:
            teade = f'<div class="v">Salvestatud, aga ühendus ei õnnestunud: {_h.escape(str(e))}</div>'
    html = (SEADED_HTML.replace("%TEADE%", teade)
            .replace("%KASUTAJA%", _h.escape(t.get("smartprint_kasutaja", ""), quote=True))
            .replace("%PH%", "salvestatud (jäta tühjaks, kui ei muuda)" if t.get("smartprint_parool") else "parool"))
    return Response(html, content_type="text/html; charset=utf-8")


@app.get("/")
def leht():
    if not session.get("sees"):
        return redirect("/login")
    return send_from_directory(os.path.join(HERE, "static"), "ladu.html", max_age=0)


@app.route("/login", methods=["GET", "POST"])
def login():
    viga = ""
    if request.method == "POST":
        from werkzeug.security import check_password_hash
        ip, nyyd = _ip(), time.time()
        with _KATSED_LUKK:
            k = [t for t in _KATSED.get(ip, []) if nyyd - t < 900]
            _KATSED[ip] = k
        if len(k) >= 10:
            viga = "Liiga palju valesid katseid. Proovi 15 minuti pärast uuesti."
        elif PAROOL_RASI and check_password_hash(PAROOL_RASI, request.form.get("parool", "")):
            session.clear()
            session.permanent = True
            session["sees"] = True
            _logi(f"veebi sisselogimine {ip}")
            return redirect("/")
        else:
            time.sleep(1)
            with _KATSED_LUKK:
                _KATSED.setdefault(ip, []).append(nyyd)
            viga = "Vale parool." if PAROOL_RASI else "Parool on serveris seadistamata."
    html = LOGIN_HTML.replace("%VIGA%", f'<div class="v">{viga}</div>' if viga else "")
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.get("/logout")
def logout():
    session.clear()
    return redirect("/login")


# ---------------------------------------------------------------- telefoni skänner (sama leht mis Postikeskkonna v107)
@app.get("/skanner")
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


# ---------------------------------------------------------------- ülevaade, eksport, varukoopia
@app.get("/api/ladu/ylevaade")
def ladu_ylevaade():
    """Ülevaate-lehe arvud: kuude kaupa liikumised (12 kuud), müügi top, seisev kaup, laoväärtus."""
    k = filament.kataloog()["by_sku"]
    tana = dt.date.today()
    alg = (tana.replace(day=1) - dt.timedelta(days=335)).replace(day=1).isoformat()
    p30, p90 = (tana - dt.timedelta(days=30)).isoformat(), (tana - dt.timedelta(days=90)).isoformat()
    with db() as c:
        laos = filament._laoseis(c)
        kuud = {}
        for r in c.execute("SELECT substr(aeg,1,7) m, tyyp, SUM(ABS(kogus)) q FROM filament_liikumine WHERE aeg>=? GROUP BY m, tyyp", (alg,)):
            kuud.setdefault(r["m"], {})[r["tyyp"]] = r["q"]
        myyk30 = {r["sku"]: r["q"] for r in c.execute(
            "SELECT sku, SUM(-kogus) q FROM filament_liikumine WHERE tyyp='sale' AND aeg>=? GROUP BY sku", (p30,))}
        myyk90 = {r["sku"]: r["q"] for r in c.execute(
            "SELECT sku, SUM(-kogus) q FROM filament_liikumine WHERE tyyp='sale' AND aeg>=? GROUP BY sku", (p90,))}
        viimane = {r["sku"]: r["a"] for r in c.execute(
            "SELECT sku, MAX(aeg) a FROM filament_liikumine WHERE tyyp IN ('sale','receipt') GROUP BY sku")}
        tana_n = c.execute("SELECT COUNT(*) n, COALESCE(SUM(CASE WHEN tyyp='sale' THEN -kogus ELSE 0 END),0) v FROM filament_liikumine WHERE aeg>=?",
                           (tana.isoformat(),)).fetchone()
    hind = lambda s: (k.get(s) or {}).get("hind") or 0
    m = []
    d = tana.replace(day=1)
    for _ in range(12):
        key = d.strftime("%Y-%m")
        m.append({"kuu": key, **{t: kuud.get(key, {}).get(t, 0) for t in ("sale", "receipt", "inventory", "adjust")}})
        d = (d - dt.timedelta(days=1)).replace(day=1)
    m.reverse()
    top = sorted(myyk30.items(), key=lambda x: -x[1])[:10]
    seisev = sorted([(s, q) for s, q in laos.items() if q > 0 and not myyk90.get(s) and s in k],
                    key=lambda x: -x[1] * hind(x[0]))[:15]
    return ok(
        tooteid=sum(1 for v in laos.values() if v > 0), rulle=sum(v for v in laos.values() if v > 0),
        vaartus=round(sum(v * hind(s) for s, v in laos.items() if v > 0), 2),
        negatiivsed=[{"sku": s, "laos": q} for s, q in laos.items() if q < 0][:20],
        myyk30=sum(myyk30.values()), myyk30_eur=round(sum(q * hind(s) for s, q in myyk30.items()), 2),
        myyk90=sum(myyk90.values()), kuud=m, tana_kandeid=tana_n["n"], tana_myyk=tana_n["v"],
        top=[{"sku": s, "q": q, "laos": laos.get(s, 0)} for s, q in top],
        seisev=[{"sku": s, "laos": q, "vaartus": round(q * hind(s), 2), "viimane": viimane.get(s, "")} for s, q in seisev])


@app.get("/api/ladu/laoseis.csv")
def ladu_laoseis_csv():
    """Laoseis CSV-na (Excel, raamatupidamine, inventuuri aruanne). ?kuupaev=YYYY-MM-DD -> seis selle päeva lõpu seisuga."""
    import csv, io
    kp = (request.args.get("kuupaev") or "").strip()[:10]
    k = filament.kataloog()
    with db() as c:
        if kp:
            laos = {r["sku"]: r["s"] for r in c.execute(
                "SELECT sku, SUM(kogus) s FROM filament_liikumine WHERE substr(aeg,1,10)<=? GROUP BY sku", (kp,))}
        else:
            laos = filament._laoseis(c)
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["SKU", "EAN", "Nimetus", "Tootja", "Materjal", "Värv", "Kogus", "Ostuhind €", "Väärtus €"])
    kokku = 0.0
    for t in k["tooted"]:
        q = laos.get(t["sku"], 0)
        if not q:
            continue
        v = round(q * (t["hind"] or 0), 2)
        kokku += v
        w.writerow([t["sku"], t["ean"], t["nimi"], t["tootja"], t["mat"], t["varv"], q,
                    str(t["hind"] or "").replace(".", ","), str(v).replace(".", ",")])
    w.writerow(["", "", "KOKKU", "", "", "", sum(v for v in laos.values() if v), "", str(round(kokku, 2)).replace(".", ",")])
    nimi = f"laoseis-{kp or dt.date.today().isoformat()}.csv"
    return ("\ufeff" + out.getvalue()), 200, {"Content-Type": "text/csv; charset=utf-8",
                                              "Content-Disposition": f'attachment; filename="{nimi}"'}


def _varukoopia_tsykkel():
    """Iga päev üks koopia andmed/varu/ladu-YYYY-MM-DD.db (SQLite backup API), alles 30 viimast."""
    varu = os.path.join(ANDMED, "varu")
    os.makedirs(varu, exist_ok=True)
    while True:
        try:
            siht = os.path.join(varu, f"ladu-{dt.date.today().isoformat()}.db")
            if not os.path.exists(siht):
                src = sqlite3.connect(DB_PATH)
                dst = sqlite3.connect(siht)
                with dst:
                    src.backup(dst)
                src.close(); dst.close()
                for f in sorted(x for x in os.listdir(varu) if x.startswith("ladu-") and x.endswith(".db"))[:-30]:
                    os.remove(os.path.join(varu, f))
        except Exception as e:
            print("Varukoopia viga:", e)
        time.sleep(3600)


# ---------------------------------------------------------------- e-pood (ainult lugemine)
@app.get("/api/pood/laoseis")
def pood_laoseis():
    """Laoseis poele: {sku: {laos, teel, eta}} + aeg. Ainult tooted, millel on laos või teel midagi
    (?koik=1 -> kõik kataloogi tooted). teel/eta tulevad teel olevatest tellimustest."""
    k = filament.kataloog()
    with db() as c:
        laos = filament._laoseis(c)
        teel, eta = {}, {}
        for r in c.execute("SELECT read, eta FROM filament_tellimus WHERE staatus='ordered'"):
            try:
                read = json.loads(r["read"] or "{}")
            except ValueError:
                read = {}
            for sku, q in read.items():
                teel[sku] = teel.get(sku, 0) + int(q or 0)
                if r["eta"] and (sku not in eta or r["eta"] < eta[sku]):
                    eta[sku] = r["eta"]
    skud = k["by_sku"].keys() if request.args.get("koik") else set(laos) | set(teel)
    out = {s: {"laos": max(0, laos.get(s, 0)), "teel": teel.get(s, 0), "eta": eta.get(s, "")}
           for s in skud if s in k["by_sku"]}
    return ok(laoseis=out, aeg=filament._aeg())


@app.post("/api/pood/myyk")
def pood_myyk():
    """Makstud poetellimus -> 'Välja' kanded. {"tellimus": "1234", "read": {"FG-P1-E1": 2}}.
    Korduv sama tellimuse numbriga saatmine on ohutu (guid pood-<tellimus>-<sku>)."""
    j = request.get_json(silent=True) or {}
    nr = "".join(ch for ch in str(j.get("tellimus") or "") if ch.isalnum() or ch in "-_")[:40]
    read = j.get("read") or {}
    if not nr or not isinstance(read, dict) or not read:
        abort(400, "Tellimuse number või read puuduvad.")
    k = filament.kataloog()["by_sku"]
    tundmatud = [s for s in read if s not in k]
    if tundmatud:
        abort(400, "Tundmatud tooted: " + ", ".join(map(str, tundmatud[:10])))
    lisatud, olemas = {}, {}
    with db() as c:
        for sku, q in read.items():
            try:
                q = int(q)
            except (TypeError, ValueError):
                abort(400, f"Vigane kogus: {sku}")
            if q <= 0:
                continue
            g = f"pood-{nr}-{sku}"
            if c.execute("SELECT 1 FROM filament_liikumine WHERE guid=?", (g,)).fetchone():
                olemas[sku] = q
                continue
            filament._lisa(c, sku, q, "sale", k[sku].get("ean") or "", f"E-pood tellimus {nr}", None, "pood", g)
            lisatud[sku] = q
        laos = filament._laoseis(c)
    return ok(tellimus=nr, lisatud=lisatud, juba_olemas=olemas, laos={s: laos.get(s, 0) for s in read})


# ---------------------------------------------------------------- andmefailid (kataloog, hinnad, arved) Postikeskkonnast
PILDI_LAIENDID = (".jpg", ".jpeg", ".png", ".webp")


def _pilt_ok(nimi):
    osad = nimi.split("/")
    return (osad[0] == "pildid" and 2 <= len(osad) <= 3 and nimi.lower().endswith(PILDI_LAIENDID)
            and all(o and o not in (".", "..") and all(ch.isalnum() or ch in "-_." for ch in o) for o in osad))


def _faili_tee(nimi):
    nimi = (nimi or "").replace("\\", "/").strip("/")
    ok_nimi = nimi in LUBATUD_FAILID or (nimi.startswith("arved/") and nimi.lower().endswith(".csv") and "/" not in nimi[6:]
                                         and ".." not in nimi and len(nimi) < 120) or _pilt_ok(nimi)
    if not ok_nimi:
        abort(400, "Seda faili ei saa üles laadida.")
    p = os.path.join(ANDMED, *nimi.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


@app.get("/api/ladu/pildid")
def ladu_pildid():
    """Serveris olevad tootepildid: suhteline tee -> suurus (Postikeskkond laeb puuduvad üles)."""
    base = os.path.join(ANDMED, "pildid")
    out = {}
    for root, _, files in os.walk(base):
        for f in files:
            if f.lower().endswith(PILDI_LAIENDID):
                p = os.path.join(root, f)
                out[os.path.relpath(p, ANDMED).replace(os.sep, "/")] = os.path.getsize(p)
    return ok(pildid=out)


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
    if not _pilt_ok(request.args.get("nimi") or ""):
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

if __name__ == "__main__" and "--parool" not in sys.argv:
    threading.Thread(target=_varukoopia_tsykkel, daemon=True, name="varu").start()

if __name__ == "__main__" and "--parool" in sys.argv:   # python app.py --parool  -> prindib räsi LADU_PAROOL_RASI jaoks
    import getpass
    from werkzeug.security import generate_password_hash
    print(generate_password_hash(getpass.getpass("Uus parool: ")))
    sys.exit(0)

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
