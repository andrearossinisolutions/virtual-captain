import thrust as T

DET = T.DETENTS


def test_decode_lever_detents():
    for i in range(6):
        assert T.decode_lever(i, 10 - i) == i
        assert T.decode_lever(str(float(i)), str(10.0 - i)) == i
    assert T.DETENTS == ["REVERSE", "REVERSE IDLE", "IDLE", "CLIMB", "FLX", "TOGA"]


def test_decode_lever_between_and_unreadable():
    assert T.decode_lever(2.5, 7.5) == -1  # coerente ma non intero
    assert T.decode_lever(2, 7) == -1  # input != 10 - gate
    assert T.decode_lever(3, 8) == -1
    assert T.decode_lever(6, 4) is None  # gate fuori 5..10
    assert T.decode_lever(0, 11) is None
    assert T.decode_lever(0, None) is None
    assert T.decode_lever(None, 10) is None  # input assente
    assert T.decode_lever(0, 0) is None  # LVar assente (0/0): non e' REVERSE
    assert T.decode_lever("x", 10) is None and T.decode_lever(0, "x") is None
    assert T.decode_lever(0, 10) == 0  # REVERSE solo con gate 10
    assert T.decode_lever(2 + 1e-9, 8) == 2  # tolleranza


def test_annotate():
    assert T.annotate(None) == {"thr_det_l": None, "thr_det_r": None}
    assert T.annotate({}) == {"thr_det_l": None, "thr_det_r": None}
    raw = {"thr_in_l": 5, "thr_gate_l": 5, "thr_in_r": 4, "thr_gate_r": 6}
    assert T.annotate(raw) == {"thr_det_l": 5, "thr_det_r": 4}
    raw = {"thr_in_l": 2, "thr_gate_l": 8, "thr_in_r": 2, "thr_gate_r": 9}
    assert T.annotate(raw) == {"thr_det_l": 2, "thr_det_r": -1}


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
    print("ok")
