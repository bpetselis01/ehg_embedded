# Deployment runbook: the cloud core on Oracle Always Free

**You run every step; I never touch the cloud or ask for credentials.** This is written from the code in [`deploy/`](../deploy/README.md) and from facts checked on 2026-11-03.
Nothing in it has been run against a real Oracle account, so expect to correct it: the log at the end is where you note what you actually saw, and the checklist ([`deploy/runbook/checklist.md`](../deploy/runbook/checklist.md)) is the tick-box version.

**Purpose of the unit:** a reachable VM, with the Mac gateway publishing over TLS 1.3 and a client certificate **from the internet** (Checkpoint 3). Since 11 Nov 2026 the stack has no step from the broker to the database: Telegraf is the standard tool for it and is not configured here.

## Decisions already made (2026-11-03)

| Question | Answer | Consequence |
|---|---|---|
| Hostname | **DuckDNS + Caddy** (HTTP-01 on port 80). duckdns.org loaded fine on 2026-11-03; its free-subdomain limit and update URL were **not** confirmed from the page | A name such as `yourname.duckdns.org` points at the VM's IP; Caddy and the broker certificate use it |
| Home region | **Sydney, `ap-sydney-1`** (permanent once chosen). A1 capacity there is unverified | Latency to UNSW is lowest; if "out of host capacity" blocks you, see step 7 |
| Repository | **Private** | The VM clones over SSH with a read-only **deploy key** (step 10). You must push the branch and a tag yourself: nothing was ever pushed from this machine |
| Oracle card | **PENDING.** You deferred this question ("skip this step first"), so I have not assumed consent | Do not start step 2 until you have decided. Oracle needs a phone number and a credit card for verification (prepaid and virtual cards were reported rejected, unverified). Never upgrade to Pay-As-You-Go; the Terraform adds a 1 USD budget alert that emails you if anything is ever billed |

Not decided here: whether the soak and H3 are done (you skipped them), and how the VM gets updates beyond [`deploy/update.sh`](../deploy/update.sh).

## What the plan will create (so you can check `terraform plan`)

14 resources to add and nothing to change or destroy: 1 VCN, 1 internet gateway, 1 route table, 1 security list, 1 subnet; 1 instance (A1, 2 OCPU, 12 GB, 50 GB boot), 1 block volume (150 GB), 1 volume attachment; 1 bucket (`ehg-backups`);
1 notification topic, 1 email subscription, 1 alarm (`ehg-vm-down`), 1 budget (1 USD a month) and 1 budget alert rule. Anything else, or any other shape, is a mistake: stop.

## Steps

Menu names in the Oracle console are from memory and **unverified**; Oracle changes them. If something differs, trust the console and note it in the log.

### 1. Tools on your Mac

Terraform is already installed (1.14.9). `brew install age sops` for the secrets (neither was installed when this was written). You also need `ssh`, `scp`, `openssl` 3 (see [troubleshooting](troubleshooting.md)) and `mosquitto_sub` (`brew install mosquitto`, for step 14).

### 2. Oracle account (only after you have decided about the card)

Go to Oracle Cloud's Free Tier sign-up. Choose **Sydney (`ap-sydney-1`)** as the home region: it **cannot be changed**. Verify the phone and card. Choose the **Always Free** account type and **do not upgrade** when invited.
When you are in the console, look at your Free Tier / account status to confirm it says Always Free, and note your **tenancy OCID** (profile menu, Tenancy) and **home region**.

### 3. API key for Terraform

Console, your profile, **API keys**, add one and let Oracle generate the pair. Download the **private** key to `~/.oci/oci_api_key.pem` (`chmod 600`), and copy the config snippet Oracle shows into `~/.oci/config`:

```ini
[DEFAULT]
user=ocid1.user.oc1..REPLACE
fingerprint=REPLACE
tenancy=ocid1.tenancy.oc1..REPLACE
region=ap-sydney-1
key_file=/Users/YOU/.oci/oci_api_key.pem
```

Nothing from `~/.oci/` ever goes in this repository (Terraform reads it directly).

### 4. A DuckDNS name

Sign in at duckdns.org (it offered Google, GitHub, Twitter, Persona and Reddit on 2026-11-03), create a name, and keep the **token** in your password manager only. Use the page's own IP box to set the address in step 8, so the token never sits in a shell history or a file.

### 5. age key and the encrypted secrets

```sh
age-keygen -o ~/ehg-age.key && chmod 600 ~/ehg-age.key      # prints "Public key: age1..."; back this file up OFF this computer
```

Put the public key in [`deploy/.sops.yaml`](../deploy/.sops.yaml), then follow [`deploy/secrets/README.md`](../deploy/secrets/README.md) to make `deploy/secrets/secrets.sops.yaml` (random values: `openssl rand -hex 24`: hex, not base64, because the passwords go into URLs and connection strings). The list now also has `KEYCLOAK_ADMIN_PASSWORD` and the client secret `FHIR_SYNC_CLIENT_SECRET` (see `deploy/secrets/secrets.example.yaml`).
Commit the **encrypted** file and `.sops.yaml` only. The encrypt command passes the recipient with `--age`; it is untested because sops was not installed here.

