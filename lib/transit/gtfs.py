"""Static GTFS for every feed the planner uses, merged into one SQLite file.

Three feeds: WMATA bus and WMATA rail (both need the API key) and Montgomery
County Ride On (public). They are downloaded into ledger/raw/transit/, which
is disposable, and loaded into ledger/raw/transit/gtfs.db with every id
prefixed by its feed ("wb:", "wr:", "ro:") so the three cannot collide.

Times are stored as seconds after midnight of the service day, which can run
past 86400 for trips after midnight, exactly as GTFS writes them.

A rebuild writes gtfs.db.new and renames it over the old file, so a query
running during a rebuild still sees a whole database.
"""
import csv, io, math, os, sqlite3, time, urllib.request, zipfile

from herald import config

DIR = config.RAW / "transit"
DB = DIR / "gtfs.db"

FEEDS = {
    "wb": {"name": "Metrobus", "url": "https://api.wmata.com/gtfs/bus-gtfs-static.zip", "key": True},
    "wr": {"name": "Metrorail", "url": "https://api.wmata.com/gtfs/rail-gtfs-static.zip", "key": True},
    "ro": {"name": "Ride On", "url": "https://www.montgomerycountymd.gov/DOT-Transit/Resources/Files/GTFS/RideOnGTFS.zip", "key": False},
}

# Stops closer than this are treated as a walkable transfer.
TRANSFER_M = 350

