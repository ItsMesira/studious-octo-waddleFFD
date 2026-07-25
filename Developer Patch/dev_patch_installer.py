#!/usr/bin/env python3
"""
Dev Patch Installer — temporarily runs as the bot to install developer tools.
Upload this file via the main bot's !devpatchupload command.
The installer replaces the main bot, installs extras, then restores the original.

UPDATE_TYPE: Full Installer (HDMI Dashboard + Web UI + ngrok tunnel)
"""
import os, sys, shutil, asyncio, subprocess, time, json
from datetime import datetime

import discord
from discord.ext import commands

REPO_DIR = os.environ.get("REPO_DIR", "/home/sira/fishfeeder")

def load_dotenv():
    env_path = os.path.join(REPO_DIR, ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, val = line.split('=', 1)
                    val = val.strip().strip('"').strip("'")
                    os.environ[key.strip()] = val

load_dotenv()

TOKEN = os.environ.get("DISCORD_TOKEN", "")
OWNER_ID = int(os.environ.get("OWNER_ID", 0))
LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID", 0))
WEB_PORT = 5000
NGROK_AUTH = os.environ.get("NGROK_AUTH", "")
NGROK_DOMAIN = os.environ.get("NGROK_DOMAIN", "")
NGROK_CONFIG_FILE = os.path.join(REPO_DIR, "ngrok_config.json")

BACKUP_PATH = __file__ + ".backup"
WEB_FILE = os.path.join(REPO_DIR, "web_dashboard.py")
GUI_FILE = os.path.join(REPO_DIR, "pi_gui.py")

WEB_CODE = r"""import os, json, time
from flask import Flask, jsonify, request, make_response, render_template_string

REPO = "/home/sira/fishfeeder"
PORT = int(os.environ.get("WEB_PORT", 5000))

T = {
"en":{"title":"FishFeeder","dash":"Dashboard","ctrl":"Controls","bat":"Battery","motor":"Motor","sensor":"Sensor","schedule":"Schedule","last_feed":"Last Feed","feed":"Feed (s)","reverse":"Reverse (s)","stop":"STOP","kill":"KILL","back":"Back","idle":"IDLE","running":"FORWARD","rev":"REVERSE","stopped":"STOPPED","pressed":"PRESSED","open":"OPEN","na":"N/A","next":"Next","total":"Total","today":"Today","tomorrow":"Tomorrow","ago":"{:.0f}s ago","launch":"Launching...","cmd_sent":"Command sent","cmd_fail":"Failed","no_data":"---","lang_en":"EN","lang_th":"TH","lang_zh":"ZH","feed_ok":"Feed sent","rev_ok":"Reverse sent","stop_ok":"Stop sent","kill_ok":"Kill sent"},
"th":{"title":"\u0e1b\u0e25\u0e32\u0e2d\u0e31\u0e08\u0e09\u0e23\u0e34\u0e22\u0e30","dash":"\u0e41\u0e14\u0e0a\u0e1a\u0e2d\u0e23\u0e4c\u0e14","ctrl":"\u0e04\u0e27\u0e1a\u0e04\u0e38\u0e21","bat":"\u0e41\u0e1a\u0e15\u0e40\u0e15\u0e2d\u0e23\u0e35\u0e48","motor":"\u0e21\u0e40\u0e15\u0e2d\u0e23\u0e4c","sensor":"\u0e40\u0e0b\u0e47\u0e19\u0e40\u0e0b\u0e2d\u0e23\u0e4c","schedule":"\u0e15\u0e32\u0e23\u0e32\u0e07","last_feed":"\u0e21\u0e37\u0e2d\u0e25\u0e48\u0e32\u0e2a\u0e38\u0e14","feed":"\u0e1b\u0e49\u0e2d\u0e19 (\u0e27\u0e34\u0e19\u0e32\u0e17\u0e35)","reverse":"\u0e22\u0e49\u0e2d\u0e19 (\u0e27\u0e34\u0e19\u0e32\u0e17\u0e35)","stop":"\u0e2b\u0e22\u0e38\u0e14","kill":"\u0e09\u0e38\u0e14\u0e40\u0e09\u0e34\u0e19","back":"\u0e01\u0e25\u0e31\u0e1a","idle":"\u0e27\u0e48\u0e32\u0e07","running":"\u0e17\u0e33\u0e07\u0e32\u0e19","rev":"\u0e22\u0e49\u0e2d\u0e19","stopped":"\u0e2b\u0e22\u0e38\u0e14","pressed":"\u0e01\u0e14","open":"\u0e40\u0e1b\u0e34\u0e14","na":"N/A","next":"\u0e16\u0e31\u0e14\u0e44\u0e1b","total":"\u0e17\u0e31\u0e49\u0e07\u0e2b\u0e21\u0e14","today":"\u0e27\u0e31\u0e19\u0e19\u0e35\u0e49","tomorrow":"\u0e1e\u0e23\u0e38\u0e48\u0e07\u0e19\u0e35\u0e49","ago":"{:.0f}\u0e27\u0e34\u0e19\u0e32\u0e17\u0e35\u0e17\u0e35\u0e48\u0e41\u0e25\u0e49\u0e27","launch":"\u0e01\u0e33\u0e25\u0e31\u0e07\u0e40\u0e1b\u0e34\u0e14...","cmd_sent":"\u0e2a\u0e48\u0e07\u0e04\u0e33\u0e2a\u0e31\u0e48\u0e07\u0e41\u0e25\u0e49\u0e27","cmd_fail":"\u0e25\u0e49\u0e21\u0e40\u0e2b\u0e25\u0e27","no_data":"---","lang_en":"EN","lang_th":"TH","lang_zh":"ZH","feed_ok":"\u0e2a\u0e48\u0e07\u0e1b\u0e49\u0e2d\u0e19\u0e41\u0e25\u0e49\u0e27","rev_ok":"\u0e2a\u0e48\u0e07\u0e22\u0e49\u0e2d\u0e19\u0e41\u0e25\u0e49\u0e27","stop_ok":"\u0e2a\u0e48\u0e07\u0e2b\u0e22\u0e38\u0e14\u0e41\u0e25\u0e49\u0e27","kill_ok":"\u0e2a\u0e48\u0e07\u0e09\u0e38\u0e14\u0e40\u0e09\u0e34\u0e19\u0e41\u0e25\u0e49\u0e27"},
"zh":{"title":"\u0e1b\u0e25\u0e32\u0e2d\u0e31\u0e08\u0e09\u0e23\u0e34\u0e22\u0e30","dash":"\u0e34\u0e40\u0e2d\u0e47\u0e01\u0e01\u0e23\u0e30\u0e14\u0e32\u0e19\u0e04\u0e27\u0e1a\u0e04\u0e38\u0e21","ctrl":"\u0e04\u0e27\u0e1a\u0e04\u0e38\u0e21","bat":"\u0e44\u0e1f\u0e1f\u0e49\u0e32","motor":"\u0e40\u0e04\u0e37\u0e48\u0e2d\u0e07\u0e08\u0e31\u0e01\u0e23","sensor":"\u0e40\u0e0b\u0e47\u0e19\u0e40\u0e0b\u0e2d\u0e23\u0e4c","schedule":"\u0e01\u0e33\u0e2b\u0e19\u0e14\u0e01\u0e32\u0e23","last_feed":"\u0e25\u0e48\u0e32\u0e2a\u0e38\u0e14","feed":"\u0e43\u0e2b\u0e49\u0e2d\u0e32\u0e2b\u0e32\u0e23 (\u0e27\u0e34\u0e19\u0e32\u0e17\u0e35)","reverse":"\u0e22\u0e49\u0e2d\u0e19\u0e01\u0e25\u0e31\u0e1a (\u0e27\u0e34\u0e19\u0e32\u0e17\u0e35)","stop":"\u0e2b\u0e22\u0e38\u0e14","kill":"\u0e09\u0e38\u0e14\u0e40\u0e09\u0e34\u0e19","back":"\u0e01\u0e25\u0e31\u0e1a","idle":"\u0e27\u0e48\u0e32\u0e07","running":"\u0e17\u0e33\u0e07\u0e32\u0e19","rev":"\u0e22\u0e49\u0e2d\u0e19","stopped":"\u0e2b\u0e22\u0e38\u0e14","pressed":"\u0e01\u0e14","open":"\u0e40\u0e1b\u0e34\u0e14","na":"N/A","next":"\u0e16\u0e31\u0e14\u0e44\u0e1b","total":"\u0e17\u0e31\u0e49\u0e07\u0e2b\u0e21\u0e14","today":"\u0e27\u0e31\u0e19\u0e19\u0e35\u0e49","tomorrow":"\u0e1e\u0e23\u0e38\u0e48\u0e07\u0e19\u0e35\u0e49","ago":"{:.0f}\u0e27\u0e34\u0e19\u0e32\u0e17\u0e35\u0e17\u0e35\u0e48\u0e41\u0e25\u0e49\u0e27","launch":"\u0e01\u0e33\u0e25\u0e31\u0e07\u0e40\u0e1b\u0e34\u0e14...","cmd_sent":"\u0e2a\u0e48\u0e07\u0e04\u0e33\u0e2a\u0e31\u0e48\u0e07\u0e41\u0e25\u0e49\u0e27","cmd_fail":"\u0e25\u0e49\u0e21\u0e40\u0e2b\u0e25\u0e27","no_data":"---","lang_en":"EN","lang_th":"TH","lang_zh":"ZH","feed_ok":"\u0e2a\u0e48\u0e07\u0e1b\u0e49\u0e2d\u0e19\u0e41\u0e25\u0e49\u0e27","rev_ok":"\u0e2a\u0e48\u0e07\u0e22\u0e49\u0e2d\u0e19\u0e41\u0e25\u0e49\u0e27","stop_ok":"\u0e2a\u0e48\u0e07\u0e2b\u0e22\u0e38\u0e14\u0e41\u0e25\u0e49\u0e27","kill_ok":"\u0e2a\u0e48\u0e07\u0e09\u0e38\u0e14\u0e40\u0e09\u0e34\u0e19\u0e41\u0e25\u0e49\u0e27"}
}

HTML = '''<!DOCTYPE html>
<html lang="{{lang}}">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{{t["title"]}}</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');
*{font-family:'Inter',sans-serif}
body{background:#0f172a;color:#fff;min-height:100vh}
.nav{background:#1e293b;border-radius:16px;padding:4px;display:inline-flex;gap:4px;margin-bottom:24px}
.nav button{padding:10px 28px;border-radius:12px;font-weight:600;font-size:.95rem;transition:all .15s;border:none;cursor:pointer}
.nav button.active{background:#0ea5e9;color:#fff}
.nav button:not(.active){background:transparent;color:#94a3b8}
.nav button:not(.active):hover{color:#e2e8f0}
.card{background:#1e293b;border-radius:16px;padding:20px;transition:all .2s;border:2px solid transparent}
.card:hover{border-color:var(--ac);transform:translateY(-2px)}
.val{font-size:1.8rem;font-weight:700;margin-top:6px}
.lbl{font-size:.8rem;color:#94a3b8;text-transform:uppercase;letter-spacing:.5px}
.tag{background:rgba(255,255,255,.06);padding:2px 10px;border-radius:999px;font-size:.75rem}
.btn{display:block;width:100%;padding:16px;border-radius:12px;font-weight:700;font-size:1.1rem;text-align:center;border:none;cursor:pointer;transition:all .15s}
.btn:hover{transform:translateY(-1px);filter:brightness(1.1)}
.btn:active{transform:translateY(0);filter:brightness(.95)}
.btn-green{background:#22c55e;color:#fff}
.btn-blue{background:#3b82f6;color:#fff}
.btn-red{background:#ef4444;color:#fff}
.btn-amber{background:#f59e0b;color:#fff}
.btn-outline{background:transparent;border:2px solid #475569;color:#94a3b8}
.btn-outline:hover{border-color:#64748b;color:#e2e8f0}
.inp{width:100%;padding:12px 16px;border-radius:10px;background:#0f172a;border:2px solid #334155;color:#fff;font-size:1rem;text-align:center;outline:none;transition:border .15s}
.inp:focus{border-color:#3b82f6}
.ctrl-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;max-width:400px;margin:0 auto}
.ctrl-full{grid-column:1/-1}
</style></head>
<body>
<div class="max-w-3xl mx-auto px-4 py-6">
<div class="flex flex-wrap items-center justify-between mb-4">
<div><h1 class="text-2xl font-bold text-sky-400">\U0001F41F {{t["title"]}}</h1>
<p class="text-slate-500 text-xs mt-1">{{t["last"]}}: <span id="ts">--</span></p></div>
<div class="flex gap-1.5">
<button onclick="setLang('en')" class="px-2.5 py-1 rounded-lg text-xs font-medium {{'bg-sky-500 text-white' if lang=='en' else 'bg-slate-700 text-slate-400 hover:bg-slate-600'}}">{{t["lang_en"]}}</button>
<button onclick="setLang('th')" class="px-2.5 py-1 rounded-lg text-xs font-medium {{'bg-sky-500 text-white' if lang=='th' else 'bg-slate-700 text-slate-400 hover:bg-slate-600'}}">{{t["lang_th"]}}</button>
<button onclick="setLang('zh')" class="px-2.5 py-1 rounded-lg text-xs font-medium {{'bg-sky-500 text-white' if lang=='zh' else 'bg-slate-700 text-slate-400 hover:bg-slate-600'}}">{{t["lang_zh"]}}</button>
</div></div>

<div class="nav" id="nav">
<button class="active" onclick="showView('dash')" id="nav-dash">{{t["dash"]}}</button>
<button onclick="showView('ctrl')" id="nav-ctrl">{{t["ctrl"]}}</button>
</div>

<div id="view-dash">
<div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
<div class="card" style="--ac:#22c55e"><div class="flex items-center justify-between"><span class="lbl">{{t["bat"]}}</span><span class="tag" id="bat_lbl">--</span></div><div class="val" id="bat_val" style="color:#22c55e">--</div><div class="text-xs text-slate-500 mt-0.5" id="bat_sub"></div></div>
<div class="card" style="--ac:#3b82f6"><div class="flex items-center justify-between"><span class="lbl">{{t["motor"]}}</span><span class="tag" id="motor_lbl">--</span></div><div class="val" id="motor_val" style="color:#3b82f6">--</div><div class="text-xs text-slate-500 mt-0.5" id="motor_sub"></div></div>
<div class="card" style="--ac:#eab308"><div class="flex items-center justify-between"><span class="lbl">{{t["sensor"]}}</span><span class="tag" id="sensor_lbl">--</span></div><div class="val" id="sensor_val" style="color:#eab308">--</div><div class="text-xs text-slate-500 mt-0.5" id="sensor_sub"></div></div>
<div class="card" style="--ac:#a855f7"><div class="flex items-center justify-between"><span class="lbl">{{t["schedule"]}}</span><span class="tag" id="sched_lbl">--</span></div><div class="val" id="sched_val" style="color:#a855f7;font-size:1.4rem">--</div><div class="text-xs text-slate-500 mt-0.5" id="sched_sub"></div></div>
</div>
<div class="grid grid-cols-2 gap-3 mt-3">
<div class="card" style="--ac:#f59e0b"><div class="flex items-center justify-between"><span class="lbl">{{t["last_feed"]}}</span><span class="tag" id="feed_lbl">--</span></div><div class="val" id="feed_val" style="color:#f59e0b;font-size:1.2rem">--</div><div class="text-xs text-slate-500 mt-0.5" id="feed_sub"></div></div>
</div></div>

<div id="view-ctrl" style="display:none">
<div class="ctrl-grid">
<div class="ctrl-full"><label class="lbl block text-center mb-1.5">{{t["feed"]}}</label>
<input class="inp" id="feed_secs" type="number" value="5" min="1" max="30">
<button class="btn btn-green mt-2" onclick="sendCmd('feed')">{{t["feed"]}}</button></div>
<div class="ctrl-full"><label class="lbl block text-center mb-1.5">{{t["reverse"]}}</label>
<input class="inp" id="rev_secs" type="number" value="3" min="1" max="30">
<button class="btn btn-blue mt-2" onclick="sendCmd('reverse')">{{t["reverse"]}}</button></div>
<button class="btn btn-amber ctrl-full" onclick="sendCmd('stop')">{{t["stop"]}}</button>
<button class="btn btn-red ctrl-full" onclick="sendCmd('kill')">{{t["kill"]}}</button>
<div class="ctrl-full text-center text-sm text-slate-500" id="cmd_status" style="min-height:20px"></div>
</div></div>

<script>
const T = {{t|tojson|safe}}; let lang = "{{lang}}";
function setLang(l){lang=l;document.cookie="lang="+l+";path=/;max-age=31536000";window.location.reload();}
function showView(v){document.getElementById("view-dash").style.display=v==="dash"?"":"none";document.getElementById("view-ctrl").style.display=v==="ctrl"?"":"none";document.getElementById("nav-dash").className=v==="dash"?"active":"";document.getElementById("nav-ctrl").className=v==="ctrl"?"active":"";}

async function sendCmd(a){const s=a==="feed"?document.getElementById("feed_secs").value:a==="reverse"?document.getElementById("rev_secs").value:0;document.getElementById("cmd_status").textContent=T.launch;try{const r=await fetch("/api/"+a,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({seconds:parseInt(s)||5})});const d=await r.json();document.getElementById("cmd_status").textContent=d.ok?T.cmd_sent:T.cmd_fail;}catch(e){document.getElementById("cmd_status").textContent=T.cmd_fail;}}

async function poll(){try{
const r=await fetch("/api/status");const s=await r.json();const now=Math.floor(Date.now()/1000);const ago=s.ts?now-s.ts:0;
const bv=s.battery_voltage;
if(bv!==null&&bv!==undefined){document.getElementById("bat_val").textContent=bv.toFixed(2)+"V";document.getElementById("bat_sub").textContent=s.battery_current!==null?s.battery_current.toFixed(0)+"mA":"";if(bv>7)document.getElementById("bat_lbl").textContent=((bv-6.5)/(8.4-6.5)*100).toFixed(0)+"%";else document.getElementById("bat_lbl").textContent="OK";}
else{document.getElementById("bat_val").textContent=T.na;document.getElementById("bat_lbl").textContent="";}
const m=s.motor||"IDLE";const mm={FORWARD:T.running,REVERSE:T.rev,STOPPED:T.stopped,IDLE:T.idle};
document.getElementById("motor_val").textContent=mm[m]||m;document.getElementById("motor_val").style.color=(m==="FORWARD"||m==="REVERSE")?"#22c55e":"#3b82f6";
document.getElementById("motor_sub").textContent=m==="FORWARD"||m==="REVERSE"?T.ago.replace("{:.0f}",ago.toFixed(0)):"";
const sn=s.sensor||"OPEN";const sv=sn==="PRESSED"?T.pressed:T.open;
document.getElementById("sensor_val").textContent=sv;document.getElementById("sensor_val").style.color=sn==="PRESSED"?"#ef4444":"#eab308";
document.getElementById("sensor_sub").textContent=sn==="PRESSED"?T.ago.replace("{:.0f}",ago.toFixed(0)):"";
const sc=s.schedule;const lst=s.last_feed;
if(sc&&sc.next){document.getElementById("sched_val").textContent=sc.next;document.getElementById("sched_sub").textContent=sc.total+" "+T.total;document.getElementById("sched_lbl").textContent=sc.next_idx===0?T.today:T.tomorrow;}
else{document.getElementById("sched_val").textContent=T.no_data;document.getElementById("sched_sub").textContent="";document.getElementById("sched_lbl").textContent="";}
if(lst){const fd=new Date(lst*1000);const diff=now-lst;document.getElementById("feed_val").textContent=fd.toLocaleTimeString();document.getElementById("feed_sub").textContent=diff<3600?Math.floor(diff/60)+"m ago":Math.floor(diff/3600)+"h ago";document.getElementById("feed_lbl").textContent=fd.toLocaleDateString();}
else{document.getElementById("feed_val").textContent=T.no_data;document.getElementById("feed_sub").textContent="";document.getElementById("feed_lbl").textContent="";}
if(s.ts){const d=new Date(s.ts*1000);document.getElementById("ts").textContent=d.toLocaleTimeString();}
}catch(e){console.log("poll err",e);}setTimeout(poll,2000);}
poll();
</script></body></html>'''

app = Flask(__name__)

def read_state():
    try:
        with open(os.path.join(REPO, "shared_state.json")) as f:
            return json.load(f)
    except: return {}

def read_json(file):
    try:
        with open(os.path.join(REPO, file)) as f:
            return json.load(f)
    except: return {}

@app.route("/")
def index():
    lang = request.args.get("lang") or request.cookies.get("lang", "en")
    if lang not in T: lang = "en"
    resp = make_response(render_template_string(HTML, t=T[lang], lang=lang))
    resp.set_cookie("lang", lang, max_age=86400*365)
    return resp

@app.route("/api/status")
def api_status():
    state = read_state()
    now = time.time()
    sched = read_json("schedules.json")
    schedule_info = {}
    if sched:
        t = time.localtime()
        curr = t.tm_hour * 60 + t.tm_min
        times = []
        for e in sched:
            if isinstance(e, dict):
                h, m = int(e.get("hour", 0)), int(e.get("minute", 0))
            elif isinstance(e, str) and ":" in e:
                h, m = map(int, e.split(":"))
            else: continue
            times.append((h, m))
        times.sort(key=lambda x: x[0]*60+x[1])
        nxt = None; idx = -1
        for i, (h, m) in enumerate(times):
            if h*60+m > curr:
                nxt = f"{h:02d}:{m:02d}"; idx = i; break
        if not nxt and times:
            nxt = f"{times[0][0]:02d}:{times[0][1]:02d}"
            schedule_info["next"] = nxt
            schedule_info["next_idx"] = 0
            schedule_info["total"] = len(times)
        elif nxt:
            schedule_info["next"] = nxt
            schedule_info["next_idx"] = idx
            schedule_info["total"] = len(times)
    state["schedule"] = schedule_info
    lf = None
    st = read_json("state.json")
    if st and "last_feed_time" in st:
        try:
            lft = st["last_feed_time"]
            if isinstance(lft, (int, float)):
                lf = lft
            elif isinstance(lft, str):
                lf = time.mktime(time.strptime(lft, "%Y-%m-%d %H:%M:%S"))
        except: pass
    if lf is None and "last_feed" in state:
        lf = state["last_feed"]
    if lf is not None:
        state["last_feed"] = lf
    return jsonify(state)

@app.route("/api/<action>", methods=["POST"])
def api_command(action):
    data = request.get_json(force=True, silent=True) or {}
    seconds = int(data.get("seconds", 5))
    cmd = {"action": action, "seconds": seconds, "ts": time.time()}
    try:
        with open(os.path.join(REPO, "command.json"), "w") as f:
            json.dump(cmd, f)
        return jsonify({"ok": True, "action": action})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
"""

WEB_SERVICE = """[Unit]
Description=FishFeeder Web Dashboard
After=network.target
[Service]
User=sira
WorkingDirectory=/home/sira/fishfeeder
ExecStart=/home/sira/feederbot/bin/python /home/sira/fishfeeder/web_dashboard.py
Restart=always
RestartSec=3
Environment=WEB_PORT=5000
[Install]
WantedBy=multi-user.target
"""

GUI_CODE = r'''import tkinter as tk
from tkinter import font
import json, os, time

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEDULE_FILE = os.path.join(REPO_DIR, "schedules.json")
BATTERY_CONFIG = os.path.join(REPO_DIR, "battery_config.json")
SHARED_STATE = os.path.join(REPO_DIR, "shared_state.json")
DEV_PATCH_VERSION_FILE = os.path.join(REPO_DIR, ".dev_patch_version")
INSTALLER_VERSION = "1.0.0"

def read_shared_state():
    try:
        if os.path.exists(SHARED_STATE):
            with open(SHARED_STATE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {}

class FishFeederGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("FishFeeder Real-Time Dashboard")
        self.configure(bg="#0f172a")
        self.bind("<Escape>", lambda e: self.destroy())
        self.overrideredirect(True)
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{sw}x{sh}+0+0")

        self.title_font = font.Font(family="Helvetica", size=36, weight="bold")
        self.card_font = font.Font(family="Helvetica", size=18, weight="bold")
        self.value_font = font.Font(family="Helvetica", size=36, weight="bold")
        self.sub_font = font.Font(family="Helvetica", size=18)

        self.main_container = tk.Frame(self, bg="#0f172a")
        self.main_container.place(relx=0.5, rely=0.5, anchor="center")

        title_lbl = tk.Label(self.main_container, text="\U0001F41F FishFeeder Dashboard", font=self.title_font, bg="#0f172a", fg="#38bdf8")
        title_lbl.grid(row=0, column=0, columnspan=2, pady=(0, 50))

        self.cards = {}
        self.cards["battery"] = self.create_card(self.main_container, 1, 0, "\U0001F50B BATTERY STATUS", "#1e293b", "#22c55e")
        self.cards["motor"] = self.create_card(self.main_container, 1, 1, "\u2699\uFE0F MOTOR STATUS", "#1e293b", "#3b82f6")
        self.cards["sensor"] = self.create_card(self.main_container, 2, 0, "\U0001F518 SENSOR (TS)", "#1e293b", "#eab308")
        self.cards["schedule"] = self.create_card(self.main_container, 2, 1, "\U0001F552 NEXT SCHEDULE", "#1e293b", "#a855f7")

        # Footer — bot & patch version, update status
        self.footer = tk.Frame(self.main_container, bg="#1e293b", padx=20, pady=12)
        self.footer.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        self.bot_ver_lbl = tk.Label(self.footer, text="Bot v-", font=("Helvetica", 13, "bold"),
                                    bg="#1e293b", fg="#94a3b8")
        self.bot_ver_lbl.pack(side=tk.LEFT, padx=(0, 20))

        self.patch_ver_lbl = tk.Label(self.footer, text="Patch v-", font=("Helvetica", 13, "bold"),
                                      bg="#1e293b", fg="#94a3b8")
        self.patch_ver_lbl.pack(side=tk.LEFT, padx=(0, 20))

        self.status_lbl = tk.Label(self.footer, text="Starting...", font=("Helvetica", 13, "bold"),
                                   bg="#1e293b", fg="#facc15")
        self.status_lbl.pack(side=tk.LEFT, padx=(0, 20))

        self.last_upd_lbl = tk.Label(self.footer, text="", font=("Helvetica", 11),
                                     bg="#1e293b", fg="#64748b")
        self.last_upd_lbl.pack(side=tk.RIGHT)

        self.changes_lbl = tk.Label(self.footer, text="", font=("Helvetica", 10),
                                    bg="#1e293b", fg="#475569")
        self.changes_lbl.pack(side=tk.RIGHT, padx=(0, 15))

        self.motor_status = "IDLE"
        self.sensor_status = "OPEN"
        self._after_ids = {}

        self.update_battery()
        self.update_schedule()
        self.poll_hardware()
        self.update_log_ui()
        self.update_bot_info()

    def create_card(self, parent, row, col, title, bg_color, accent_color):
        card = tk.Frame(parent, bg=bg_color, padx=20, pady=20, highlightbackground=accent_color, highlightthickness=3, width=380, height=200)
        card.grid(row=row, column=col, padx=20, pady=20)
        card.pack_propagate(False)
        title_lbl = tk.Label(card, text=title, font=self.card_font, bg=bg_color, fg=accent_color)
        title_lbl.pack(anchor="w")
        val_lbl = tk.Label(card, text="--", font=self.value_font, bg=bg_color, fg="#f8fafc")
        val_lbl.pack(expand=True, fill="both")
        return val_lbl

    def update_battery(self):
        aft = self._after_ids.pop("battery", None)
        if aft: self.after_cancel(aft)
        state = read_shared_state()
        v = state.get("battery_voltage")
        if v is not None:
            cfg = {}
            if os.path.exists(BATTERY_CONFIG):
                with open(BATTERY_CONFIG, 'r') as f:
                    cfg = json.load(f)
            empty = cfg.get("empty_voltage")
            full = cfg.get("full_voltage")
            if empty and full:
                pct = (v - empty) / (full - empty) * 100
                pct = max(0, min(100, pct))
                self.cards["battery"].config(text=f"{pct:.0f}%\n({v:.2f}V)", fg="#22c55e")
            else:
                self.cards["battery"].config(text=f"{v:.2f}V", fg="#f8fafc")
        else:
            self.cards["battery"].config(text="N/A", fg="#94a3b8")
        self._after_ids["battery"] = self.after(2000, self.update_battery)

    def update_schedule(self):
        aft = self._after_ids.pop("schedule", None)
        if aft: self.after_cancel(aft)
        try:
            if os.path.exists(SCHEDULE_FILE):
                with open(SCHEDULE_FILE, 'r') as f:
                    data = json.load(f)
                if data:
                    now = time.localtime()
                    curr_mins = now.tm_hour * 60 + now.tm_min
                    parsed = []
                    for entry in data:
                        if isinstance(entry, dict):
                            h = int(entry.get("hour", 0))
                            m = int(entry.get("minute", 0))
                        elif isinstance(entry, str) and ':' in entry:
                            h, m = map(int, entry.split(':'))
                        else:
                            continue
                        parsed.append((h, m))
                    parsed.sort(key=lambda x: x[0] * 60 + x[1])
                    next_time = None
                    for h, m in parsed:
                        if h * 60 + m > curr_mins:
                            next_time = f"{h:02d}:{m:02d}"
                            break
                    if next_time:
                        self.cards["schedule"].config(text=f"{next_time}\n({len(parsed)} total)")
                    else:
                        self.cards["schedule"].config(text=f"Tomorrow\n({len(parsed)} total)")
                else:
                    self.cards["schedule"].config(text="No active")
            else:
                self.cards["schedule"].config(text="No file")
        except Exception:
            self.cards["schedule"].config(text="Error")
        self._after_ids["schedule"] = self.after(10000, self.update_schedule)

    def update_log_ui(self):
        aft = self._after_ids.pop("log_ui", None)
        if aft: self.after_cancel(aft)
        if "RUNNING" in self.motor_status or "REVERSE" in self.motor_status:
            self.cards["motor"].config(text=self.motor_status, fg="#22c55e")
        else:
            self.cards["motor"].config(text=self.motor_status, fg="#f8fafc")
        if "PRESSED" in self.sensor_status:
            self.cards["sensor"].config(text=self.sensor_status, fg="#ef4444")
        else:
            self.cards["sensor"].config(text=self.sensor_status, fg="#f8fafc")
        self._after_ids["log_ui"] = self.after(100, self.update_log_ui)

    def update_bot_info(self):
        aft = self._after_ids.pop("bot_info", None)
        if aft: self.after_cancel(aft)
        state = read_shared_state()
        bv = state.get("bot_version", "?")
        pv = state.get("installer_version")
        self.bot_ver_lbl.config(text=f"Bot v{bv}")
        self.patch_ver_lbl.config(text=f"Patch v{pv}" if pv else "Patch --")

        s = state.get("update_status", "")
        enabled = state.get("auto_update_enabled", True)
        if not enabled:
            self.status_lbl.config(text="Auto-update OFF", fg="#ef4444")
            self.changes_lbl.config(text="")
        elif s == "up_to_date":
            self.status_lbl.config(text="Up to date", fg="#22c55e")
            self.changes_lbl.config(text="")
        elif s == "checking":
            self.status_lbl.config(text="Checking...", fg="#facc15")
        elif s == "updating_bot":
            self.status_lbl.config(text="Updating Bot...", fg="#3b82f6")
            ch = state.get("update_changes", [])
            self.changes_lbl.config(text=ch[0] if ch else "")
        elif s == "updating_dev_patch":
            ut = state.get("update_type", "")
            label = f"Updating {ut}..." if ut else "Updating Dev Patch..."
            self.status_lbl.config(text=label, fg="#a855f7")
            ch = state.get("update_changes", [])
            self.changes_lbl.config(text=ch[0] if ch else "")
        elif s == "installing_dev_patch":
            ut = state.get("update_type", "")
            label = f"Installing {ut}..." if ut else "Installing Dev Patch (new)..."
            self.status_lbl.config(text=label, fg="#a855f7")
        elif s == "restarting":
            self.status_lbl.config(text="Restarting...", fg="#f97316")
        elif s == "error":
            self.status_lbl.config(text="Error — check logs", fg="#ef4444")
        else:
            self.status_lbl.config(text=s.replace("_", " ").title(), fg="#94a3b8")

        lu = state.get("last_updated")
        if lu:
            ts = time.strftime("%H:%M %Y-%m-%d", time.localtime(lu))
            self.last_upd_lbl.config(text=f"Updated: {ts}")
        elif state.get("last_update_check"):
            ts = time.strftime("%H:%M %Y-%m-%d", time.localtime(state["last_update_check"]))
            self.last_upd_lbl.config(text=f"Checked: {ts}")
        else:
            self.last_upd_lbl.config(text="")

        self._after_ids["bot_info"] = self.after(5000, self.update_bot_info)

    def poll_hardware(self):
        aft = self._after_ids.pop("hardware", None)
        if aft: self.after_cancel(aft)
        state = read_shared_state()
        raw = state.get("motor", "IDLE")
        if raw == "FORWARD":
            self.motor_status = "RUNNING \u25B6"
        elif raw == "REVERSE":
            self.motor_status = "REVERSE \u25C0"
        elif raw == "STOPPED":
            self.motor_status = "STOPPED"
        else:
            self.motor_status = raw
        self.sensor_status = state.get("sensor", "OPEN")
        self._after_ids["hardware"] = self.after(100, self.poll_hardware)

if __name__ == "__main__":
    if not os.environ.get("DISPLAY"):
        os.environ["DISPLAY"] = ":0"
    app = FishFeederGUI()
    app.mainloop()
'''

AUTOSTART_CONFIG = """[Desktop Entry]
Type=Application
Name=FishFeeder GUI
Exec={python_path} "{gui_path}"
StartupNotify=false
Terminal=false
"""

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


def restore_and_reboot():
    if os.path.exists(BACKUP_PATH):
        shutil.copy(BACKUP_PATH, __file__)
        os.remove(BACKUP_PATH)
    subprocess.run(["sudo", "reboot"])


@bot.event
async def on_ready():
    print(f"Dev Patch Installer active as {bot.user}")
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel:
        await channel.send(
            "🛠️ **Dev Patch Installer active**\n\n"
            "`!y` — Install HDMI Dashboard (tkinter + autostart) then restore bot & reboot\n"
            "`!webui` — Install Web Dashboard (Flask + ngrok) then restore bot & reboot\n"
            "`!n` — Skip installation, restore original bot & reboot"
        )


@bot.command(name="y")
async def cmd_y(ctx):
    if ctx.author.id != OWNER_ID:
        return
    msg = await ctx.send("📺 Installing HDMI Dashboard...")
    try:
        with open(GUI_FILE, "w") as f:
            f.write(GUI_CODE)

        autostart_dir = os.path.expanduser("~/.config/autostart")
        os.makedirs(autostart_dir, exist_ok=True)
        python_path = "/home/sira/feederbot/bin/python"
        if not os.path.exists(python_path):
            python_path = "/usr/bin/python3"
        with open(os.path.join(autostart_dir, "fishfeeder_gui.desktop"), "w") as f:
            f.write(AUTOSTART_CONFIG.format(gui_path=GUI_FILE, python_path=python_path))

        await msg.edit(content="✅ HDMI Dashboard installed! Restoring original bot and rebooting...")
        await asyncio.sleep(2)
    except Exception as e:
        await msg.edit(content=f"❌ HDMI Dashboard install failed: {e}")
        await asyncio.sleep(2)
    restore_and_reboot()


@bot.command(name="n")
async def cmd_n(ctx):
    if ctx.author.id != OWNER_ID:
        return
    await ctx.send("⏭️ Skipping installation. Restoring original bot and rebooting...")
    await asyncio.sleep(2)
    restore_and_reboot()


@bot.command(name="webui")
async def cmd_webui(ctx):
    if ctx.author.id != OWNER_ID:
        return
    msg = await ctx.send("\U0001F310 Setting up Web Dashboard...")
    try:
        try:
            import flask
        except ImportError:
            await msg.edit(content="\U0001F310 Installing Flask...")
            subprocess.run([sys.executable, "-m", "pip", "install", "flask"], capture_output=True, timeout=60)

        with open(WEB_FILE, "w") as f:
            f.write(WEB_CODE)

        svc_path = "/etc/systemd/system/web_dashboard.service"
        subprocess.run(["sudo", "tee", svc_path], input=WEB_SERVICE.encode(), check=True)
        subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
        subprocess.run(["sudo", "systemctl", "enable", "web_dashboard"], check=True)
        subprocess.run(["sudo", "systemctl", "restart", "web_dashboard"], check=True)

        ngrok_path = subprocess.run(["which", "ngrok"], capture_output=True, text=True).stdout.strip()
        if not ngrok_path:
            await msg.edit(content="\U0001F310 Installing ngrok agent...")
            subprocess.run("curl -sSL https://ngrok-agent.s3.amazonaws.com/ngrok.asc | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null", shell=True, timeout=30)
            subprocess.run("echo 'deb https://ngrok-agent.s3.amazonaws.com bookworm main' | sudo tee /etc/apt/sources.list.d/ngrok.list", shell=True, timeout=10)
            subprocess.run(["sudo", "apt", "update"], capture_output=True, timeout=120)
            subprocess.run(["sudo", "apt", "install", "-y", "ngrok"], capture_output=True, timeout=120)
            ngrok_path = "/usr/local/bin/ngrok"

        if ngrok_path:
            subprocess.run(["pkill", "-f", "ngrok"], capture_output=True)
            subprocess.run([ngrok_path, "config", "add-authtoken", NGROK_AUTH], capture_output=True, timeout=10)
            subprocess.Popen([ngrok_path, "http", "--url=" + NGROK_DOMAIN, str(WEB_PORT)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            with open(NGROK_CONFIG_FILE, "w") as f:
                json.dump({"auth": NGROK_AUTH, "domain": NGROK_DOMAIN}, f)

        await msg.edit(content=f"\U0001F310 Web Dashboard installed! https://{NGROK_DOMAIN}")
    except Exception as e:
        await ctx.send(f"❌ Web Dashboard install error: {e}")

    await ctx.send("Restoring original bot and rebooting...")
    await asyncio.sleep(2)
    restore_and_reboot()


if __name__ == "__main__":
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("Error: TOKEN not found")
