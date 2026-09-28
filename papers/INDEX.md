# Reference index — EHG BLE→MQTT→InfluxDB telemetry thesis

Source: interim report reference list [1]–[76]. Numbers [33], [39], [71], [73] do not exist in the report's list.
Local = file in this folder. "OA-browser" = legally open access at the publisher, but the site blocks curl; open the link in a browser.
"paywalled" = get via UNSW library.

## Datasets

| Ref | Short title | Year | Rel | Informs | Take-away number / pattern | Local |
|---|---|---|---|---|---|---|
| 30 | TPEHGT DS (PhysioNet) + Jager PLoS One descriptor | 2018 | High | datasets, firmware injection, H4 | 31 records (13 preterm + 13 term + 5 non-pregnant), 20 Hz, 8 signals (EHG1-3, TOCO, each raw plus Butter-4-bi-0.08-5 filtered), 35300 samples (~29.4 min), WFDB 16-bit | ref30_TPEHGT_Jager2018_PLoSOne.pdf |
| 31 | ICEHG DS: induced/caesarean EHG, Sci Data | 2023 | High | datasets, H4 | 126 × 30-min records, 20 Hz, 3 ch + DOCFILT-4-0.08-5 filtered copies (6 signals), 35280 samples, **same WFDB layout as TPEHG, so the same injection path works** | ref31_ICEHG_Jager2023.pdf |
| 7 | Icelandic 16-electrode EHGDB, Sci Data | 2015 | High | datasets (stretch 16 ch 200 Hz) | 122 recordings (4×4 grid), 45 women, TOCO + event annotations | ref07_IcelandicEHGDB_Alexandersson2015.pdf |
| 6 | Fele-Zorz: TPEHG origin (linear/non-linear separation) | 2008 | Med | datasets, classifier hook | Original TPEHG feature set (RMS, median freq, peak freq, sample entropy) | paywalled: https://doi.org/10.1007/s11517-008-0350-y |

### Dataset locations (s3://physionet-open, `--no-sign-request`)

| Dataset | S3 path | Size | Web / DOI |
|---|---|---|---|
| TPEHGT 1.0.0 | `s3://physionet-open/tpehgt/1.0.0/` (+ `tpehgt/tpehgt-1.0.0.zip`) | 98 objects, 83.5 MB total; zip 41.8 MB (includes the 23.9 MB PLoS One PDF) | https://physionet.org/content/tpehgt/1.0.0/ (already in `data/raw/tpehgt`) |
| ICEHG DS 1.0.1 (latest) | `s3://physionet-open/icehg-ds/1.0.1/` (+ `icehg-ds/icehg-ds-1.0.1.zip`) | 646 objects, 121.2 MB unpacked; zip 93.3 MB. Subdirs: `early_/later_` × `induced`, `cesarean`, `induced-cesarean`, plus `icehgdsmat/` (MATLAB copies) | https://physionet.org/content/icehg-ds/1.0.1/, DOI 10.13026/vrvd-mf28 (1.0.0 is also on S3, same zip size) |
| TPEHGDB 1.0.1 | `s3://physionet-open/tpehgdb/1.0.1/` | zip 257.5 MB | already in `data/raw/tpehgdb` |
| Icelandic EHGDB 1.0.0 | `s3://physionet-open/ehgdb/1.0.0/` | zip 2.13 GB | already in `data/raw/ehgdb` |

## Firmware / BLE link

