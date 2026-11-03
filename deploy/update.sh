#!/bin/sh
# Update the EHG cloud stack to a tagged release, check it came up healthy, and roll back by itself if it did not.
#   deploy/update.sh --tag v0.2.0 --commit 0123abc...full-40-char-sha     on the VM: move to that release and (re)start the stack
#   deploy/update.sh --ssh ubuntu@ehg.example.org --tag v0.2.0 --commit SHA   from your Mac: runs the line above on the VM over SSH
#   deploy/update.sh --bootstrap                                           first boot / after a reboot: start the stack as it is, IF the age key is in place
#   options: --dry-run (print every step, change nothing)  --verify-signature (also require a valid git tag signature)  --timeout SECONDS (default 300)
#
# PURPOSE: One safe, repeatable way to change what runs on the VM. Running it twice with the same arguments changes nothing the second time.
# INPUT: a tag and the FULL commit hash you expect it to point to (you read the hash on your Mac: git rev-parse TAG). If the tag has moved, nothing is changed.
#   Files on the VM: /opt/ehg/secrets/age.key (copied once by hand, never in git), deploy/secrets/secrets.sops.yaml (encrypted, in git), /opt/ehg/etc/site.env.
#   e.g. deploy/update.sh --ssh ubuntu@ehg-demo.duckdns.org --tag v0.2.0 --commit 6f1c2a9d0b3e4f5a6b7c8d9e0f1a2b3c4d5e6f70
# OUTPUT: exit 0 = every service running (and healthy where it has a health check). Exit 1 = it failed and the previous release was put back (or says why it could not).
#   The previous commit is kept in /opt/ehg/state/previous. Decrypted secrets exist only in /run/ehg/runtime.env (memory, mode 600), never on disk.
# HOW: take a lock; fetch the tag; refuse if the tag does not point at --commit; check out that commit; decrypt the secrets with sops + age; docker compose pull;
#   docker compose up -d; wait until the services are healthy; on timeout or error check out the previous commit and bring it up again.
#   `sops` is NOT installed by cloud-init (no distribution package): install it by hand and check its checksum (the deployment runbook, Unit 5). This script says so if it is missing.
# STATUS: UNVERIFIED on a real VM. --dry-run and the argument checks are tested (tests/test_deploy_assets.py).
# ROLE IN THESIS: The update path of the cloud core (docs/decisions/0017-backup-dr-and-design-only-ha.md); never needs cloud credentials.
# BEFORE -> AFTER: Before: the VM runs release A. After: it runs release B and is healthy, or it runs A again and you are told why B failed.
set -eu

EHG_HOME="${EHG_HOME:-/opt/ehg}"
REPO="$EHG_HOME/repo"
STATE="$EHG_HOME/state"
COMPOSE_FILE="deploy/compose/docker-compose.cloud.yml"
RUNTIME_ENV="${EHG_RUNTIME_ENV:-/run/ehg/runtime.env}"
TAG="" COMMIT="" SSH_TARGET="" BOOTSTRAP=0 DRY=0 VERIFY_SIG=0 TIMEOUT=300

die() { echo "update.sh: $*" >&2; exit 1; }
say() { echo "update.sh: $*"; }
usage() { sed -n '2,/^set -/p' "$0" | sed '$d;s/^# \{0,1\}//'; }
# run CMD...: do it, or just print it in --dry-run
run() { if [ "$DRY" = 1 ]; then echo "DRY-RUN: $*"; else "$@"; fi; }

while [ $# -gt 0 ]; do
    case "$1" in
        --tag) [ $# -ge 2 ] || die "--tag needs a value"; TAG=$2; shift 2 ;;
        --commit) [ $# -ge 2 ] || die "--commit needs a value"; COMMIT=$2; shift 2 ;;
        --ssh) [ $# -ge 2 ] || die "--ssh needs user@host"; SSH_TARGET=$2; shift 2 ;;
        --timeout) [ $# -ge 2 ] || die "--timeout needs seconds"; TIMEOUT=$2; shift 2 ;;
        --bootstrap) BOOTSTRAP=1; shift ;;
        --dry-run) DRY=1; shift ;;
        --verify-signature) VERIFY_SIG=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown option $1 (see --help)" ;;
    esac
done
case "$TIMEOUT" in ''|*[!0-9]*) die "--timeout must be a whole number of seconds" ;; esac
if [ "$BOOTSTRAP" = 0 ]; then
    [ -n "$TAG" ] && [ -n "$COMMIT" ] || die "give --tag TAG and --commit FULL_SHA (or --bootstrap)"
    echo "$COMMIT" | grep -Eq '^[0-9a-f]{40}$' || die "--commit must be the full 40-character hash (git rev-parse $TAG), not an abbreviation"
    echo "$TAG" | grep -Eq '^[A-Za-z0-9._-]+$' || die "--tag may only contain letters, digits, '.', '_' and '-'"
fi

# from your Mac: run the same thing on the VM as the service user
if [ -n "$SSH_TARGET" ]; then
    remote="sudo -u ehg $REPO/deploy/update.sh --timeout $TIMEOUT"
    [ "$BOOTSTRAP" = 1 ] && remote="$remote --bootstrap" || remote="$remote --tag $TAG --commit $COMMIT"
    [ "$VERIFY_SIG" = 1 ] && remote="$remote --verify-signature"
    [ "$DRY" = 1 ] && remote="$remote --dry-run"
    say "running on $SSH_TARGET: $remote"
    if [ "$DRY" = 1 ]; then echo "DRY-RUN: ssh $SSH_TARGET $remote"; exit 0; fi
    exec ssh "$SSH_TARGET" "$remote"
