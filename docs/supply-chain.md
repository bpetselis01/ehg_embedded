# Supply chain and CI

What guards the code and the images, what is only planned, and how to run the heavier scanners yourself. Written 2026-11-03 (Unit 14). Nothing in `.github/` has run on GitHub yet: the repository has never been pushed.

## In place (checked by tests that run in CI)

| Control | Where | Checked by |
|---|---|---|
| Every image in the compose files is pinned by version AND digest, and every digest was checked for `linux/arm64` | `deploy/compose/docker-compose.cloud.yml`, `backend/docker-compose.platform.yml` | `tests/test_deploy_assets.py`, `tests/test_platform_compose.py` |
| Dockerfile base images carry an explicit tag (not `latest`); digests for them are NOT pinned | `backend/ingest/Dockerfile`, `backend/platform.Dockerfile` | `tests/test_ci_config.py` (tag only) |
| Python dependencies pinned in `uv.lock`, installed with `--frozen` | `pyproject.toml`, `uv.lock` | CI (`uv sync --frozen`) |
| No commit carries an AI co-author or generated-with line | `.github/workflows/tests.yml` | CI |
| Secret-looking text, state files, keys and plans cannot be committed | `.gitignore`, deployment tests | `tests/test_deploy_assets.py` |
| Shell scripts are linted | `.github/workflows/security.yml` | CI (first run pending) |
| Weekly update PRs for packages, actions, Dockerfile bases, the Terraform provider | `.github/dependabot.yml` | `tests/test_ci_config.py` (structure) |
| Workflows use Linux runners only, read-only token permissions, no `pull_request_target` | `.github/workflows/` | `tests/test_ci_config.py` |

## Planned, not wired (versions or availability could not be verified)

Syft (SBOM), Trivy (image, filesystem and IaC scans), cosign (signing of images built for arm64), gitleaks (secret scan), CodeQL (not available: the repository is private). Run them on your Mac when you want a report; none of these commands has been run here:

```sh
syft dir:. -o spdx-json > sbom.spdx.json          # SBOM of the repository's dependencies
trivy fs --scanners vuln,secret,misconfig .       # vulnerabilities, secrets, IaC misconfigurations
trivy image <image@digest>                         # one image from the compose files
gitleaks detect --source . --redact                # secrets in the working tree and history
```

## Vulnerability handling (IEC 81001-5-1 style, "designed with reference to")

A finding from any scan, review or dependency update is recorded in `docs/progress.md` with its decision (fixed, accepted with a reason, or not applicable) and, if it affects security, in `docs/security.md`. Findings of the automated commit reviews during Units 2 to 11 were handled this way. Images are re-pinned by hand: look up the new digest, check `linux/arm64`, change tag and digest together, run the tests.

## First scan results (2026-11-04, run by hand with Docker; not in CI)
| Scanner | Scope | Result |
|---|---|---|
| shellcheck (`-S warning`) | all 12 shell scripts | 3 warnings, all real (an inner single quote in `init-offline.sh`; `build.sh` built its options as one string): fixed, clean |
| hadolint | `backend/platform.Dockerfile` | clean |
| gitleaks (working tree and all git history) | whole repository | 3 hits, all deliberate: the documented dev-mode tokens and a fake key that tests the repository's own scanner. Allowlisted by exact pattern in `.gitleaks.toml`; a real key anywhere else still fails |
| trivy config | Dockerfiles and Terraform | `backend/ingest/Dockerfile` ran as root (DS-0002): now `USER 10001` |
| trivy image | `ehg-platform:cloud` | operating system 0; Python: msgpack, urllib3 and a setuptools copy VENDORED INSIDE pip carried HIGH CVEs: `pip` is now uninstalled from the final image (nothing needs it at run time): 0 fixable HIGH/CRITICAL |
| trivy fs | `uv.lock` (development environment) | aiohttp, certifi and pillow had HIGH/CRITICAL fixes: upgraded (`uv lock --upgrade-package ...`), suite re-run |
Not scanned: the third-party images in the compose files (Keycloak, HAPI, Grafana, ... are pinned by digest; scan them when choosing versions), the Terraform provider, the Swift package.
Re-run: `docker run --rm -v "$PWD:/mnt" -w /mnt koalaman/shellcheck:stable -S warning $(git ls-files '*.sh')`; `docker run --rm -i hadolint/hadolint < backend/platform.Dockerfile`; `docker run --rm -v "$PWD:/repo" ghcr.io/gitleaks/gitleaks:latest git /repo --config /repo/.gitleaks.toml`; `trivy image ehg-platform:cloud --ignore-unfixed --severity HIGH,CRITICAL`.

