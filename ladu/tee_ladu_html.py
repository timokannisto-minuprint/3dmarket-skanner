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

    teema = open(os.path.join(HERE, "web", "teema.css"), encoding="utf-8").read()
    ylevaade = open(os.path.join(HERE, "web", "ylevaade.js"), encoding="utf-8").read()
    # Ülevaade vaade (ainult veebis): nupp vaadete reale ja oma paneel
    fil = fil.replace('<div class="seg filviews" id="filViews">', '<div class="seg filviews" id="filViews"><button data-v="yl">Ülevaade</button>', 1)
    fil = fil.replace('<div class="filbody filv on" id="filV-laos">', '<div class="filv yl" id="filV-yl"></div>\n    <div class="filbody filv on" id="filV-laos">', 1)
    html = f"""<!doctype html>
<html lang="et">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>3dmarket ladu</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🧵</text></svg>">
{css}
<style>
{teema}
</style>
</head>
<body>
<div class="app fil-mode" id="app">
  <aside class="side">
    <div class="brand"><span class="logo">🧵</span><span>3dmarket ladu<small>ladu.3dmarket.ee</small></span></div>
    <a class="skbtn" href="/skanner"><span class="ic">📱</span><span>Ava laoskänner<small>kaamera · EAN</small></span></a>
    <nav class="nav">
      <button data-v="yl" onclick="filSetView('yl')"><span class="ic">📊</span>Ülevaade</button>
      <button data-v="laos" onclick="filSetView('laos')"><span class="ic">🧵</span>Laoseis</button>
      <button data-v="ord" onclick="filSetView('ord')"><span class="ic">🛒</span>Tellimine <span class="tabn" id="filN"></span></button>
      <button data-v="cart" onclick="filSetView('cart')"><span class="ic">📝</span>Avatud tellimus</button>
      <button data-v="teel" onclick="filSetView('teel')"><span class="ic">🚚</span>Teel</button>
      <div class="navh">Tööriistad</div>
      <a href="/api/ladu/laoseis.csv"><span class="ic">⬇</span>Laoseis CSV</a>
      <a href="/logout"><span class="ic">↩</span>Logi välja</a>
    </nav>
    <div class="foot" id="foot"></div>
  </aside>
  {fil}
</div>
<div class="modal" id="modal"><div class="mwrap"><button class="mclose" onclick="closeModal()" title="Sulge (Esc)">✕</button><div class="mbox" id="mbox"></div></div></div>
<div class="toast" id="toast"></div>
<a class="skfab" href="/skanner"><span>📱</span> Ava laoskänner</a>
<script>
// Tehtud skriptiga tee_ladu_html.py Postikeskkonna index.html-ist (Filament-vaade) + web/ylevaade.js. Ära muuda käsitsi.
{abi}
{modal}
{js}
{ylevaade}
document.addEventListener('keydown', e=>{{ if (e.key==='Escape'){{ closeModal(); }} }});
function setMode(){{}}
api('/api/tervis').then(j=>{{ $('foot').textContent = 'Versioon ' + (j.versioon||''); }}).catch(()=>{{}});
loadFil(false, true).then(()=>filSetView('yl'));
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
