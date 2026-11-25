"""UrbanPulse - Smart City Intelligence and Response System (Flask + SQLite).

The "AI" here is a LOCAL, rule-based analysis engine (keyword classification,
weighted scoring, similarity detection). It is NOT a live AI model.
To plug a real model in later, replace `analyze()` (see README).
"""
import heapq, json, math, os, random, re, sqlite3
from datetime import datetime, timedelta

from flask import Flask, g, jsonify, request

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "urbanpulse.db")
app = Flask(__name__, static_folder="static", static_url_path="")

# ----------------------------------------------------------------- city data
DISTRICTS = {
    "downtown": {"ar": "وسط المدينة", "en": "Downtown", "x": 50, "y": 50},
    "north": {"ar": "حي الشمال", "en": "North District", "x": 50, "y": 16},
    "south": {"ar": "حي الجنوب", "en": "South District", "x": 50, "y": 84},
    "east": {"ar": "حي الشرق", "en": "East District", "x": 84, "y": 50},
    "west": {"ar": "حي الغرب", "en": "West District", "x": 16, "y": 50},
    "university": {"ar": "حي الجامعة", "en": "University District", "x": 80, "y": 20},
    "industrial": {"ar": "المنطقة الصناعية", "en": "Industrial Zone", "x": 20, "y": 82},
}
RESOURCES = [  # type, name_ar, name_en, total units
    ("maintenance", "فرق الصيانة", "Maintenance Teams", 6),
    ("garbage", "مركبات جمع النفايات", "Garbage Vehicles", 5),
    ("water", "فرق إصلاح المياه", "Water Repair Teams", 4),
    ("electric", "فرق الصيانة الكهربائية", "Electrical Teams", 4),
    ("emergency", "وحدات الطوارئ", "Emergency Units", 3),
]
DURATION = {"maintenance": 4, "garbage": 2, "water": 3, "electric": 2, "emergency": 1.5}  # hours per job
TEAM_BY_CAT = {"road": "maintenance", "garbage": "garbage", "water": "water",
               "light": "electric", "traffic": "maintenance", "other": "maintenance"}
LEVELS = [(75, "critical"), (55, "high"), (35, "medium"), (0, "low")]
SLA_HOURS = {"critical": 1, "high": 4, "medium": 24, "low": 72}
ACTIONS = {
    "road": {"ar": "إرسال فريق صيانة لتسييج الموقع وإصلاح الحفرة/الأضرار", "en": "Dispatch maintenance to cordon off and repair the road damage"},
    "garbage": {"ar": "جدولة مركبة جمع نفايات وتنظيف الموقع", "en": "Schedule a garbage vehicle and clean the site"},
    "water": {"ar": "عزل خط المياه وإرسال فريق إصلاح التسرب", "en": "Isolate the water line and send a leak-repair crew"},
    "light": {"ar": "إرسال فريق كهرباء لفحص الأعمدة وإعادة الإنارة", "en": "Send an electrical crew to inspect and restore lighting"},
    "traffic": {"ar": "تأمين الموقع وتنظيم حركة المرور وإرسال الفريق المناسب", "en": "Secure the site, manage traffic and send the right crew"},
    "other": {"ar": "مراجعة البلاغ ميدانياً وتحديد الجهة المختصة", "en": "Inspect on site and route to the responsible department"},
}


def norm(s):
    s = re.sub(r"[\u064B-\u0652\u0640]", "", (s or "").lower())
    return s.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه"}))


def _kw(d):
    return {norm(k): v for k, v in d.items()}


