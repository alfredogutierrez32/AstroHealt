"""
AstroHealth - wristband on the PHONE (physical prototype), local network only, nothing to install.

How it works:
- The console (astrohealth.py) starts this server inside the same program.
- The phone, on the same WiFi, opens  http://<computer IP>:<port>/?p=<1..5>&k=<code>
- Ship -> phone: the mission state is sent in real time (Server-Sent Events).
- Phone -> ship: every tap (ACKNOWLEDGED, VIEW THEIR DATA, I'M ON MY WAY, complete routine) reaches
  the console, goes through actions.py / routines.py like any wristband and is saved in the database.

Threads: the web server runs in separate threads and NEVER touches the mission. The console (Tk thread):
  1) builds the snapshot with `snapshot(...)` and publishes it with `publish(...)`;
  2) drains `pending_actions()` and runs them with `execute_action(...)`.

Security: listens on the local network; every request needs the 6-digit code shown by the
console. It does not use the internet or external services. Data travels unencrypted over WiFi (http):
use it only on a trusted network and with synthetic or pseudonymized data.
"""
import json
import queue
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import actions
from routines import ROUTINES, ROUT, BADGES, TEAM_GOAL
from simulation import (INDICATORS, IND, OK, ALERT, SIGNAL, OPS, PHASE_CRITICAL, STATE_TEXT,
                        QUALITY_TEXT, Q_OK, format_value, protocol_for)

PORT = 8765
VALID_ACTIONS = {"ack", "view", "go", "complete", "silence"}
HEALTH = ["hr", "spo2", "temp", "sbp", "glu", "rad"]
BODY = ["spo2", "temp", "sbp", "glu"]
TITLES = {"hr": "PULSE", "spo2": "OXYGEN SpO₂", "temp": "TEMPERATURE", "sbp": "PRESSURE",
           "glu": "GLUCOSE", "sleep": "SLEEP (24 h)", "exercise": "EXERCISE TODAY", "rad": "RADIATION"}


