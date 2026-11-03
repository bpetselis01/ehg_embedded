# deploy/secrets

Only two kinds of file belong in git here: `*.sops.yaml` (encrypted with SOPS + age, readable only with your private key) and `*.example.yaml` (fake values).
Everything else in this folder is gitignored on purpose (`.gitignore`), including a plain `secrets.yaml`.

Steps (the `age` and `sops` programs are installed on your Mac, not by this project):

1. `age-keygen -o ~/ehg-age.key` and put the printed public key (`age1...`) in [`../.sops.yaml`](../.sops.yaml). Back up `~/ehg-age.key` somewhere safe and off this computer: without it the secrets and the encrypted backups cannot be read.
2. Copy [`secrets.example.yaml`](secrets.example.yaml) to `secrets.yaml`, set real random values, then `sops --encrypt secrets.yaml > secrets.sops.yaml` and delete `secrets.yaml`.
3. Copy the private key to the VM once, by hand, to `/opt/ehg/secrets/age.key` (mode 600, owner `ehg`). Then `deploy/update.sh --bootstrap` starts the stack.

Nothing in this repository can decrypt the file without that key. See [`../README.md`](../README.md) and `docs/deployment.md` (Unit 5).