CAT_KW = {
    "road": _kw({"حفرة": 3, "حفر": 2, "اسفلت": 3, "أسفلت": 0, "تشقق": 2, "هبوط": 3, "انهيار": 3, "طريق": 1, "رصيف": 1,
                 "pothole": 3, "road": 1, "asphalt": 3, "crack": 2, "sinkhole": 4}),
    "garbage": _kw({"نفايات": 3, "قمامة": 3, "زبالة": 3, "حاويات": 2, "مخلفات": 2, "رائحة": 1,
                    "garbage": 3, "trash": 3, "waste": 2, "litter": 2, "bins": 2, "smell": 1}),
    "water": _kw({"تسرب": 3, "تسريب": 3, "مياه": 2, "ماء": 1, "انبوب": 2, "فيضان": 3, "مجاري": 2, "انفجار": 1,
                  "leak": 3, "water": 2, "pipe": 2, "flood": 3, "burst": 2, "sewage": 2}),
    "light": _kw({"انارة": 3, "إنارة": 0, "مصباح": 3, "عمود": 2, "اعمدة": 2, "مظلم": 2, "ظلام": 2, "كهرباء": 2, "انقطاع": 2,
                  "streetlight": 3, "lamp": 2, "dark": 2, "power": 1, "outage": 2, "cable": 1}),
    "traffic": _kw({"حادث": 3, "تصادم": 3, "ازدحام": 3, "زحام": 3, "اشارة": 2, "مرور": 2,
                    "traffic": 3, "accident": 3, "crash": 3, "collision": 3, "signal": 2, "congestion": 3}),
}
SAFETY_KW = _kw({"خطر": 18, "مصابين": 25, "اصابه": 25, "حريق": 30, "صعق": 30, "مكشوف": 18, "انهيار": 25, "فيضان": 15,
                 "طفل": 12, "اطفال": 12, "مدرسه": 12, "مستشفي": 15, "مشاه": 8, "المارة": 8, "danger": 18, "injur": 25,
                 "fire": 30, "electrocut": 30, "exposed": 18, "collapse": 25, "child": 12, "school": 12, "hospital": 15})
URGENT_KW = [norm(k) for k in ["فورا", "عاجل", "طارئ", "حالا", "urgent", "immediately", "emergency", "asap"]]
DAMAGE_KW = [norm(k) for k in ["انفجار", "انهيار", "تضرر", "تلف", "هبوط", "فيضان", "burst", "collapse", "damage", "flood"]]
ACCIDENT_KW = [norm(k) for k in ["حادث", "تصادم", "مصابين", "accident", "crash", "collision", "injur"]]
BASE_RISK = {"road": 35, "garbage": 12, "water": 28, "light": 30, "traffic": 45, "other": 15}
IMPACT_BASE = {"road": 55, "garbage": 45, "water": 60, "light": 50, "traffic": 65, "other": 30}
WEIGHTS = {"safety": .30, "people": .20, "damage": .20, "urgency": .15, "impact": .15}


def locate(district, street):
    d = DISTRICTS[district]
    rnd = random.Random(norm(street) or district)
    clamp = lambda v: max(3, min(97, v))
    return round(clamp(d["x"] + rnd.uniform(-8, 8)), 1), round(clamp(d["y"] + rnd.uniform(-8, 8)), 1)


def tokens(s):
    return {w for w in re.findall(r"\w+", norm(s)) if len(w) > 2}


def find_duplicate(cat, title, desc, x, y, street, existing, exclude=None):
    tk, best = tokens(title + " " + desc), None
    for r in existing:
        if r["id"] == exclude or r["category"] != cat or r["status"] == "resolved":
            continue
        d = math.hypot(r["x"] - x, r["y"] - y)
        if d > 12:
            continue
        tr = tokens(r["title"] + " " + r["description"])
        score = len(tk & tr) / max(1, len(tk | tr))
        score += 0.3 if street and norm(street) == norm(r["street"]) else 0
        score += 0.15 if d < 5 else 0
        if score >= 0.3 and (not best or score > best["score"]):
            best = {"id": r["id"], "title": r["title"], "score": round(score, 2), "distance": round(d, 1)}
    return best


