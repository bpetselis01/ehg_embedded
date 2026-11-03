"""Checks the docs and the duplicated facts still match the code, so stale documentation fails the
test suite instead of misleading a reader: the 2 copies of the wire format (ehg_packet.h, packet.py),
the Bluetooth IDs and commands, links between docs, the file index, the serial baud rate and --help."""

import re
import subprocess
from pathlib import Path

import pytest

from gateway import gateway, packet
from tools import feed

ROOT = Path(__file__).parents[1]
SRC = ROOT / "firmware/ehg_node/src"


def tracked(*patterns: str) -> list[Path]:
    """Files git tracks (so generated and gitignored files are skipped), matching the patterns."""
    out = subprocess.run(["git", "ls-files", *patterns], cwd=ROOT, capture_output=True, text=True, check=True)
    return [ROOT / p for p in out.stdout.splitlines() if (ROOT / p).is_file()]


def c_define(text: str, name: str) -> str:
    m = re.search(rf"^#define {name}\s+(\S+)", text, re.M)
    assert m, f"#define {name} not found"
    return m.group(1)


def test_wire_version_and_flags():
    h = (SRC / "ehg_packet.h").read_text()
    assert int(c_define(h, "EHG_PKT_VERSION")) == packet.VERSION
    assert int(c_define(h, "EHG_CRC_BYTES")) == packet.CRC.size
    assert c_define(h, "EHG_FLAG_RETRANS") == f"BIT({packet.FLAG_RETRANS.bit_length() - 1})"
    assert c_define(h, "EHG_FLAG_24BIT") == f"BIT({packet.FLAG_24BIT.bit_length() - 1})"


def test_wire_header_layout():
    h = (SRC / "ehg_packet.h").read_text()
    body = re.search(r"struct ehg_pkt_hdr \{(.*?)\} __packed;", h, re.S)
    assert body, "struct ehg_pkt_hdr not found in ehg_packet.h"
    types = re.findall(r"^\s*uint(8|16|32)_t \w+;", body.group(1), re.M)
    fmt = "".join({"8": "B", "16": "H", "32": "I"}[t] for t in types)
    assert fmt == packet.HEADER.format.lstrip("<"), f"C header {fmt} vs packet.py {packet.HEADER.format}"
    size = re.search(r"BUILD_ASSERT\(sizeof\(struct ehg_pkt_hdr\) == (\d+)", h)
    assert size and int(size.group(1)) == packet.HEADER.size


def test_gatt_ids_and_commands():
    h = (SRC / "ehg_service.h").read_text()
    m = re.search(r"BT_UUID_128_ENCODE\(0x([0-9a-f]+) \+ \(n\), 0x(\w+), 0x(\w+), 0x(\w+), 0x(\w+)\)", h)
    assert m, "EHG_UUID_BASE not found in ehg_service.h"
    base, *rest = m.groups()
    for n, uuid in ((1, gateway.SERVICE_UUID), (2, gateway.DATA_UUID), (3, gateway.CTRL_UUID)):
        assert "-".join([f"{int(base, 16) + n:08x}", *rest]) == uuid
    p = (SRC / "ehg_packet.h").read_text()
    for name, value in (("START", gateway.OP_START), ("STOP", gateway.OP_STOP), ("RESEND", gateway.OP_RESEND)):
        op = re.search(rf"EHG_OP_{name} = (0x[0-9a-fA-F]+)", p)
        assert op and int(op.group(1), 16) == value, f"EHG_OP_{name}"


def test_relative_links_resolve():
    broken = []
    for md in tracked("*.md"):
        text = re.sub(r"```.*?```|`[^`\n]*`", "", md.read_text(), flags=re.S)  # code is not a link
        for target in re.findall(r"\]\(([^)\s]+)\)", text):
            path = target.split("#")[0]
            if path and not path.startswith(("http://", "https://", "mailto:")):
                if not (md.parent / path).exists():
                    broken.append(f"{md.relative_to(ROOT)} -> {target}")
    assert not broken, broken


def test_doc_paths_in_code_exist():
    """Code comments cannot hold links, so plain-text docs/... paths get the same check."""
    code = [p for p in tracked() if p.suffix not in (".md", ".lock", ".png", ".json")]
    missing = [f"{p.relative_to(ROOT)}: {d}" for p in code
               for d in re.findall(r"docs/[\w/.-]+?\.md", p.read_text(errors="ignore"))
               if not (ROOT / d).exists()]
    assert not missing, missing


def test_file_index_complete():
    guide = (ROOT / "docs/codebase-guide.md").read_text()
    files = tracked(*(f"{d}/*.{ext}" for d in ("tools", "eval", "gateway", "viewer", "backend", "firmware", "deploy")
                      for ext in ("py", "sh")))
    assert files
    missing = [str(f.relative_to(ROOT)) for f in files if f.name not in guide]
    assert not missing, f"not in docs/codebase-guide.md file index: {missing}"


def test_one_baud_rate():
    overlay = (ROOT / "firmware/ehg_node/boards/nrf52840dk_nrf52840.overlay").read_text()
    baud = int(re.search(r"current-speed = <(\d+)>", overlay).group(1))
    assert feed.BAUD == baud
    # plans/, sessions/ and research/ are dated records, so only current docs and the firmware are checked
    current = [ROOT / "README.md", *(ROOT / "docs").glob("*.md"), *tracked("firmware/ehg_node/src")]
    wrong = [f"{p.relative_to(ROOT)}: {b}" for p in current
             for b in re.findall(r"(\d{5,7}) baud", p.read_text()) if int(b) != baud]
    assert not wrong, f"board overlay says {baud} baud: {wrong}"


@pytest.mark.parametrize("path", [p for p in tracked("*.py") if "ArgumentParser(" in p.read_text()],
                         ids=lambda p: str(p.relative_to(ROOT)))
def test_cli_help_is_module_docstring(path):
    assert "ArgumentParser(description=__doc__" in path.read_text(), "--help should print the file's header"


def test_citation_has_required_keys():
    keys = re.findall(r"^([\w-]+):", (ROOT / "CITATION.cff").read_text(), re.M)
    assert {"cff-version", "message", "title", "authors"} <= set(keys)


def test_decision_records_have_sections():
    records = sorted((ROOT / "docs/decisions").glob("[0-9][0-9][0-9][0-9]-*.md"))
    assert records
    for r in records:
        text = r.read_text()
        missing = [h for h in ("Status", "Context", "Decision", "Consequences") if f"## {h}" not in text]
        assert not missing, f"{r.name} lacks {missing}"