fi

compose() {
    run docker compose --env-file "$RUNTIME_ENV" --env-file "$EHG_HOME/etc/site.env" -f "$REPO/$COMPOSE_FILE" "$@"
}

decrypt_secrets() {
    if [ ! -f "$EHG_HOME/secrets/age.key" ]; then
        if [ "$DRY" = 1 ]; then echo "DRY-RUN: note: $EHG_HOME/secrets/age.key is missing, so a real run would stop at this point"
        else say "waiting: $EHG_HOME/secrets/age.key is not there yet. Copy it by hand (the deployment runbook, Unit 5), then run: deploy/update.sh --bootstrap"; return 2; fi
    fi
    command -v sops >/dev/null 2>&1 || [ "$DRY" = 1 ] || die "sops is not installed on this VM; install it by hand and verify its checksum (the deployment runbook, Unit 5)"
    run mkdir -p "$(dirname "$RUNTIME_ENV")"
    if [ "$DRY" = 1 ]; then echo "DRY-RUN: SOPS_AGE_KEY_FILE=$EHG_HOME/secrets/age.key sops --decrypt --output-type dotenv $REPO/deploy/secrets/secrets.sops.yaml > $RUNTIME_ENV (mode 600)"; return 0; fi
    ( umask 077; SOPS_AGE_KEY_FILE="$EHG_HOME/secrets/age.key" sops --decrypt --output-type dotenv "$REPO/deploy/secrets/secrets.sops.yaml" > "$RUNTIME_ENV" ) \
        || die "could not decrypt deploy/secrets/secrets.sops.yaml (wrong age key, or the file is not encrypted for it)"
}

# wait until every service is running, and healthy where it has a health check
wait_healthy() {
    [ "$DRY" = 1 ] && { echo "DRY-RUN: wait up to ${TIMEOUT}s for all services to be running/healthy"; return 0; }
    waited=0
    while [ "$waited" -lt "$TIMEOUT" ]; do
        bad=$(docker compose --env-file "$RUNTIME_ENV" --env-file "$EHG_HOME/etc/site.env" -f "$REPO/$COMPOSE_FILE" ps --all --format json \
              | jq -r 'select((.State != "running") or ((.Health // "") != "" and .Health != "healthy")) | .Service' | sort -u | tr '\n' ' ')
        [ -z "$bad" ] && { say "all services are up"; return 0; }
        sleep 5; waited=$((waited + 5))
    done
    say "NOT healthy after ${TIMEOUT}s: $bad"
    return 1
}

deploy_current() {
    decrypt_secrets || return $?
    compose pull
    compose up -d --remove-orphans
    wait_healthy
}

# one update at a time
run mkdir -p "$STATE"
if [ "$DRY" = 0 ]; then
    mkdir "$STATE/update.lock" 2>/dev/null || die "another update is running (or crashed: remove $STATE/update.lock if you are sure it is not)"
    trap 'rmdir "$STATE/update.lock" 2>/dev/null || true' EXIT INT TERM
fi

if [ "$BOOTSTRAP" = 1 ]; then
    say "bootstrap: starting the stack as checked out"
    rc=0; deploy_current || rc=$?
    [ "$rc" = 2 ] && exit 0   # waiting for the age key is not an error; the message above says what to do
    exit "$rc"
fi

if [ "$DRY" = 1 ]; then
    echo "DRY-RUN: cd $REPO && git fetch --depth 1 origin tag $TAG"
    echo "DRY-RUN: refuse unless git rev-parse $TAG^{commit} = $COMMIT"
    [ "$VERIFY_SIG" = 1 ] && echo "DRY-RUN: git verify-tag $TAG"
    echo "DRY-RUN: remember the current commit in $STATE/previous, then git checkout --detach $COMMIT"
else
    cd "$REPO"
    git fetch --depth 1 origin tag "$TAG" || die "could not fetch tag $TAG"
    actual=$(git rev-parse "$TAG^{commit}")
    [ "$actual" = "$COMMIT" ] || die "tag $TAG points at $actual, not at $COMMIT: refusing (the tag moved, or the hash is wrong)"
    [ "$VERIFY_SIG" = 0 ] || git verify-tag "$TAG" || die "signature check failed for tag $TAG"
    previous=$(git rev-parse HEAD)
    echo "$previous" > "$STATE/previous"
    if [ "$previous" = "$COMMIT" ]; then say "already at $COMMIT; re-applying (this changes nothing if it is healthy)"; fi
    git checkout --detach "$COMMIT"
fi

rc=0; deploy_current || rc=$?
if [ "$rc" = 0 ]; then if [ "$DRY" = 1 ]; then echo "DRY-RUN: finished, nothing was changed"; else say "now running $TAG ($COMMIT)"; fi; exit 0; fi
[ "$rc" = 2 ] && die "the age key is missing, so nothing could be started"

say "release $TAG failed (code $rc): rolling back"
if [ "$DRY" = 1 ]; then echo "DRY-RUN: git checkout --detach \$(cat $STATE/previous) and bring that release up again"; exit 1; fi
previous=$(cat "$STATE/previous")
[ "$previous" != "$COMMIT" ] || die "there is no earlier release to go back to"
git checkout --detach "$previous"
if deploy_current; then die "release $TAG failed; the previous release ($previous) is running again"; fi
die "release $TAG failed AND the rollback to $previous did not come up healthy: look at 'docker compose ps' and the logs on the VM"
