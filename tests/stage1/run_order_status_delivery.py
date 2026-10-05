"""Live 1C status reader against a local HTTP fixture, with all database writes rolled back."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.request import Request, urlopen
import uuid

from run_register_concurrency import MCP, PROBE, decode


def run(port, proof_path, output):
    proof = json.loads(proof_path.read_text(encoding="utf-8"))["response"]["result"]
    expected = json.loads(proof["content"][0]["text"])["data"]["baseFingerprint"]
    client = MCP(port)
    observed = client.execute(PROBE)
    if (observed["baseFingerprint"] != expected
            or observed["configuration"] != "КомплекснаяАвтоматизацияДляКазахстана"
            or observed["version"] != "2.4.5.18"):
        raise RuntimeError("AUTHORIZED_DEMO_REQUIRED")
    base, order, operation = (str(uuid.uuid4()) for _ in range(3))
    requests = []
    counts = {"operation": 0}

    class Fixture(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            if self.path == "/v1/operations/" + operation:
                counts["operation"] += 1
                if counts["operation"] <= 6 and counts["operation"] % 2:
                    self.send_response(503)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                data = {"baseId": base, "orderId": order, "operationId": operation,
                        "state": "unknown" if counts["operation"] <= 6 else "acknowledged",
                        "conflict": None if counts["operation"] <= 6 else "duplicate_task_tag"}
            elif self.path == "/v1/task-requests/" + operation:
                data = {"operationId": operation, "initiatorId": "17",
                        "workplaceId": "status-contract", "deliveryState": "opened"}
            else:
                self.send_error(404)
                return
            body = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        code = Path(__file__).with_name("order_status_delivery_contract.bsl").read_text(encoding="utf-8")
        for key, value in {"BASE": base, "ORDER": order, "OPERATION": operation,
                           "PORT": str(server.server_port)}.items():
            code = code.replace("__" + key + "__", value)
        # Keep the fixture alive while the human confirms the native Toolkit prompt.
        client.request_id += 1
        payload = {"jsonrpc": "2.0", "id": client.request_id, "method": "tools/call",
                   "params": {"name": "execute_code", "arguments": {"code": code, "execution_context": "server"}}}
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if client.session:
            headers["Mcp-Session-Id"] = client.session
        request = Request(client.url, json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers)
        with urlopen(request, timeout=600) as result:
            envelope = decode(result.read().decode("utf-8"))
        if "error" in envelope:
            raise RuntimeError("MCP_RESULT_NOT_CONFIRMED")
        response = envelope["result"]
        blocks = [block["text"] for block in response.get("content", []) if block.get("type") == "text"]
        if response.get("isError") or len(blocks) != 1:
            raise RuntimeError("MCP_RESULT_NOT_CONFIRMED")
        answer = json.loads(blocks[0])
        report = {"response": answer, "httpRequests": requests,
                  "cleanup": "transaction_rollback" if "success" in answer else "unknown"}
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        success = answer.get("success") and answer.get("data") == "ORDER_DELIVERY_CONTRACT_GREEN"
        if success and (counts["operation"] != 7 or len(requests) != 11):
            raise RuntimeError("EXPECTED_HTTP_SEQUENCE_NOT_OBSERVED")
        print(json.dumps(answer, ensure_ascii=False))
        return bool(success)
    except Exception:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({"response": "not_confirmed", "httpRequests": requests,
                                     "cleanup": "unknown"}, ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=6004)
    parser.add_argument("--demo-proof", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(".local/stage-1/order-delivery-private.json"))
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be in 1024..65535")
    try:
        passed = run(args.port, args.demo_proof, args.output)
    except Exception as error:
        print(json.dumps({"status": "not_confirmed", "errorType": type(error).__name__,
                          "reason": str(error) if type(error) is RuntimeError else ""}))
        raise SystemExit(1)
    raise SystemExit(0 if passed else 1)
