"""Settings service for the phone, served on the Pi's WiFi hotspot.

Separate aiohttp app from the loopback data server. Lets you enable/disable
alarms, edit every threshold, the shift-light stages, scaling overrides
(calibration) and the ECU connection, then writes config.yaml atomically and
hot-reloads the daemon. Also exposes live values so you can calibrate RPM
against the tacho while looking at the phone.
"""
import copy
import logging
import os
import tempfile

import yaml
from aiohttp import web

# Pure module (no pygame): single source of truth for what a tile can show
# and which theme colours exist.
from s300ui.layout import DEFAULT_THEME, DEFAULT_TILES, SENSORS, TILE_STYLES

log = logging.getLogger("s300d.settings")

HUB = web.AppKey("hub", object)
PATH = web.AppKey("path", str)

# Only these top-level keys may be changed from the phone.
EDITABLE = ("alarms", "shift_light", "scaling_overrides", "poll_hz", "mac", "rfcomm_channel",
            "ui")
# ...and only this subset of "ui"; ws/fullscreen/buttons stay file-only.
UI_TILE_SLOTS = {"tiles_big": 4, "tiles_small": 3}
RULE_FIELDS = {"enabled": bool, "warn": float, "critical": float, "clear": float,
               "samples": int, "rpm_min": float, "tps_min": float,
               "window_s": float, "sustained_s": float}
TYPE_NAMES = ("CT_RPM", "CT_SPEED", "CT_MBAR", "CT_KPA", "CT_TPS", "CT_INJ", "CT_IGN",
              "CT_RETARD", "CT_TEMP", "CT_PCT", "CT_5V", "CT_19V", "CT_LAMBDA")