| Ref | Short title | Year | Rel | Informs | Take-away number / pattern | Local |
|---|---|---|---|---|---|---|
| 15 | Reevaluating BLE for ingestibles (nRF54L15) | 2026 | High | BLE, H1, energy | BLE is lower-power than Sub-GHz below 100 kbps; CI fixed at 7.5 ms; vitals tolerate ≤250 ms, surgery ≤10 ms; link-layer retransmission gives in-order delivery; PPK-style current measurement method | ref15_BLE-Ingestibles_Zhou2026.pdf |
| 16 | Mitigating BLE packet loss in wearables | 2021 | High | BLE, gateway, H2 | Bundling + lower notify rate gives <1% loss (e.g. 38 pkt/s → 4 pkts bundled into 120 B at 9.5 pkt/s); a queue plus host re-request removes the remaining loss. **This is the model for the seq-number re-request layer** | ref16_BLEPacketLoss_Tipparaju2021.pdf |
| 10 | BioGAP-Ultra | 2025 | High | firmware/BLE, energy | nRF5340 BLE 5.4, measured 1.4 Mbit/s; 9.3 to 32.8 mW streaming; open NCS firmware (see extras) | ref10_BioGAP-Ultra_Frey2025.pdf |
| 9 | BioGAP | 2023 | Med | firmware/BLE, energy | 3.6 µJ/sample streaming vs 2.2 µJ/sample on-board; 97% BW reduction from on-device FFT | ref09_BioGAP_Frey2023.pdf |
| 19 | τ-Ring open smart-ring platform | 2025 | Med | firmware | nRF52840 (64 MHz M4F, 1 MB flash, 256 KB RAM); real-time streaming plus ≥8 h offline logging | ref19_TauRing_Tang2025.pdf |
| 20 | Serrani: validating wireless engines (nRF52840 dongle + RPi3) | 2024 | High | BLE, gateway, H3 | nRF52840 nodes to a Raspberry Pi 3 gateway; dynamic CI adaptation; throughput 30.5% worse beyond 8 m, so fix node-gateway distance in the test protocol | ref20_WirelessEngines_Serrani2024.pdf |
| 48 | BLE time sync / data alignment (SDA, LIDA) | 2023 | Med | firmware timestamps, H1 | App-layer sync error: 384 ± 387 µs (SDA), 190 ± 205 µs (LIDA). Per-node timestamps are enough for independent patients | ref48_BLETimeSync_Wang2023.pdf |
| 60 | Sensor-in-the-Loop data injection timing/jitter | 2021 | High | firmware injection, H1, H4 | Injects prerecorded data into real hardware at 1.6 kHz with ≥3× lower jitter than real sensors; uses Gage R&R. **Template for the digital-injection method** | ref60_TimingJitter_Gis2021.pdf |
| 45 | Punch Through: Maximizing BLE throughput pt 4 | 2025 | High | BLE | 2M PHY + DLE (251 B) + MTU 247 + multiple notifications per CI | web: https://punchthrough.com/ble-throughput-part-4/ |
| 46 | Engenius: BLE 5.0 throughput testing | 2026 | Med | BLE | Practical 2M PHY throughput ceiling | web: https://www.engeniustech.com/bluetooth-low-energy-5-0-throughput-testing/ |
| 47 | Nordic S140 SoftDevice spec | — | Low-Med | BLE | **nRF5 SDK SoftDevice, not used on Zephyr/NCS.** Cite the NCS "SoftDevice Controller" docs instead | web: https://www.nordicsemi.com/Products/Development-software/S140 |
| 68 | Nordic nRF Connect SDK vs nRF5 SDK statement | — | Med | firmware | nRF5 SDK is in maintenance mode, so use NCS/Zephyr | web: https://devzone.nordicsemi.com/nordic/nordic-blog/b/blog/posts/nrf-connect-sdk-and-nrf5-sdk-statement |
| 49 | Silicon Labs BLE current consumption | — | Med | energy (stretch) | Longer connection interval gives large average current cut (report: ~93% from 7.5 ms to 1 s) | web: https://docs.silabs.com/bluetooth/latest/bluetooth-fundamentals-system-performance/current-consumption |
| 50 | Lossless EMG (uterine) transmission, EUMTS | 2017 | Med | firmware payload | Lossless CR 3.61 vs LZW 1.87 / Huffman 1.90 on PhysioNet uterine EMG; 1470.6 ms wake-up saved (simulated) | ref50_LosslessEMG_Cho2017.pdf |
| 44 | AI smart sportswear | 2025 | Low | firmware (contrast only) | ESP32 + **Wi-Fi**, not BLE; 92.1% classifier accuracy | ref44_SmartSportswear_Tang2025.pdf |

## Gateway (Bleak central) and transport (MQTT)

