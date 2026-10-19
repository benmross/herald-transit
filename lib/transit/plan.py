"""A trip planner over the merged schedule: Connection Scan with a rides dimension.

Connection Scan (Dibbelt et al., 2013) walks every vehicle hop in a time window
in departure order, once, and keeps the earliest arrival at each stop. Keeping
that per number of rides taken gives the Pareto set a person actually chooses
from: the fastest trip, and the one with fewer changes that arrives a little
later. That is what the Transit app shows as its rows.

Walking is straight-line distance times DETOUR at WALK_MPS. Transfers are the
precomputed stop pairs in gtfs.transfers. This is schedule-only. It knows
nothing about detours or delays, so the CLI checks live alerts and advisories
for the routes in the answer and prints them next to it.
"""
import bisect, datetime

from . import gtfs

WALK_MPS = 1.3
DETOUR = 1.25
TRANSFER_SLACK = 60     # seconds added to every walked transfer
MAX_RIDES = 4
WINDOW = 3 * 3600
INF = float("inf")


def walk_secs(metres):
    return int(metres * DETOUR / WALK_MPS)


class Network:
    """The connections for one service day and window, loaded once and reused."""

    def __init__(self, con, date: str, start: int, end: int):
        self.con = con
        svc = gtfs.active_services(con, date)
        con.execute("CREATE TEMP TABLE IF NOT EXISTS svc (id TEXT PRIMARY KEY)")
        con.execute("DELETE FROM svc")
        con.executemany("INSERT INTO svc VALUES (?)", ((s,) for s in svc))
        rows = con.execute(
            "SELECT st.trip, st.seq, st.stop, st.arr, st.dep, st.pickup, st.dropoff "
            "FROM stop_times st JOIN trips t ON t.id = st.trip JOIN svc ON svc.id = t.service "
            "WHERE st.dep BETWEEN ? AND ? ORDER BY st.trip, st.seq", (start, end + 3600)).fetchall()
        conns = []
        prev = None
        for r in rows:
            if prev is not None and prev[0] == r[0]:
                # (dep, arr, from, to, trip, can_board, can_alight)
                conns.append((prev[4], r[3], prev[2], r[2], r[0], prev[5] != 1, r[6] != 1))
            prev = r
        conns.sort(key=lambda c: c[0])
        self.conns = conns
        self.deps = [c[0] for c in conns]
        self.transfers = {}
        for a, b, m in con.execute("SELECT a, b, metres FROM transfers"):
            self.transfers.setdefault(a, []).append((b, m))

    def scan(self, access: dict, egress: dict, t0: int):
        """access/egress: {stop: metres}. Returns {rides: journey} on the Pareto front."""
        K = MAX_RIDES
        T = [dict() for _ in range(K + 1)]
        J = [dict() for _ in range(K + 1)]
        for s, m in access.items():
            T[0][s] = t0 + walk_secs(m)
            J[0][s] = ("access", None, m)
        trip_k, trip_board = {}, {}
        best = {}
        cutoff = INF
        for i in range(bisect.bisect_left(self.deps, t0), len(self.conns)):
            dep, arr, a, b, trip, can_board, can_alight = self.conns[i]
            if dep > cutoff:
                break
            k0 = trip_k.get(trip)
            if can_board:
                for k in range(0, K if k0 is None else min(k0, K)):
                    if T[k].get(a, INF) <= dep:
                        trip_k[trip] = k0 = k
                        trip_board[trip] = (i, a)
                        break
            if k0 is None or not can_alight:
                continue
            k1 = k0 + 1
            if arr < T[k1].get(b, INF):
                T[k1][b] = arr
                J[k1][b] = ("ride", trip, trip_board[trip], i)
                for c, m in self.transfers.get(b, ()):
                    t = arr + walk_secs(m) + TRANSFER_SLACK
                    if t < T[k1].get(c, INF):
                        T[k1][c] = t
                        J[k1][c] = ("walk", b, m)
                if b in egress:
                    done = arr + walk_secs(egress[b])
                    if done < best.get(k1, (INF,))[0]:
                        best[k1] = (done, b)
                        # A journey arriving 40 minutes after the fastest is not one
                        # anyone would pick, so stop scanning past that.
                        cutoff = min(cutoff, done + 2400)
        front, last = {}, INF
        for k in sorted(best):
            done, stop = best[k]
            if done < last:
                front[k] = self._journey(J, k, stop, egress[stop], done)
                last = done
        return front

    def _journey(self, J, k, stop, egress_m, done):
        legs = [{"mode": "walk", "to": "destination", "metres": egress_m, "from_stop": stop,
                 "arrive": done}]
        while True:
            entry = J[k][stop]
            if entry[0] == "access":
                legs.append({"mode": "walk", "from": "origin", "to_stop": stop, "metres": entry[2]})
                break
            if entry[0] == "walk":
                legs.append({"mode": "walk", "from_stop": entry[1], "to_stop": stop, "metres": entry[2]})
                stop = entry[1]
                continue
            _, trip, (bi, bstop), ai = entry
            legs.append({"mode": "ride", "trip": trip, "from_stop": bstop, "to_stop": stop,
                         "depart": self.conns[bi][0], "arrive": self.conns[ai][1]})
            stop, k = bstop, k - 1
        legs.reverse()
        first = next(l for l in legs if l["mode"] == "ride")
        legs[0]["depart"] = first["depart"] - walk_secs(legs[0]["metres"])
        return {"leave": legs[0]["depart"], "arrive": done, "legs": legs,
                "rides": sum(1 for l in legs if l["mode"] == "ride")}


