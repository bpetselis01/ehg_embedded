# tpehgdb: what's in each folder

PhysioNet Term-Preterm EHG DB v1.0.1. 300 recordings, 1 per pregnancy, ~30 min, 3 channels, 20 Hz.
262 term, 38 preterm (<37 wk). Main replay source. Download = README step 0. Converted by `tools/convert.py`.
EHG = electrical signal of the uterus, measured by electrodes on the belly.

## data/raw/tpehgdb/ (downloaded, untouched)

| Item | What |
|---|---|
| `tpehgdb/` | the 300 recordings (`.hea` + `.dat`), below |
| `RECORDS` | list of the 300 record names |
| `tpehgdb.pdf` | PhysioNet's description of the DB |
| `tpehgdb.smr` | group per record (term/preterm, early/later) |
| `*.fvl` (3) | precomputed features per filter. Unused |
| `SHA256SUMS.txt` | file fingerprints, checks download intact |

## data/raw/tpehgdb/tpehgdb/ (the recordings)

2 files per recording, same name. `.hea` = labels (text). `.dat` = numbers (binary). Format name: WFDB.
Examples: `tpehg1007` (preterm), `tpehg572` (term).

### `.hea` (tpehg1007.hea): text, 13 lines of labels + patient notes

Line 1 = whole recording:

| Field | Value | Meaning |
|---|---|---|
| record name | `tpehg1007` | |
| n signals | `12` | 3 raw + 9 filtered |
| sampling rate | `20.000000` | samples per second |
| n samples | `35180` | 35180 / 20 = 1759 s = ~29 min |

Lines 2-13 = 1 per signal. Example, line 2 (signal "1"):

| Field | Value | Meaning |
|---|---|---|
| file | `tpehg1007.dat` | where its numbers are |
| format | `16` | 16-bit whole number |
| gain | `13107/mV` | mV = number / 13107 (range ±2.5 mV) |
| ADC resolution | `16` | bits |
| ADC zero | `0` | stored value = 0 mV |
| first value | `1093` | = .dat row 0 |
| checksum | `-7439` | integrity check |
| block size | `0` | unused |
| name | `1` | signal name |

The 12 signal lines (names):

| Lines | Names | Kind |
|---|---|---|
| 2, 6, 10 | `1`, `2`, `3` | raw. convert.py keeps these |
| other 9 | `1_DOCFILT-4-0.08-4`, `..-0.3-3`, `..-0.3-4` (same for 2, 3) | filtered copies. convert.py drops |

Patient notes (no dates in these files):

| Field | 1007 | 572 | Meaning |
|---|---|---|---|
| `RecID` | 1007 | 572 | record number |
| `Gestation` | 35 | 40 | pregnancy week at birth (35 = preterm) |
| `Rectime` | 31.3 | 32.3 | pregnancy week of this recording |
| `Age` / `Weight` | 30 / 58 | 27 / 59 | mother, years / kg |
| `Parity` / `Abortions` | 0 / 0 | 0 / 0 | earlier births / abortions |
| `Hypertension`, `Diabetes`, `Smoker` | no | no | mother's conditions |
| `Placental_position` | front | end | where placenta sits |
| `Bleeding_first/second_trimester` | no | no | bleeding earlier in pregnancy |
| `Funneling` | negative | negative | cervical funnelling on ultrasound |

`Gestation`/`Rectime` = Birth/Recording columns of Table 1 in `tpehgdb.pdf` (field names not defined there).

### `.dat` (tpehg1007.dat, 844,320 bytes): binary

Numbers only, no labels. Voltage readings from the belly electrodes, stored as 16-bit whole numbers (2 bytes, low byte first).
Divide by 13107 for mV. 1 row per sample instant ("frame"), 12 numbers per row, in `.hea` signal order.
35180 rows x 12 x 2 bytes = 844,320.

Column map, with the first 2 rows of tpehg1007:

