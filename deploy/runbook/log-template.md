# Deployment log template

Copy the table into [`docs/deployment.md`](../../docs/deployment.md) (section "Log") and add a row per thing you did or saw. Be specific: exact messages, timings, region and availability domain used, and anything that differs from the runbook.
Never write a secret, a token, a private key or a password here.

| Date | Step | What you did | What you saw (output, errors, timings) | Fix / note |
|---|---|---|---|---|
| | 2 | Oracle sign-up: home region, account type shown | | |
| | 7 | `terraform plan`: counts; `apply`: availability domain that worked, time taken, any "out of host capacity" | | |
| | 9 | First-boot status lines; minutes from "running" to "first boot finished" | | |
| | 12 | Did the broker read `broker.key` as mode 600? If not, what fixed it | | |
| | 13 | `docker stats --no-stream` of Caddy + Mosquitto (memory and CPU) | | |
| | 14 | Round trip from the Mac: packets received, the first few `t_gw_rx`/`t_mqtt_pub` values, negative checks | | |
| | - | Oracle console labels that differed from the runbook | | |