| Ref | Short title | Year | Rel | Informs | Take-away number / pattern | Local |
|---|---|---|---|---|---|---|
| 14 | RHMS: MQTT remote health monitoring | 2025 | High | MQTT, backend, H1, H3 | Median 480 ms, P95 590 ms end-to-end (abstract says "P95 <600 ms"); 500 × 1 Hz streams, peak 545 streams/s; Mosquitto 2.0.18 + paho 2.1.0; **QoS1 vitals / QoS0 UI pings**; topic `rhms/<patientId>/vitals/<signal>`; keep-alive 30 s, session-expiry 60 s, no retained vitals; Locust load test; latency CDF figure | ref14_RHMS_Khan2025.pdf |
| 52 | MQTT vital-sign monitoring (SHMS) | 2022 | Med | MQTT | Broker-based pub/sub middleware; <1% error ratio over 1000 tests, 6-month run | ref52_MQTTVitals_Tsao2022.pdf |
| 18 | Stress-testing MQTT brokers | 2021 | Med | MQTT, H3 | Mosquitto best on most metrics (single-threaded); ActiveMQ scales best. Test used 3 publishing threads + 15 subscribers, measuring CPU, latency and msg rate | ref18_MQTTBrokerStress_Mishra2021.pdf |
| 17 | AES-GCM E2E ESP32→MQTT→RPi | 2026 | Med | MQTT, security, H2 | 99.72% decrypt-and-store over 1 h (718/720); ESP32 prep 41.8 ms; payload-level AES-GCM so the broker never sees plaintext; InfluxDB only "optional" | ref17_AESGCM-MQTT_Amirkhanova2026.pdf |
| 53 | HTTP/3 vs MQTT vs CoAP | 2024 | Med | transport choice | Protocol comparison for the MQTT justification | OA-browser (gold): https://dl.acm.org/doi/pdf/10.1145/3716554.3716830 |
| 51 | Smart-ring ward latency and continuity | 2025 | Med | gateway, H1, H2 | Phone BLE→Wi-Fi bridge, 128 patients; **latency in minutes** (mean 7.23 min BP, 4.35 min SpO2); SAR ≈90% (≥80% threshold); burst-cached uploads excluded | ref51_SmartRingLatency_Lim2025.pdf |
| 54 | Wearable IoT aldehyde sensor | 2018 | Low | gateway | BLE → phone gateway → cloud; 7-day battery | ref54_AldehydeIoT_Li2018.pdf |

## Backend: storage, cloud, visualisation

| Ref | Short title | Year | Rel | Informs | Take-away number / pattern | Local |
|---|---|---|---|---|---|---|
| 55 | Benchmarking TSDBs for high-frequency data | 2023 | Med | InfluxDB choice | Own result: InfluxDB ingest 9.6× slower than kdb+ (TimescaleDB also faster). The "3.5× / 3.4–71×" figures are quoted from **Timescale's vendor benchmark** [their ref 24], not measured here | ref55_HFDatabases_Barez2023.pdf |
| 69 | InfluxDB 3 Core/Enterprise GA | 2025 | High | InfluxDB version | 3.x GA April 2025. Pin one major version (report: use the earlier stable one) | web: https://www.influxdata.com/blog/influxdata-announces-influxdb-3-OSS-GA/ |
| 70 | Grafana Live setup | — | High | Grafana, viewer | WebSocket push. Justifies the split: Grafana for slow metrics, a custom WS viewer for raw waveform | web: https://grafana.com/docs/grafana/latest/setup-grafana/set-up-grafana-live |
| 56 | Simons Observatory DAQ/monitoring | 2020 | Med | backend, viewer | InfluxDB + Grafana for slow housekeeping (Hz-level); high-rate streams kept out of Grafana (200 Hz UDP sidecar); docker. **Slow/fast split pattern** | ref56_SimonsObsDAQ_Koopman2020.pdf |
| 8 | ICU Cockpit multimodal waveform platform | 2022 | Med-High | backend, viewer | Separates waveform streaming from the analytics/DB path | OA-browser (green AAM): https://www.zora.uzh.ch/id/eprint/218912/1/Boss_JAMIA_submission_v7_accepted.pdf |
| 57 | RPM on commercial clouds (Azure/AWS) | 2024 | Med | deployment | Containerised microservices on HIPAA-eligible cloud; k8s | ref57_RPMCloud_Cao2024.pdf |
| 58 | ScalableDigitalHealth (SDH) | 2024 | Med | deployment, H3 | Latency-aware horizontal pod autoscaling (e.g. scale out when response time >50 ms) | ref58_SDH_Alasmary2024.pdf |
| 40 | IoT + wearables healthcare monitoring review | 2025 | Med | architecture background | Survey of layered IoT health architectures | OA-browser (bronze): https://dl.acm.org/doi/pdf/10.1145/3731595 |

