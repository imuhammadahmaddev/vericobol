"""Example tests for the VeriCOBOL GnuCOBOL MCP server (newline-delimited JSON-RPC).

Spawns ``python -m mcp_server.server`` as a subprocess with cwd = backend dir.

Validates: Requirements 9.3, 9.4, 9.5, 9.6, 9.7, 9.8, 9.9, 9.10, 9.11, 12.1, 12.7
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
LOAN_SOURCE = (BACKEND_DIR / "cobol_samples" / "loan_calc.cbl").read_text(encoding="utf-8")
LINE_TIMEOUT = 60  # seconds; generous for verification on slow CPUs


class McpClient:
    def __init__(self) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "mcp_server.server"],
            cwd=str(BACKEND_DIR),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.raw_lines: list[str] = []
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._next_id = 1
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self._queue.put(line)
        self._queue.put(None)

    def send_raw(self, text: str) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(text + "\n")
        self.proc.stdin.flush()

    def read(self) -> dict:
        line = self._queue.get(timeout=LINE_TIMEOUT)
        assert line is not None, "server closed stdout unexpectedly"
        self.raw_lines.append(line)
        return json.loads(line)

    def request(self, method: str, params: dict | None = None) -> dict:
        req_id = self._next_id
        self._next_id += 1
        msg: dict = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params
        self.send_raw(json.dumps(msg))
        resp = self.read()
        assert resp["id"] == req_id
        return resp

    def call(self, name: str, arguments: dict) -> dict:
        resp = self.request("tools/call", {"name": name, "arguments": arguments})
        return resp["result"]

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            self.proc.kill()
        # drain any remaining output for the purity check
        while True:
            try:
                line = self._queue.get(timeout=2)
            except queue.Empty:
                break
            if line is None:
                break
            self.raw_lines.append(line)


def payload(result: dict) -> dict:
    assert result["content"][0]["type"] == "text"
    return json.loads(result["content"][0]["text"])


@pytest.fixture(scope="module")
def client():
    c = McpClient()
    yield c
    c.close()


def test_initialize(client: McpClient) -> None:
    resp = client.request(
        "initialize",
        {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
    )
    result = resp["result"]
    assert result["serverInfo"]["name"] == "vericobol-gnucobol"
    assert "tools" in result["capabilities"]
    # notification gets no reply; the next request still lines up by id
    client.send_raw(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))


def test_tools_list(client: McpClient) -> None:
    tools = client.request("tools/list")["result"]["tools"]
    assert [t["name"] for t in tools] == [
        "list_cobol_presets",
        "compile_and_run_cobol",
        "verify_cobol_parity",
    ]
    for t in tools:
        assert t["inputSchema"]["type"] == "object"


def test_list_presets(client: McpClient) -> None:
    result = client.call("list_cobol_presets", {})
    assert not result.get("isError")
    presets = payload(result)["presets"]
    assert {p["preset_id"] for p in presets} == {"loan_calc", "compound_int", "daily_ledger"}
    for p in presets:
        assert p["input_fields"] and p["output_fields"]


def test_compile_and_run_happy_path(client: McpClient) -> None:
    result = client.call(
        "compile_and_run_cobol",
        {"cobol_source": LOAN_SOURCE, "input_rows": ["10000.00 5.000 12"]},
    )
    assert not result.get("isError")
    body = payload(result)
    assert body["exit_code"] == 0
    assert body["timed_out"] is False
    assert len(body["stdout"].split()) == 3


def test_verify_parity_loan_calc(client: McpClient) -> None:
    result = client.call("verify_cobol_parity", {"preset_id": "loan_calc", "num_cases": 150})
    assert not result.get("isError")
    report = payload(result)
    assert report["verified"] is True
    assert report["parity_score_pct"] == "100.00"
    assert report["total_cases"] == 150


def test_sandbox_rejected_source(client: McpClient) -> None:
    source = (
        "IDENTIFICATION DIVISION.\nPROGRAM-ID. EVIL.\nPROCEDURE DIVISION.\n"
        '    CALL "SYSTEM" USING "echo hi".\n    STOP RUN.\n'
    )
    result = client.call("compile_and_run_cobol", {"cobol_source": source})
    assert result["isError"] is True


def test_unknown_method(client: McpClient) -> None:
    resp = client.request("no/such/method")
    assert resp["error"]["code"] == -32601


def test_malformed_json_then_loop_continues(client: McpClient) -> None:
    client.send_raw("{this is not json")
    resp = client.read()
    assert resp["error"]["code"] == -32700
    assert resp["id"] is None
    assert client.request("ping")["result"] == {}


def test_stdout_purity(client: McpClient) -> None:
    client.close()
    assert client.raw_lines
    for line in client.raw_lines:
        msg = json.loads(line)
        assert msg["jsonrpc"] == "2.0"
        assert "result" in msg or "error" in msg
