# transit

DC-region transit for Herald: Metrobus and Metrorail through WMATA's developer
API and GTFS, Montgomery County Ride On through its GTFS and the live data
behind RideOn.app, and a Connection Scan trip planner over all three.

Ask Herald how to get somewhere, when to leave, or whether your bus is coming,
and the `transit` skill answers from live predictions, vehicle positions,
alerts and the published schedules. Trips are planned between places Herald
already knows (your gazetteer), addresses, coordinates or stop codes.

## Install

Send your Herald this repository's URL and ask it to install it, or run:

```bash
herald ext install https://github.com/benmross/herald-transit
~/herald/venv/bin/pip install gtfs-realtime-bindings
herald secret request transit.wmata_api_key --why "WMATA arrivals"
herald ext enable transit
herald-transit gtfs update          # builds the schedule, about a minute
```

The WMATA key is free: register at developer.wmata.com and copy the
default-tier primary key. `herald secret request` sends you a one-time page
to paste it into, so it never passes through chat.

## Use

`herald-transit plan | arrivals | stops | vehicles | alerts | advisory | gtfs`,
with `--json` on any of them. `skills/transit/SKILL.md` says what each answer
rests on and where it is wrong, including the SECU Stadium game-day
detours that skip stops the advisory says are served.

## Notes

- Schedules live in `ledger/raw/transit/gtfs.db`, rebuilt weekly by the
  `transit_gtfs` collector.
- RideOn.app endpoints were found by recording the site's own network
  traffic on 16 Oct 2026; `lib/transit/live.py` documents them. They are
  public and unauthenticated. If the county ships a real GTFS-realtime feed,
  this should switch to it.
