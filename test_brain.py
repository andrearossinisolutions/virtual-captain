from brain import Brain, Briefing


def s(**k):
    d = dict(on_ground=0, vs=0, agl=0, alt=0, gear=1, landing=1, seatbelt=1)
    d.update(k)
    return d


def test_climb_sequence():
    b = Brain(Briefing(ta=5000, accel_agl=1500, mode="silent"))
    assert b.update(s(on_ground=1), 0) == []
    assert b.update(s(vs=1500, agl=50, alt=50), 1) == ["Positive rate, gear up"]
    assert b.update(s(vs=1500, agl=60, alt=60), 2) == []  # una volta sola
    assert b.update(s(vs=2000, agl=1600, alt=1600, gear=0), 20) == ["Acceleration altitude"]
    assert b.update(s(vs=2000, alt=5100, agl=5100, gear=0), 40) == ["Transition altitude, standard"]
    assert "Ten thousand" in b.update(s(alt=10100, agl=10100, gear=0), 60)[0]


def test_gear_repeat_and_rearm():
    b = Brain(Briefing())
    b.update(s(vs=1500, agl=50), 0)
    assert b.update(s(vs=1500, agl=300, gear=1), 11) == ["Gear is still down, gear up"]
    b.update(s(on_ground=1), 100)  # atterraggio: riarmo
    assert b.update(s(vs=1500, agl=50), 120)


def test_readout():
    from brain import readout
    f = dict(origin="LIMC", destination="EDDF", runway="35R", ta="6000", v1="140", vr="141", v2="145")
    p = dict(sid="ROBE1A", star="TOP2B")
    t = readout(f, p, "silent")
    assert t == ("Briefing received. Departure LIMC runway 35R, SID ROBE1A. Transition altitude 6000 feet. "
                 "V1 140, rotate 141, V2 145. Arrival EDDF, STAR TOP2B.")
    assert readout(f, p, "training").endswith("Check the SID in the FMC before the clearance call.")
    assert readout(dict(ta="5000"), {}, "training") == "Briefing received. Transition altitude 5000 feet."


if __name__ == "__main__":
    test_climb_sequence()
    test_gear_repeat_and_rearm()
    test_readout()
    print("ok")