def load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def save_atomic(path, conf):
    """Write YAML to a temp file in the same directory, fsync, rename."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".yaml", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("# Written by the s300d settings page. Comments are not preserved.\n")
            yaml.safe_dump(conf, fh, sort_keys=False, default_flow_style=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _hex_color(value):
    s = str(value).strip().lower()
    if len(s) == 7 and s[0] == "#" and all(c in "0123456789abcdef" for c in s[1:]):
        return s
    raise ValueError("colour must look like #rrggbb")


def _coerce_rule(existing, patch):
    rule = dict(existing)
    for key, value in patch.items():
        if key not in RULE_FIELDS:
            raise ValueError("unknown rule field %r" % key)
        conv = RULE_FIELDS[key]
        rule[key] = bool(value) if conv is bool else conv(value)
    if "warn" in rule and "clear" in rule and rule.get("type") != "rate":
        if rule.get("direction", "above") == "above" and rule["clear"] > rule["warn"]:
            raise ValueError("clear must be <= warn for an 'above' rule")
        if rule.get("direction") == "below" and rule["clear"] < rule["warn"]:
            raise ValueError("clear must be >= warn for a 'below' rule")
    if rule.get("samples", 1) < 1:
        raise ValueError("samples must be >= 1")
    return rule


def apply_patch(conf, patch):
    """Validate + merge a settings patch into a copy of ``conf``."""
    new = copy.deepcopy(conf)
    for key, value in patch.items():
        if key not in EDITABLE:
            raise ValueError("%r is not editable from the settings page" % key)
        if key == "alarms":
            if not isinstance(value, dict):
                raise ValueError("alarms must be a mapping")
            rules = new.setdefault("alarms", {})
            for rule_id, rule_patch in value.items():
                if rule_id not in rules:
                    raise ValueError("unknown alarm %r" % rule_id)
                rules[rule_id] = _coerce_rule(rules[rule_id], rule_patch or {})
        elif key == "shift_light":
            stages = {k: int(value[k]) for k in ("amber", "red", "flash")}
            if not stages["amber"] <= stages["red"] <= stages["flash"]:
                raise ValueError("shift stages must be amber <= red <= flash")
            new["shift_light"] = stages
        elif key == "scaling_overrides":
            ov = {}
            for name, v in (value or {}).items():
                if name not in TYPE_NAMES:
                    raise ValueError("unknown type %r" % name)
                if v in ("", None):
                    continue  # blank = remove override
                if isinstance(v, dict):
                    ov[name] = {k: float(v[k]) for k in ("scale", "offset") if k in v}
                else:
                    ov[name] = float(v)
            new["scaling_overrides"] = ov
        elif key == "poll_hz":
            hz = float(value)
            if not 1 <= hz <= 50:
                raise ValueError("poll_hz must be 1-50")
            new["poll_hz"] = hz
        elif key == "ui":
            if not isinstance(value, dict):
                raise ValueError("ui must be a mapping")
            ui = new.setdefault("ui", {})
            for k, v in value.items():
                if k == "units":
                    if v not in ("metric", "imperial"):
                        raise ValueError("units must be metric or imperial")
                    ui["units"] = v
                elif k == "show_rpm":
                    ui["show_rpm"] = bool(v)
                elif k == "tile_style":
                    if v not in TILE_STYLES:
                        raise ValueError("tile_style must be one of %s" % (TILE_STYLES,))
                    ui["tile_style"] = v
                elif k == "danger_rpm":
                    if v in ("", None):
                        ui["danger_rpm"] = None
                    else:
                        rpm = float(v)
                        if not 1000 <= rpm <= 20000:
                            raise ValueError("danger_rpm must be 1000-20000 (or blank for off)")
                        ui["danger_rpm"] = rpm
                elif k == "theme":
                    if not isinstance(v, dict):
                        raise ValueError("theme must be a mapping")
                    theme = ui.setdefault("theme", {})
                    for name, colour in v.items():
                        if name not in DEFAULT_THEME:
                            raise ValueError("unknown theme colour %r" % name)
                        theme[name] = _hex_color(colour)
                elif k in UI_TILE_SLOTS:
                    if not isinstance(v, list) or len(v) != UI_TILE_SLOTS[k]:
                        raise ValueError("%s needs exactly %d entries" % (k, UI_TILE_SLOTS[k]))
                    for name in v:
                        if name not in SENSORS:
                            raise ValueError("unknown sensor %r" % name)
                    ui[k] = list(v)
                else:
                    raise ValueError("%r is not editable under ui" % k)
        elif key == "rfcomm_channel":
            new["rfcomm_channel"] = int(value)
        elif key == "mac":
            mac = str(value).strip().upper()
            parts = mac.split(":")
            if len(parts) != 6 or not all(len(p) == 2 and all(c in "0123456789ABCDEF" for c in p)
                                          for p in parts):
                raise ValueError("MAC must look like AA:BB:CC:DD:EE:FF")
            new["mac"] = mac
    return new


# --- handlers ----------------------------------------------------------------

async def get_config(request):
    conf = load(request.app[PATH])
    return web.json_response({k: conf.get(k) for k in EDITABLE})


async def put_config(request):
    try:
        patch = await request.json()
        conf = apply_patch(load(request.app[PATH]), patch)
        save_atomic(request.app[PATH], conf)
        request.app[HUB].reload(conf)
    except (ValueError, TypeError, KeyError) as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    except OSError as exc:
        return web.json_response({"ok": False, "error": "cannot write config: %s" % exc},
                                 status=500)
    return web.json_response({"ok": True})


async def get_live(request):
    hub = request.app[HUB]
    snap = hub.snapshot(1.0)
    with hub.lock:
        snap["raw"] = dict(getattr(hub, "last_values", {}) or {})
    return web.json_response(snap)


async def post_command(request):
    body = await request.json()
    cmd = body.get("cmd") if isinstance(body, dict) else None
    try:
        request.app[HUB].command(cmd)
    except ValueError:
        return web.json_response({"ok": False, "error": "unknown command"}, status=400)
    return web.json_response({"ok": True})


async def index(request):
    return web.Response(text=PAGE, content_type="text/html")


def make_app(hub, config_path):
    app = web.Application()
    app[HUB] = hub
    app[PATH] = config_path
    app.router.add_get("/", index)
    app.router.add_get("/api/config", get_config)
    app.router.add_put("/api/config", put_config)
    app.router.add_get("/api/live", get_live)
    app.router.add_post("/api/command", post_command)
    return app


PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Del Sol cluster settings</title>
<style>
:root{--bg:#0b0d10;--card:#151a20;--fg:#eae6da;--mut:#7d8791;--acc:#ffb400;--ok:#3ac569;--bad:#e02020;--line:#232a32}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.4 -apple-system,system-ui,sans-serif}
header{position:sticky;top:0;background:#000;padding:12px 16px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--line)}
h1{font-size:18px;margin:0}h2{font-size:14px;color:var(--mut);text-transform:uppercase;letter-spacing:.08em;margin:22px 16px 8px}
.card{background:var(--card);margin:0 12px 10px;border-radius:12px;padding:12px 14px}
.row{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:6px 0;border-bottom:1px solid var(--line)}.row:last-child{border:0}
label{color:var(--mut);font-size:14px}input[type=number],input[type=text],select{width:110px;background:#000;color:var(--fg);border:1px solid #333;border-radius:8px;padding:8px;font-size:16px;text-align:right}
select{width:180px;text-align:left}
input[type=checkbox]{width:22px;height:22px}
input[type=color]{width:64px;height:36px;padding:2px;border:1px solid #333;border-radius:8px;background:#000}
.rule{display:grid;grid-template-columns:1fr 1fr;gap:6px 12px}.rule .full{grid-column:1/3;display:flex;justify-content:space-between;align-items:center;font-weight:600}
.live{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;text-align:center}.live div{background:#000;border-radius:8px;padding:8px}.live b{display:block;font-size:22px}.live small{color:var(--mut)}
button{background:var(--acc);color:#000;border:0;border-radius:10px;padding:12px 16px;font-size:16px;font-weight:600}button.sec{background:#333;color:var(--fg)}
.bar{position:sticky;bottom:0;background:#000;padding:10px 12px;display:flex;gap:8px;border-top:1px solid var(--line)}.bar button{flex:1}
#msg{font-size:14px;color:var(--mut)}.badge{padding:2px 8px;border-radius:99px;font-size:12px;background:#333}.badge.ok{background:var(--ok);color:#000}.badge.bad{background:var(--bad)}
.hint{color:var(--mut);font-size:12px;margin:4px 16px}
</style></head><body>
<header><h1>Del Sol cluster</h1><span id="state" class="badge">…</span></header>

<h2>Live</h2>
<div class="card"><div class="live" id="live"></div>
<div class="row" style="margin-top:8px"><span id="alarms">no alarms</span><button class="sec" onclick="cmd('ack_alarms')">Ack</button></div></div>

<h2>Bluetooth</h2>
<div class="card">
<div class="row"><label>ECU MAC</label><input type="text" id="mac" style="width:190px;text-align:left"></div>
<div class="row"><label>RFCOMM channel</label><input type="number" id="rfcomm_channel" min="1" max="30"></div>
<div class="row"><label>Poll rate (Hz)</label><input type="number" id="poll_hz" min="1" max="50" step="1"></div>
<div class="row"><label>Free ECU for SManager / phone app</label><span><button class="sec" onclick="cmd('release_bt')">Release</button> <button class="sec" onclick="cmd('resume_bt')">Resume</button></span></div>
</div>

<h2>Display</h2>
<div class="card" id="display"></div>
<p class="hint">Pick what each tile shows (always 4 big + 3 small). Hide the rpm bar to give the tiles the whole screen. The cluster applies saved changes within a couple of seconds — no restart.</p>

<h2>Shift light (rpm)</h2>
<div class="card" id="shift"></div>

<h2>Alarms</h2>
<div id="rules"></div>
<p class="hint">Trip = value goes beyond warn/critical for N consecutive samples. Clear = value must come back past this before the alarm resets (deadband). Critical alarms latch until Ack.</p>

<h2>Calibration (scaling overrides)</h2>
<div class="card" id="scaling"></div>
<p class="hint">Leave blank to use the built-in factor. CT_RPM: compare the Live rpm above against the tacho and adjust until they match. CT_RETARD: compare against a known timing-retard value. Saving a calibration change reconnects to the ECU.</p>

<div class="bar"><span id="msg" style="flex:2;align-self:center"></span><button class="sec" onclick="loadAll()">Reload</button><button onclick="save()">Save</button></div>

<script>
const $=s=>document.querySelector(s);let cfg={};
const TYPES=%TYPES%;const TILES=%TILES%;const TILE_DEF=%TILE_DEF%;const THEME=%THEME%;const STYLES=%STYLES%;
const LIVE=[["rpm","rpm",0],["map_kpa","kPa",1],["boost_psi","psi",1],["ect_c","°C",0],["iat_c","°C",0],["vbat","V",1],["tps","%",0],["knock_retard","° ret",1],["shift_stage","shift",0]];
function num(id,v,step){return `<input type="number" id="${id}" value="${v??''}" step="${step||'any'}">`}
async function loadAll(){cfg=await (await fetch('/api/config')).json();
 $('#mac').value=cfg.mac||'';$('#rfcomm_channel').value=cfg.rfcomm_channel||1;$('#poll_hz').value=cfg.poll_hz||10;
 const ui=cfg.ui||{};const sel=(id,v)=>`<select id="${id}">`+TILES.map(t=>`<option ${t===v?'selected':''}>${t}</option>`).join('')+'</select>';
 $('#display').innerHTML=
  `<div class="row"><label>show rpm bar</label><input type="checkbox" id="ui_show_rpm" ${ui.show_rpm===false?'':'checked'}></div>`+
  `<div class="row"><label>units</label><select id="ui_units"><option ${ui.units!=='imperial'?'selected':''}>metric</option><option ${ui.units==='imperial'?'selected':''}>imperial</option></select></div>`+
  `<div class="row"><label>big tile style</label><select id="ui_tile_style">`+STYLES.map(s=>`<option ${s===(ui.tile_style||'digital')?'selected':''}>${s}</option>`).join('')+`</select></div>`+
  `<div class="row"><label>danger-to-manifold rpm (blank = off)</label><input type="number" id="ui_danger_rpm" min="1000" max="20000" step="100" value="${ui.danger_rpm??''}"></div>`+
  TILE_DEF.tiles_big.map((d,i)=>`<div class="row"><label>big tile ${i+1}</label>${sel('big_'+i,(ui.tiles_big||TILE_DEF.tiles_big)[i]||d)}</div>`).join('')+
  TILE_DEF.tiles_small.map((d,i)=>`<div class="row"><label>small tile ${i+1}</label>${sel('small_'+i,(ui.tiles_small||TILE_DEF.tiles_small)[i]||d)}</div>`).join('')+
  Object.keys(THEME).map(k=>`<div class="row"><label>${k} colour</label><input type="color" id="th_${k}" value="${(ui.theme||{})[k]||THEME[k]}"></div>`).join('');
 $('#shift').innerHTML=['amber','red','flash'].map(k=>`<div class="row"><label>${k}</label>${num('shift_'+k,cfg.shift_light?.[k],100)}</div>`).join('');
 $('#rules').innerHTML=Object.entries(cfg.alarms||{}).map(([id,r])=>`<div class="card rule" data-id="${id}">
  <div class="full"><span>${id}<br><small style="color:var(--mut);font-weight:400">${r.channel} · ${r.type==='rate'?'increase per '+r.window_s+'s':r.direction}</small></span><input type="checkbox" id="en_${id}" ${r.enabled?'checked':''}></div>
  <div class="row"><label>warn</label>${num('warn_'+id,r.warn)}</div><div class="row"><label>critical</label>${num('critical_'+id,r.critical)}</div>
  <div class="row"><label>clear</label>${num('clear_'+id,r.clear)}</div><div class="row"><label>samples</label>${num('samples_'+id,r.samples,1)}</div>
  ${r.rpm_min!==undefined?`<div class="row"><label>only when rpm ></label>${num('rpm_min_'+id,r.rpm_min,100)}</div>`:''}
  ${r.tps_min!==undefined?`<div class="row"><label>only when tps ></label>${num('tps_min_'+id,r.tps_min,5)}</div>`:''}
  ${r.sustained_s!==undefined?`<div class="row"><label>sustained (s)</label>${num('sustained_s_'+id,r.sustained_s,1)}</div>`:''}
  </div>`).join('');
 $('#scaling').innerHTML=TYPES.map(t=>{const v=cfg.scaling_overrides?.[t];const val=(v&&typeof v==='object')?v.scale:v;return `<div class="row"><label>${t}</label>${num('sc_'+t,val)}</div>`}).join('');
 $('#msg').textContent='';}
function val(id){const e=document.getElementById(id);return e&&e.value!==''?Number(e.value):undefined}
async function save(){const p={mac:$('#mac').value,rfcomm_channel:val('rfcomm_channel'),poll_hz:val('poll_hz'),
 shift_light:{amber:val('shift_amber'),red:val('shift_red'),flash:val('shift_flash')},alarms:{},scaling_overrides:{},
 ui:{show_rpm:$('#ui_show_rpm').checked,units:$('#ui_units').value,tile_style:$('#ui_tile_style').value,
  danger_rpm:$('#ui_danger_rpm').value===''?null:Number($('#ui_danger_rpm').value),
  tiles_big:TILE_DEF.tiles_big.map((d,i)=>$('#big_'+i).value),
  tiles_small:TILE_DEF.tiles_small.map((d,i)=>$('#small_'+i).value),
  theme:Object.fromEntries(Object.keys(THEME).map(k=>[k,$('#th_'+k).value]))}};
 for(const [id,r] of Object.entries(cfg.alarms||{})){const o={enabled:$('#en_'+id).checked};
  for(const f of ['warn','critical','clear','samples','rpm_min','tps_min','sustained_s']){const v=val(f+'_'+id);if(v!==undefined)o[f]=v}p.alarms[id]=o}
 for(const t of TYPES){const v=val('sc_'+t);p.scaling_overrides[t]=v===undefined?'':v}
 const r=await fetch('/api/config',{method:'PUT',headers:{'content-type':'application/json'},body:JSON.stringify(p)});const j=await r.json();
 $('#msg').textContent=j.ok?'Saved & applied':'Error: '+j.error;$('#msg').style.color=j.ok?'var(--ok)':'var(--bad)';if(j.ok)loadAll();}
async function cmd(c){const r=await fetch('/api/command',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({cmd:c})});$('#msg').textContent=(await r.json()).ok?c+' sent':'rejected'}
async function live(){try{const s=await (await fetch('/api/live')).json();const st=$('#state');st.textContent=s.state+(s.stale?' · stale':'');st.className='badge '+(s.state==='STREAMING'&&!s.stale?'ok':(s.state==='ERROR'?'bad':''));
 $('#live').innerHTML=LIVE.map(([k,u,d])=>`<div><b>${s.d?.[k]==null?'–':Number(s.d[k]).toFixed(d)}</b><small>${k} ${u}</small></div>`).join('');
 $('#alarms').textContent=s.a?.length?s.a.map(a=>`${a.level.toUpperCase()} ${a.id} (${Number(a.value).toFixed(1)})${a.latched?' latched':''}`).join(' · '):'no alarms';}catch(e){$('#state').textContent='no daemon'}}
loadAll();live();setInterval(live,500);
</script></body></html>
""".replace("%TYPES%", str(list(TYPE_NAMES)).replace("'", '"')) \
   .replace("%TILES%", str(list(SENSORS)).replace("'", '"')) \
   .replace("%TILE_DEF%", str({"tiles_big": DEFAULT_TILES["big"],
                               "tiles_small": DEFAULT_TILES["small"]}).replace("'", '"')) \
   .replace("%THEME%", str(DEFAULT_THEME).replace("'", '"')) \
   .replace("%STYLES%", str(list(TILE_STYLES)).replace("'", '"'))
