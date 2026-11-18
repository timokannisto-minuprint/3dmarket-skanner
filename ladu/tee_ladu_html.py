# -*- coding: utf-8 -*-
"""Teeb static/ladu.html (laohalduse veebileht) Postikeskkonna index.html-ist: võtab sealt stiilid, 🧵 Filament-vaate
HTML-i ja JS-i, et mõlemad näeksid välja ja töötaksid ühtemoodi. Käivita, kui Postikeskkonna Filament-vaade muutub:
    python tee_ladu_html.py <tee/postikeskkond/index.html>
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))


def vahel(s, algus, lopp, kaasa_lopp=False):
    i = s.index(algus)
    j = s.index(lopp, i + len(algus))
    return s[i:j + (len(lopp) if kaasa_lopp else 0)]


def tee(src):
    s = open(src, encoding="utf-8").read()
    css = vahel(s, "<style>", "</style>", True)
    fil = vahel(s, '<section class="fil">', "</section>", True)
    abi = vahel(s, "const S = {", "/* ---------- algus */")
    modal = vahel(s, "function closeModal(){", "$('modal').onclick", False) + "$('modal').onclick = e => { if (e.target.id==='modal') closeModal(); };\n"
    # abifunktsioonid, mis on index.html-is mujal defineeritud
    lisad = [ln for ln in s.splitlines() if ln.startswith(("const eur = ", "const dEE = "))]
    abi = abi + "\n".join(lisad) + "\n"
    js = vahel(s, "/* ---------- 3D filamendi ladu (v82) */", "document.addEventListener('keydown', e=>{ if (e.key==='Escape')")

    # külgriba tekstid veebi jaoks
    i = fil.index('<div class="filh" style="margin-top:18px">Vana laoskänner')
    j = fil.index("</button>", i) + len("</button>")
    fil = fil[:i] + fil[j:]
    if 'class="ghost filweb"' in fil:   # Postikeskkonna "Ava veebis" nupp pole veebis vaja
        i = fil.index('<a class="ghost filweb"'); fil = fil[:i] + fil[fil.index("</a>", i) + 4:]
    fil = fil.replace("Telefon samas võrgus saadab iga skänni kohe siia.", "Telefon saadab iga skänni kohe lattu (töötab igal pool, kus on internet).")

    lisa_css = """
<style>
.app.fil-mode{height:100vh}
.side .brand small{display:block}
.side .lnk{display:block;padding:8px 12px;border-radius:8px;color:inherit;text-decoration:none;margin:2px 0}
.side .lnk:hover{background:rgba(127,127,127,.12)}
</style>"""
    html = f"""<!doctype html>
<html lang="et">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>3dmarket ladu</title>
{css}{lisa_css}
</head>
<body>
<div class="app fil-mode" id="app">
  <aside class="side">
    <div class="brand">3dmarket ladu<small>ladu.3dmarket.ee</small></div>
    <div class="tabs"><button id="tabFil" class="on"><span class="ic">🧵</span>Filament <span class="tabn" id="filN"></span></button></div>
    <a class="lnk" href="/skanner" target="_blank">📱 Telefoni skänner</a>
    <a class="lnk" href="/logout">Logi välja</a>
    <div class="foot" id="foot"></div>
  </aside>
  {fil}
</div>
<div class="modal" id="modal"><div class="mwrap"><button class="mclose" onclick="closeModal()" title="Sulge (Esc)">✕</button><div class="mbox" id="mbox"></div></div></div>
<div class="toast" id="toast"></div>
<script>
// Tehtud skriptiga tee_ladu_html.py Postikeskkonna index.html-ist (Filament-vaade). Ära muuda käsitsi.
{abi}
{modal}
{js}
document.addEventListener('keydown', e=>{{ if (e.key==='Escape'){{ closeModal(); }} }});
function setMode(){{}}
api('/api/tervis').then(j=>{{ $('foot').textContent = 'Versioon ' + (j.versioon||''); }}).catch(()=>{{}});
loadFil(); setTimeout(()=>loadFo(false, true), 1500);
</script>
</body>
</html>
"""
    out = os.path.join(HERE, "static", "ladu.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print("Kirjutatud", out, len(html), "baiti")


if __name__ == "__main__":
    tee(sys.argv[1])
