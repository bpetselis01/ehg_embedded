# 0001. Raw ADC integers end to end

**Date:** 2026-09-29 (commit `cd4d06b`, converter)

## Status

Accepted

## Context

H4 asks whether the stored samples are identical to the source recording, and H2 counts losses frame by frame. Any
scaling, filtering or float conversion on the way would make "identical" a tolerance question instead of an exact one.
Zicheng's classifier filters its own input (0.08–4 Hz band-pass), so the pipeline does not need to.

## Decision

Samples travel as the source's raw integer ADC units (int16 by default, 24-bit as an option via flags bit1) from
[`tools/convert.py`](../../tools/convert.py) through the firmware, the BLE packet, the MQTT JSON (JSON integers are
exact) and InfluxDB. Conversion to millivolts, (value − baseline) ÷ gain, happens only on the way out
([`tools/fhir_export.py`](../../tools/fhir_export.py)) or in a consumer. `convert.py` refuses values that would wrap.

## Consequences

- [`eval/integrity.py`](../../eval/integrity.py) checks H4 by exact comparison with the `.npz` file: `bit-exact mismatches: 0`.
- Dashboards show ADC units, not mV (labelled as such).
- 24-bit sources cost 3 bytes per sample instead of 2 (fewer frames per packet).

**Sources:** [design spec](../design/codebase-design.md) assumption A4; [interface-spec.md](../interface-spec.md) §3; commit `0d0ae1d` (24-bit option).
