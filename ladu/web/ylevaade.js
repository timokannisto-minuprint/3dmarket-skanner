/* ladu.3dmarket.ee: Ülevaade (ainult veebis). */
const _filSetView0 = filSetView;
filSetView = function(v){
  const on = v === 'yl';
  $('filV-yl').classList.toggle('on', on);
  document.querySelectorAll('.side .nav [data-v]').forEach(b=>b.classList.toggle('on', b.dataset.v === v));
  if (on) {
    S.filView = 'yl';
    document.querySelectorAll('#filViews button').forEach(b=>b.classList.toggle('on', b.dataset.v === 'yl'));
    ['laos','ord','cart','teel'].forEach(x=>$('filV-'+x).classList.remove('on'));
    document.querySelector('section.fil').classList.add('notlaos');
    loadYl(); return;
  }
  _filSetView0(v);
};
const ylNimi = sku => (S.fil.by[sku] || {}).nimi || sku;
const ylPilt = sku => { const p = (S.fil.by[sku] || {}).pilt; return p ? `<img loading="lazy" src="/api/filament/pilt?p=${encodeURIComponent(p)}" onerror="this.style.visibility='hidden'">` : '<img style="visibility:hidden">'; };
const ylKuu = m => ['jaan','veebr','märts','apr','mai','juuni','juuli','aug','sept','okt','nov','dets'][parseInt(m.slice(5,7))-1];
const ylN = n => Number(n||0).toLocaleString('et-EE');