def analyze(title, desc, cat_hint, sev, people, district, street, existing, exclude=None):
    """Local rule-based engine. Returns a dict that can be swapped for a real AI API response."""
    text = norm(f"{title} {desc}")
    scores, hits = {}, {}
    for c, kw in CAT_KW.items():
        hits[c] = [k for k in kw if k in text]
        scores[c] = sum(kw[k] for k in hits[c])
    detected = max(scores, key=scores.get) if max(scores.values()) > 0 else "other"
    cat = cat_hint if cat_hint in TEAM_BY_CAT else detected
    sh = [k for k in SAFETY_KW if k in text]
    uh = [k for k in URGENT_KW if k in text]
    dh = [k for k in DAMAGE_KW if k in text]
    f = {
        "safety": min(100, BASE_RISK[cat] + sum(SAFETY_KW[k] for k in sh) + (sev - 1) * 8),
        "people": min(100, 33 * math.log10(people + 1)),
        "damage": min(100, sev * 14 + 15 * len(dh)),
        "urgency": min(100, sev * 12 + 25 * len(uh) + (20 if cat in ("water", "traffic") else 0)),
    }
    f["impact"] = min(100, IMPACT_BASE[cat] * 0.6 + f["people"] * 0.4)
    f = {k: round(v) for k, v in f.items()}
    base = sum(f[k] * w for k, w in WEIGHTS.items())
    x, y = locate(district, street)
    dup = find_duplicate(cat, title, desc, x, y, street, existing, exclude)
    bonus = 6 if dup else 0
    priority = max(0, min(100, round(base + bonus)))
    level = next(l for th, l in LEVELS if priority >= th)
    team = TEAM_BY_CAT[cat]
    if cat == "traffic" and any(k in text for k in ACCIDENT_KW):
        team = "emergency"
    return {
        "category": cat, "priority": priority, "level": level, "team": team,
        "urgency_h": SLA_HOURS[level], "x": x, "y": y, "duplicate_of": dup["id"] if dup else None,
        "analysis": {
            "engine": "local-rules-v1 (simulated, no live AI model)",
            "detected": detected, "chosen": cat, "scores": scores, "hits": {c: h for c, h in hits.items() if h},
            "factors": f, "weights": WEIGHTS, "signals": {"safety": sh, "urgency": uh, "damage": dh},
            "bonus": bonus, "base": round(base), "dup": dup, "action": ACTIONS[cat],
        },
    }


# ------------------------------------------------------------------ database
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_):
    db = g.pop("db", None)
    if db:
        db.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS reports(
 id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, description TEXT, category TEXT, district TEXT, street TEXT,
 x REAL, y REAL, severity INTEGER, people INTEGER, created_at TEXT, status TEXT DEFAULT 'new',
 priority INTEGER, level TEXT, team TEXT, urgency_h INTEGER, analysis TEXT, duplicate_of INTEGER,
 assigned_team TEXT, resolved_at TEXT);
