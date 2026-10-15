"""Checks tools/make_replay.py writes every sample (including the int16 extremes) into
the generated firmware header unchanged."""

import re

import numpy as np
import pytest

from tools.make_replay import render


def test_rendered_header_contains_exact_samples():
    s = np.array([[-32768, 1, 2], [3, -4, 32767]], dtype=np.int16)
    h = render(s, fs=20.0, record_id=7)
    assert "#define REPLAY_N_CH 3" in h and "#define REPLAY_N_FRAMES 2" in h
    assert "#define REPLAY_FS_HZ 20" in h and "#define REPLAY_RECORD_ID 7" in h
    body = h[h.index("{") + 1:h.index("}")]
    vals = [int(v) for v in re.findall(r"-?\d+", body)]
    assert vals == s.flatten().tolist()


def test_24bit_header_uses_int32_and_flags_width():
    s = np.array([[-(2**23), 2**23 - 1]], dtype=np.int32)
    h = render(s, fs=200.0, record_id=1, bits=24)
    assert "#define REPLAY_SAMPLE_BITS 24" in h and "static const int32_t replay_samples" in h
    body = h[h.index("{") + 1:h.index("}")]
    assert [int(v) for v in re.findall(r"-?\d+", body)] == s.flatten().tolist()


def test_16bit_is_default_and_refuses_values_that_would_wrap():
    assert "#define REPLAY_SAMPLE_BITS 16" in render(np.zeros((1, 1), np.int16), 20.0, 0)
    with pytest.raises(ValueError):
        render(np.array([[40000]], dtype=np.int32), 20.0, 0)