### 6. SSH keys

```sh
ssh-keygen -t ed25519 -f ~/.ssh/ehg_vm -C ehg-vm              # to log in to the VM
ssh-keygen -t ed25519 -f ~/.ssh/ehg_deploy -N "" -C ehg-vm-deploy   # lets the VM read the private repository
```

On GitHub: the repository, Settings, **Deploy keys**, add `~/.ssh/ehg_deploy.pub`, and leave **"Allow write access" unticked**.
Then push what the VM should run (nothing has been pushed from the working machine): the branch you want and an annotated tag, e.g. `git push origin feat/enterprise-topology && git tag -a v0.1.0 -m "first cloud deploy" && git push origin v0.1.0`.
Write down the tag's full hash: `git rev-parse v0.1.0`.

### 7. Terraform: variables, plan, apply

```sh
cd deploy/terraform
cp terraform.tfvars.example terraform.tfvars        # then edit every REPLACE value; admin_cidr is YOUR public IP as a.b.c.d/32
terraform init                                      # creates .terraform.lock.hcl: commit it
terraform plan -out=ehg.tfplan                      # READ it against "What the plan will create" above
terraform apply ehg.tfplan
```

Set `repo_url` to the SSH form (`git@github.com:OWNER/REPO.git`), `repo_tag` to the tag you pushed, `domain_name` to your DuckDNS name. If `terraform apply` fails with **"Out of host capacity"** for the A1 shape, set `availability_domain_index = 1` (then 2) and apply again, or retry later; this is common.
Do not change the shape or sizes: they are the whole free allowance. Oracle emails you a link to confirm the alarm subscription: click it, or "VM down" will never reach you.
If you ever want everything gone: `terraform destroy` (read what it lists first). Remote state in Object Storage is optional ([`backend.tf.example`](../deploy/terraform/backend.tf.example), untested).

### 8. Point the name at the VM

`terraform output public_ip`, then set that address for your name on the DuckDNS page. Check from the Mac: `dig +short yourname.duckdns.org` shows the same address (DNS can take a few minutes). The address is ephemeral: it changes if the VM is ever recreated.

### 9. First login and the first-boot check

```sh
ssh -i ~/.ssh/ehg_vm ubuntu@$(terraform output -raw public_ip)
sudo cat /opt/ehg/state/first-boot.status    # expect: data volume mounted, docker installed, containers cannot reach the metadata service, firewall on, first boot finished
df -h /srv/ehg-data; docker --version; sudo ufw status; systemctl is-active ehg-block-imds
```

First boot takes a few minutes after the VM shows as running. **A "git clone failed" NOTE is expected** for a private repository until step 10. Any line starting with `ERROR` is a real problem: the full output is in `/var/log/ehg-first-boot.log`; note it in the log below.
If the data volume never appeared, Docker was deliberately not installed (it would fill the 50 GB boot disk): check the volume attachment in the console before going on.

### 10. Deploy key, host keys and the repository on the VM

```sh
# from the Mac
scp -i ~/.ssh/ehg_vm ~/.ssh/ehg_deploy ubuntu@IP:/tmp/deploy_key
# on the VM
sudo install -o ehg -g ehg -m 600 /tmp/deploy_key /opt/ehg/secrets/deploy_key && shred -u /tmp/deploy_key
curl -s https://api.github.com/meta | jq -r '.ssh_keys[] | "github.com " + .' | sudo tee /opt/ehg/etc/known_hosts >/dev/null
ssh-keygen -lf /opt/ehg/etc/known_hosts       # compare with GitHub's published fingerprints; on 2026-11-03 the ED25519 one was SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU
sudo chown ehg:ehg /opt/ehg/etc/known_hosts
sudo -H -u ehg git clone --depth 1 --branch v0.1.0 git@github.com:OWNER/REPO.git /opt/ehg/repo
```

The git settings that make `ehg` use that key with strict host-key checking were written by cloud-init (`/opt/ehg/.gitconfig`). Check the fingerprints against GitHub's own documentation page, not only against what the VM fetched.

### 11. `sops` and the age key on the VM

cloud-init does not install `sops` (no Ubuntu package). On the VM, download the **linux arm64** binary and its checksums file from the sops releases page on GitHub (I did not open it; check that the project and release names are what you expect), verify with `sha256sum -c`, then `sudo install -m 755 sops /usr/local/bin/sops`.
Then copy the age **private** key, once:

```sh
scp -i ~/.ssh/ehg_vm ~/ehg-age.key ubuntu@IP:/tmp/age.key
sudo install -o ehg -g ehg -m 600 /tmp/age.key /opt/ehg/secrets/age.key && shred -u /tmp/age.key
```

### 12. Certificates for the broker

On the Mac (the signing keys never go to the VM):

```sh
tools/pki.sh init --host yourname.duckdns.org --out ~/ehg-pki
```

