import re

import numpy as np

from tools.make_replay import render


def test_rendered_header_contains_exact_samples():
    s = np.array([[-32768, 1, 2], [3, -4, 32767]], dtype=np.int16)
    h = render(s, fs=20.0, record_id=7)
    assert "#define REPLAY_N_CH 3" in h and "#define REPLAY_N_FRAMES 2" in h
    assert "#define REPLAY_FS_HZ 20" in h and "#define REPLAY_RECORD_ID 7" in h
    body = h[h.index("{") + 1:h.index("}")]
    vals = [int(v) for v in re.findall(r"-?\d+", body)]
    assert vals == s.flatten().tolist()
