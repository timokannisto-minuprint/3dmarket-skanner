# -*- coding: utf-8 -*-
"""
filament.py – 3dmarket filamendi ladu. ladu.3dmarket.ee serveri versioon (app.py), algselt Postikeskkonna
vahekaart 🧵 Filament (v82–v107). Postikeskkond on nüüd ainult klient: tema filament.py suunab /api/filament/* päringud siia.

- Kataloog loetakse failist <filament_kaust>/kataloog.csv (Dropbox "3dmarket claude"), pildid samast kaustast pildid/.
- Laoseis = liikumiste summa (tabel filament_liikumine, iga kanne oma rida). Nii ei lähe kahe arvuti muudatused kaotsi.
- Õpitud triipkoodid (Eryone jt, millel kataloogis EAN-i pole) tabelis filament_kood.
- Mõlemad tabelid sünkroonitakse PC <-> Mac (SYNC_TABLES), ridu seotakse guid-iga.
- Liikumise tüübid on samad mis laoskänneris: inventory (inventuur), receipt (sisse), sale (välja), adjust (parandus).
  kogus on märgiga: välja = negatiivne.
- v107 telefoniskänner: filament_tel.py (HTTPS kohtvõrgus, port 8766), tundmatud koodid tabelis filament_tundmatu.
- v85 tellimine: tabelid filament_tellimus (mustand guid 'draft-sp', tellimused guid 'ord-<nimi>') ja filament_seade
  (guid 'set-<võti>': 'mm:<sku>' min/siht, 'pref:<sku>' eelista Smart Printi, 'reeglid' low/cover). Ostusoovituse arvutus käib
  brauseris (sama loogika mis laoskänneris). Andmefailid <filament_kaust>/skanner/: tellimine.json (ostuajalugu, kuukulu),
  hinnad_tana.json (praegused hinnad, saadavus), toonerid.json – need uuendab Claude.
"""
import csv, datetime as dt, json, os, sys, threading

from flask import Blueprint, request, send_file, abort

bp = Blueprint("filament", __name__)
_db = _ok = _now = None
_K = {}
IS_WIN = sys.platform.startswith("win")
TYYBID = {"inventory": "Inventuur", "receipt": "Sisse", "sale": "Välja", "adjust": "Parandus"}
_KAT = {"mtime": None, "tooted": [], "by_sku": {}, "by_ean": {}, "viga": ""}
_KAT_LUKK = threading.Lock()


def init(app, db, ok, now, sync_tables, K=None):
    global _db, _ok, _now, _K
    _db, _ok, _now, _K = db, ok, now, (K or {})
    sync_tables["filament_liikumine"] = set()   # sünkrooni PC <-> Mac
    sync_tables["filament_kood"] = set()
    sync_tables["filament_tellimus"] = set()
    sync_tables["filament_seade"] = set()
    sync_tables["filament_tundmatu"] = set()   # v107: telefonist tulnud tundmatud koodid (ootavad sidumist)
    app.register_blueprint(bp)


