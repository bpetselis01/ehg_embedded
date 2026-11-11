"""Static guard for Unit 13 (LESC pairing, own signing key). The firmware itself is only tested on the board, so this keeps the pieces that can silently rot:
the default build stays open and key-free, the secure overlay keeps every protection, and no private key is ever tracked by git."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
NODE = ROOT / "firmware/ehg_node"


def text(rel: str) -> str:
    return (NODE / rel).read_text()


def conf_symbols(src: str) -> dict[str, str]:
    return dict(re.findall(r"^(CONFIG_\w+)=(\S+)", src, re.M))


def test_default_build_stays_open():
    prj = conf_symbols(text("prj.conf"))
    assert not {"CONFIG_BT_SMP", "CONFIG_BT_SETTINGS", "CONFIG_EHG_REQUIRE_PAIRING"} & set(prj)
    kconfig = text("Kconfig")
    assert re.search(r"config EHG_REQUIRE_PAIRING\n\tbool.*\n\tdefault n", kconfig)
    assert "BOOT_SIGNATURE_KEY_FILE" not in text("sysbuild.conf").replace("# ", "").split("SB_CONFIG_BOOTLOADER_MCUBOOT")[1], "the default build must not point at a key"


def test_secure_overlay_keeps_every_protection():
    sec = conf_symbols(text("security.conf"))
    for sym in ("CONFIG_EHG_REQUIRE_PAIRING", "CONFIG_BT_SMP", "CONFIG_BT_SMP_SC_ONLY", "CONFIG_BT_SMP_ENFORCE_MITM", "CONFIG_BT_BONDABLE",
                "CONFIG_BT_SETTINGS", "CONFIG_SETTINGS", "CONFIG_NVS", "CONFIG_MCUMGR_TRANSPORT_BT_PERM_RW_AUTHEN"):
        assert sec.get(sym) == "y", sym
    assert "CONFIG_BT_FIXED_PASSKEY" not in sec and "CONFIG_BT_USE_DEBUG_KEYS" not in sec  # a known passkey or debug keys would void the protection


def test_gatt_permissions_are_authenticated_under_the_flag():
    svc = text("src/ehg_service.c")
    block = svc.split("#ifdef CONFIG_EHG_REQUIRE_PAIRING")[1].split("#else")[0]
    assert "BT_GATT_PERM_READ_AUTHEN" in block and "BT_GATT_PERM_WRITE_AUTHEN" in block
    assert "_ENCRYPT" not in block and "_LESC" not in block  # _ENCRYPT alone can be met by Just Works; _LESC alone checks nothing (GHSA-p6f3-f63q-5mc2)
    assert "BT_GATT_CCC(NULL, EHG_PERM_CCC)" in svc and "EHG_PERM_CTRL, NULL, ctrl_write" in svc


def test_pairing_callbacks_are_display_only_and_peripheral_never_asks():
    main = text("src/main.c")
    assert "passkey_display" in main and ".cancel" in main
    assert "passkey_entry" not in main and "pairing_confirm" not in main  # DisplayOnly: any more would change the IO capability
    # Apple's rules: the central starts pairing when an access is refused. Only the opt-in button build asks the central to pair
    # (iOS did not start pairing by itself on the first phone test), and only inside its Button 2 window.
    assert "bt_conn_set_security" not in main.split("#ifdef CONFIG_EHG_PAIRING_BUTTON\n/* Button pairing")[0]


def test_bootloader_overlay_keeps_the_boot_partition():
    # a user overlay replaces mcuboot's own app.overlay; without this line the bootloader was linked into the app slot and never ran
    assert "zephyr,code-partition = &boot_partition" in text("sysbuild/mcuboot.overlay")


def test_build_script_secure_mode_is_separate_and_refuses_a_key_in_the_repo():
    script = (ROOT / "firmware/build.sh").read_text()
    assert 'BUILD="$APP/build-secure"' in script and "EXTRA_CONF_FILE" in script and "SB_CONFIG_BOOT_SIGNATURE_KEY_FILE" in script
    assert "OUTSIDE the repository" in script and "OUTSIDE the repository" in (ROOT / "firmware/gen_signing_key.sh").read_text()
    for sh in ("build.sh", "gen_signing_key.sh"):
        assert subprocess.run(["sh", "-n", str(ROOT / "firmware" / sh)]).returncode == 0


def test_no_private_key_is_tracked_and_pem_is_ignored():
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    for rel in tracked:
        if rel.endswith((".pem", ".key")) and not rel.startswith(("tests/", "backend/pki/")):
            raise AssertionError(f"key file tracked: {rel}")
    assert not any(l for l in (ROOT / ".gitignore").read_text().splitlines() if l.strip() == "!*.pem")


def test_gateway_never_pairs():
    src = (ROOT / "gateway/gateway.py").read_text()
    assert ".pair(" not in src and "pair=True" not in src  # Bleak cannot pair on macOS; the system does it


def test_button_pairing_is_opt_in_and_keeps_the_rest():
    assert re.search(r"config EHG_PAIRING_BUTTON\n\tbool.*\n\tdepends on EHG_REQUIRE_PAIRING\n\tdefault n", text("Kconfig"))
    btn = conf_symbols(text("security-button.conf"))
    for sym in ("CONFIG_EHG_REQUIRE_PAIRING", "CONFIG_EHG_PAIRING_BUTTON", "CONFIG_BT_SMP", "CONFIG_BT_SMP_SC_PAIR_ONLY", "CONFIG_BT_SMP_APP_PAIRING_ACCEPT",
                "CONFIG_BT_BONDABLE", "CONFIG_BT_SETTINGS", "CONFIG_SETTINGS", "CONFIG_NVS", "CONFIG_MCUMGR_TRANSPORT_BT_PERM_RW_ENCRYPT"):
        assert btn.get(sym) == "y", sym
    assert int(btn["CONFIG_BT_MAX_PAIRED"]) >= 2  # the default of 1 lets a stale bond block the next central
    assert not {"CONFIG_BT_FIXED_PASSKEY", "CONFIG_BT_USE_DEBUG_KEYS", "CONFIG_MCUMGR_TRANSPORT_BT_PERM_RW_AUTHEN"} & set(btn)
    assert "CONFIG_BT_SMP_SC_ONLY" not in btn  # level-4-only mode rejects Just Works (seen on the phone)
    assert btn["CONFIG_BT_SMP_ENFORCE_MITM"] == "n"  # the Kconfig default is y; with no passkey it must not demand MITM


def test_button_pairing_permissions_and_window():
    svc = text("src/ehg_service.c")
    block = svc.split("#ifdef CONFIG_EHG_PAIRING_BUTTON")[1].split("#else")[0]
    assert "BT_GATT_PERM_READ_ENCRYPT" in block and "BT_GATT_PERM_WRITE_ENCRYPT" in block and "AUTHEN" not in block
    main = text("src/main.c")
    button = main.split("#ifdef CONFIG_EHG_PAIRING_BUTTON\n/* Button pairing")[1].split("#else")[0]
    assert "BT_SECURITY_ERR_PAIR_NOT_ALLOWED" in button and "pairing_window_open()" in button  # no window, no new pairing
    assert "passkey_display" not in button and "BT_CONN_TYPE_LE" in button
    assert "DK_BTN2_MSK" in main and "open_pairing_window()" in main
    complete = main.split("static void pairing_complete")[1].split("}")[0]
    assert "window_end_ms = 0" in complete  # one new pairing per button press: the window closes after the first bond


def test_build_script_has_a_separate_button_build():
    script = (ROOT / "firmware/build.sh").read_text()
    assert "--pairing-button" in script and 'BUILD="$APP/build-button"' in script and "security-button.conf" in script
