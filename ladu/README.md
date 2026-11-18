# ladu.3dmarket.ee – 3dmarketi laoserver

Filamentide (ja hiljem muude poe toodete) laoseis, tellimine ja telefoni kaamera-skänner. Üks andmebaas, mida kasutavad:

- **Postikeskkond** (vahekaart 🧵 Filament): `postikeskkond/filament.py` suunab kõik `/api/filament/*` päringud siia (päis `X-Ladu-Voti`). Seaded failis `ladu.json` Postikeskkonna kaustas: `{"url": "https://ladu.3dmarket.ee", "voti": "…"}`.
- **Veebi laohaldus**: `https://ladu.3dmarket.ee` (parooliga sisselogimine, sama Filament-vaade mis Postikeskkonnas; leht `static/ladu.html` tehakse skriptiga `tee_ladu_html.py` Postikeskkonna index.html-ist).
- **Telefoni skänner**: `https://ladu.3dmarket.ee/skanner#k=<TEL_VOTI>` (QR-kood Postikeskkonnas: Filament → 📱 Telefon).
- hiljem uus pood ja SimplBooksi arved.

Andmed: SQLite `LADU_ANDMED/ladu.db` (kanded `filament_liikumine`, laoseis = summa), kataloog ja hinnafailid samas kaustas (Postikeskkond laeb need Dropboxist üles, kui muutuvad). Tootepildid laeb Postikeskkond taustal üles (`pildid/`, ainult puuduvad).

## Paigaldus veebimajutuses (Elkdata, pakett Tee-ise-veeb Premium)

1. Iseteenindus → 3dmarket.ee → Veebiserver → **Alamdomeenid**: lisa `ladu`. **SSH kasutajad**: lisa kasutaja (avalik võti).
2. SSH-s:
   ```sh
   cd /www/apache/domains/www.3dmarket.ee
   git clone https://github.com/timokannisto-minuprint/3dmarket-skanner.git ladu
   python3.12 -m venv ladu-venv && ladu-venv/bin/pip install -r ladu/ladu/requirements.txt
   mkdir -p ladu-andmed && cp ladu/ladu/ecosystem.config.js.naidis ladu/ladu/ecosystem.config.js   # täida võtmed
   ```
3. Iseteenindus → **PM2 ja Node.JS**: aktiveeri, lisa rakendus `ladu/ladu/ecosystem.config.js` (või `pm2 start ecosystem.config.js`).
4. **Portide suunamine**: `ladu.3dmarket.ee` → port 3000.
5. Kontroll: `https://ladu.3dmarket.ee/api/tervis`.

Uuendamine: `cd ladu && git pull && pm2 restart ladu`.

## API (kõik vajavad `X-Ladu-Voti`, telefoni teed `X-Voti`)

- `/api/filament/*` – sama mis Postikeskkonna v107 (laoseis, kanded, skänn, tellimine, Smartprinti arved)
- `/api/nimed`, `/api/kanne`, `/api/tagasi` – telefoni skänner
- `GET /api/ladu/olek`, `GET /api/ladu/eksport` (kogu andmebaas JSON-ina), `GET /api/ladu/failid`, `PUT /api/ladu/fail?nimi=…`, `POST /api/ladu/import`
- `GET /api/pood/laoseis` – e-poele (päis `X-Pood-Voti`): `{laoseis: {sku: {laos, teel, eta}}, aeg}`; `?koik=1` kõik tooted
- `POST /api/pood/myyk` – e-poe makstud tellimus (päis `X-Pood-Voti`): `{"tellimus": "1234", "read": {"FG-P1-E1": 2}}` → Välja-kanded, korduv saatmine ohutu
- `GET /api/tervis` – avalik
