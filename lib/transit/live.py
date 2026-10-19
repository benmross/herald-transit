"""Live data: WMATA's API and GTFS-realtime feeds, wmata.com's advisories, and RideOn.app.

WMATA (api.wmata.com) is the documented developer API, authenticated with the
default-tier key in secrets.json under transit.wmata_api_key.

wmata.com's advisories list is where planned detours appear days ahead (a
football game, a street festival). The API's incidents and the GTFS-rt alerts
feed only carry them once they are live, so a question about tomorrow needs
this one. The JSON endpoint is the one the public page itself calls.

Ride On publishes no realtime feed. RideOn.app, the county's own public site,
reads its data from three unauthenticated endpoints, found by recording the
site's network traffic on 16 Oct 2026:

  /busstop?take=N&stopId=<GTFS stop_id>   predictions for one stop, as HTML
  /GetBusPositions?StopId=<stop_id>        buses approaching a stop, as JSON
  /Delays                                  current detours, as HTML

Ride On stop signs show stop_code (five digits, e.g. 22700); the endpoints take
stop_id (2700). gtfs.stops holds both. The site renders HTML, so the parsing
below is keyed on its element ids and breaks if the county redesigns it; the
fixture test in tests/ catches that.
"""
import datetime, html, json, re, time, urllib.parse, urllib.request

from herald import config

WMATA = "https://api.wmata.com"
RIDEON = "https://rideon.app"
UA = "Mozilla/5.0 (herald-transit)"


