# icehg: what's in each folder

Induced / Cesarean EHG DS (ICEHG DS), PhysioNet. 126 recordings, ~30 min, 3 channels, 20 Hz, Ljubljana (same hospital as tpehgdb).
Pregnancies expected to end in spontaneous term delivery, but ended induced or cesarean.
Each recorded early (~23 wk) or later (~31 wk). Download = `data/raw/icehg-ds/` (README step 0). Converted by `tools/convert.py icehg`.
Same WFDB `.hea`/`.dat` format as tpehgdb: **field-by-field explanation in [tpehgdb.md](tpehgdb.md)**. Only differences below.

## data/raw/icehg-ds/ (downloaded, untouched)

| Item | What |
|---|---|
| `early_*/`, `later_*/` (6 folders) | the recordings, by recording time x outcome (below). Each: `.hea` + `.dat` + `_fltrd.jpg` plot |
| `icehgdsmat/` | same 126 recordings as MATLAB: `<name>m.hea` + `.mat`, in the same 6 sub-folders. **No `.dat` here** |
| `RECORDS`, `RECORDS_*` | record lists (all / cesarean / induced / induced-cesarean) |
| `README`, `LICENSE.txt` | description, license |
| `Table_1.txt` .. `Table_3.txt` | per-pregnancy table: age, weights, record IDs, gestation, newborn weight |
| `Figure_1.jpg`, `Figure_2.jpg` | figures |
| `SHA256SUMS.txt` | file fingerprints |

| Folder | Records |
|---|---|
| `early_induced` / `later_induced` | 38 / 43 |
| `early_cesarean` / `later_cesarean` | 11 / 8 |
| `early_induced-cesarean` / `later_induced-cesarean` | 13 / 13 |

## .hea / .dat differences from tpehgdb (example: early_cesarean/icehg1089)

| | tpehgdb | icehg |
|---|---|---|
| signals per record | 12 | 6 |
| signal names | `1` `2` `3` + 3 filters each | `S1` `S2` `S3` + 1 filtered copy each (`_DOCFILT-4-0.08-5`) |
| raw columns in .dat | 0, 4, 8 | 0, 2, 4 |
| patient notes | `Gestation`, `Rectime`, ... | `RecID`, `RecType` (Cesarean/Induced), `Gestation`, `Rectime`, `Age`, `Weight`, `Placental_position`, `Height`, `Newborn_weight`, `Pair_RecID` (the early/later partner record) |

icehg1089: 35280 rows x 6 x 2 bytes = 423,360 bytes. Row 0 = 406, 3, 165, 1, -526, -4 (cols 0-5).
Kept by convert.py: cols 0, 2, 4 -> `[406, 165, -526]`.

## data/converted/icehg/

Same layout as tpehgdb (see [tpehgdb.md](tpehgdb.md) for the `.npz` array table).

| Item | What |
|---|---|
| `<record>.npz` x126 | 3 raw channels (`S1` `S2` `S3`) |
| `manifest.json` | 126 records, `record_id` 0..125 in name order. `record_id` is tied to the name: re-running convert.py keeps it; new records get the next free id. Tracked in git |

`icehgdsmat/` is skipped by `convert.py` (same 126 recordings as MATLAB copies; checked identical).
Before 2026-10-16 it was converted too, giving 252 records with each recording twice.
