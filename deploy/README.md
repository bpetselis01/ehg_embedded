# deploy/: the cloud core as code (Unit 4)

**Status: Written, never applied. Trimmed on 11 Nov 2026:** the custom services (bridge, TimescaleDB writer, triage, enrolment API, Mosquitto exporter) and NATS were removed. **The stack now has no step that moves packets from the broker into the database.** Telegraf (`mqtt_consumer` input, PostgreSQL output) is the standard tool for it and is not configured here. Not part of the measured results (H1 to H4). It uses standard tools throughout; see the [reuse ledger](../docs/licences.md#reuse-ledger).

Everything needed to build the Oracle Always Free VM and put the stack on it. **I wrote this; you run it.** Nothing here has been applied: no Oracle account exists yet,
Docker was not running when it was written, and the cloud steps are yours (`terraform apply`, copying the age key). Decisions: [0010](../docs/decisions/0010-oracle-always-free-cloud-target.md),
[0012](../docs/decisions/0012-timescaledb-nats-data-platform.md), [0016](../docs/decisions/0016-observability-and-slos.md), [0017](../docs/decisions/0017-backup-dr-and-design-only-ha.md).

| Path | What it is |
|---|---|
| [`terraform/`](terraform) | Terraform for the VM: `modules/network` (VCN, subnet, security list: SSH only from you, 80, 443, 8883), `modules/compute` (A1 VM 2 OCPU / 12 GB, 50 GB boot + 150 GB data volume = the 200 GB allowance), `modules/storage` (private backup bucket), `modules/monitoring` ("VM down" alarm and a 1 USD cost guard). [`backend.tf.example`](terraform/backend.tf.example) shows remote state; [`terraform.tfvars.example`](terraform/terraform.tfvars.example) lists what you must set |
| [`cloud-init/user-data.yaml.tftpl`](cloud-init/user-data.yaml.tftpl) | First-boot script: hardened SSH, unattended security updates, firewall, data volume, then Docker, then the repository at a pinned tag |
| [`compose/docker-compose.cloud.yml`](compose/docker-compose.cloud.yml) | The stack as written (15 services, plus step-ca in the optional `enrol` profile): Caddy, Mosquitto (mTLS), PostgreSQL + TimescaleDB with its `migrate` job, Keycloak, HAPI FHIR, the FHIR sync worker (`fhir-sync`; it reads TimescaleDB, which nothing fills until the Telegraf step exists), and the observability stack. `migrate` and `fhir-sync` share one image built on the VM from `backend/platform.Dockerfile`. An earlier 18-service version was started once on a laptop; **this trimmed version has never been started**, only resolved with `docker compose config` |
| [`update.sh`](update.sh) | Idempotent update: refuses a tag that does not point at the commit you name, decrypts secrets into memory, pulls, starts, waits for health, rolls back on failure. `--dry-run` shows every step |
| [`caddy/`](caddy), [`postgres/`](postgres), [`observability/`](observability) | Config files mounted by the compose file |
| [`.sops.yaml`](.sops.yaml), [`secrets/`](secrets) | SOPS + age: only encrypted files are committed. The recipient key in `.sops.yaml` is a **placeholder** until you put your own public key there |

## The order of things (the full runbook is [`docs/deployment.md`](../docs/deployment.md); tick-box version: [`runbook/checklist.md`](runbook/checklist.md))

1. Decide on the Oracle account and the DNS name with me first (the sign-up needs a phone number and a credit card for verification; never upgrade to Pay-As-You-Go).
2. `cd deploy/terraform && cp terraform.tfvars.example terraform.tfvars`, fill it in, `terraform init`, **`terraform plan` and read it**, then `terraform apply`. Commit the `.terraform.lock.hcl` that `init` creates.
3. Point the DNS name at the printed `public_ip`. SSH in as `ubuntu`; read `/opt/ehg/state/first-boot.status`.
4. Put your age **private** key at `/opt/ehg/secrets/age.key` (mode 600, owner `ehg`) and install `sops` by hand with its checksum verified (`deploy/secrets/README.md`). Run `deploy/update.sh --bootstrap`.
5. Later releases: from your Mac, `deploy/update.sh --ssh ubuntu@NAME --tag vX --commit $(git rev-parse vX)`.

## Checked, and not checked

| Checked | How |
|---|---|
| `terraform fmt` clean; `terraform validate` passes against the real Oracle provider 9.11.0 schema | Terraform 1.14.9 here; `EHG_INTEGRATION=1 uv run pytest tests/test_deploy_assets.py` (needs network for `init`) |
| Free-tier numbers (2 OCPU / 12 GB A1, 200 GB volumes, 10 TB egress, 20 GB object storage, idle rule) | Oracle's own "Always Free Resources" page, fetched 2026-11-03 |
| Every image pinned by version and digest has a `linux/arm64` manifest (Keycloak 26.8.1, HAPI v7.6.0 and the Python base image added and checked 2026-11-04) | Registry HTTP API (Docker was not running) |
| Compose resolves; a missing secret stops it with the variable's name; only 80/443/8883 are published; host mounts are limited to five read-only paths (no host root, `/run`, Docker data directory or socket); only Caddy and Mosquitto are on a network with a route out; memory limits plus reserved services fit the 9.5 GB ceiling | `docker compose config` (needs no daemon) and the tests |
| `update.sh` input checks, dry run, ssh form, bootstrap-without-key | Tests |

| NOT checked (needs a real VM, a cloud account or a running Docker) |
|---|
| `terraform plan`/`apply`; the block-volume device path `/dev/oracleoci/oraclevdb`; the "VM down" alarm (needs Oracle's monitoring plugin); A1 capacity in your region; the budget alert rule |
| The metadata lock-down (instance setting plus iptables unit), journald logging, the Alloy journal source and its permission to read the journal, the textfile disk metrics; cAdvisor naming containers by cgroup id only |
| Any container actually starting: the hardening flags (`cap_drop`, `read_only`), the TimescaleDB-HA init script and data path, Loki and Alloy configs, healthcheck commands; the Docker packages from Ubuntu's repository |
| `update.sh` against a real checkout and a real stack (the git, sops, compose and rollback paths); `shellcheck` on any script (not installed here) |
| Real memory use: the limits are the plan's estimates, to be replaced by `docker stats` numbers after the first run |

If something fails on the first boot, the likely suspects are in the second and third rows. Record what you saw in `docs/deployment.md`.
