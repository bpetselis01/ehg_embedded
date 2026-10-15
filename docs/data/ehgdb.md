# ehgdb: what's in each folder

Icelandic 16-electrode EHG DB (Alexandersson 2015, doi 10.1038/sdata.2015.17). Stretch-goal replay source.
123 recordings, 16 channels at 200 Hz (10x tpehgdb rate). 4x4 electrode grid on belly.
Pregnancy (`p`, ~61 min) and labour (`l`, ~36 min) recordings. Download = README step 0. Converted by `tools/convert.py ehgdb`.
Same WFDB `.hea`/`.dat` format as tpehgdb: **field-by-field explanation in [tpehgdb.md](tpehgdb.md)**. Only differences below.

## data/raw/ehgdb/ (downloaded, untouched; recordings sit directly here)

| Item | What |
|---|---|
| `ice###_<p or l>_<n>of<m>.hea` + `.dat` (123) | the recordings |
| `*.atr` (112) | manually annotated events. Unused |
| `*.jpg` | scanned tocograph for each recording (1 small square = 30 s) |
| `info.txt` | table: 1 row per record (age, BMI, gestation, delivery mode, duration, ...) |
| `README.txt` | database description |
| `Figure1.jpg`, `Figure2.jpg` | electrode numbering, electrode positions |
| `icelandic16ehgmat.zip` | same data as MATLAB files. Unused |
| `RECORDS`, `ANNOTATORS`, `SHA256SUMS.txt` | record list, `.atr` description, fingerprints |

Record name `ice001_l_1of1`: participant `ice001` | type `l` labour / `p` pregnancy | recording 1 of 1 for that participant.

## .hea / .dat differences from tpehgdb (example: ice001_l_1of1)

| | tpehgdb | ehgdb |
|---|---|---|
| signals per record | 12 (3 + filtered) | 16, no filtered copies |
| signal names | `1` `2` `3` | `EHG1`..`EHG16` = 1 electrode each, **listed alphabetically**: EHG1, EHG10, EHG11 ... EHG16, EHG2 ... EHG9 |
| sampling rate | 20 Hz | 200 Hz |
| gain | 13107/mV | 131.068/mV (15-bit ADC) |
| channel meaning | voltage between 2 electrodes | voltage of 1 electrode ("monopolar") vs a reference |
| patient notes | `#Comments:` list | `#Info:` list: `Age`, `BMI`, `Gravidity`, `Parity`, `Gestational age at recording/delivery` (weeks/days), `Mode of delivery`, ... |

ice001_l_1of1: 100000 rows x 16 x 2 bytes = 3,200,000 bytes (100000 / 200 = 500 s = 8 min 20 s). First row all 0.
convert.py keeps all 16 and **reorders to numeric EHG1..EHG16**, so `.npz` column 0 = `.dat` column 0 (`EHG1`),
`.npz` column 1 = `.dat` column 8 (`EHG2`), etc.

## data/converted/ehgdb/

Same layout as tpehgdb (see [tpehgdb.md](tpehgdb.md) for the `.npz` array table).

| Item | What |
|---|---|
| `<record>.npz` x123 | 16 raw channels, int16 [n_frames, 16], `fs` = 200.0, gain 131.068 |
| `manifest.json` | 123 records, `record_id` 0..122 in name order. `record_id` is tied to the name: re-running convert.py keeps it; new records get the next free id. Tracked in git |

Note: `README.txt` says 122 recordings; `RECORDS` and the files = 123.