| Col | Signal | Kept? | Row 0 | Row 1 | Row 0 in mV |
|---|---|---|---|---|---|
| **0** | **ch 1 raw** | **yes** | **1093** | **1145** | **0.083** |
| 1 | ch 1 filt 0.08–4 Hz | no | 32 | 183 | |
| 2 | ch 1 filt 0.3–3 Hz | no | 10 | 68 | |
| 3 | ch 1 filt 0.3–4 Hz | no | 29 | 167 | |
| **4** | **ch 2 raw** | **yes** | **171** | **180** | **0.013** |
| 5 | ch 2 filt 0.08–4 Hz | no | 10 | 58 | |
| 6 | ch 2 filt 0.3–3 Hz | no | 6 | 42 | |
| 7 | ch 2 filt 0.3–4 Hz | no | 16 | 94 | |
| **8** | **ch 3 raw** | **yes** | **-748** | **-790** | **-0.057** |
| 9 | ch 3 filt 0.08–4 Hz | no | -7 | -42 | |
| 10 | ch 3 filt 0.3–3 Hz | no | -2 | -13 | |
| 11 | ch 3 filt 0.3–4 Hz | no | -7 | -39 | |

Channel = voltage between 2 belly electrodes (E1-E4, Fig. 1 of `tpehgdb.pdf`):

| Channel | Electrodes | Position |
|---|---|---|
| 1 | E2–E1 | top pair |
| 2 | E2–E3 | left pair |
| 3 | E4–E3 | bottom pair |

Other facts:

| | tpehg1007 | tpehg572 |
|---|---|---|
| rows | 35180 | 35100 |
| bytes | 844,320 | 842,400 |
| time between rows | 50 ms | 50 ms |

How the signals were filtered:

| Stage | Where | What | Source |
|---|---|---|---|
| before sampling | hardware (analog circuit in the recorder) | 3-pole Butterworth, 0–5 Hz, on every signal incl. the "raw" ones | `tpehgdb.pdf` (Fele-Žorž 2008) |
| filtered copies (cols 1-3, 5-7, 9-11) | software, after recording | 4-pole digital Butterworth, "double-pass" (forward + backward = no phase shift), 3 bands | [PhysioNet page](https://physionet.org/content/tpehgdb/1.0.1/) |
| caveat | | first + last 180 s of filtered copies = filter start-up artefacts, ignore | PhysioNet page |

`DOCFILT-4-0.08-4` = 4-pole, 0.08–4 Hz. tpehgt's `Butter-4-bi-0.08-5` = 4-pole, bidirectional, 0.08–5 Hz.

## data/converted/tpehgdb/ (from tools/convert.py)

| Item | What |
|---|---|
| `<record>.npz` x300 | 1 recording, 3 raw channels. `.npz` = NumPy zip of named arrays, uncompressed |
| `manifest.json` | all records, `record_id` 0,1,2.. in name order. `record_id` rides in every packet to trace back to the file. `record_id` is tied to the name: re-running convert.py keeps it; new records get the next free id. Tracked in git (data/converted/*/manifest.json; .npz files are not) |

`tpehg1007.npz` (212 KB = 35180 x 3 x 2 bytes + labels):

| Array | Value | Meaning |
|---|---|---|
| `samples` | int16 [35180, 3], row 0 = 1093 171 -748 | = `.dat` cols 0,4,8. Unchanged |
| `fs` | 20.0 | samples/s |
| `channels` | "1" "2" "3" | signal names |
| `gain` | 13107 x3 | units per mV |
| `baseline` | 0 x3 | stored value = 0 mV |
| `units` | "mV" x3 | physical unit |

mV = (samples − baseline) / gain.

| Field | Why it matters |
|---|---|
| `gain` | stored numbers are ADC counts, not volts. Gain = counts per mV. Differs per DB: 1000 counts = 0.076 mV here (gain 13107) but 7.6 mV in ehgdb (gain 131.068). Without it, numbers across DBs aren't comparable |
| `baseline` | count that means 0 mV (offset). 0 for every TPEHG signal; other recorders can use non-zero |
| why convert to mV | classifier filters/thresholds, plots and papers all work in mV; also lets different DBs be compared |
| why *not* converted here | mV = decimals; decimals don't round-trip exactly. Raw ints stay end to end for the bit-exact check (H2/H4). Conversion only happens downstream (classifier, plots) |

Why `.npz` (inferred from code): numbers + labels in 1 file, 1-line NumPy load, same int type, later steps all Python.
