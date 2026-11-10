#!/bin/sh
# PURPOSE: Makes the ECDSA P-256 key that signs firmware for the secure build (firmware/build.sh --secure). The key never goes in the repository.
# USAGE: EHG_MCUBOOT_KEY=$HOME/.ehg-keys/mcuboot-p256.pem firmware/gen_signing_key.sh
# WARNING: BACK THE KEY UP (password manager or an encrypted disk image). Lose it and no firmware update (OTA) can be accepted by a board whose bootloader holds its public half.
#   A board running a bootloader built with this key refuses images signed with the default development key, and the other way round: changing keys means reflashing the bootloader over the DK's J-Link.
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd -P)"
[ -n "$EHG_MCUBOOT_KEY" ] || { echo "set EHG_MCUBOOT_KEY to the path of the new key (outside the repo)"; exit 1; }
[ ! -e "$EHG_MCUBOOT_KEY" ] || { echo "$EHG_MCUBOOT_KEY exists: not overwriting"; exit 1; }
mkdir -p "$(dirname "$EHG_MCUBOOT_KEY")"
case "$(cd "$(dirname "$EHG_MCUBOOT_KEY")" && pwd -P)/" in "$REPO"/*) echo "the key must be OUTSIDE the repository"; exit 1 ;; esac
umask 077
nrfutil toolchain-manager launch --ncs-version v3.4.1 -- python3 "$HOME/ncs/v3.4.1/bootloader/mcuboot/scripts/imgtool.py" keygen -k "$EHG_MCUBOOT_KEY" -t ecdsa-p256
chmod 600 "$EHG_MCUBOOT_KEY"
echo "key written to $EHG_MCUBOOT_KEY (mode 600). Back it up now."
