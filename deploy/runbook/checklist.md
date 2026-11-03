# Deployment checklist (Unit 5)

Tick as you go. The explanation of each step is in [`docs/deployment.md`](../../docs/deployment.md); the same numbers are used. Stop at the first thing that does not match and write it in the log ([`log-template.md`](log-template.md)).

## Before anything

- [ ] **Decision: the Oracle card.** You deferred this on 2026-11-03. Decide now; if no, stop here and tell me.
- [ ] 1. `brew install age sops mosquitto`; `terraform version` works; `openssl version` says OpenSSL 3
- [ ] 6. Pushed the branch and an annotated tag; wrote down `git rev-parse TAG` (the full 40 characters)

## Accounts and keys

- [ ] 2. Oracle account created, **Always Free**, home region **Sydney**, not upgraded; tenancy OCID noted
- [ ] 3. API key downloaded to `~/.oci/` (mode 600) and `~/.oci/config` written; nothing from it in the repository
- [ ] 4. DuckDNS name created; token only in the password manager
- [ ] 5. `age-keygen` done; `~/ehg-age.key` **backed up off this computer**; public key in `deploy/.sops.yaml`; `secrets.sops.yaml` made and committed; `secrets.yaml` deleted
- [ ] 6. SSH key and deploy key made; deploy key added on GitHub **read-only**

## Terraform

- [ ] 7. `terraform.tfvars` filled in (`admin_cidr` is your own `/32`, `repo_url` is the SSH form); `terraform init`; `.terraform.lock.hcl` committed
- [ ] 7. `terraform plan -out=ehg.tfplan` shows **14 to add, 0 to change, 0 to destroy**, shape `VM.Standard.A1.Flex`, 2 OCPU, 12 GB, boot 50 GB, block 150 GB
- [ ] 7. `terraform apply ehg.tfplan` succeeded (or: "Out of host capacity", tried `availability_domain_index` 1, 2, later)
- [ ] 7. Confirmed the alarm subscription from the email Oracle sent
- [ ] 8. DuckDNS name points at `terraform output public_ip` (`dig +short NAME`)

## On the VM

- [ ] 9. `/opt/ehg/state/first-boot.status` has no `ERROR` line; `/srv/ehg-data` mounted; `docker --version`; `ufw` active; `ehg-block-imds` active
- [ ] 10. Deploy key placed (mode 600, owner `ehg`); `known_hosts` built and the fingerprint compared with GitHub's documentation; repo cloned at the tag
- [ ] 11. `sops` installed by hand, checksum verified; age private key at `/opt/ehg/secrets/age.key` (mode 600, owner `ehg`)
- [ ] 12. `tools/pki.sh init` run on the Mac; `root.key` moved off this computer; ONLY the seven files of step 12 copied to `/opt/ehg/pki/` (`ca-chain.crt`, `broker.crt`, `broker.key`, `ca.crl` and the bridge's `ehg-bridge.crt` and `.key`, key made readable for UID 10001; both bridge files MUST exist or Docker creates directories in their place); no `intermediate/` or `root/` on the VM
- [ ] 13. `update.sh --bootstrap --services "caddy mosquitto"` finished healthy; `docker stats --no-stream` copied into the log; `curl -sI https://NAME` answers 200 with a valid certificate

## The proof (Checkpoint 3)

- [ ] 14. Mac gateway connected over TLS to the VM; the reader identity received packets and the `online` status
- [ ] 14. Negative checks failed as they should: no certificate; TLS 1.2; ports 1883, 5432, 4222, 3000 closed
- [ ] 15. Log filled in; told me the result

Nothing here needs a cloud credential to be pasted into a chat. If a step asks you to paste a secret anywhere but the places named above, stop.
