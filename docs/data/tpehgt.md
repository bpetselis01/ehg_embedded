# tpehgt: what's in each folder

PhysioNet Term-Preterm EHG DS with TOCO (Jager 2018, `journal.pone.0202125.pdf`). 31 recordings, ~30 min, 20 Hz.
3 EHG channels + 1 TOCO (tocogram: external pressure sensor on belly = mechanical contractions).
Stretch-goal replay source. Download = README step 0. Converted by `tools/convert.py tpehgt`.
Same WFDB `.hea`/`.dat` format as tpehgdb: **field-by-field explanation in [tpehgdb.md](tpehgdb.md)**. Only differences below.

## data/raw/tpehgt/ (downloaded, untouched; recordings sit directly here)

| Item | What |
|---|---|
| `tpehgt_*.hea` + `.dat` (31) | the recordings |
| `tpehgt_*.atr` (31) | manual annotations: contraction / dummy intervals. Unused |
| `RECORDS` | list of record names |
| `journal.pone.0202125.pdf` | the paper |
| `ANNOTATORS` | says what `.atr` is |
| `SHA256SUMS.txt` | file fingerprints |

Record name = `tpehgt_<type><nnn>`:

| Type | Count | Meaning (`RecType` in .hea) |
|---|---|---|
| `n` | 5 | non-pregnant |
| `p` | 13 | pregnant, ended preterm |
| `t` | 13 | pregnant, ended term |

## .hea / .dat differences from tpehgdb (example: tpehgt_n001)

| | tpehgdb | tpehgt |
|---|---|---|
| signals per record | 12 | 8 |
| signal names | `1` `2` `3` + 9 filtered | `EHG1` `EHG2` `EHG3` `TOCO` + 4 filtered (`_Butter-4-bi-0.08-5`) |
| raw columns in .dat | 0, 4, 8 | 0, 2, 4, 6 (raw, filtered alternate) |
| gain | 13107/mV | 13107/mV; `TOCO` can differ (tpehgt_p001: 819/mV) |
| patient notes | pregnancy, bleeding, etc. | `RecType`, `Gestation`, `Rectime`, `Age`, `Parity`, `Abortions`, `Weight`, `Placental_position`, `Smoker`. Non-pregnant: `N/A` / `None` |

tpehgt_n001: 35300 rows x 8 x 2 bytes = 564,800 bytes. Row 0 = 371, 281, 898, 543, -5261, -1311, -4394, -1328 (cols 0-7).
Kept by convert.py: cols 0, 2, 4, 6 = `EHG1`, `EHG2`, `EHG3`, `TOCO` -> `[371, 898, -5261, -4394]`.

## data/converted/tpehgt/

Same layout as tpehgdb (see [tpehgdb.md](tpehgdb.md) for the `.npz` array table).

| Item | What |
|---|---|
| `<record>.npz` x31 | 4 raw channels (`EHG1` `EHG2` `EHG3` `TOCO`). `tpehgt_n001.npz` `samples` = int16 [35300, 4] |
| `manifest.json` | 31 records, `record_id` 0..30 in name order (n, p, t). `record_id` is tied to the name: re-running convert.py keeps it; new records get the next free id. Tracked in git |