## EHG processing and server-side classifier hook (H4)

| Ref | Short title | Year | Rel | Informs | Take-away number / pattern | Local |
|---|---|---|---|---|---|---|
| 28 | Spectrotemporal EHG+TOCO features (TPEHGT) | 2025 | Med-High | classifier hook, H4 | MFCC + wavelet stats + normalized-PSD peaks; KLT denoise; CatBoost 97.28% acc / AUC 0.9988 on TPEHGT segments | ref28_SpectrotemporalEHG_Jayakody2025.pdf |
| 27 | Slow/fast-wave EHG processing | 2025 | Med | classifier hook | Slow vs fast wave relationship improves contraction detection and noise resilience | ref27_SlowFastWaveEHG_Gao2025.pdf |
| 29 | Advanced bioelectrical processing pt III (incl. EHG) | 2021 | Med | filters, H4 | Review of EHG filtering and feature methods | ref29_BiosignalProcIII_Martinek2021.pdf |
| 24 | PregCare EHG recording system | 2019 | Med | related system | 8 EHG + TOCO over Bluetooth to a PC; UC features RMS, PF, MDF, SampEn; UC PSD dominant at 0–0.7 Hz | ref24_EHGRecordingSystem_Hao2019.pdf |
| 26 | EHG in modern obstetrics review | 2026 | Med | classifier hook background | ML/processing review | OA-browser (hybrid): https://doi.org/10.1016/j.artmed.2025.103303 |
| 32 | FetalCare home telemonitoring (fECG + EHG) | 2024 | High | closest prior system, gap statement | Reports power/battery life but not latency distribution, loss under load or multi-node scaling (the gap H1–H3 fill) | paywalled: https://doi.org/10.1109/JSEN.2024.3374332 |
| 38 | Soft wireless pregnancy sensor network (Ryu, PNAS) | 2021 | Low-Med | related system | Time-synchronized multi-sensor wireless network, phone/tablet host | ref38_SoftSensorNetwork_Ryu2021.pdf |
| 59 | SPIRIT on-chip seizure prediction | 2024 | Low | on-device ML contrast | 17.2 µW classifier | ref59_SPIRIT_Pandey2024.pdf |

## Standards and interfaces

| Ref | Short title | Year | Rel | Informs | Take-away | Local |
|---|---|---|---|---|---|---|
| 64 | HL7 FHIR R5 | — | High | data export format | `Observation.valueSampledData` (origin, period, factor, dimensions, data) maps 1:1 onto WFDB gain/baseline + 20 Hz blocks: https://hl7.org/fhir/R5/datatypes.html#SampledData | web: https://hl7.org/fhir/ |
| 65 | ISO/IEEE 11073-10206:2024 (ACOM) | 2024 | Med | data model | Abstract content model for PHD observations (sample arrays, timestamps) | paywalled (free iTeh preview): https://cdn.standards.iteh.ai/samples/87828/a1d37fc36d12474db540b6de10ce59da/ISO-IEEE-11073-10206-2024.pdf |
| 42 | 11073 PHD SpO2/ECG agents over BT HDP | 2022 | Med | standards | Continua-conformant agent/manager implementation. HDP is Classic BT (deprecated), so use for the data model only | ref42_11073PHD_CristobalHuerta2022.pdf |
| 41 | Continua personal health ecosystem | 2007 | Low-Med | standards | Background for 11073 PHD | paywalled: https://doi.org/10.1109/MPRV.2007.72 |
| 61 | IEC 62304 | 2015 | Low-Med | SW process (docs) | Software safety class / lifecycle framing | paywalled (iTeh preview in report) |
| 62 | IEC 60601-1-11 | 2015 | Low | home-use framing | — | iTeh sample (link in report) |
| 63 | ISO 14971 | 2019 | Low-Med | risk register | — | paywalled (report link is an unofficial mirror, so avoid citing it) |
| 66, 67 | Privacy Act 1988; NHMRC National Statement | — | Low | ethics | Public data only, so no HREC needed | web |