def local_ip():
    """IP of this computer on the local network (sends nothing: it only looks up the route)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


# ---------------------------------------------------------------- snapshot (Tk thread)
def _num(v):
    return None if v is None else round(float(v), 2)


def snapshot(mission, routines, voice=None, notifications=(), restarts=0):
    """Everything the phone page needs, as plain data (JSON)."""
    crew_ = []
    for i, t in enumerate(mission.crew):
        inds = {}
        for ind in INDICATORS:
            c = ind.key
            v = t.values_.get(c)
            qual = t.quality.get(c, Q_OK)
            inds[c] = {
                "title": TITLES.get(c, ind.name_.upper()), "name": ind.name_, "unit": ind.unit,
                "value": None if v is None else format_value(c, v), "status": t.indicator_state(c),
                "quality": "" if qual == Q_OK else QUALITY_TEXT.get(qual, qual),
                "range": f"{ind.range_[0]}–{ind.range_[1]}",
                "series": [_num(x) for x in t.history.get(c, [])[-21:]],
            }
        act_you, _ = actions.action_line(t, True)
        act_other, _ = actions.action_line(t, False)
        p = routines.progress[i] if routines else None
        crew_.append({
            "name": t.name_, "short": t.name_.split()[0], "role": t.role, "initials": t.initials,
            "status": t.overall_state(), "support": t.support_state(), "ind": inds,
            "action_you": act_you, "action_other": act_other,
            "plss": _num(t.o2_tank), "ops": _num(t.o2_reserve), "o2_time": mission.time_left(t),
            "reported_mood": t.reported_mood,
            "routines": None if p is None else {
                "points": p.points, "level": p.level, "in_level": p.points_in_level, "streak": p.streak,
                "done": dict(p.done_today), "badges": list(p.badges),
            },
        })
    alarms = []
    for d in mission.broadcast_queue():
        t = mission.crew[d.affected]
        tit_you, prob_you, steps_you = protocol_for(t, True, key=d.key, value=d.value)
        tit_o, prob_o, steps_o = protocol_for(t, False, key=d.key, value=d.value)
        alarms.append({
            "id": d.id, "affected": d.affected, "critical": d.phase == PHASE_CRITICAL,
            "indicator": d.indicator_name(), "value": d.value_text(), "key": d.key,
            "attended_by": d.attended_by, "cycle_acks": sorted(d.cycle_acks),
            "escalated": d.escalated, "repetitions": d.repetitions, "silenced": mission.is_silenced(d),
            "you": {"title": tit_you, "problem": prob_you, "steps": steps_you},
            "other": {"title": tit_o, "problem": prob_o, "steps": steps_o},
        })
    return {
        "minute": mission.minutes_, "clock": mission.clock(), "mode": mission.mode, "restarts": restarts,
        "states": STATE_TEXT, "crew": crew_, "alarms": alarms,
        "silence": {"n": mission.silences, "left": int(mission.silence_left())},
        "routines_def": [{"key": r.key, "name": r.name_, "points": r.points} for r in ROUTINES],
        "badges_def": BADGES,
        "goal": {"progress": round(routines.team_progress(), 3) if routines else 0, "target": TEAM_GOAL},
        "voice": [{"n": n, "text": tx} for n, (_, tx) in enumerate(voice.history)][-5:] if voice else [],
        "notifications": list(notifications)[-20:],
    }


class Notifier:
    """Discreet notifications for the phone (the same as on the desktop wristband)."""
    COLORS = {"red": "var(--red)", "yellow": "var(--yellow)", "green": "var(--green)", "cyan": "var(--cyan)"}

    def __init__(self, mission, routines, start_at=0):
        self.m, self.r = mission, routines
        self.items_ = []
        self._n = start_at              # on restart the numbering continues (the phone does not repeat notices)
        self._cur = {"res": len(mission.resolutions), "exits": len(mission.critical_exits),
                     "sup": len(mission.support_notices), "otw": len(mission.on_the_way), "rout": len(routines.records) if routines else 0}

    def _append(self, text, color, to_=None, speak=False):
        self._n += 1
        self.items_.append({"n": self._n, "text": text, "color": self.COLORS[color], "to": to_, "speak": speak})
        del self.items_[:-40]

    def check(self):
        m = self.m
        for d in m.critical_exits[self._cur["exits"]:]:
            name_ = m.crew[d.affected].name_.split()[0]
            self._append(f"! {name_} left critical state ({d.indicator_name()}) · still in caution",
                          "yellow")
        for d in m.resolutions[self._cur["res"]:]:
            name_ = m.crew[d.affected].name_.split()[0]
            self._append(f"✓ {name_} back within range ({d.indicator_name()})", "green")
        for av in m.support_notices[self._cur["sup"]:]:
            self._append(("▲ " if av.level == ALERT else "! ") + av.text,
                          "red" if av.level == ALERT else "yellow", to_=av.affected)
        # "X is on the way to help Y": each person gets their own text; only the person in red hears it
        for ow in m.on_the_way[self._cur["otw"]:]:
            helper = m.crew[ow.helper].name_.split()[0]
            affected = m.crew[ow.affected].name_.split()[0]
            for i in range(len(m.crew)):
                if i == ow.helper:
                    self._append(f"🏃 You are on your way to help {affected}", "cyan", to_=i)
                elif i == ow.affected:
                    self._append(f"🏃 {helper} is on the way to help you", "cyan", to_=i, speak=True)
                else:
                    self._append(f"🏃 {helper} is on the way to help {affected}", "cyan", to_=i)
        self._cur.update(exits=len(m.critical_exits), res=len(m.resolutions), sup=len(m.support_notices),
                         otw=len(m.on_the_way))
        if self.r is not None:
            index_ = {t.name_: i for i, t in enumerate(m.crew)}
            for rec in self.r.records[self._cur["rout"]:]:
                if rec.points and rec.crew_member in index_:
                    self._append(f"★ +{rec.points} pts · {rec.reason}", "cyan", to_=index_[rec.crew_member])
            self._cur["rout"] = len(self.r.records)
        return self.items_


def execute_action(mission, routines, action):
    """Runs (on the Tk thread) an action that arrived from the phone. Returns True if something changed."""
    person, kind = action["person"], action["action"]
    if kind == "silence":
        return mission.silence(person)
    if kind in ("ack", "view", "go"):
        d = next((x for x in mission.broadcast_queue() if x.id == action.get("id")), None)
        if d is None:
            return False
        if kind == "go" and d.affected == person:
            return False
        actions.respond(mission, routines, person, d, kind)
        return True
    if kind == "complete" and routines is not None:
        key = action.get("key")
        if key not in ROUT or key in routines.progress[person].done_today:
            return False
        if key == "mood":
            mood = action.get("mood")
            if not isinstance(mood, int) or isinstance(mood, bool) or not 1 <= mood <= 5:
                return False
            routines.complete_routine(person, key, mood=mood)
        else:
            routines.complete_routine(person, key)
        return True
    return False


def validate_action(data_, n_people):
    """Checks an action from the phone BEFORE passing it to the console. Returns a clean dict or None."""
    if not isinstance(data_, dict):
        return None
    p, kind = data_.get("person"), data_.get("action")
    if not isinstance(p, int) or isinstance(p, bool) or not 0 <= p < n_people:
        return None
    if kind not in VALID_ACTIONS:
        return None
    clean = {"person": p, "action": kind}
    if kind == "silence":
        return clean
    if kind in ("ack", "view", "go"):
        id_ = data_.get("id")
        if not isinstance(id_, int) or isinstance(id_, bool) or id_ < 0:
            return None
        clean["id"] = data_["id"]
    else:
        if not isinstance(data_.get("key"), str) or data_["key"] not in ROUT:
            return None
        clean["key"] = data_["key"]
        if "mood" in data_:
            clean["mood"] = data_["mood"]
    return clean


# ---------------------------------------------------------------- server (separate threads)
class MobileServer:
    def __init__(self, n_people, port=PORT, code_=None, host="0.0.0.0"):
        self.n_people = n_people
        self.port = port
        self.host = host
        self.code_ = code_ or f"{secrets.randbelow(10 ** 6):06d}"
        self._lock = threading.Lock()
        self._json = b"{}"
        self._version = 0
        self._changed = threading.Condition(self._lock)
        self._connected = {}               # person -> number of connected phones
        self._actions = queue.Queue()
        self._httpd = None
        self._thread = None

    # ---- from the console (Tk thread)
    def start(self):
        server_ = self

        class Handler(_Handler):
            srv = server_

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        with self._changed:
            self._version += 1
            self._changed.notify_all()

    def running(self):
        return self._httpd is not None

    def address(self, person=0):
        return f"http://{local_ip()}:{self.port}/?p={person + 1}&k={self.code_}"

    def publish(self, data_):
        text = json.dumps(data_, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        with self._changed:
            if text != self._json:
                self._json = text
                self._version += 1
                self._changed.notify_all()

    def pending_actions(self):
        output = []
        while True:
            try:
                output.append(self._actions.get_nowait())
            except queue.Empty:
                return output

    def connected(self):
        with self._lock:
            return {p for p, n in self._connected.items() if n > 0}

    # ---- from the server threads
    def _connect(self, person, delta):
        with self._lock:
            self._connected[person] = self._connected.get(person, 0) + delta

    def _wait(self, version, time_):
        with self._changed:
            if self._version == version and self._httpd is not None:
                self._changed.wait(time_)
            return self._version, self._json


class _Handler(BaseHTTPRequestHandler):
    srv = None
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):             # no logs with data in the console
        pass

    def _code_ok(self, q):
        return secrets.compare_digest(q.get("k", [""])[0], self.srv.code_)

    def _reply(self, status, body=b"", kind="text/plain; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            if not self._code_ok(q):
                return self._reply(403, "Missing the correct code (?k=…). Look it up in the console.".encode("utf-8"))
            return self._reply(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        if u.path == "/events":
            if not self._code_ok(q):
                return self._reply(403)
            try:
                person = int(q.get("p", ["1"])[0]) - 1
            except ValueError:
                return self._reply(400)
            if not 0 <= person < self.srv.n_people:
                return self._reply(400)
            return self._events(person)
        return self._reply(404)

    def _events(self, person):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.srv._connect(person, +1)
        version = -1
        try:
            last = 0.0
            while self.srv.running():
                new_, data_ = self.srv._wait(version, 2.0)
                if new_ != version:
                    version = new_
                    self.wfile.write(b"data: " + data_ + b"\n\n")
                    last = time.monotonic()
                elif time.monotonic() - last >= 2:
                    self.wfile.write(b"event: beat\ndata: 1\n\n")   # still alive even if nothing changed
                    last = time.monotonic()
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            self.srv._connect(person, -1)

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path != "/action":
            return self._reply(404)
        if not self._code_ok(q):
            return self._reply(403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self._reply(400)
        if not 0 < length <= 2048:
            return self._reply(400)
        try:
            data_ = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._reply(400)
        clean = validate_action(data_, self.srv.n_people)
        if clean is None:
            return self._reply(400)
        self.srv._actions.put(clean)
        return self._reply(202, b"ok")


# ---------------------------------------------------------------- phone page
PAGE = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, user-scalable=no">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#06121F">
<title>AstroHealth · wristband</title>
<style>
:root{--bg:#06121F;--panel:#0B1C2E;--edge:#1E3A55;--cyan:#5FD3F3;--text:#E6F4FF;--dim:#7F9BB3;
--green:#3DDC97;--yellow:#F2C14E;--red:#FF4D5E;--gray:#8A97A6}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;height:100%;background:var(--bg);color:var(--text);
font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;overscroll-behavior:none}
body{display:flex;flex-direction:column;padding-top:env(safe-area-inset-top)}
header{padding:10px 12px 6px;border-bottom:1px solid var(--edge)}
.row{display:flex;align-items:center;gap:8px}
.name{font-size:20px;font-weight:800;letter-spacing:.5px}
.sub{color:var(--dim);font-size:12px}
.link{margin-left:auto;font-size:12px;font-weight:700;padding:4px 8px;border-radius:999px;border:1px solid var(--edge)}
.nav button,.tabs button,.btn{min-height:48px;min-width:48px;border-radius:12px;border:1px solid var(--edge);
background:var(--panel);color:var(--text);font-size:18px;font-weight:800}
.nav{display:flex;gap:6px;margin-top:8px}.nav button{flex:1}
.tabs{display:flex;gap:6px;padding:8px 12px}.tabs button{flex:1;font-size:14px;letter-spacing:1px}
.tabs button.active{background:var(--cyan);color:#04121C;border-color:var(--cyan)}
main{flex:1;overflow-y:auto;padding:0 12px 12px;-webkit-overflow-scrolling:touch}
.light{border-radius:16px;padding:14px;margin:4px 0 10px;text-align:center;font-size:28px;font-weight:900;letter-spacing:1px;border:2px solid}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
.card{background:var(--panel);border:1px solid var(--edge);border-radius:14px;padding:10px;position:relative}
.card .t{font-size:11px;color:var(--dim);font-weight:800;letter-spacing:1px}
.card .v{font-size:30px;font-weight:900;margin-top:2px}.card .u{font-size:13px;color:var(--dim);margin-left:3px}
.chip{display:inline-block;font-size:11px;font-weight:800;padding:2px 7px;border-radius:999px;margin-top:4px}
.card svg{position:absolute;right:8px;bottom:10px}
.action{margin:10px 0;padding:12px;border-radius:12px;font-weight:700;border-left:6px solid}
.crew{display:grid;grid-template-columns:repeat(5,1fr);gap:6px;margin-top:12px}
.tile{background:var(--panel);border:2px solid var(--edge);border-radius:12px;padding:8px 2px;text-align:center;min-height:64px}
.tile b{display:block;font-size:18px}.tile span{font-size:11px;color:var(--dim)}
.tile.sel{outline:3px solid var(--cyan)}
.h{font-size:12px;font-weight:800;color:var(--dim);letter-spacing:1px;margin:14px 0 6px}
#alarms{display:none;padding:6px 12px;gap:6px;flex-direction:column;border-top:1px solid var(--edge)}
#alarms .a{border:2px solid;border-radius:12px;padding:10px;font-weight:800;font-size:14px;min-height:48px}
footer{padding:6px 12px calc(6px + env(safe-area-inset-bottom));font-size:11px;color:var(--dim);border-top:1px solid var(--edge)}
#toast{position:fixed;left:12px;right:12px;top:calc(8px + env(safe-area-inset-top));padding:12px;border-radius:12px;
font-weight:800;display:none;z-index:5;border:2px solid;background:#0B1C2E}
#modal{position:fixed;inset:0;background:rgba(3,8,14,.92);display:none;z-index:10;padding:calc(16px + env(safe-area-inset-top)) 14px 16px;overflow-y:auto}
#modal .box{border:3px solid var(--red);border-radius:18px;padding:16px;background:#1A0A10}
#modal h2{margin:0 0 6px;font-size:24px;color:var(--red)}#modal p{font-size:17px;font-weight:700}
#modal ol{font-size:16px;line-height:1.45;padding-left:22px}
#modal .btns{display:grid;gap:8px;margin-top:12px}#modal .btn{font-size:17px}
.btn.red{background:var(--red);color:#fff;border-color:var(--red)}
.btn.cyan{background:var(--cyan);color:#04121C;border-color:var(--cyan)}
.flash{animation:flash .5s steps(1) infinite}@keyframes flash{50%{background:#5A0A14}}
.tanks{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.tq{height:110px;border:2px solid var(--edge);border-radius:12px;position:relative;overflow:hidden;background:var(--panel)}
.tq i{position:absolute;left:0;right:0;bottom:0;background:rgba(95,211,243,.35)}
.tq b{position:absolute;top:8px;left:10px;font-size:14px}.tq em{position:absolute;bottom:8px;right:10px;font-style:normal;font-size:22px;font-weight:900}
.rout{display:flex;align-items:center;gap:10px;background:var(--panel);border:1px solid var(--edge);border-radius:12px;padding:10px;margin-bottom:8px}
.rout .n{flex:1;font-weight:800}.rout .p{color:var(--dim);font-size:12px}
.mood{display:flex;gap:6px;margin-top:8px}.mood button{flex:1}
.bar{height:12px;background:#132A40;border-radius:999px;overflow:hidden}.bar i{display:block;height:100%;background:var(--cyan)}
canvas{width:100%;height:120px;background:var(--panel);border:1px solid var(--edge);border-radius:14px}
.note{font-size:11px;color:var(--dim)}
#unlock{position:fixed;inset:0;z-index:20;display:none;align-items:center;justify-content:center;
background:rgba(3,8,14,.94);text-align:center;padding:24px}
#unlock div{border:2px solid var(--cyan);border-radius:18px;padding:22px;font-size:20px;font-weight:800;color:var(--cyan)}
#unlock small{display:block;margin-top:10px;font-size:13px;color:var(--dim);font-weight:600}
select{width:100%;min-height:48px;border-radius:12px;background:var(--panel);color:var(--text);border:1px solid var(--edge);font-size:15px;padding:0 10px}
.badge{display:inline-block;margin:3px;padding:6px 10px;border-radius:999px;border:1px solid var(--cyan);font-size:12px;font-weight:700}
</style></head><body>
<header>
  <div class="row"><div><div class="name" id="name">—</div><div class="sub" id="role">connecting…</div></div>
  <div class="link" id="link">NO LINK</div></div>
  <div class="nav"><button onclick="cycle(-1)">◀</button><button onclick="viewPerson(ME)">⌂ ME</button>
  <button onclick="cycle(1)">▶</button><button id="btnvoice" onclick="toggleVoice()">🔇</button></div>
</header>
<div class="tabs"><button data-v="health" class="active">HEALTH</button><button data-v="body">BODY</button><button data-v="routines">ROUTINES</button></div>
<main id="main"></main>
<div id="alarms"></div>
<footer id="footer">—</footer>
<div id="toast"></div>
<div id="modal"><div class="box" id="box"></div></div>
<div id="unlock"><div>🔊 Tap the screen to enable the alert voice<small>The phone's browser only allows speech after a tap.
You can turn it off with the 🔊 button at the top.</small></div></div>
<script>
"use strict";
const Q=new URLSearchParams(location.search);const K=Q.get("k")||"";
const ME=Math.max(0,(parseInt(Q.get("p")||"1",10)||1)-1);
let viewed=ME,view="health",S=null,lastData=0,force=null,closedModal=null;
let seenNotif=null,seenVoice=null,seenAlarms=new Set(),seenSilence=null,seenRestarts=null;
const COL={ok:"var(--green)",caution:"var(--yellow)",alert:"var(--red)",no_signal:"var(--gray)",invalid:"var(--gray)"};
const WORD={ok:"IN RANGE",caution:"CAUTION",alert:"ALERT",no_signal:"NO SIGNAL",invalid:"INVALID"};
const BIG={ok:"IN RANGE",caution:"CAUTION",alert:"RED ALERT",no_signal:"NO SIGNAL",invalid:"INVALID DATA"};
const $=id=>document.getElementById(id);
const esc=s=>String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
document.querySelectorAll(".tabs button").forEach(b=>b.onclick=()=>{view=b.dataset.v;
  document.querySelectorAll(".tabs button").forEach(x=>x.classList.toggle("active",x===b));$("main").scrollTop=0;paint();});
function cycle(d){if(!S)return;const n=S.crew.length;viewPerson((viewed+d+n)%n);}
function viewPerson(i){viewed=i;paint();}
function send(obj){obj.person=ME;fetch("/action?k="+encodeURIComponent(K),{method:"POST",
  headers:{"Content-Type":"application/json"},body:JSON.stringify(obj)}).catch(()=>toast("Could not send: no link with the ship","var(--red)"));}
function vibrate(p){try{navigator.vibrate&&navigator.vibrate(p);}catch(e){}}
let toastT=null;
function toast(txt,col){const t=$("toast");t.textContent=txt;t.style.borderColor=col;t.style.color=col;t.style.display="block";
  clearTimeout(toastT);toastT=setTimeout(()=>t.style.display="none",5000);}
// ---- assistant voice on the phone (same phrases and rules as the ship's voice)
const MALE=/daniel|george|ryan|arthur|oliver|thomas|david|mark|james|guy|alex|fred|male|man\b/i;
let voiceOn=load("voiceOn","1")==="1",voiceUnlocked=false,voiceChosen=load("voice","");
function load(k,def){try{const v=localStorage.getItem("ah_"+k);return v==null?def:v;}catch(e){return def;}}
function store(k,v){try{localStorage.setItem("ah_"+k,v);}catch(e){}}
function englishVoices(){return window.speechSynthesis?speechSynthesis.getVoices().filter(v=>/^en/i.test(v.lang)):[];}
function assistantVoice(){const vs=englishVoices();if(!vs.length)return null;
  return vs.find(v=>v.voiceURI===voiceChosen)||vs.find(v=>/en[-_]GB/i.test(v.lang)&&MALE.test(v.name))||
         vs.find(v=>MALE.test(v.name))||vs.find(v=>/en[-_]GB/i.test(v.lang))||vs[0];}
function say(txt){if(!window.speechSynthesis)return;const u=new SpeechSynthesisUtterance(txt);
  const v=assistantVoice();if(v){u.voice=v;u.lang=v.lang;}else u.lang="en-GB";
  u.pitch=0.75;u.rate=0.98;u.volume=1;speechSynthesis.speak(u);}
function speak(txt){if(!voiceOn||!voiceUnlocked)return;
  // if alerts pile up it does not fall behind: drops the old ones and reads the newest
  if(speechSynthesis.pending&&speechSynthesis.speaking){speechSynthesis.cancel();}
  say(txt);}
function paintVoiceButton(){$("btnvoice").textContent=voiceOn?"🔊":"🔇";
  $("unlock").style.display=(voiceOn&&!voiceUnlocked&&window.speechSynthesis)?"flex":"none";}
function unlockVoice(){if(voiceUnlocked||!window.speechSynthesis)return;voiceUnlocked=true;
  if(voiceOn)say("Voice system online.");paintVoiceButton();}
document.addEventListener("pointerdown",unlockVoice,{once:false,capture:true});
function toggleVoice(){voiceOn=!voiceOn;store("voiceOn",voiceOn?"1":"0");
  if(!voiceOn&&window.speechSynthesis)speechSynthesis.cancel();
  if(voiceOn&&voiceUnlocked)say("Voice enabled.");paintVoiceButton();}
function chooseVoice(uri){voiceChosen=uri;store("voice",uri);say("Sir, this is the alert voice.");}
if(window.speechSynthesis)speechSynthesis.onvoiceschanged=()=>{if(view==="routines")paint();};
function voicePanel(){if(!window.speechSynthesis)return `<div class="h">VOICE</div><div class="note">This browser cannot speak.</div>`;
  const vs=englishVoices(),act=assistantVoice();
  return `<div class="h">ALERT VOICE ON THIS PHONE</div>
  <select onchange="chooseVoice(this.value)">${vs.length?vs.map(v=>`<option value="${esc(v.voiceURI)}" ${act&&v.voiceURI===act.voiceURI?"selected":""}>${esc(v.name)} (${esc(v.lang)})</option>`).join(""):"<option>System voice</option>"}</select>
  <div style="display:flex;gap:8px;margin-top:8px"><button class="btn" style="flex:1" onclick="say('Sir, red alert. Mei’s oxygen saturation is at 85 percent.')">TEST VOICE</button>
  <button class="btn" style="flex:1" onclick="toggleVoice();paint()">${voiceOn?"TURN OFF":"TURN ON"}</button></div>
  <div class="note">Reads the same alerts as the ship (red alerts and suit oxygen). It only plays with the page open and the screen on.</div>`;}
function chip(e){return `<span class="chip" style="background:${COL[e]};color:#04121C">${WORD[e]||e}</span>`;}
function spark(series,e){const vals=series||[];const n=vals.length;const nums=vals.filter(v=>v!=null);if(nums.length<2)return "";
  let lo=Math.min(...nums),hi=Math.max(...nums);if(hi-lo<1e-6){hi+=1;lo-=1;}const W=60,H=18;let d="",pen=false;
  vals.forEach((v,i)=>{if(v==null){pen=false;return;}const x=i/(n-1)*W,y=H-(v-lo)/(hi-lo)*H;d+=(pen?"L":"M")+x.toFixed(1)+" "+y.toFixed(1);pen=true;});
  return `<svg width="${W}" height="${H}"><path d="${d}" fill="none" stroke="${COL[e]}" stroke-width="1.6"/></svg>`;}
function card(c,i){const value=i.value==null?"--":i.value;return `<div class="card" style="border-color:${i.status==="ok"?"var(--edge)":COL[i.status]}">
  <div class="t">${esc(i.title)}</div><div><span class="v">${esc(value)}</span><span class="u">${esc(i.unit)}</span></div>
  ${chip(i.status)}${i.quality?`<div class="note">${esc(i.quality)}</div>`:""}${spark(i.series,i.status)}</div>`;}
function healthView(t){const is=viewed===ME;let h=`<div class="light" style="color:${COL[t.status]};border-color:${COL[t.status]}">${BIG[t.status]}</div>`;
  h+=`<div class="grid">${["hr","spo2","temp","sbp","glu","rad"].map(c=>card(c,t.ind[c])).join("")}</div>`;
  const act=is?t.action_you:t.action_other;
  if(act)h+=`<div class="action" style="border-color:${COL[t.status]};background:#0F2236">${esc(act)}</div>`;
  h+=`<div class="h">CREW · tap to view their data</div><div class="crew">`+S.crew.map((p,k)=>
   `<div class="tile ${k===viewed?"sel":""}" style="border-color:${COL[p.status]}" onclick="viewPerson(${k})"><b>${esc(p.initials)}</b>
   <span>${p.ind.hr.value==null?"--":esc(p.ind.hr.value)} bpm</span></div>`).join("")+`</div>`;return h;}
let ecgT=0,ecgRaf=null;
function beat(x){const g=(t,m,s)=>Math.exp(-((t-m)**2)/(2*s*s));return .12*g(x,.15,.03)-.15*g(x,.30,.008)+g(x,.33,.01)-.3*g(x,.36,.01)+.3*g(x,.55,.04);}
function animateECG(){const cv=$("ecg");if(!cv){ecgRaf=null;return;}const t=S&&S.crew[viewed];const hr=t&&t.ind.hr.value?parseFloat(t.ind.hr.value):null;
  const W=cv.width=cv.clientWidth*2,H=cv.height=240,c=cv.getContext("2d");c.clearRect(0,0,W,H);
  if(hr){ecgT+=1/60;const per=60/hr;c.strokeStyle=COL[t.ind.hr.status];c.lineWidth=3;c.beginPath();
   for(let x=0;x<W;x++){const tt=ecgT-(W-x)/W*3;const y=H*.6-beat(((tt%per)+per)%per/per)*H*.45;x?c.lineTo(x,y):c.moveTo(x,y);}c.stroke();}
  else{c.fillStyle="#7F9BB3";c.font="28px system-ui";c.fillText("No pulse data",20,H/2);}
  ecgRaf=requestAnimationFrame(animateECG);}
function bodyView(t){const hr=t.ind.hr;let h=`<div class="h">HEART RATE · ${hr.value==null?"--":esc(hr.value)} bpm</div>
  <canvas id="ecg"></canvas><div class="note">Illustrative curve animated from the pulse: not a measured ECG.</div>
  <div class="h">INDICATORS</div><div class="grid">${["spo2","temp","sbp","glu"].map(c=>card(c,t.ind[c])).join("")}</div>
  <div class="h">SUIT OXYGEN · ${esc(t.o2_time)}</div><div class="tanks">`+
  [["PLSS (main)",t.plss],["OPS (reserve)",t.ops]].map(([n,v])=>`<div class="tq"><i style="height:${v==null?0:Math.max(0,Math.min(100,v))}%"></i>
  <b>${n}</b><em>${v==null?"--":Math.round(v)+" %"}</em></div>`).join("")+`</div>`;return h;}
function routinesView(){const t=S.crew[ME],r=t.routines;if(!r)return "<p>No routines.</p>";
  let h=`<div class="h">YOUR ROUTINES TODAY · ${esc(t.short)}</div>`;
  h+=S.routines_def.map(d=>{const done=d.key in r.done;let extra="";
   if(d.key==="mood"&&!done)extra=`<div class="mood">${[1,2,3,4,5].map(n=>`<button class="btn" onclick="complete('mood',${n})">${n}</button>`).join("")}</div>`;
   return `<div class="rout" style="flex-wrap:wrap"><div class="n">${esc(d.name)}<div class="p">+${d.points} pts${done?" · done "+esc(r.done[d.key]):""}${d.key==="mood"&&t.reported_mood?" · mood "+t.reported_mood+"/5":""}</div></div>
   ${done?`<span class="chip" style="background:var(--green);color:#04121C">✓ DONE</span>`:(d.key==="mood"?"":`<button class="btn cyan" style="font-size:14px;padding:0 12px" onclick="complete('${d.key}')">MARK</button>`)}${extra}</div>`;}).join("");
  h+=`<div class="h">LEVEL ${r.level} · ${r.points} pts · streak ${r.streak} days</div><div class="bar"><i style="width:${r.in_level}%"></i></div>`;
  h+=`<div class="h">BADGES</div>`+(r.badges.length?r.badges.map(n=>`<span class="badge">${esc(S.badges_def[n]||n)}</span>`).join(""):`<div class="note">None yet.</div>`);
  h+=`<div class="h">TEAM GOAL · ${Math.round(S.goal.progress*100)} % of ${Math.round(S.goal.target*100)} %</div><div class="bar"><i style="width:${Math.round(S.goal.progress*100)}%"></i></div>`;return h+voicePanel();}
function silence(){send({action:"silence"});if(window.speechSynthesis)speechSynthesis.cancel();}
function complete(key,mood){const o={action:"complete",key:key};if(mood)o.mood=mood;send(o);}
function currentNotice(){if(!S)return null;const queue=S.alarms;
  if(force){const d=queue.find(a=>a.id===force);if(d)return d;force=null;}
  for(const d of queue){if(!d.critical)continue;if(d.cycle_acks.includes(ME)||d.attended_by===ME)continue;
   if(d.id===closedModal)continue;if(d.affected===ME||d.attended_by==null)return d;}return null;}
function respond(kind,id){send({action:kind,id:id});force=null;closedModal=id;
  if(kind==="view"){const d=S.alarms.find(a=>a.id===id);if(d){viewed=d.affected;view="health";
   document.querySelectorAll(".tabs button").forEach(x=>x.classList.toggle("active",x.dataset.v==="health"));}}paintModal();paint();}
function paintModal(){const d=currentNotice();const m=$("modal");if(!d){m.style.display="none";document.body.classList.remove("flash");return;}
  const t=S.crew[d.affected],is=d.affected===ME,p=is?d.you:d.other;
  const pending=!d.cycle_acks.includes(ME)&&d.attended_by==null;
  document.body.classList.toggle("flash",is&&pending&&d.critical);
  const who=d.attended_by==null?"":`<p style="color:var(--yellow)">${d.attended_by===ME?"You are on your way to help":esc(S.crew[d.attended_by].short)+" is on the way to help"}</p>`;
  $("box").innerHTML=`<h2>EMERGENCY · ${esc(t.short)} ${is?"(YOU)":"("+esc(t.role)+")"}</h2>
   <div class="note">${esc(p.title)}${d.escalated?" · ESCALATED TO THE SHIP":""}</div>
   <p>${esc(p.problem)} <span class="note">(value when triggered)</span></p>${who}<ol>${p.steps.map(x=>`<li>${esc(x)}</li>`).join("")}</ol>
   <div class="btns"><button class="btn red" onclick="respond('ack',${d.id})">ACKNOWLEDGED</button>
   ${is?"":`<button class="btn" onclick="respond('view',${d.id})">VIEW THEIR DATA</button>
   <button class="btn cyan" onclick="respond('go',${d.id})">I'M ON MY WAY</button>`}</div>`;
  m.style.display="block";}
function paintAlarms(){const box=$("alarms");const byPerson={};S.alarms.forEach(a=>{(byPerson[a.affected]=byPerson[a.affected]||[]).push(a);});
  const items=Object.values(byPerson);if(!items.length){box.style.display="none";return;}
  box.style.display="flex";const crit=S.alarms.filter(a=>a.critical);
  const sil=crit.some(a=>!a.silenced)?`<button class="btn red" onclick="silence()">🔇 SILENCE ALARMS AND VOICE</button>`:
    crit.length?`<div class="note">🔇 Silenced ${Math.floor(S.silence.left/60)}:${String(S.silence.left%60).padStart(2,"0")} · the alarms are still active</div>`:"";
  box.innerHTML=sil+items.map(l=>{const d=l[0],t=S.crew[d.affected];
   const who=d.affected===ME?"YOU":t.short;const st=!d.critical?"left critical · in caution":
    d.attended_by==null?"waiting for help"+(d.escalated?" · ESCALATED":""):(d.attended_by===ME?"you are on your way":S.crew[d.attended_by].short+" is on the way");
   const datum=d.key==="signal"?"no signal":d.indicator.split(" (")[0]+" "+d.value;const col=d.critical?"var(--red)":"var(--yellow)";
   return `<div class="a" style="border-color:${col};color:${col}" onclick="force=${d.id};closedModal=null;paintModal()">▲ ${esc(who)} · ${esc(datum)}${l.length>1?" (and "+(l.length-1)+" more)":""} · ${esc(st)}</div>`;}).join("");}
function paint(){if(!S)return;const t=S.crew[viewed];
  $("name").textContent=(viewed===ME?"YOU · ":"")+t.name;$("role").textContent=viewed===ME?t.role:"CREWMATE · "+t.role.toUpperCase();
  const m=$("main");const y=m.scrollTop;
  m.innerHTML=view==="health"?healthView(t):view==="body"?bodyView(t):routinesView();m.scrollTop=y;
  if(view==="body"&&!ecgRaf)animateECG();
  paintAlarms();paintModal();
  $("footer").textContent=`Mode: ${S.mode} · mission clock ${S.clock} UTC · demo rules, do not use for real medical decisions`;}
function newNotices(){
  // discreet notifications (red->yellow, back within range, oxygen, points)
  const ns=S.notifications.filter(n=>n.to==null||n.to===ME);
  if(seenNotif===null){seenNotif=new Set(ns.map(n=>n.n));}
  else ns.forEach(n=>{if(!seenNotif.has(n.n)){seenNotif.add(n.n);toast(n.text,n.color);
    if(n.speak)speak("Sir, "+n.text.replace(/^\S+\s/,"")+".");}});
  // silence (from any wristband): cut what is being said
  if(seenSilence!==null&&S.silence.n!==seenSilence&&window.speechSynthesis)speechSynthesis.cancel();
  seenSilence=S.silence.n;
  // voice: the same the ship reads (already filtered and spaced out)
  if(seenVoice===null)seenVoice=new Set(S.voice.map(v=>v.n));
  else S.voice.forEach(v=>{if(!seenVoice.has(v.n)){seenVoice.add(v.n);speak(v.text);}});
  // vibration with every new red alert
  S.alarms.forEach(a=>{if(a.critical&&!seenAlarms.has(a.id+":"+a.repetitions)){
    // if someone is already on the way, only the phone of the person in red vibrates
    if(!a.silenced&&(a.attended_by==null||a.affected===ME))vibrate(a.affected===ME?[400,150,400,150,400]:[250,120,250]);seenAlarms.add(a.id+":"+a.repetitions);
    if(closedModal===a.id&&a.repetitions>0)closedModal=null;}});}
function link(){const alive=Date.now()-lastData<8000;const e=$("link");
  e.textContent=alive?"● LINKED TO THE SHIP":"○ NO LINK";e.style.color=alive?"var(--green)":"var(--red)";
  if(!alive&&S)$("role").textContent="No link: the data on screen may be out of date";}
setInterval(link,1000);
paintVoiceButton();
function connect(){const es=new EventSource("/events?p="+(ME+1)+"&k="+encodeURIComponent(K));
  es.onmessage=ev=>{try{S=JSON.parse(ev.data);}catch(e){return;}lastData=Date.now();
    if(seenRestarts!==null&&S.restarts!==seenRestarts){   // the ship restarted the simulation
      seenAlarms=new Set();seenSilence=null;force=null;closedModal=null;toast("↺ Simulation restarted","var(--cyan)");}
    seenRestarts=S.restarts;
    if(viewed>=S.crew.length)viewed=0;newNotices();paint();link();};
  es.addEventListener("beat",()=>{lastData=Date.now();link();});
  es.addEventListener("error",()=>{link();});}
connect();
</script></body></html>
"""