SCHEMA = """
CREATE TABLE stops (id TEXT PRIMARY KEY, feed TEXT, raw_id TEXT, code TEXT, name TEXT,
                    lat REAL, lon REAL, kind INTEGER, parent TEXT);
CREATE TABLE routes (id TEXT PRIMARY KEY, feed TEXT, short TEXT, long TEXT, type INTEGER);
CREATE TABLE trips (id TEXT PRIMARY KEY, route TEXT, service TEXT, headsign TEXT, direction INTEGER);
CREATE TABLE stop_times (trip TEXT, seq INTEGER, stop TEXT, arr INTEGER, dep INTEGER,
                         pickup INTEGER, dropoff INTEGER);
CREATE TABLE calendar (service TEXT PRIMARY KEY, days TEXT, start TEXT, end TEXT);
CREATE TABLE calendar_dates (service TEXT, date TEXT, type INTEGER);
CREATE TABLE transfers (a TEXT, b TEXT, metres INTEGER);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""
INDEXES = """
CREATE INDEX st_dep ON stop_times(dep);
CREATE INDEX st_trip ON stop_times(trip, seq);
CREATE INDEX st_stop ON stop_times(stop, dep);
CREATE INDEX stops_code ON stops(code);
CREATE INDEX tr_a ON transfers(a);
CREATE INDEX cd_date ON calendar_dates(date);
"""


def _secs(t: str):
    if not t:
        return None
    h, m, s = t.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def download(feed: str) -> "pathlib.Path":
    f = FEEDS[feed]
    DIR.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(f["url"], headers={"User-Agent": "herald-transit"})
    if f["key"]:
        req.add_header("api_key", config.secret("transit.wmata_api_key"))
    path = DIR / f"{feed}.zip"
    with urllib.request.urlopen(req, timeout=300) as r, open(str(path) + ".part", "wb") as out:
        while chunk := r.read(1 << 20):
            out.write(chunk)
    os.replace(str(path) + ".part", path)
    return path


def _rows(z: zipfile.ZipFile, name: str):
    if name not in z.namelist():
        return
    with z.open(name) as fh:
        yield from csv.DictReader(io.TextIOWrapper(fh, "utf-8-sig"))


def _load(con, feed: str, path):
    p = feed + ":"
    z = zipfile.ZipFile(path)
    con.executemany("INSERT INTO stops VALUES (?,?,?,?,?,?,?,?,?)", (
        (p + r["stop_id"], feed, r["stop_id"], r.get("stop_code") or None, r["stop_name"].strip(),
         float(r["stop_lat"]), float(r["stop_lon"]), int(r.get("location_type") or 0),
         (p + r["parent_station"]) if r.get("parent_station") else None)
        for r in _rows(z, "stops.txt")))
    con.executemany("INSERT INTO routes VALUES (?,?,?,?,?)", (
        (p + r["route_id"], feed, r.get("route_short_name") or r["route_id"],
         r.get("route_long_name") or "", int(r.get("route_type") or 3))
        for r in _rows(z, "routes.txt")))
    con.executemany("INSERT INTO trips VALUES (?,?,?,?,?)", (
        (p + r["trip_id"], p + r["route_id"], p + r["service_id"], r.get("trip_headsign") or "",
         int(r["direction_id"]) if r.get("direction_id") else None)
        for r in _rows(z, "trips.txt")))
    con.executemany("INSERT INTO stop_times VALUES (?,?,?,?,?,?,?)", (
        (p + r["trip_id"], int(r["stop_sequence"]), p + r["stop_id"],
         _secs(r["arrival_time"]) if r["arrival_time"] else _secs(r["departure_time"]),
         _secs(r["departure_time"]) if r["departure_time"] else _secs(r["arrival_time"]),
         int(r.get("pickup_type") or 0), int(r.get("drop_off_type") or 0))
        for r in _rows(z, "stop_times.txt") if r["arrival_time"] or r["departure_time"]))
    days = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    con.executemany("INSERT INTO calendar VALUES (?,?,?,?)", (
        (p + r["service_id"], "".join(r[d] for d in days), r["start_date"], r["end_date"])
        for r in _rows(z, "calendar.txt")))
    con.executemany("INSERT INTO calendar_dates VALUES (?,?,?)", (
        (p + r["service_id"], r["date"], int(r["exception_type"]))
        for r in _rows(z, "calendar_dates.txt")))
    info = next(_rows(z, "feed_info.txt"), None) or {}
    con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)",
                (f"{feed}.version", info.get("feed_version") or info.get("feed_end_date") or ""))


def _transfers(con):
    """Stop pairs within TRANSFER_M, found on a coarse grid instead of n^2."""
    stops = con.execute("SELECT id, lat, lon FROM stops WHERE kind=0").fetchall()
    cell = 0.004  # about 440 m of latitude
    grid = {}
    for s in stops:
        grid.setdefault((int(s[1] / cell), int(s[2] / cell)), []).append(s)
    out = []
    for (gy, gx), here in grid.items():
        near = [t for dy in (-1, 0, 1) for dx in (-1, 0, 1) for t in grid.get((gy + dy, gx + dx), ())]
        for a in here:
            for b in near:
                if a[0] == b[0]:
                    continue
                d = distance_m(a[1], a[2], b[1], b[2])
                if d <= TRANSFER_M:
                    out.append((a[0], b[0], round(d)))
    con.executemany("INSERT INTO transfers VALUES (?,?,?)", out)
    return len(out)


def distance_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def build(feeds=None, fetch=True) -> dict:
    feeds = feeds or list(FEEDS)
    counts = {}
    for f in feeds:
        if fetch or not (DIR / f"{f}.zip").exists():
            download(f)
    tmp = DIR / "gtfs.db.new"
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;" + SCHEMA)
    for f in FEEDS:
        if (DIR / f"{f}.zip").exists():
            _load(con, f, DIR / f"{f}.zip")
    counts["transfers"] = _transfers(con)
    con.executescript(INDEXES)
    con.execute("INSERT OR REPLACE INTO meta VALUES ('built', ?)", (time.strftime("%Y-%m-%dT%H:%M:%S"),))
    con.commit()
    for t in ("stops", "routes", "trips", "stop_times"):
        counts[t] = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
    con.close()
    os.replace(tmp, DB)
    return counts


def connect() -> sqlite3.Connection:
    if not DB.exists():
        raise SystemExit("No schedule database yet. Run: herald-transit gtfs update")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def active_services(con, date: str) -> set:
    """Service ids running on date (YYYYMMDD)."""
    import datetime
    wd = datetime.date(int(date[:4]), int(date[4:6]), int(date[6:])).weekday()
    on = {r[0] for r in con.execute(
        "SELECT service FROM calendar WHERE start<=? AND end>=? AND substr(days,?,1)='1'",
        (date, date, wd + 1))}
    for svc, typ in con.execute("SELECT service, type FROM calendar_dates WHERE date=?", (date,)):
        if typ == 1:
            on.add(svc)
        else:
            on.discard(svc)
    return on


def stops_near(con, lat, lon, radius_m=400, limit=40):
    dlat = radius_m / 111000
    dlon = radius_m / (111000 * math.cos(math.radians(lat)))
    rows = con.execute(
        "SELECT s.*, (SELECT group_concat(DISTINCT r.short) FROM stop_times st JOIN trips t ON t.id=st.trip "
        "JOIN routes r ON r.id=t.route WHERE st.stop=s.id) AS routes "
        "FROM stops s WHERE kind=0 AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
        (lat - dlat, lat + dlat, lon - dlon, lon + dlon)).fetchall()
    out = [(distance_m(lat, lon, r["lat"], r["lon"]), r) for r in rows]
    out = [x for x in out if x[0] <= radius_m]
    out.sort(key=lambda x: x[0])
    return out[:limit]