def _get(url, headers=None, timeout=20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def wmata(path, **params):
    q = ("?" + urllib.parse.urlencode(params)) if params else ""
    return json.loads(_get(WMATA + path + q, {"api_key": config.secret("transit.wmata_api_key")}))


def wmata_rt(feed):
    """A GTFS-realtime feed: bus-gtfsrt-alerts, bus-gtfsrt-tripupdates, ..."""
    from google.transit import gtfs_realtime_pb2
    m = gtfs_realtime_pb2.FeedMessage()
    m.ParseFromString(_get(f"{WMATA}/gtfs/{feed}.pb", {"api_key": config.secret("transit.wmata_api_key")}))
    return m


# ---- WMATA -----------------------------------------------------------------

def bus_predictions(stop_code):
    """stop_code is the 7-digit number on a Metrobus sign (gtfs stops.code)."""
    d = wmata("/NextBusService.svc/json/jPredictions", StopID=stop_code)
    return {"stop": d.get("StopName"), "predictions": [
        {"route": p["RouteID"], "toward": p.get("DirectionText"), "minutes": p["Minutes"],
         "vehicle": p.get("VehicleID"), "trip": p.get("TripID"), "live": True}
        for p in d.get("Predictions", [])]}


def bus_positions(route):
    d = wmata("/Bus.svc/json/jBusPositions", RouteID=route)
    return [{"vehicle": b["VehicleID"], "lat": b["Lat"], "lon": b["Lon"],
             "toward": b.get("TripHeadsign") or b.get("DirectionText"),
             "deviation_min": b.get("Deviation"), "at": b.get("DateTime"), "trip": b.get("TripID")}
            for b in d.get("BusPositions", [])]


def rail_predictions(station_codes):
    d = wmata(f"/StationPrediction.svc/json/GetPrediction/{station_codes}")
    return [{"line": t["Line"], "to": t.get("DestinationName") or t.get("Destination"),
             "min": t["Min"], "cars": t.get("Car"), "station": t["LocationName"], "track": t.get("Group")}
            for t in d.get("Trains", [])]


def rail_stations():
    return wmata("/Rail.svc/json/jStations").get("Stations", [])


def alerts(route=None):
    """Live alerts: GTFS-rt alerts for bus and rail, plus rail incidents."""
    out = []
    for feed in ("bus-gtfsrt-alerts", "rail-gtfsrt-alerts"):
        try:
            m = wmata_rt(feed)
        except Exception as e:
            out.append({"source": feed, "error": str(e)})
            continue
        for ent in m.entity:
            a = ent.alert
            routes = sorted({ie.route_id for ie in a.informed_entity if ie.route_id})
            if route and route.upper() not in {r.upper() for r in routes}:
                continue
            text = lambda ts: (ts.translation[0].text if ts.translation else "")
            periods = [(p.start, p.end) for p in a.active_period]
            out.append({"source": "wmata-live", "routes": routes, "header": text(a.header_text).strip(),
                        "description": text(a.description_text).strip(), "url": text(a.url),
                        "active": [(_ts(s), _ts(e)) for s, e in periods]})
    return out


def _ts(epoch):
    if not epoch:
        return None
    return datetime.datetime.fromtimestamp(epoch).strftime("%a %d %b %-I:%M %p")


ADVISORIES = ("https://www.wmata.com/ride/alerts-and-advisories/_jcr_content/root/container/"
              "row-container/main-container/main-editable/alerts_advisories_li.results.json")


def advisories(date=None, route=None):
    """Planned advisories from wmata.com in effect on date (YYYY-MM-DD), with full text."""
    date = date or datetime.date.today().isoformat()
    d = json.loads(_get(f"{ADVISORIES}?dateRangeStart={date}"))
    day = datetime.date.fromisoformat(date)
    out = []
    for a in d.get("advisoryResults", []):
        p = a.get("properties")
        p = json.loads(p) if isinstance(p, str) else (p or {})
        routes = [r["shortName"] for r in (p.get("relatedBusRoutes") or [])]
        lines = [r.get("shortName") or r.get("longName") for r in (p.get("relatedRailLines") or [])]
        if route and route.upper() not in {x.upper() for x in routes + lines if x}:
            continue
        start = _day(p.get("effectiveDateStart")); end = _day(p.get("effectiveDateEnd"))
        if start and start > day:
            continue
        if end and end < day:
            continue
        out.append({"title": a.get("title"), "routes": routes, "lines": lines,
                    "from": p.get("effectiveDateStart"), "to": p.get("effectiveDateEnd"),
                    "summary": a.get("description"), "url": a.get("url")})
    return out


def _day(s):
    try:
        return datetime.datetime.strptime(s, "%d %b %Y").date()
    except (TypeError, ValueError):
        return None


def advisory_text(url):
    """The body of one advisory page, as plain text."""
    page = _get(url).decode("utf-8", "replace")
    m = re.search(r'<main.*?</main>', page, re.S)
    body = m.group(0) if m else page
    body = re.sub(r"<(script|style|nav|header|footer)\b.*?</\1>", " ", body, flags=re.S)
    body = re.sub(r"<(li)\b[^>]*>", "\n- ", body)
    body = re.sub(r"<(p|br|h\d|div)\b[^>]*>", "\n", body)
    text = html.unescape(re.sub(r"<[^>]+>", " ", body))
    lines = [re.sub(r"[ \t]+", " ", l).strip() for l in text.splitlines()]
    # The page repeats each block as escaped JSON in an attribute; those lines
    # still carry markup and are dropped.
    text = "\n".join(l for l in lines if l and "<" not in l and "\\r\\n" not in l)
    i = text.find("Advisory Effective")
    text = text[i:] if i >= 0 else text
    return text.split("Get in touch with Metro")[0].strip()


# ---- Ride On ---------------------------------------------------------------

def _clean(s):
    return html.unescape(re.sub(r"<[^>]+>", " ", s)).replace("\xa0", " ").strip()


def parse_rideon_stop(page: str) -> dict:
    head = re.search(r'id="BusStopArrivals1_txtHeading"[^>]*value="([^"]*)"', page) or \
        re.search(r'value="([^"]*)"[^>]*id="BusStopArrivals1_txtHeading"', page)
    rows = []
    for m in re.finditer(r'<tr title="([^"]*)"[^>]*>(.*?)</tr>', page, re.S):
        title, body = m.group(1), m.group(2)
        def cell(name):
            c = re.search(rf'_{name}_\d+"[^>]*>(.*?)</span>', body, re.S)
            return _clean(c.group(1)) if c else None
        color = re.search(r'hiddencol[^>]*>\s*(#[0-9A-Fa-f]{6})', body)
        wifi = re.search(r'imgWifi_\d+" title="([^"]*)"', body)
        sched = re.search(r"SchedTime:\s*([\d:]+\s*[AP]M)", title)
        bus = re.search(r"Bus #(\d+)", title)
        load = re.search(r'imgLoad_\d+" title="Shows bus passenger load: ([^"]*)"', body)
        rows.append({"route": cell("lbRoutheShortName"), "toward": cell("lbTripHeadSign"),
                     "time": cell("lbArrivalTime"), "minutes": cell("lbMinutes"),
                     "scheduled": sched.group(1) if sched else None,
                     "vehicle": bus.group(1) if bus else None,
                     # The site colours a row by its source: a black row is the
                     # timetable, anything else is a tracked bus. Its own note says a
                     # black row under 15 minutes very likely will not show.
                     "live": bool(color and color.group(1).upper() != "#000000"),
                     "status": wifi.group(1) if wifi else None,
                     "load": load.group(1) if load else None})
    return {"stop": html.unescape(head.group(1)).strip() if head else None, "predictions": rows}


def rideon_stop(stop_id, take=15):
    page = _get(f"{RIDEON}/busstop?take={take}&ShowRefreshNote=false&stopId={stop_id}").decode("utf-8", "replace")
    return parse_rideon_stop(page)


def rideon_approaching(stop_id):
    d = json.loads(_get(f"{RIDEON}/GetBusPositions?GetShapesCoordinates=false&StopId={stop_id}"))
    return [{"route": v.get("RouteShortName"), "toward": v.get("TripHeadsign"), "vehicle": v.get("vehicle_id"),
             "lat": v.get("latitude"), "lon": v.get("longitude"),
             "next_stop": (v.get("Next1StopName") or "").replace("\r\n", " "),
             "stops_away": v.get("StopsAwayFromStop1"), "status": _clean(v.get("StatusInfo") or "").split("  ")[0]}
            for v in d]


def parse_rideon_delays(page: str) -> list:
    out = []
    parts = re.split(r'id="MainContent_rptDetours_lblColTitle_\d+">', page)[1:]
    for p in parts:
        title = _clean(p.split("</span>", 1)[0])
        field = lambda k: (re.search(rf"<strong>{k}:\s*</strong>\s*(.*?)</div>", p, re.S) or [None, ""])[1]
        stops = [_clean(s) for s in re.findall(r'<div class="col-12">Stop:\s*(.*?)</div>', p, re.S)]
        out.append({"title": re.sub(r"\s+", " ", title), "from": _clean(field("Start Date")),
                    "to": _clean(field("End Date")), "description": _clean(field("Description")),
                    "stops": stops})
    return out


def rideon_delays(route=None):
    rows = parse_rideon_delays(_get(f"{RIDEON}/Delays").decode("utf-8", "replace"))
    if route:
        rows = [r for r in rows if r["title"].split("-", 1)[0].strip() == str(route)]
    return rows
