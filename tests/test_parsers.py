"""RideOn.app is parsed from HTML. These fail if the county changes its markup."""
import pathlib, sys, unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "lib"))

from transit import live, plan


class Parsers(unittest.TestCase):


    def test_rideon_stop_fixture(self):
        page = (HERE / "fixtures" / "rideon_busstop.html").read_text()
        res = live.parse_rideon_stop(page)
        assert res["stop"].startswith("FRIENDSHIP HEIGHTS STATION")
        p = res["predictions"][0]
        assert p["route"] == "1" and p["toward"] == "Silver Spring"
        assert p["minutes"] == "11" and p["live"] is True and p["vehicle"] == "4021"


    def test_rideon_delays_parse(self):
        page = ('<span id="MainContent_rptDetours_lblColTitle_0">1-EAST:&nbsp;&nbsp; 1-Silver Spring</span>'
                '<div><strong>Start Date:</strong>  10/1/2026\n</div><div><strong>End Date:</strong> 1/1/2027</div>'
                '<div><strong>Description: </strong>  Purple Line detour</div>'
                '<div class="col-12">Stop: 21908-EAST WEST HWY & CAREY LN</div>')
        d = live.parse_rideon_delays(page)[0]
        assert d["from"] == "10/1/2026" and d["description"] == "Purple Line detour"
        assert d["stops"] == ["21908-EAST WEST HWY & CAREY LN"]


    def test_prune_drops_extra_ride_for_same_arrival(self):
        a = {"leave": 100, "arrive": 1000, "rides": 2}
        b = {"leave": 100, "arrive": 997, "rides": 3}
        assert plan._prune([a, b]) == [a]


    def test_clock_is_12_hour(self):
        assert plan.clock(18 * 3600 + 5 * 60) == "6:05 PM"


if __name__ == "__main__":
    unittest.main()