Move `~/ehg-pki/root/root.key` to offline storage now. Copy **only** these four files to the VM, into `/opt/ehg/pki/` (create it owned by `ehg`, mode 700): `ca-chain.crt`, `broker.crt`, `broker.key` and `ca.crl`. Never copy `intermediate/` or `root/`.
The containers run as fixed non-root users (Mosquitto uid/gid 1883, the Python services uid 10001, step-ca uid 1000), and on Linux a bind-mounted key keeps its host owner and mode, so a key that is 600 and owned by `ehg` is UNREADABLE to them (this never shows on a Mac). After copying the certificates, and whenever you replace one, run `sudo deploy/fix-key-permissions.sh`: it makes `broker.key` group-readable for gid 1883, hands the step-ca folder to uid 1000, and prints one line per file (**untested on a VM**; the logic is tested with recorders in `tests/test_fix_key_permissions.py`).
No job refreshes the revocation list since 11 Nov 2026: after a revocation, copy the new `ca.crl` to `/opt/ehg/pki/` by hand and send Mosquitto SIGHUP.
Your gateway certificate stays on the Mac: `~/ehg-pki/clients/ehg-gw-01.{crt,key}` (valid 30 days; renewal is manual until Unit 7).

### 13. Start the edge subset (Caddy and Mosquitto only)

The compose file also holds `migrate`, `fhir-sync`, Keycloak and HAPI. **They have never been started anywhere**, so this runbook still brings up only the edge first; start the rest only after step 14 works, one service at a time
(`update.sh --services postgres`, then `migrate`, and last `keycloak hapi`, which together need about 3.7 GB), and expect first-start fixes. Optional: the `enrol` profile (step-ca only: run `deploy/step-ca/init-offline.sh ~/ehg-ca` on your computer, copy `~/ehg-ca/vm` to `/opt/ehg/step-ca`, run `sudo deploy/fix-key-permissions.sh`, append `~/ehg-ca/env.txt` to `/opt/ehg/etc/site.env`, keep `~/ehg-ca/offline` OFFLINE; certificates are then issued with the `step` CLI, and the broker must trust `intermediate_ca.crt` + `root_ca.crt`).

```sh
sudo -H -u ehg /opt/ehg/repo/deploy/update.sh --bootstrap --services "caddy mosquitto"
cd /opt/ehg/repo/deploy/compose && sudo docker compose --env-file /run/ehg/runtime.env --env-file /opt/ehg/etc/site.env -f docker-compose.cloud.yml ps
sudo docker stats --no-stream            # write the numbers in the log
```

From the Mac: `curl -sI https://yourname.duckdns.org` should answer 200 with a valid certificate (the first request can take a few seconds while Caddy obtains it). If Caddy cannot get a certificate, check that port 80 is reachable and the name points at the VM.

### 14. Prove it from the internet (the point of this unit)

On the Mac, at home on your normal connection:

```sh
uv run python -m gateway.gateway --simulate 1 --mqtt yourname.duckdns.org --mqtt-tls \
    --ca ~/ehg-pki/ca-chain.crt --cert ~/ehg-pki/clients/ehg-gw-01.crt --key ~/ehg-pki/clients/ehg-gw-01.key --gateway-id ehg-gw-01
# in a second terminal: read it back with the reader identity
mosquitto_sub -h yourname.duckdns.org -p 8883 --cafile ~/ehg-pki/ca-chain.crt --cert ~/ehg-pki/clients/ehg-viewer.crt --key ~/ehg-pki/clients/ehg-viewer.key -t 'ehg/#' -v -C 5
```

Expect `[mqtt] connected over TLS` on the gateway and JSON lines (and `online` on `ehg/_gw/ehg-gw-01/status`) on the reader. **Negative checks, all should fail:**
`mosquitto_sub` without a certificate; `openssl s_client -connect yourname.duckdns.org:8883 -tls1_2 -CAfile ~/ehg-pki/ca-chain.crt`; and `nc -vz -w 5 IP 1883`, `5432`, `4222`, `3000` (closed or filtered).
Also run this unit's local equivalent once to compare: `EHG_INTEGRATION=1 uv run pytest tests/test_mqtt_tls_config.py -q`.

### 15. Record what you saw, then stop

Fill in the log below and the checklist. **Checkpoint 3** is: you have a reachable VM and the Mac gateway publishes over mTLS from the internet. Tell me, with the log, and I will carry on to the data platform (Unit 6) only after that.

## What I could not check (so look here first when something fails)

The Oracle console labels; sign-up behaviour and capacity in Sydney; whether prepaid cards are rejected; the DuckDNS update flow and limits; the block-volume device path; the Docker packages from Ubuntu's repository; the monitoring alarm (needs Oracle's monitoring plugin on the VM); the metadata lock-down;
every container starting (see [`deploy/README.md`](../deploy/README.md) for the list); key-file readability inside the Mosquitto container; the `sops` commands; `update.sh` on a real checkout.

## Log (fill in; newest facts matter most)

Copy the table from [`deploy/runbook/log-template.md`](../deploy/runbook/log-template.md) here as you go. Nothing is recorded yet: no step of this runbook has been run.

| Date | Step | What you did | What you saw (output, errors, timings) | Fix / note |
|---|---|---|---|---|
| | | | | |
