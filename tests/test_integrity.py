import numpy as np

from eval.integrity import reconcile

SRC = np.arange(30, dtype=np.int16).reshape(10, 3)  # 10 frames, 3 ch


def frames(idx_list, src=SRC):
    return {i: list(src[i % len(src)]) for i in idx_list}


def test_perfect_delivery():
    r = reconcile(frames(range(10)), SRC, expected_frames=10)
    assert r["delivery_pct"] == 100.0 and r["mismatches"] == [] and r["missing_ranges"] == []


def test_missing_frames_reported_as_ranges():
    got = frames([0, 1, 2, 6, 7, 8, 9])
    r = reconcile(got, SRC, expected_frames=10)
    assert r["delivered"] == 7 and r["missing_ranges"] == [(3, 5)]
    assert r["delivery_pct"] == 70.0


def test_corrupted_value_detected():
    got = frames(range(10))
    got[4] = [12, 13, -1]
    r = reconcile(got, SRC, expected_frames=10)
    assert r["mismatches"] == [4]


def test_frame_idx_beyond_record_wraps_to_source():
    r = reconcile(frames(range(25)), SRC, expected_frames=25)
    assert r["delivery_pct"] == 100.0 and r["mismatches"] == []