## Extra open resources (not in the reference list)

| Resource | Informs | Take-away | Link |
|---|---|---|---|
| pulp-bio/BioGAP repo | firmware/BLE | **Now contains NCS/Zephyr firmware for the nRF5340 (`Firmware/src_NRF`, Apache-2.0, since Jul 2025)**. `prj.conf` uses `BT_USER_PHY_UPDATE`, `BT_USER_DATA_LEN_UPDATE`, conn interval 6 (7.5 ms), latency 5, timeout 400, NUS. Direct reference for 2M PHY/DLE Kconfig | https://github.com/pulp-bio/BioGAP |
| pulp-bio/biogui | viewer | PySide6 acquisition/plot GUI (Apache-2.0); `interface_*.py` board plugins; TCP/Unix-socket forwarding module | https://github.com/pulp-bio/biogui |
| Nordic DevAcademy: BLE Fundamentals | firmware/BLE | Lessons on conn params, 2M PHY, DLE/MTU, notifications (NCS) | https://academy.nordicsemi.com/courses/bluetooth-low-energy-fundamentals/ |
| Nordic DevAcademy: nRF Connect SDK Fundamentals | firmware | Zephyr threads, Kconfig/devicetree, logging | https://academy.nordicsemi.com/courses/nrf-connect-sdk-fundamentals/ |
| NCS throughput sample | BLE, H1 | Reference 2M PHY/DLE throughput benchmark on nRF52840 DK | https://docs.nordicsemi.com/bundle/ncs-latest/page/nrf/samples/bluetooth/throughput/README.html |
| SenithJayakody/tpehgt-preterm-emd | classifier hook | MIT, Python, TPEHGT, uses dataset-filtered 0.08–5 Hz channels, record-wise CV. **Easiest drop-in feature/classifier hook** (same first author as [28]) | https://github.com/SenithJayakody/tpehgt-preterm-emd |
| GillesVandewiele/EHG-Oversampling | classifier hook | `ehgfeatures` Python package (TPEHG feature engineering); no license file, so cite and don't vendor | https://github.com/GillesVandewiele/EHG-Oversampling |
| predict-idlab/TPEHGDB-Experiments | classifier eval | Shows oversampling-before-split leakage on TPEHG. Use when stating H4 label-agreement caveats | https://github.com/predict-idlab/TPEHGDB-Experiments |
| uri-goldsztejn/Predicting_preterm_birth_from_EHG | classifier (alt) | GPL-3.0 MATLAB, deep learning on TPEHG | https://github.com/uri-goldsztejn/Predicting_preterm_birth_from_EHG |

## Low relevance: clinical or market background only (not downloaded)

| Ref | Short title | Year | Why low |
|---|---|---|---|
| 1, 2, 11, 12, 13 | WHO / Lancet / AIHW / NT cohort / GBD preterm-birth epidemiology | 2023–24 | Motivation only |
| 3, 4 | Euliano; Vlemminx: EHG vs TOCO vs IUPC | 2013, 2017 | Clinical validation of EHG |
| 5 | Wearable ultrasound fetal patch (Nat Biotech) | 2026 | Different modality |
| 21, 22 | Frenken: EHG PhD / epidural pilot | 2024–25 | Clinical |
| 23 | Continuous CTG analysis | 2016 | Clinical |
| 25 | Electrode interface patent | — | Hardware AFE, out of scope (digital injection) |
| 34, 35, 36, 37 | Novii+ PR, FDA K231964, Nuvo INVU (×2) | 2020–23 | Commercial landscape |
| 43 | Novel Bits: BLE SoC comparison | 2026 | SoC choice already fixed |
| 72 | "BioGAP GitHub repository": **URL actually points to pulp-bio/VowelNet** | — | Fix the citation; see extras above |
| 74, 75, 76 | FMBV power-Doppler (Rubin; Welsh ×2) | 1997–2019 | Ultrasound, previous topic |