function ylChart(kuud){
  const W = 640, H = 220, P = 26, max = Math.max(1, ...kuud.map(k => Math.max(k.sale, k.receipt)));
  const bw = (W - P) / kuud.length, b = Math.max(4, bw/2 - 5);
  let g = '';
  for (let i = 0; i <= 4; i++) { const y = 10 + (H-40) * i/4; g += `<line x1="${P}" x2="${W}" y1="${y}" y2="${y}" stroke="var(--line)"/><text x="${P-6}" y="${y+4}" text-anchor="end" font-size="10" fill="var(--muted)">${Math.round(max*(1-i/4))}</text>`; }
  kuud.forEach((k, i) => {
    const x = P + i*bw + bw/2, hs = (H-40)*k.sale/max, hr = (H-40)*k.receipt/max;
    g += `<rect x="${x-b-1}" y="${H-30-hs}" width="${b}" height="${hs}" rx="3" fill="var(--ok)"><title>${k.kuu}: välja ${k.sale}</title></rect>`;
    g += `<rect x="${x+1}" y="${H-30-hr}" width="${b}" height="${hr}" rx="3" fill="var(--blue)" opacity=".55"><title>${k.kuu}: sisse ${k.receipt}</title></rect>`;
    g += `<text x="${x}" y="${H-12}" text-anchor="middle" font-size="10.5" fill="var(--muted)">${ylKuu(k.kuu)}</text>`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${g}</svg>`;
}

async function loadYl(){
  const el = $('filV-yl');
  if (!el.dataset.ok) el.innerHTML = '<div class="empty">Laen…</div>';
  await guard(async()=>{
    if (!S.fil.kat) await loadFil(false, true);
    const j = await api('/api/ladu/ylevaade');
    if (!S.fo.loaded) { try { await loadFo(false, true); } catch(e){} }
    const sug = parseInt(($('filN') || {}).textContent) || 0, teel = (S.fo.tell || []).filter(o => o.staatus === 'ordered').reduce((a, o) => a + (o.kogus || 0), 0);
    const maxTop = Math.max(1, ...j.top.map(t => t.q));
    el.dataset.ok = 1;
    el.innerHTML = `
      <div class="kpis">
        <div class="kpi hi"><span class="ic">🧵</span><div class="v">${ylN(j.rulle)}</div><div class="l">rulli laos · ${ylN(j.tooteid)} toodet</div></div>
        <div class="kpi"><span class="ic">💶</span><div class="v">${fmtE(j.vaartus)} €</div><div class="l">laoväärtus ostuhinnas, km-ta</div></div>
        <div class="kpi"><span class="ic">📦</span><div class="v">${ylN(j.myyk30)}</div><div class="l">rulli välja 30 päevaga (${fmtE(j.myyk30_eur)} € ostuhinnas)</div></div>
        <div class="kpi ${sug ? 'warnk' : ''}" style="cursor:pointer" onclick="filSetView('ord')"><span class="ic">🛒</span><div class="v">${sug}</div><div class="l">toodet vaja tellida →</div></div>
        <div class="kpi" style="cursor:pointer" onclick="filSetView('teel')"><span class="ic">🚚</span><div class="v">${teel}</div><div class="l">rulli teel →</div></div>
        <div class="kpi"><span class="ic">⚡</span><div class="v">${ylN(j.tana_kandeid)}</div><div class="l">kannet täna · ${ylN(j.tana_myyk)} välja</div></div>
      </div>
      <div class="grid">
        <div class="card chart"><h3>Liikumine kuude kaupa</h3><div class="hint">Rullid, viimased 12 kuud</div>${ylChart(j.kuud)}
          <div class="legend"><span><i style="background:var(--ok)"></i>Välja (müük)</span><span><i style="background:var(--blue);opacity:.55"></i>Sisse (tarnijalt)</span></div></div>
        <div class="card"><h3>Enim müüdud</h3><div class="hint">Viimased 30 päeva</div><div class="rows">${j.top.length ? j.top.map(t => `
          <div class="r" onclick="filCard('${esc(t.sku)}')">${ylPilt(t.sku)}<span class="n">${esc(ylNimi(t.sku))}</span><span class="bar"><i style="width:${Math.round(100*t.q/maxTop)}%"></i></span><span class="q">${t.q}</span><span class="sub" style="width:58px;text-align:right">laos ${t.laos}</span></div>`).join('') : '<div class="empty2">Müüke veel pole.</div>'}</div></div>
        <div class="card"><h3>Seisev kaup</h3><div class="hint">Laos, aga 90 päeva jooksul pole müüdud. Kõige suurema väärtusega ees: kaalu allahindlust.</div><div class="rows">${j.seisev.length ? j.seisev.map(t => `
          <div class="r" onclick="filCard('${esc(t.sku)}')">${ylPilt(t.sku)}<span class="n">${esc(ylNimi(t.sku))}</span><span class="q">${t.laos} tk</span><span class="sub" style="width:76px;text-align:right">${fmtE(t.vaartus)} €</span></div>`).join('') : '<div class="empty2">Seisvat kaupa pole.</div>'}</div></div>
        <div class="card"><h3>Aruanded</h3><div class="hint">Laoseis Excelisse või raamatupidamisele (SimplBooks). Vali kuupäev, kui vajad seisu kindla päeva lõpu seisuga, näiteks 31.12 inventuuriks.</div>
          <div class="exp"><input type="date" id="ylKp"><button class="green" onclick="location.href='/api/ladu/laoseis.csv'+($('ylKp').value?'?kuupaev='+$('ylKp').value:'')">⬇ Laoseis CSV</button></div>
          ${j.negatiivsed.length ? `<div style="margin-top:16px"><h3>Kontrolli üle</h3><div class="hint">Nende toodete laoseis on miinuses, ilmselt on sisse-kanne puudu.</div><div class="chips">${j.negatiivsed.map(t => `<span class="chip" onclick="filCard('${esc(t.sku)}')">${esc(ylNimi(t.sku))}: ${t.laos}</span>`).join('')}</div></div>` : ''}
          <div class="hint" style="margin-top:16px">Andmebaasist tehakse iga päev automaatne varukoopia (alles 30 päeva).</div></div>
      </div>`;
  });
}

/* ---------- Avatud tellimus -> Smartprinti ostukorv (ladu.3dmarket.ee) */
const _renderFoCart0 = renderFoCart;
renderFoCart = function(){
  _renderFoCart0();
  const el = [...document.querySelectorAll('#filV-cart .sub')].find(x => /pane tellimus korvi/.test(x.textContent));
  if (!el) return;
  el.innerHTML = `<div class="spkorv"><button class="green" onclick="foSpKorvi(this)">🛒 Saada Smartprinti ostukorvi</button>
    <span>Ladu paneb read koos kogustega portaali ostukorvi. Seal vaatad üle ja vajutad <b>Order</b>. Siis vajuta siin „Kinnita tellimus“, et kaup läheks teele.</span></div>`;
};
async function foSpKorvi(b, ikka){
  b.disabled = true; const t0 = b.textContent; b.textContent = 'Saadan portaali…';
  try {
    const j = await api('/api/filament/sp_korvi', {json:{ikka: !!ikka}});
    if (j.korv_tais) {
      $('mbox').innerHTML = `<h2>Smartprinti korvis on juba ${j.korv_tais} rida</h2>
        <p>Kui lisan read juurde, võivad mõned kogused minna topelt. Soovitan korvi portaalis enne üle vaadata või tühjendada.</p>
        <p style="display:flex;gap:8px;flex-wrap:wrap"><a class="btnlink" href="${esc(j.link)}" target="_blank" rel="noopener">Ava ostukorv →</a>
        <button onclick="closeModal();foSpKorvi(this,true)">Lisa ikkagi juurde</button></p>`;
      $('modal').classList.add('open'); b.disabled = false; b.textContent = t0; return;
    }
    const lisa = [];
    if (j.korv_enne) lisa.push(`Korvis oli juba enne ${j.korv_enne} rida, lisasin need juurde.`);
    if (j.puudu.length) lisa.push(`Portaali koodi pole, lisa käsitsi: ${j.puudu.map(ylNimi).join(', ')}`);
    if (j.vead.length) lisa.push(`Vead: ${j.vead.join('; ')}`);
    $('mbox').innerHTML = `<h2>Ostukorv täidetud</h2><p>Portaali korvi läks <b>${j.lisatud}</b> rida (${j.rulle} tk).</p>
      ${lisa.map(x => `<p class="hint">${esc(x)}</p>`).join('')}
      <p><a class="green btnlink" href="${esc(j.link)}" target="_blank" rel="noopener">Ava Smartprinti ostukorv →</a></p>
      <p class="hint">Kui oled portaalis tellimuse esitanud, vajuta siin „Kinnita tellimus“.</p>`;
    $('modal').classList.add('open');
  } catch(e){ toast(e.message, true); }
  b.disabled = false; b.textContent = t0;
}
