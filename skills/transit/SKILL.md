---
name: transit
description: Plan and check public transit trips in the DC region for the user: Metrobus, Metrorail and Montgomery County Ride On. Use for any question about getting somewhere by bus or Metro, when to leave, whether a bus is coming or running late, where a bus is right now, which stop to use, and whether a detour, game day or service alert affects a trip. Also for trip timing around events (movies, games, appointments).
---

# Transit

`herald-transit` (on PATH) answers from three places: WMATA's API (key in
secrets, `transit.wmata_api_key`), the schedules of all three agencies in
`ledger/raw/transit/gtfs.db`, and RideOn.app's live data. Run the commands;
do not reason about routes from memory.

```bash
herald-transit plan "Denton Hall" "900 Ellsworth Dr, Silver Spring" --arrive "7:10 PM" --date tomorrow
herald-transit plan "UMD campus" "Rockville station" --depart "5:30 PM"   # today by default
herald-transit arrivals 3004396 27100 E09        # stop codes from signs, or a rail station code
herald-transit arrivals --near "Denton Hall" --radius 300 --route P31
herald-transit stops near "Denton Hall"          # codes, distance, routes served
herald-transit vehicles P31                      # where every P31 bus is now (Metrobus)
herald-transit vehicles --stop 17587             # Ride On buses approaching one stop
herald-transit alerts --route P31 --date tomorrow
herald-transit advisory <url>                    # full text of a wmata.com advisory
herald-transit gtfs status                       # when the schedule was last built
```

Places: `here` (the newest shared location: from Telegram, where the user can share
live, or a location server; the command prints how old the fix is, so say so if it is
stale), a gazetteer name from Herald's known places (`Denton Hall`, `UMD campus`,
`Home`), an address, `lat,lon`, or a stop code. With no recent fix, ask them to share
their location on Telegram. Times are 12-hour. `--json` on any command.

## What each answer rests on, and where it is wrong

- **`plan` is schedule-only.** It is the GTFS timetable, with walking at
  about 1.04 m/s along a straight line. It knows nothing about detours, and it
  prints the advisories and alerts for the routes it chose underneath. Read
  them and decide whether a leg is still valid before recommending it. If a
  detour touches the first leg, re-plan from a stop outside the detour.
- **A game-day detour can skip stops the advisory says are served.** On 26 Sep
  2026 (UCLA game) a P31 drove past Stadium Dr at Denton Hall at about 5:40 PM
  even though the advisory said westbound buses "resume regular route on
  Stadium Dr at Denton Hall". During a SECU Stadium detour window, treat every
  Stadium Dr and Campus Dr stop as unserved, and board on University Blvd west
  of Stadium Dr. `herald-transit vehicles P31` shows where buses really are.
  It happened again on 17 Oct 2026 (Rutgers game, ~12:20 PM): a rider watched a
  westbound P31 stay on University Blvd past Denton. The first westbound stop
  after Denton is University Bl+Temple St (3002158), about a 15 minute walk;
  plan from there, never from Denton, on any game day.
- **Planned detours are on wmata.com days ahead**; `alerts --date` reads them.
  The API's live alerts only carry a detour once it has started.
- **Ride On rows marked SCHEDULED ONLY** are timetable, not a tracked bus. The
  county's own note says one under 15 minutes very likely will not show.
- Metrobus predictions are minutes from now; say the clock time too.
- Metro shows `BRD` (boarding) and `ARR` (arriving) instead of minutes.

## Codes

Metrobus stop codes are 7 digits (`3004396`, Stadium Dr+Denton Hall). Ride On
signs show 5 digits (`27100`). Metro stations use WMATA codes (`E09` College
Park, `B08` Silver Spring); `stops find <name>` resolves the rest.

## Upkeep

The `transit_gtfs` collector rebuilds the schedule weekly. RideOn.app is HTML
parsed by element id, so if Ride On results go empty, run `herald test` (the
fixture test fails on a markup change) and see `lib/transit/live.py`.
