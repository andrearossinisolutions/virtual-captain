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
    assert t == ("Briefing received. Departure Lima India Mike Charlie runway three five Right, SID ROBE 1 Alpha. "
                 "Transition altitude 6000 feet. V1 140, rotation 141, V2 145. "
                 "Arrival Echo Delta Delta Foxtrot, STAR TOP 2 Bravo.")
    assert readout(f, p, "training").endswith("Check the SID in the FMC before the clearance call.")
    assert readout(dict(ta="5000"), {}, "training") == "Briefing received. Transition altitude 5000 feet."


def test_phonetic():
    from brain import spell_icao, spell_proc
    assert spell_icao("EDDF") == "Echo Delta Delta Foxtrot"
    assert spell_proc("EMPA5B") == "EMPA 5 Bravo"
    assert spell_proc("ROBE1A") == "ROBE 1 Alpha"
    assert spell_proc("BAMES1") == "BAMES 1"       # senza lettera finale
    from brain import spell_runway
    assert spell_runway("35R") == "three five Right"
    assert spell_runway("09L") == "zero nine Left"
    assert spell_runway("27") == "two seven"
    assert spell_runway("18C") == "one eight Center"
    assert spell_runway("??") == "??"
    assert spell_proc("N/A") == "N/A"              # formato inatteso: invariato


def test_takeoff_callouts():
    b = Brain(Briefing(v1=140, vr=141, mode="silent"))
    g = lambda ias: s(on_ground=1, ias=ias)
    assert b.update(g(0), 0) == []
    assert b.update(g(100), 1) == []
    assert b.update(g(140), 2) == ["V1"]
    assert b.update(g(141), 3) == ["Rotate"]
    assert b.update(g(150), 4) == []                      # una volta sola
    assert b.update(s(vs=1500, agl=50, ias=155), 5) == ["Positive rate, gear up"]
    # atterraggio: rullaggio veloce a terra, nessun V1/Rotate
    assert b.update(s(on_ground=1, ias=145), 100) == []
    assert b.update(s(on_ground=1, ias=100), 101) == []
    assert b.update(s(on_ground=1, ias=20), 110) == []    # fermo: si riarma
    assert b.update(g(145), 120) == ["V1", "Rotate"]      # nuovo decollo


def test_rejected_takeoff_rearms():
    b = Brain(Briefing(v1=140, vr=141, mode="silent"))
    b.update(s(on_ground=1, ias=0), 0)
    assert b.update(s(on_ground=1, ias=145), 1) == ["V1", "Rotate"]
    b.update(s(on_ground=1, ias=10), 2)                   # decollo abortito, fermo
    assert b.update(s(on_ground=1, ias=145), 3) == ["V1", "Rotate"]


def test_no_speeds_no_callouts():
    b = Brain(Briefing(mode="silent"))
    b.update(s(on_ground=1, ias=0), 0)
    assert b.update(s(on_ground=1, ias=150), 1) == []


if __name__ == "__main__":
    test_climb_sequence()
    test_gear_repeat_and_rearm()
    test_readout()
    test_phonetic()
    test_takeoff_callouts()
    test_rejected_takeoff_rearms()
    test_no_speeds_no_callouts()
    print("ok")
