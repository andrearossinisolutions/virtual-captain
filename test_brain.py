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


if __name__ == "__main__":
    test_climb_sequence()
    test_gear_repeat_and_rearm()
    print("ok")