def endpoints(con, lat, lon, max_walk):
    return {r["id"]: round(d) for d, r in gtfs.stops_near(con, lat, lon, max_walk, limit=400)}


def plan(con, origin, dest, date: str, depart=None, arrive=None, max_walk=900, count=4):
    """origin/dest are (lat, lon). depart/arrive are seconds after midnight on date."""
    access = endpoints(con, *origin, max_walk)
    egress = endpoints(con, *dest, max_walk)
    if not access or not egress:
        return {"error": "no stops within walking distance of " + ("origin" if not access else "destination")}
    if arrive is not None:
        start = arrive - WINDOW
        net = Network(con, date, start, arrive)
    else:
        start = depart
        net = Network(con, date, depart, depart + WINDOW)
    found, seen, t = [], set(), start
    for _ in range(40):
        front = net.scan(access, egress, t)
        if not front:
            break
        for j in front.values():
            sig = tuple((l.get("trip"), l.get("from_stop")) for l in j["legs"] if l["mode"] == "ride")
            if sig not in seen and (arrive is None or j["arrive"] <= arrive):
                seen.add(sig)
                found.append(j)
        # Move just past the earliest departure found, to get the next one.
        t = min(j["leave"] for j in front.values()) + 60
        if arrive is None and len(found) >= count * 2:
            break
        if arrive is not None and min(j["arrive"] for j in front.values()) > arrive:
            break
    if arrive is not None:
        found.sort(key=lambda j: (-j["leave"], j["rides"]))
    else:
        found.sort(key=lambda j: (j["arrive"], j["rides"]))
    return {"journeys": _prune(found)[:count]}


def _prune(js):
    """Drop journeys that leave earlier and arrive no sooner than another, with no fewer rides."""
    keep = []
    for j in js:
        # Within two minutes counts as the same arrival: an extra bus to save
        # seconds of walking is not a different option.
        dominated = any(o is not j and o["leave"] >= j["leave"] - 60 and o["arrive"] <= j["arrive"] + 120
                        and o["rides"] <= j["rides"] and (o["rides"] < j["rides"] or o["leave"] > j["leave"]
                                                           or o["arrive"] < j["arrive"]) for o in js)
        if not dominated:
            keep.append(j)
    return keep


def describe(con, j) -> dict:
    """Names for every id in a journey, for printing."""
    def stop(sid):
        r = con.execute("SELECT name, code FROM stops WHERE id=?", (sid,)).fetchone()
        return f"{r['name']}" + (f" ({r['code']})" if r["code"] else "") if r else sid
    out = []
    for l in j["legs"]:
        l = dict(l)
        if l["mode"] == "ride":
            r = con.execute("SELECT r.short, r.long, r.type, r.feed, t.headsign FROM trips t "
                            "JOIN routes r ON r.id=t.route WHERE t.id=?", (l["trip"],)).fetchone()
            l.update(route=r["short"], route_long=r["long"], feed=r["feed"], headsign=r["headsign"],
                     rail=r["type"] == 1)
        for k in ("from_stop", "to_stop"):
            if k in l and l[k] not in (None,):
                l[k + "_name"] = stop(l[k])
        out.append(l)
    return {**j, "legs": out}


def clock(secs) -> str:
    secs = int(secs) % 86400
    return datetime.time(secs // 3600, (secs % 3600) // 60).strftime("%-I:%M %p")