CREATE TABLE IF NOT EXISTS resources(type TEXT PRIMARY KEY, name_ar TEXT, name_en TEXT, total INTEGER);
"""
now_iso = lambda dt=None: (dt or datetime.now()).strftime("%Y-%m-%dT%H:%M:%S")


def row(r):
    d = dict(r)
    d["analysis"] = json.loads(d["analysis"]) if d.get("analysis") else {}
    return d


def all_reports(db):
    return [row(r) for r in db.execute("SELECT * FROM reports ORDER BY id")]


def insert_report(db, p, a, status="new", created=None, assigned=None, resolved=None):
    cur = db.execute(
        "INSERT INTO reports(title,description,category,district,street,x,y,severity,people,created_at,status,priority,level,"
        "team,urgency_h,analysis,duplicate_of,assigned_team,resolved_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (p["title"], p["description"], a["category"], p["district"], p["street"], a["x"], a["y"], p["severity"], p["people"],
         created or now_iso(), status, a["priority"], a["level"], a["team"], a["urgency_h"], json.dumps(a["analysis"], ensure_ascii=False),
         a["duplicate_of"], assigned, resolved))
    return cur.lastrowid


SEED = [  # title, description, district, street, severity, people, hours_ago, status, resolution_hours
    ("حفرة عميقة في شارع الجمهورية", "حفرة كبيرة وعميقة في منتصف الشارع الرئيسي تسببت بتضرر عدة سيارات وتشكل خطرا على المارة", "downtown", "شارع الجمهورية", 4, 300, 30, "review", 0),
    ("حفرة قرب المدرسة في شارع الجمهورية", "حفرة عميقة في الشارع الرئيسي قرب المدرسة والسيارات تتضرر", "downtown", "شارع الجمهورية", 4, 200, 20, "new", 0),
    ("انفجار أنبوب مياه رئيسي", "انفجار في أنبوب المياه الرئيسي وفيضان في الشارع مع انقطاع المياه عن الحي، نرجو التدخل فورا", "east", "شارع الشرق", 5, 800, 3, "new", 0),
    ("تراكم النفايات عند الحاويات", "حاويات ممتلئة منذ ايام ونفايات متراكمة ورائحة كريهة", "north", "شارع الزهور", 3, 120, 50, "progress", 0),
    ("أعمدة إنارة معطلة قرب الجامعة", "عدة أعمدة انارة لا تعمل والشارع مظلم قرب الجامعة والطلاب يعبرون ليلا", "university", "شارع الطلبة", 3, 400, 10, "new", 0),
    ("حادث تصادم عند التقاطع", "تصادم بين سيارتين عند التقاطع مع وجود مصابين واغلاق الطريق، عاجل", "downtown", "تقاطع الملك", 5, 15, 1, "new", 0),
    ("تسرب مياه بسيط", "تسرب مياه بسيط من انبوب فرعي امام المنزل", "west", "شارع النخيل", 2, 10, 70, "resolved", 6),
    ("ازدحام بسبب إشارة معطلة", "اشارة المرور معطلة وازدحام شديد في ساعة الذروة", "south", "شارع الجنوب", 3, 600, 5, "progress", 0),
    ("كابل كهرباء مكشوف", "كابل كهرباء مكشوف ملقى على الرصيف بعد العاصفة وخطر صعق قرب اطفال", "west", "شارع الحدائق", 5, 50, 2, "new", 0),
    ("مخلفات بناء مهملة", "مخلفات بناء متراكمة على الرصيف", "industrial", "شارع المصانع", 2, 20, 90, "resolved", 18),
    ("تشقق في الأسفلت", "تشقق في الاسفلت وهبوط بسيط في الطريق", "south", "شارع السلام", 2, 60, 120, "resolved", 30),
    ("Broken streetlight", "Streetlight not working and the corner is dark at night", "north", "شارع الورد", 2, 30, 8, "review", 0),
    ("Water leak near school", "Water leaking from a pipe near the school entrance, flooding the sidewalk", "university", "شارع الجامعة", 4, 250, 12, "new", 0),
    ("Overflowing bins", "Trash bins overflowing and litter spreading on the street", "east", "شارع الأمل", 2, 80, 15, "new", 0),
    ("مصباح شارع محروق", "مصباح الشارع محروق منذ يومين", "downtown", "شارع البنك", 1, 20, 60, "resolved", 4),
]


def seed(db):
    db.execute("DELETE FROM reports")
    db.execute("DELETE FROM resources")
    db.execute("DELETE FROM sqlite_sequence WHERE name='reports'")
    db.executemany("INSERT INTO resources VALUES(?,?,?,?)", RESOURCES)
    existing, now = [], datetime.now()
    for t, d, dist, st, sev, ppl, ago, status, resh in SEED:
        p = {"title": t, "description": d, "district": dist, "street": st, "severity": sev, "people": ppl}
        a = analyze(t, d, None, sev, ppl, dist, st, existing)
        created = now - timedelta(hours=ago)
        res = (created + timedelta(hours=resh)) if status == "resolved" else None
        rid = insert_report(db, p, a, status, now_iso(created), a["team"] if status in ("progress", "resolved") else None, now_iso(res) if res else None)
        existing.append({"id": rid, "category": a["category"], "title": t, "description": d, "x": a["x"], "y": a["y"], "street": st, "status": status})
    db.commit()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    db.row_factory = sqlite3.Row
    if db.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 0:
        seed(db)
    db.close()


def err(ar, en, code=400):
    return jsonify(error=ar if request.headers.get("X-Lang", "ar") == "ar" else en), code


# ------------------------------------------------------- scheduling/allocation
def schedule(items, caps, busy):
    """Priority-first scheduler. Each unit is a worker; jobs go to the earliest-free unit.
    Critical incidents may fall back to emergency units if that gets them served >1h sooner."""
    heaps = {}
    for t, c in caps.items():
        b = min(busy.get(t, 0), c)
        heaps[t] = [DURATION[t] / 2] * b + [0] * (c - b)
        heapq.heapify(heaps[t])
    out = {}
    for it in sorted(items, key=lambda i: -i["priority"]):
        opts = [it["team"]] + (["emergency"] if it["level"] == "critical" and it["team"] != "emergency" else [])
        best = None
        for t in opts:
            if heaps.get(t):
                key = heaps[t][0] + (0 if t == it["team"] else 1)
                if best is None or key < best[2]:
                    best = (t, heaps[t][0], key)
        if not best:
            out[it["id"]] = {"team": it["team"], "start": None, "fallback": False}
            continue
        t, s, _ = best
        heapq.heapreplace(heaps[t], s + DURATION[t])
        out[it["id"]] = {"team": t, "start": s, "fallback": t != it["team"]}
    return out


def caps_busy(db):
    caps = {r["type"]: r["total"] for r in db.execute("SELECT * FROM resources")}
    busy = {t: 0 for t in caps}
    for r in db.execute("SELECT assigned_team, COUNT(*) c FROM reports WHERE status='progress' AND assigned_team IS NOT NULL GROUP BY assigned_team"):
        busy[r["assigned_team"]] = r["c"]
    return caps, busy


def pending(reps):
    return [r for r in reps if r["status"] in ("new", "review") and not r["duplicate_of"]]


def metrics(items, sched, caps, busy):
    etas = [(sched[i["id"]]["start"] + 0.5) if sched[i["id"]]["start"] is not None else 24 for i in items]
    crit = [e for i, e in zip(items, etas) if i["level"] == "critical"]
    util = {}
    for t, c in caps.items():
        now_assigned = sum(1 for i in items if sched[i["id"]]["team"] == t and sched[i["id"]]["start"] == 0)
        util[t] = {"util": round(100 * min(c, busy.get(t, 0) + now_assigned) / c) if c else 100, "total": c,
                   "demand": sum(1 for i in items if sched[i["id"]]["team"] == t)}
    return {"pending": len(items), "critical": sum(1 for i in items if i["level"] == "critical"),
            "avg_priority": round(sum(i["priority"] for i in items) / len(items)) if items else 0,
            "avg_eta": round(sum(etas) / len(etas), 1) if etas else 0, "crit_eta": round(sum(crit) / len(crit), 1) if crit else 0,
            "max_eta": round(max(etas), 1) if etas else 0,
            "waiting": sum(1 for i in items if sched[i["id"]]["start"] != 0), "util": util}


# ------------------------------------------------------------------- routes
@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.get("/api/meta")
def meta():
    return jsonify(districts=DISTRICTS, teams=[{"type": t, "ar": a, "en": e} for t, a, e, _ in RESOURCES])


def read_payload(data, partial=None):
    """Validate a report payload; returns (clean, error_tuple)."""
    src = {**(partial or {}), **data}
    title, desc = (src.get("title") or "").strip(), (src.get("description") or "").strip()
    if len(title) < 3:
        return None, ("العنوان قصير جداً (3 أحرف على الأقل)", "Title is too short (min 3 characters)")
    if len(desc) < 10:
        return None, ("الوصف قصير جداً (10 أحرف على الأقل)", "Description is too short (min 10 characters)")
    if src.get("district") not in DISTRICTS:
        return None, ("اختر الحي من القائمة", "Choose a district from the list")
    try:
        sev, ppl = int(src.get("severity", 3)), int(src.get("people") or 0)
    except (TypeError, ValueError):
        return None, ("الشدة وعدد المتأثرين يجب أن يكونا أرقاماً", "Severity and people affected must be numbers")
    if not 1 <= sev <= 5 or ppl < 0:
        return None, ("الشدة بين 1 و5 وعدد المتأثرين لا يقل عن 0", "Severity must be 1-5 and people affected ≥ 0")
    return {"title": title[:120], "description": desc[:1500], "district": src["district"], "street": (src.get("street") or "").strip()[:80],
            "severity": sev, "people": ppl, "category": src.get("category")}, None


@app.post("/api/analyze")
def api_analyze():
    p, e = read_payload(request.get_json(silent=True) or {})
    if e:
        return err(*e)
    a = analyze(p["title"], p["description"], p["category"], p["severity"], p["people"], p["district"], p["street"], all_reports(get_db()))
    return jsonify(a)


@app.get("/api/reports")
def list_reports():
    reps = all_reports(get_db())
    q = norm(request.args.get("q", ""))
    for k in ("category", "level", "status"):
        v = request.args.get(k)
        if v:
            reps = [r for r in reps if r[k] == v]
    if q:
        reps = [r for r in reps if q in norm(r["title"] + r["description"] + r["street"]) or q == str(r["id"])]
    return jsonify(sorted(reps, key=lambda r: -r["priority"]))


@app.post("/api/reports")
def create_report():
    db = get_db()
    p, e = read_payload(request.get_json(silent=True) or {})
    if e:
        return err(*e)
    a = analyze(p["title"], p["description"], p["category"], p["severity"], p["people"], p["district"], p["street"], all_reports(db))
    created = request.get_json().get("created_at") or None
    try:
        created = now_iso(datetime.fromisoformat(created)) if created else None
    except ValueError:
        return err("تاريخ غير صالح", "Invalid date")
    rid = insert_report(db, p, a, created=created)
    db.commit()
    return jsonify(row(db.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone())), 201


@app.route("/api/reports/<int:rid>", methods=["GET", "PUT", "DELETE"])
def one_report(rid):
    db = get_db()
    r = db.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
    if not r:
        return err("البلاغ غير موجود", "Report not found", 404)
    r = row(r)
    if request.method == "GET":
        return jsonify(r)
    if request.method == "DELETE":
        db.execute("DELETE FROM reports WHERE id=?", (rid,))
        db.execute("UPDATE reports SET duplicate_of=NULL WHERE duplicate_of=?", (rid,))
        db.commit()
        return jsonify(ok=True)
    data = request.get_json(silent=True) or {}
    status = data.get("status", r["status"])
    if status not in ("new", "review", "progress", "resolved"):
        return err("حالة غير صالحة", "Invalid status")
    team = data.get("assigned_team", r["assigned_team"]) or None
    if team and team not in TEAM_BY_CAT.values() and team != "emergency" and team not in {x[0] for x in RESOURCES}:
        return err("فريق غير صالح", "Invalid team")
    cur = {k: r[k] for k in ("title", "description", "district", "street", "severity", "people", "category")}
    p, e = read_payload({k: v for k, v in data.items() if k in cur}, cur)
    if e:
        return err(*e)
    a = analyze(p["title"], p["description"], p["category"], p["severity"], p["people"], p["district"], p["street"], all_reports(db), exclude=rid)
    if status == "progress" and not team:
        team = a["team"]
    resolved = (r["resolved_at"] or now_iso()) if status == "resolved" else None
    db.execute("UPDATE reports SET title=?,description=?,category=?,district=?,street=?,x=?,y=?,severity=?,people=?,priority=?,level=?,team=?,"
               "urgency_h=?,analysis=?,duplicate_of=?,status=?,assigned_team=?,resolved_at=? WHERE id=?",
               (p["title"], p["description"], a["category"], p["district"], p["street"], a["x"], a["y"], p["severity"], p["people"], a["priority"],
                a["level"], a["team"], a["urgency_h"], json.dumps(a["analysis"], ensure_ascii=False), a["duplicate_of"], status, team, resolved, rid))
    db.commit()
    return jsonify(row(db.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()))


@app.get("/api/resources")
def resources():
    db = get_db()
    caps, busy = caps_busy(db)
    reps = all_reports(db)
    out = []
    for r in db.execute("SELECT * FROM resources"):
        t = r["type"]
        jobs = [{"id": x["id"], "title": x["title"], "level": x["level"]} for x in reps if x["status"] == "progress" and x["assigned_team"] == t]
        out.append({"type": t, "ar": r["name_ar"], "en": r["name_en"], "total": r["total"], "busy": min(busy[t], r["total"]),
                    "available": max(0, r["total"] - busy[t]), "util": round(100 * min(busy[t], r["total"]) / r["total"]), "jobs": jobs})
    return jsonify(out)


@app.post("/api/allocate")
def allocate():
    """Allocate pending incidents. commit=false returns recommendations only."""
    db = get_db()
    commit = bool((request.get_json(silent=True) or {}).get("commit"))
    caps, busy = caps_busy(db)
    reps = all_reports(db)
    items = pending(reps)
    sched = schedule(items, caps, busy)
    plan = []
    for i in sorted(items, key=lambda i: -i["priority"]):
        s = sched[i["id"]]
        now_ok = s["start"] == 0
        plan.append({"id": i["id"], "title": i["title"], "priority": i["priority"], "level": i["level"], "recommended": i["team"],
                     "team": s["team"], "fallback": s["fallback"], "start": s["start"], "assigned": now_ok and commit})
        if commit and now_ok:
            db.execute("UPDATE reports SET status='progress', assigned_team=? WHERE id=?", (s["team"], i["id"]))
    db.commit()
    return jsonify(plan=plan, committed=commit, merged=[{"id": r["id"], "title": r["title"], "of": r["duplicate_of"]} for r in reps
                                                          if r["duplicate_of"] and r["status"] != "resolved"],
                   metrics=metrics(items, sched, caps, busy))


@app.get("/api/stats")
def stats():
    db = get_db()
    reps = all_reports(db)
    opn = [r for r in reps if r["status"] != "resolved"]
    resolved = [r for r in reps if r["resolved_at"]]
    hrs = [(datetime.fromisoformat(r["resolved_at"]) - datetime.fromisoformat(r["created_at"])).total_seconds() / 3600 for r in resolved]
    caps, busy = caps_busy(db)
    items = pending(reps)
    m = metrics(items, schedule(items, caps, busy), caps, busy)
    dist = {k: {"count": 0, "max": 0} for k in DISTRICTS}
    for r in opn:
        dist[r["district"]]["count"] += 1
        dist[r["district"]]["max"] = max(dist[r["district"]]["max"], r["priority"])
    mean = sum(r["priority"] for r in opn) / len(opn) if opn else 0
    health = round(max(0, 100 - mean * 0.7 - 4 * sum(1 for r in opn if r["level"] == "critical")))
    count = lambda key, vals, src=reps: {v: sum(1 for r in src if r[key] == v) for v in vals}
    return jsonify(total=len(reps), pending=sum(1 for r in reps if r["status"] in ("new", "review")),
                   progress=sum(1 for r in reps if r["status"] == "progress"), resolved=len(resolved) + sum(1 for r in reps if r["status"] == "resolved" and not r["resolved_at"]),
                   critical=sum(1 for r in opn if r["level"] == "critical"),
                   avg_response=round(sum(hrs) / len(hrs), 1) if hrs else None, est_eta=m["avg_eta"], health=health, districts=dist,
                   by_status=count("status", ["new", "review", "progress", "resolved"]),
                   by_category=count("category", list(TEAM_BY_CAT)), by_level=count("level", ["critical", "high", "medium", "low"], opn),
                   duplicates=sum(1 for r in opn if r["duplicate_of"]))


@app.post("/api/reset")
def reset():
    db = get_db()
    seed(db)
    return jsonify(ok=True)


# -------------------------------------------------------------- simulation
SCENARIOS = {
    "water": {"n": 12, "cat": "water", "caps": 1.0},
    "accidents": {"n": 8, "cat": "traffic", "caps": 1.0},
    "blackout": {"n": 14, "cat": "light", "caps": 1.0},
    "garbage": {"n": 10, "cat": "garbage", "caps": 1.0},
    "limited": {"n": 6, "cat": "mixed", "caps": 0.4},
}
TEMPLATES = {
    "water": [("انفجار أنبوب مياه", "انفجار في أنبوب المياه وفيضان في الشارع وانقطاع المياه عن الحي"), ("تسرب مياه كبير", "تسرب مياه غزير يغمر الرصيف ويهدد المنازل، عاجل")],
    "traffic": [("حادث تصادم متعدد", "تصادم عدة مركبات مع وجود مصابين واغلاق الطريق، عاجل"), ("حادث سير على الطريق السريع", "حادث تصادم خطير وازدحام شديد وخطر على المارة")],
    "light": [("انقطاع الكهرباء عن الحي", "انقطاع التيار الكهربائي وظلام تام في الشارع واشارات المرور معطلة"), ("أعمدة إنارة معطلة", "اعمدة الانارة لا تعمل والمنطقة مظلمة وخطر على المشاة")],
    "garbage": [("تراكم نفايات كبير", "نفايات متراكمة وحاويات ممتلئة ورائحة كريهة منذ ايام"), ("نفايات متناثرة", "قمامة متناثرة في الشارع قرب المدرسة")],
    "road": [("حفرة عميقة", "حفرة عميقة في الطريق تسبب تضرر السيارات")],
}


@app.post("/api/simulate")
def simulate():
    db = get_db()
    data = request.get_json(silent=True) or {}
    sc = SCENARIOS.get(data.get("scenario"))
    if not sc:
        return err("سيناريو غير معروف", "Unknown scenario")
    reps = all_reports(db)
    caps, busy = caps_busy(db)
    items = pending(reps)
    before = metrics(items, schedule(items, caps, busy), caps, busy)
    rnd = random.Random(data["scenario"] + str(len(reps)))
    added = []
    for n in range(sc["n"]):
        cat = rnd.choice(["road", "garbage", "water", "light", "traffic"]) if sc["cat"] == "mixed" else sc["cat"]
        title, desc = rnd.choice(TEMPLATES[cat])
        dist = rnd.choice(list(DISTRICTS))
        p = {"title": title, "description": desc, "district": dist, "street": f"{n + 1}-{dist}", "severity": rnd.randint(3, 5),
             "people": rnd.randint(30, 900), "category": None}
        a = analyze(title, desc, None, p["severity"], p["people"], dist, p["street"], [])
        added.append((p, a))
    after_caps = {t: max(1, round(c * sc["caps"])) for t, c in caps.items()}
    after_busy = {t: min(b, after_caps[t]) for t, b in busy.items()}
    sim_items = items + [{"id": -(i + 1), "title": p["title"], "priority": a["priority"], "level": a["level"], "team": a["team"]} for i, (p, a) in enumerate(added)]
    sched = schedule(sim_items, after_caps, after_busy)
    after = metrics(sim_items, sched, after_caps, after_busy)
    decisions = []
    for it in sorted(sim_items, key=lambda i: -i["priority"])[:6]:
        s = sched[it["id"]]
        decisions.append({"title": it["title"], "priority": it["priority"], "level": it["level"], "team": s["team"], "start": s["start"], "fallback": s["fallback"],
                          "type": "queued" if s["start"] != 0 else ("fallback" if s["fallback"] else "assign")})
    overload = [t for t, u in after["util"].items() if u["demand"] > u["total"]]
    if data.get("apply"):
        for p, a in added:
            insert_report(db, p, a)
        db.commit()
    return jsonify(scenario=data["scenario"], added=len(added), before=before, after=after, decisions=decisions, overload=overload,
                   caps_factor=sc["caps"], applied=bool(data.get("apply")))


init_db()
if __name__ == "__main__":
    app.run(debug=False, port=5000)
