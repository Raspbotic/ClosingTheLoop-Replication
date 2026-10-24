"""L2SYS-REQ-02.04 ground-proximity condition. [a: Behaviour B15]"""
from flightsim.pcs import gpws


def test_condition():
    assert gpws.warning(300.0, -40.0, False, False)        # 7.5 s to impact < 10 s
    assert not gpws.warning(500.0, -40.0, False, False)    # 12.5 s
    assert not gpws.warning(100.0, 5.0, False, False)      # climbing
    assert gpws.warning(10.0, -1.0, False, False)          # below floor, descending
    assert not gpws.warning(1.0, -2.0, True, True)          # inhibited: on ground, brake set