def schema(c):   # kutsutakse init_db() seest, enne _sync_schema(c)
    c.execute("""CREATE TABLE IF NOT EXISTS filament_liikumine (
        id INTEGER PRIMARY KEY AUTOINCREMENT, sku TEXT, kood TEXT, kogus INTEGER, tyyp TEXT,
        markus TEXT, aeg TEXT, allikas TEXT, arvuti TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS filament_kood (
        id INTEGER PRIMARY KEY AUTOINCREMENT, kood TEXT, sku TEXT, aeg TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS fl_sku ON filament_liikumine(sku)")
    c.execute("""CREATE TABLE IF NOT EXISTS filament_tellimus (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nimi TEXT, staatus TEXT, read TEXT, loodud TEXT, eta TEXT,
        kogus INTEGER, vaartus REAL, arve TEXT, saabus TEXT, saabus_kogus INTEGER, muudetud TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS filament_seade (
        id INTEGER PRIMARY KEY AUTOINCREMENT, voti TEXT, vaartus TEXT, muudetud TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS filament_tundmatu (
        id INTEGER PRIMARY KEY AUTOINCREMENT, kood TEXT, kogus INTEGER, tyyp TEXT, aeg TEXT)""")


# ---------------------------------------------------------------- abi
def kaust():
    k = (_K.get("filament_kaust", "") or "").strip()
    if k:
        return os.path.abspath(os.path.expanduser(k))
    return r"E:\DropBox\Dropbox\3dmarket claude" if IS_WIN else os.path.expanduser("~/Dropbox/3dmarket claude")


def _aeg():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _arvuti():
    """Kes kande tegi: Postikeskkond saadab oma arvuti nime päises X-Arvuti, telefon -> 'telefon'."""
    try:
        a = (request.headers.get("X-Arvuti") or "").strip()[:40]
        if a:
            return a
        if request.headers.get("X-Voti"):
            return "telefon"
    except RuntimeError:   # väljaspool päringut
        pass
    return "ladu"


def _num(s):
    try:
        return float(str(s).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _kood(s):
    s = "".join(ch for ch in str(s or "") if ch.isalnum()).upper()
    if s.isdigit() and len(s) == 12:
        s = "0" + s   # UPC-A -> EAN-13
    return s


def kataloog():
    """Loeb kataloog.csv (vahemälu, laeb uuesti, kui fail muutus)."""
    p = os.path.join(kaust(), "kataloog.csv")
    try:
        m = os.path.getmtime(p)
    except OSError:
        _KAT["viga"] = f"Kataloogi ei leitud: {p}"
        return _KAT
    with _KAT_LUKK:
        if _KAT["mtime"] == m:
            return _KAT
        tooted, by_sku, by_ean = [], {}, {}
        with open(p, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                sku = (r.get("sku") or "").strip()
                if not sku:
                    continue
                pilt = ((r.get("pildid") or "").split("|")[0] or "").strip()
                t = {"sku": sku, "ean": _kood(r.get("ean")), "nimi": (r.get("nimetus") or "").strip(),
                     "tootja": (r.get("tootja") or "").strip(), "sari": (r.get("tootesari") or "").strip(),
                     "mat": (r.get("materjal") or "").strip(), "varv": (r.get("varv") or "").strip(),
                     "g": _num(r.get("netokaal_g")), "hind": _num(r.get("ostuhind_eur")), "d": (r.get("labimoot_mm") or "").strip(),
                     "saadav": (r.get("tarnija_saadavus") or "").strip(), "pilt": pilt,
                     "tid": (r.get("tarnija_id") or "").strip()}
                tooted.append(t)
                by_sku[sku] = t
                if t["ean"]:
                    by_ean[t["ean"]] = t
        _KAT.update(mtime=m, tooted=tooted, by_sku=by_sku, by_ean=by_ean, viga="")
    return _KAT


def _laoseis(c):
    return {r["sku"]: r["s"] for r in c.execute("SELECT sku, SUM(kogus) s FROM filament_liikumine GROUP BY sku")}


def _leia(c, kood):
    kood = _kood(kood)
    if not kood:
        return None
    k = kataloog()
    if kood in k["by_ean"]:
        return k["by_ean"][kood]
    if kood in k["by_sku"]:
        return k["by_sku"][kood]
    r = c.execute("SELECT sku FROM filament_kood WHERE kood=? ORDER BY id DESC LIMIT 1", (kood,)).fetchone()
    return k["by_sku"].get(r["sku"]) if r else None


def _lisa(c, sku, kogus, tyyp, kood="", markus="", aeg=None, allikas="", guid=None):
    if tyyp not in TYYBID:
        raise RuntimeError("Tundmatu kande tüüp.")
    kogus = int(kogus)
    if tyyp == "sale":
        kogus = -abs(kogus)
    elif tyyp in ("inventory", "receipt"):
        kogus = abs(kogus)
    if not kogus:
        raise RuntimeError("Kogus on 0.")
    c.execute("INSERT INTO filament_liikumine(sku, kood, kogus, tyyp, markus, aeg, allikas, arvuti, guid) VALUES(?,?,?,?,?,?,?,?,?)",
              (sku, kood or "", kogus, tyyp, markus or "", aeg or _aeg(), allikas or "", _arvuti(), guid))
    return kogus


def _row(r):
    d = dict(r)
    return {k: d.get(k) for k in ("guid", "sku", "kood", "kogus", "tyyp", "markus", "aeg", "arvuti")}


# ---------------------------------------------------------------- API
@bp.get("/api/filament/kataloog")
def filament_kataloog():
    k = kataloog()
    if k["viga"] and not k["tooted"]:
        raise RuntimeError(k["viga"])
    return _ok(tooted=k["tooted"], kaust=kaust())


@bp.get("/api/filament")
def filament_seis():
    """Laoseis (sku -> kogus), viimased kanded ja õpitud koodid."""
    with _db() as c:
        laos = _laoseis(c)
        liik = [_row(r) for r in c.execute("SELECT * FROM filament_liikumine ORDER BY aeg DESC, id DESC LIMIT 40")]
        koodid = {r["kood"]: r["sku"] for r in c.execute("SELECT kood, sku FROM filament_kood ORDER BY id")}
    k = kataloog()
    rulle = sum(v for v in laos.values() if v > 0)
    vaartus = sum(v * (k["by_sku"].get(s, {}).get("hind") or 0) for s, v in laos.items() if v > 0)
    return _ok(laos=laos, liikumised=liik, koodid=koodid, tooteid=sum(1 for v in laos.values() if v > 0),
               rulle=rulle, vaartus=round(vaartus, 2), kataloogi_viga=k["viga"], kataloog_mtime=k["mtime"])


@bp.post("/api/filament/kanne")
def filament_kanne():
    j = request.get_json() or {}
    sku = (j.get("sku") or "").strip()
    if sku not in kataloog()["by_sku"]:
        raise RuntimeError("Toodet pole kataloogis.")
    with _db() as c:
        q = _lisa(c, sku, j.get("kogus") or 1, j.get("tyyp") or "inventory", j.get("kood"), j.get("markus"))
        seis = _laoseis(c).get(sku, 0)
    return _ok(kogus=q, laos=seis)


@bp.post("/api/filament/skanni")
def filament_skanni():
    """Triipkood (EAN, SKU või õpitud kood) -> kanne. Tundmatu koodi korral tundmatu=True (leht pakub sidumist)."""
    j = request.get_json() or {}
    kood = _kood(j.get("kood"))
    if not kood:
        raise RuntimeError("Kood on tühi.")
    with _db() as c:
        t = _leia(c, kood)
        if not t:
            return _ok(tundmatu=True, kood=kood)
        q = _lisa(c, t["sku"], j.get("kogus") or 1, j.get("tyyp") or "inventory", kood, "Skänner")
        seis = _laoseis(c).get(t["sku"], 0)
    return _ok(sku=t["sku"], nimi=t["nimi"], kogus=q, laos=seis)


@bp.post("/api/filament/seo")
def filament_seo():
    """Seob tundmatu triipkoodi kataloogi tootega (õpitud kood) ja soovi korral lisab kohe kande."""
    j = request.get_json() or {}
    kood, sku = _kood(j.get("kood")), (j.get("sku") or "").strip()
    if not kood or sku not in kataloog()["by_sku"]:
        raise RuntimeError("Kood või toode puudub.")
    with _db() as c:
        c.execute("DELETE FROM filament_kood WHERE kood=?", (kood,))
        c.execute("INSERT INTO filament_kood(kood, sku, aeg) VALUES(?,?,?)", (kood, sku, _aeg()))
        q = 0
        if j.get("tyyp"):
            q = _lisa(c, sku, j.get("kogus") or 1, j["tyyp"], kood, "Skänner")
        ootel = _ootel_lattu(c, kood, sku)   # v107: telefonist ootel olnud skännid selle koodiga
        seis = _laoseis(c).get(sku, 0)
    return _ok(kogus=q, laos=seis, ootel=ootel)


def _ootel_lattu(c, kood, sku):
    """Telefonist tulnud tundmatu koodi skännid -> kanded (sama guid, nii et telefoni korduv saatmine ei dubleeri)."""
    n = 0
    for r in c.execute("SELECT * FROM filament_tundmatu WHERE kood=? ORDER BY id", (kood,)).fetchall():
        g = r["guid"] or None
        c.execute("DELETE FROM filament_tundmatu WHERE id=?", (r["id"],))
        if g:   # kustutamine kirjutab guid-i sync_kustutatud tabelisse – eemalda, et kanne saaks sama guid-i
            c.execute("DELETE FROM sync_kustutatud WHERE tabel='filament_tundmatu' AND guid=?", (g,))
        n += abs(_lisa(c, sku, r["kogus"] or 1, r["tyyp"] or "inventory", kood, "Telefon (seotud hiljem)", r["aeg"], "telefon", g))
    return n


# ---------------------------------------------------------------- v107 telefon
TELEFON = {}   # app.py täidab: {"skanner": "https://ladu.3dmarket.ee/#k=…", "juhend": …}


@bp.get("/api/filament/telefon")
def filament_telefon():
    with _db() as c:
        tund = [dict(kood=r["kood"], kogus=r["s"], n=r["n"], aeg=r["a"], tyyp=r["t"]) for r in c.execute(
            "SELECT kood, SUM(kogus) s, COUNT(*) n, MAX(aeg) a, GROUP_CONCAT(DISTINCT tyyp) t FROM filament_tundmatu GROUP BY kood ORDER BY a DESC")]
    return _ok(tootab=bool(TELEFON), viga="" if TELEFON else "Telefoni võti puudub serveris.", tundmatud=tund, pilv=True, **TELEFON)


@bp.delete("/api/filament/tundmatu/<path:kood>")
def filament_tundmatu_kustuta(kood):
    with _db() as c:
        n = c.execute("DELETE FROM filament_tundmatu WHERE kood=?", (_kood(kood),)).rowcount
    return _ok(kustutatud=n)


@bp.get("/api/filament/muutus")
def filament_muutus():
    """Väike päring, mida Filament-vaade küsib iga paari sekundi tagant (telefoni skännid ilmuvad ise)."""
    with _db() as c:
        a = c.execute("SELECT COUNT(*) n, MAX(id) m FROM filament_liikumine").fetchone()
        b = c.execute("SELECT COUNT(*) n FROM filament_tundmatu").fetchone()
    return _ok(sig=f"{a['n']}-{a['m']}-{b['n']}", tundmatuid=b["n"])


@bp.post("/api/filament/maara")
def filament_maara():
    """Paneb toote laoseisu täpselt antud arvuks (parandus-kanne vahega)."""
    j = request.get_json() or {}
    sku = (j.get("sku") or "").strip()
    if sku not in kataloog()["by_sku"]:
        raise RuntimeError("Toodet pole kataloogis.")
    try:
        uus = int(j.get("uus"))
    except (TypeError, ValueError):
        raise RuntimeError("Sisesta täisarv.")
    with _db() as c:
        vahe = uus - _laoseis(c).get(sku, 0)
        if vahe:
            _lisa(c, sku, vahe, "adjust", "", j.get("markus") or "Käsitsi parandus")
    return _ok(laos=uus, vahe=vahe)


@bp.delete("/api/filament/kanne/<guid>")
def filament_kustuta(guid):
    with _db() as c:
        n = c.execute("DELETE FROM filament_liikumine WHERE guid=?", (guid,)).rowcount
    if not n:
        raise RuntimeError("Kannet ei leitud.")
    return _ok()


@bp.get("/api/filament/ajalugu")
def filament_ajalugu():
    sku = request.args.get("sku", "")
    with _db() as c:
        rows = [_row(r) for r in c.execute("SELECT * FROM filament_liikumine WHERE sku=? ORDER BY aeg DESC, id DESC LIMIT 200", (sku,))]
        koodid = [r["kood"] for r in c.execute("SELECT kood FROM filament_kood WHERE sku=?", (sku,))]
    return _ok(liikumised=rows, koodid=koodid)


@bp.post("/api/filament/impordi")
def filament_impordi():
    """Toob laoskänneri (claude.ai leht) kanded failist <filament_kaust>/laoskanner_eksport.json.
    Iga kanne tuuakse üks kord (guid = 'ls-<kande id>', sama mõlemas arvutis), nii et korduv import on ohutu
    ja kustutatud kanne ei tule tagasi."""
    p = os.path.join(kaust(), "laoskanner_eksport.json")
    if not os.path.exists(p):
        raise RuntimeError(f"Faili pole: {p}. Palu Claude'il laoskänneri eksport teha.")
    with open(p, encoding="utf-8") as fh:
        data = json.load(fh)
    k = kataloog()
    lisatud = vahele = 0
    with _db() as c:
        olemas = {r["guid"] for r in c.execute("SELECT guid FROM filament_liikumine WHERE guid LIKE 'ls-%'")}
        olemas |= {r["guid"] for r in c.execute("SELECT guid FROM sync_kustutatud WHERE tabel='filament_liikumine'")}
        for m in data.get("movements", []):
            src = "ls-" + str(m.get("id"))
            sku = m.get("product_id")
            if src in olemas or sku not in k["by_sku"] or not m.get("qty"):
                vahele += 1
                continue
            aeg = m.get("created_at") or ""
            try:
                aeg = dt.datetime.fromisoformat(aeg.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                aeg = _aeg()
            tyyp = m.get("type") if m.get("type") in TYYBID else "adjust"
            _lisa(c, sku, m["qty"], tyyp, m.get("ean") or "", ("Laoskänner: " + (m.get("note") or "")).strip(": "), aeg, "laoskänner", src)
            lisatud += 1
        tell = 0
        for oid, o in (data.get("orders") or {}).items():
            g = "draft-sp" if oid == "draft-sp" else "ord-" + oid
            if c.execute("SELECT 1 FROM filament_tellimus WHERE guid=?", (g,)).fetchone() or g in olemas_t(c):
                continue
            _upsert(c, "filament_tellimus", g, nimi=oid, staatus=o.get("status") or "ordered", read=json.dumps(o.get("lines") or {}),
                    loodud=_iso_local(o.get("created_at") or o.get("updated")), eta=o.get("eta") or "", kogus=o.get("qty") or sum((o.get("lines") or {}).values()),
                    vaartus=o.get("value") or 0, arve=o.get("invoice") or "", muudetud=_aeg())
            tell += 1
        for kood, sku in (data.get("barcodes") or {}).items():
            kood = _kood(kood)
            if kood and sku in k["by_sku"] and not c.execute("SELECT 1 FROM filament_kood WHERE kood=?", (kood,)).fetchone():
                c.execute("INSERT INTO filament_kood(kood, sku, aeg) VALUES(?,?,?)", (kood, sku, _aeg()))
    return _ok(lisatud=lisatud, vahele=vahele, tellimusi=tell, eksport=data.get("aeg", ""))


@bp.get("/api/filament/pilt")
def filament_pilt():
    """Toote pilt kataloogi kaustast (ainult pildid/ alamkaustast)."""
    rel = request.args.get("p", "")
    base = os.path.abspath(os.path.join(kaust(), "pildid"))
    f = os.path.abspath(os.path.join(kaust(), rel))
    if not f.startswith(base + os.sep) or not os.path.isfile(f):
        abort(404)
    r = send_file(f, max_age=86400)
    return r


# ---------------------------------------------------------------- v85: tellimine
def _iso_local(s):
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return _aeg()


def olemas_t(c):
    return {r["guid"] for r in c.execute("SELECT guid FROM sync_kustutatud WHERE tabel='filament_tellimus'")}


def _upsert(c, t, guid, **vals):
    """Rida kindla guid-iga (sama mõlemas arvutis): olemas -> UPDATE, muidu INSERT."""
    r = c.execute(f"SELECT id FROM {t} WHERE guid=?", (guid,)).fetchone()
    if r:
        c.execute(f"UPDATE {t} SET {', '.join(k + '=?' for k in vals)} WHERE id=?", list(vals.values()) + [r["id"]])
    else:
        vals["guid"] = guid
        c.execute(f"INSERT INTO {t}({', '.join(vals)}) VALUES({', '.join('?' * len(vals))})", list(vals.values()))


def _json_fail(nimi):
    p = os.path.join(kaust(), "skanner", nimi)
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh), os.path.getmtime(p)
    except (OSError, ValueError):
        return {}, None


def _tellimus(r):
    d = dict(r)
    try:
        d["read"] = json.loads(d.get("read") or "{}")
    except ValueError:
        d["read"] = {}
    return {k: d.get(k) for k in ("guid", "nimi", "staatus", "read", "loodud", "eta", "kogus", "vaartus", "arve", "saabus", "saabus_kogus")}


def _tellimused(c):
    return [_tellimus(r) for r in c.execute("SELECT * FROM filament_tellimus ORDER BY loodud DESC, id DESC")]


def _seaded(c):
    out = {}
    for r in c.execute("SELECT voti, vaartus FROM filament_seade ORDER BY id"):
        try:
            out[r["voti"]] = json.loads(r["vaartus"])
        except (TypeError, ValueError):
            pass
    return out


@bp.get("/api/filament/tellimine")
def filament_tellimine():
    """Tellimise andmed: ostuajalugu/kuukulu (tellimine.json), praegused hinnad (hinnad_tana.json), toonerid,
    tellimused (mustand + teel + saabunud) ja seaded (min/siht, eelistused, reeglid)."""
    ordj, om = _json_fail("tellimine.json")
    nowj, nm = _json_fail("hinnad_tana.json")
    out = {"ord": ordj.get("items") or {}, "hist_to": ordj.get("hist_to") or "", "built": ordj.get("built") or "",
           "hinnad": nowj.get("items") or {}, "kontrollitud": nowj.get("checked") or "",
           "failid_puudu": [n for n, m in (("tellimine.json", om), ("hinnad_tana.json", nm)) if m is None]}
    if request.args.get("toonerid"):
        tj, _ = _json_fail("toonerid.json")
        out["toonerid"] = [{k: t.get(k) for k in ("id", "n", "b", "p", "av", "nx", "d", "px", "pr")} for t in tj.get("items") or []]
        out["toonerid_kontrollitud"] = tj.get("checked") or ""
    with _db() as c:
        out["tellimused"] = _tellimused(c)
        out["seaded"] = _seaded(c)
    return _ok(**out)


@bp.post("/api/filament/seade")
def filament_seade():
    """voti: 'mm:<sku>' {min, target} | 'pref:<sku>' {smart: true} | 'reeglid' {low, cover}; vaartus null = kustuta."""
    j = request.get_json() or {}
    voti = (j.get("voti") or "").strip()
    if not voti or not (voti == "reeglid" or voti.startswith("mm:") or voti.startswith("pref:")):
        raise RuntimeError("Tundmatu seade.")
    with _db() as c:
        if j.get("vaartus") is None:
            c.execute("DELETE FROM filament_seade WHERE guid=?", ("set-" + voti,))
        else:
            _upsert(c, "filament_seade", "set-" + voti, voti=voti, vaartus=json.dumps(j["vaartus"]), muudetud=_aeg())
        return _ok(seaded=_seaded(c))


@bp.post("/api/filament/mustand")
def filament_mustand():
    """Avatud tellimus (Smartprint24): kogu ridade sõnastik {sku: kogus} korraga."""
    j = request.get_json() or {}
    read = {str(k): int(v) for k, v in (j.get("read") or {}).items() if int(v or 0) > 0}
    with _db() as c:
        _upsert(c, "filament_tellimus", "draft-sp", nimi="draft-sp", staatus="draft", read=json.dumps(read),
                kogus=sum(read.values()), muudetud=_aeg())
    return _ok(read=read)


def _eta(paevi=7):
    return (dt.date.today() + dt.timedelta(days=paevi)).isoformat()


@bp.post("/api/filament/kinnita")
def filament_kinnita():
    """Avatud tellimus -> teel (staatus ordered, eeldatav saabumine +7 päeva). Portaalis tellimust ei esita."""
    j = request.get_json() or {}
    with _db() as c:
        r = c.execute("SELECT * FROM filament_tellimus WHERE guid='draft-sp'").fetchone()
        read = _tellimus(r)["read"] if r else {}
        if not read:
            raise RuntimeError("Avatud tellimus on tühi.")
        nimi = "T" + dt.datetime.now().strftime("%Y%m%d%H%M")
        _upsert(c, "filament_tellimus", "ord-" + nimi, nimi=nimi, staatus="ordered", read=json.dumps(read), loodud=_aeg(),
                eta=_eta(), kogus=sum(read.values()), vaartus=round(float(j.get("vaartus") or 0), 2), arve="", muudetud=_aeg())
        _upsert(c, "filament_tellimus", "draft-sp", read="{}", kogus=0, muudetud=_aeg())
    return _ok(nimi=nimi)


@bp.post("/api/filament/sp_korvi")
def filament_sp_korvi():
    """Avatud tellimus -> Smartprinti B2B portaali ostukorv (tellimust ei esita; Timo vajutab portaalis Order ise)."""
    import re
    with _db() as c:
        r = c.execute("SELECT * FROM filament_tellimus WHERE guid='draft-sp'").fetchone()
        read = _tellimus(r)["read"] if r else {}
    if not read:
        raise RuntimeError("Avatud tellimus on tühi.")
    kat = kataloog()["by_sku"]
    ton = {}
    try:
        with open(os.path.join(kaust(), "skanner", "toonerid.json"), encoding="utf-8") as fh:
            ton = {t.get("id"): t for t in (json.load(fh).get("items") or [])}
    except (OSError, ValueError):
        pass
    s = sp_sessioon()
    enne = s.get(SP_B2B + "/Cart", timeout=60).text
    korv_enne = max(0, len(re.findall(r"removeFromCart\(\s*\d", enne)))
    if korv_enne and not (request.get_json(silent=True) or {}).get("ikka"):
        return _ok(korv_tais=korv_enne, lisatud=0, link=SP_B2B + "/cart")   # korvis on juba midagi – küsi enne üle
    lisatud, puudu, vead = [], [], []
    for sku, q in read.items():
        tid = (kat.get(sku) or {}).get("tid") or (ton.get(sku) or {}).get("pid") or ""
        if not tid:
            puudu.append(sku)
            continue
        try:
            x = s.post(SP_B2B + "/Cart/AddJ", data={"id": tid, "q": int(q), "parameter": ""}, timeout=60)
            (lisatud if x.ok else vead).append(sku if x.ok else f"{sku}: HTTP {x.status_code}")
        except Exception as e:
            vead.append(f"{sku}: {e}")
    return _ok(lisatud=len(lisatud), rulle=sum(int(read[k]) for k in lisatud), puudu=puudu, vead=vead,
               korv_enne=korv_enne, link=SP_B2B + "/cart")


@bp.post("/api/filament/tellimus/eta")
def filament_eta():
    j = request.get_json() or {}
    with _db() as c:
        n = c.execute("UPDATE filament_tellimus SET eta=?, muudetud=? WHERE guid=?", (j.get("eta") or "", _aeg(), j.get("guid"))).rowcount
    if not n:
        raise RuntimeError("Tellimust ei leitud.")
    return _ok()


@bp.post("/api/filament/tellimus/tyhista")
def filament_tyhista():
    j = request.get_json() or {}
    with _db() as c:
        r = c.execute("SELECT staatus FROM filament_tellimus WHERE guid=?", (j.get("guid"),)).fetchone()
        if not r or r["staatus"] != "ordered":
            raise RuntimeError("Teel olevat tellimust ei leitud.")
        c.execute("DELETE FROM filament_tellimus WHERE guid=?", (j.get("guid"),))
    return _ok()


@bp.post("/api/filament/saabus")
def filament_saabus():
    """Kaup saabus: kanded 'Sisse' saabunud kogustega (guid rcv-<tellimus>-<sku>, topelt ei tule), tellimus -> received."""
    j = request.get_json() or {}
    g = j.get("guid") or ""
    k = kataloog()["by_sku"]
    with _db() as c:
        r = c.execute("SELECT * FROM filament_tellimus WHERE guid=?", (g,)).fetchone()
        if not r:
            raise RuntimeError("Tellimust ei leitud.")
        o = _tellimus(r)
        if o["staatus"] == "received":
            raise RuntimeError("See tellimus on juba lattu lisatud.")
        kokku = 0
        for sku, q in (j.get("read") or {}).items():
            q = int(q or 0)
            if q <= 0 or sku not in k:   # toonereid laos ei peeta
                continue
            if c.execute("SELECT 1 FROM filament_liikumine WHERE guid=?", (f"rcv-{o['nimi']}-{sku}",)).fetchone():
                continue
            _lisa(c, sku, q, "receipt", k[sku].get("ean") or "", ("Arve " + o["arve"]) if o["arve"] else "Tellimus " + o["nimi"],
                  None, "tellimus", f"rcv-{o['nimi']}-{sku}")
            kokku += q
        c.execute("UPDATE filament_tellimus SET staatus='received', saabus=?, saabus_kogus=?, muudetud=? WHERE guid=?", (_aeg(), kokku, _aeg(), g))
    return _ok(kokku=kokku)


def _arve_id(fn):
    return "".join(ch if ch.isalnum() or ch in "_-." else "_" for ch in os.path.splitext(os.path.basename(fn))[0])[:100]


@bp.get("/api/filament/arved")
def filament_arved():
    """Smartprinti arvete CSV-d kaustas <filament_kaust>/arved (teeb Claude: „tõmba uus arve“)."""
    d = os.path.join(kaust(), "arved")
    try:
        files = [f for f in os.listdir(d) if f.lower().endswith(".csv") and not any(x in f.lower() for x in ("ostuajalugu", "hinnalogi"))]
    except OSError:
        files = []
    with _db() as c:
        st = {r["nimi"]: r["staatus"] for r in c.execute("SELECT nimi, staatus FROM filament_tellimus")}
    files.sort(key=lambda f: [int(x) if x.isdigit() else x for x in __import__("re").split(r"(\d+)", f)], reverse=True)
    return _ok(arved=[{"fail": f, "id": _arve_id(f), "staatus": st.get("A-" + _arve_id(f), "")} for f in files[:12]])


@bp.post("/api/filament/arve_teele")
def filament_arve_teele():
    j = request.get_json() or {}
    fn = os.path.basename(j.get("fail") or "")
    p = os.path.join(kaust(), "arved", fn)
    if not fn.lower().endswith(".csv") or not os.path.isfile(p):
        raise RuntimeError("Arve faili ei leitud.")
    aid = _arve_id(fn)
    k = kataloog()
    read = {}
    with open(p, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            r = {(a or "").strip().lower(): (b or "").strip() for a, b in r.items()}
            t = k["by_sku"].get(r.get("sku", "")) or k["by_ean"].get(_kood(r.get("ean", "")))
            q = int(round(_num(r.get("qty")) or 0))
            if t and q > 0:
                read[t["sku"]] = read.get(t["sku"], 0) + q
    if not read:
        raise RuntimeError("Arvel ei leitud ühtegi kataloogi toodet.")
    vaartus = round(sum(q * (k["by_sku"][s].get("hind") or 0) for s, q in read.items()), 2)
    with _db() as c:
        if c.execute("SELECT 1 FROM filament_tellimus WHERE guid=?", ("ord-A-" + aid,)).fetchone():
            raise RuntimeError("See arve on juba teel või saabunud.")
        _upsert(c, "filament_tellimus", "ord-A-" + aid, nimi="A-" + aid, staatus="ordered", read=json.dumps(read), loodud=_aeg(),
                eta=_eta(), kogus=sum(read.values()), vaartus=vaartus, arve=aid, muudetud=_aeg())
    return _ok(kogus=sum(read.values()), arve=aid)


# ---------------------------------------------------------------- v99: arved otse Smartprinti portaalist (Teel -> Näita arveid)
SP_B2B = os.environ.get("SP_B2B") or "https://b2b.smartprint24.com"   # testimiseks saab üle kirjutada
_SP = {"arved": {}, "aeg": ""}   # viimati tõmmatud arved mälus: id -> {nimi, kuupaev, read, ...}
_SP_LUKK = threading.Lock()


def _arve_norm(s):
    """'FS 31806/A/2026' / 'FS31806' / 'FS31806.csv' -> 'FS31806'."""
    import re
    m = re.search(r"FS\s*(\d+)", s or "", re.I)
    return ("FS" + m.group(1)) if m else (s or "").strip()


ARVED_ALATES = 31294   # v102: Timo 18.11 – arveid näidatakse alates FS 31294 (süsteemi algus), kuni 3 viimast


def _arve_nr(s):
    import re
    m = re.search(r"FS\s*(\d+)", s or "", re.I)
    return int(m.group(1)) if m else 0


def _sp_konto():
    """Smartprinti kasutaja ini-failist; loeb faili iga kord uuesti, et pärast ini muutmist ei peaks programmi taaskäivitama."""
    import configparser
    k = dict(_K)
    try:
        cp = configparser.ConfigParser()
        cp.read(os.path.join(os.path.dirname(os.path.abspath(__file__)), "postikeskkond.ini"), encoding="utf-8")
        for sek in cp.sections():   # tavaliselt [keskkond], aga leiab ka mujalt
            for v in ("smartprint_kasutaja", "smartprint_parool"):
                if cp.has_option(sek, v) and cp.get(sek, v).strip() and not (k.get(v) or "").strip():
                    k[v] = cp.get(sek, v)
    except Exception:
        pass
    try:   # ladu.3dmarket.ee: Timo sisestab need veebis (⚙ Seaded), hoitakse andmekaustas failis tarnija.json
        with open(os.path.join(kaust(), "tarnija.json"), encoding="utf-8") as fh:
            tj = json.load(fh)
        for v in ("smartprint_kasutaja", "smartprint_parool"):
            if (tj.get(v) or "").strip() and not (k.get(v) or "").strip():
                k[v] = tj[v]
    except (OSError, ValueError):
        pass
    u = (k.get("smartprint_kasutaja") or os.environ.get("SMARTPRINT_USER") or "").strip()
    p = (k.get("smartprint_parool") or os.environ.get("SMARTPRINT_PASS") or "").strip()
    if not u or not p:
        raise RuntimeError("Smartprinti kasutaja puudub. Sisesta see laohalduse lehel ⚙ Seaded → Tarnija ühendus.")
    return u, p


def _sp_hind(s):
    try:
        return float((s or "").replace("€", "").replace("\xa0", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return 0.0


def sp_sessioon():
    """Sisse logitud requests.Session Smartprinti B2B portaali."""
    import requests
    u, p = _sp_konto()
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0"
    s.get(SP_B2B + "/Profil/LogOn", timeout=30)
    r = s.post(SP_B2B + "/Profil/LogOn", data={"UserName": u, "Password": p, "ReturnUrl": "/"}, timeout=30)
    if "logout" not in r.text.lower():
        raise RuntimeError("Smartprinti portaali sisselogimine ebaõnnestus. Kontrolli kasutajat ja parooli (⚙ Seaded).")
    return s


def _sp_tooma(n=3):
    """Logib Smartprinti B2B portaali ja toob n viimast FS arvet koos ridadega."""
    import re, html as _h
    import requests
    s = sp_sessioon()
    h = s.get(SP_B2B + "/p/StockDocument?symbol=ZK&page=1", timeout=60).text
    docs = []
    for did, nm in re.findall(r'href="/p/StockDocument/Index\?id=(\d+)" class="dropdown-item"[^>]*>(FS [^<]+)<', h):
        if (did, nm) not in docs:
            docs.append((did, nm))
    docs = [d for d in docs if _arve_nr(d[1]) >= ARVED_ALATES]   # vanemaid arveid ei näita (süsteem algab FS 31294-st)
    k = kataloog()["by_sku"]
    out = []
    for did, nm in docs[:n]:
        d = s.get(f"{SP_B2B}/p/StockDocument/Index?id={did}", timeout=60).text
        m = re.search(r"Document (FS [^ <]+) \(([\d/]+)\)", d)
        nimi = (m.group(1) if m else nm).strip()
        try:
            kp = dt.datetime.strptime(m.group(2), "%d/%m/%Y").date().isoformat() if m else ""
        except ValueError:
            kp = ""
        read, vaartus, kokku, muud = {}, 0.0, 0.0, []
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", d, re.S):
            c = [_h.unescape(re.sub(r"<[^>]+>", "", x)).strip() for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            if len(c) < 10 or not c[0].isdigit():
                continue
            try:
                q = int(round(float(c[3].replace(",", ".").replace("\xa0", ""))))
            except ValueError:
                continue
            netto = _sp_hind(c[9])
            kokku += netto
            if c[1] in k and q > 0:
                read[c[1]] = read.get(c[1], 0) + q
                vaartus += netto
            elif q > 0 and netto > 0:
                muud.append(c[2])
        out.append({"id": _arve_norm(nimi), "nimi": nimi, "kuupaev": kp, "read": read, "kogus": sum(read.values()),
                    "vaartus": round(vaartus, 2), "kokku": round(kokku, 2), "muud": muud[:8]})
    return out


def _arve_olek(c):
    """arve id -> (staatus, tellimuse guid) olemasolevatest tellimustest."""
    st = {}
    for r in c.execute("SELECT guid, nimi, arve, staatus FROM filament_tellimus WHERE staatus IN ('ordered','received')"):
        a = _arve_norm(r["arve"] or (r["nimi"][2:] if (r["nimi"] or "").startswith("A-") else ""))
        if a and (a not in st or r["staatus"] == "received"):
            st[a] = (r["staatus"], r["guid"])
    return st


@bp.get("/api/filament/tarnija_arved")
def filament_tarnija_arved():
    """Kuni 3 viimast Smartprinti arvet alates FS 31294 portaalist: kuupäev, kogus, olek uus / teel / laos."""
    with _SP_LUKK:
        arved = _sp_tooma(int(request.args.get("n") or 3))
        _SP["arved"] = {a["id"]: a for a in arved}
        _SP["aeg"] = _aeg()
    with _db() as c:
        st = _arve_olek(c)
        vabad = [_tellimus(r) for r in c.execute("SELECT * FROM filament_tellimus WHERE staatus='ordered' AND COALESCE(arve,'')='' ORDER BY loodud DESC")]
    for a in arved:
        s = st.get(a["id"])
        a["olek"] = {"ordered": "teel", "received": "laos"}.get(s[0], "uus") if s else "uus"
        a["tellimus"] = s[1] if s else ""
    return _ok(arved=arved, aeg=_SP["aeg"], vabad=[{"guid": o["guid"], "nimi": o["nimi"], "kogus": o["kogus"], "loodud": o["loodud"]} for o in vabad])


@bp.post("/api/filament/tarnija_arve_teele")
def filament_tarnija_arve_teele():
    """Uus arve -> teel. asenda=<guid>: kinnitatud tellimus (ilma arveta) saab selle arve read ja numbri (topelt ei tule)."""
    j = request.get_json() or {}
    aid = _arve_norm(j.get("id"))
    a = _SP["arved"].get(aid)
    if not a:
        raise RuntimeError("Arvet pole mälus. Vajuta uuesti „Näita arveid“.")
    if not a["read"]:
        raise RuntimeError("Arvel pole ühtegi kataloogi filamenti.")
    with _db() as c:
        if aid in _arve_olek(c):
            raise RuntimeError("See arve on juba teel või laos.")
        asenda = (j.get("asenda") or "").strip()
        if asenda:
            r = c.execute("SELECT staatus, arve FROM filament_tellimus WHERE guid=?", (asenda,)).fetchone()
            if not r or r["staatus"] != "ordered" or (r["arve"] or ""):
                raise RuntimeError("Valitud tellimust ei saa selle arvega siduda.")
            c.execute("UPDATE filament_tellimus SET read=?, kogus=?, vaartus=?, arve=?, muudetud=? WHERE guid=?",
                      (json.dumps(a["read"]), a["kogus"], a["vaartus"], aid, _aeg(), asenda))
        else:
            _upsert(c, "filament_tellimus", "ord-A-" + aid, nimi="A-" + aid, staatus="ordered", read=json.dumps(a["read"]),
                    loodud=_aeg(), eta=_eta(5), kogus=a["kogus"], vaartus=a["vaartus"], arve=aid, muudetud=_aeg())
    return _ok(arve=aid, kogus=a["kogus"])
