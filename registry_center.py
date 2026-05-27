import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from common.config import REGISTRY_HOST, REGISTRY_PORT
from common.schemas import success_response, error_response

logger = logging.getLogger("registry")


class Registry:
    def __init__(self) -> None:
        self.agents: dict[str, dict[str, Any]] = {}

    def register(self, agent_name: str, payload: dict[str, Any]) -> None:
        self.agents[agent_name] = payload
        protocol = payload.get("protocol", "unknown")
        logger.info(f"Agent registered: {agent_name} at {protocol}://{payload.get('host')}:{payload.get('port')}")

    def discover(self) -> dict[str, dict[str, Any]]:
        return self.agents


_registry = Registry()


class RegistryRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/discover":
            self._send_json(HTTPStatus.OK, success_response({"agents": _registry.discover()}))
            return
        if self.path == "/health":
            self._send_json(HTTPStatus.OK, success_response({"status": "ok"}))
            return
        self._send_json(HTTPStatus.NOT_FOUND, error_response("not_found", f"unknown path: {self.path}"))

    def do_POST(self) -> None:
        if self.path == "/register":
            try:
                payload = self._read_json()
                agent_name = payload.get("agent_name")
                if not agent_name:
                    self._send_json(HTTPStatus.BAD_REQUEST, error_response("invalid_request", "agent_name required"))
                    return
                _registry.register(agent_name, payload)
                self._send_json(HTTPStatus.OK, success_response({"status": "registered"}))
            except Exception as e:
                self._send_json(HTTPStatus.BAD_REQUEST, error_response("invalid_json", str(e)))
            return

        self._send_json(HTTPStatus.NOT_FOUND, error_response("not_found", f"unknown path: {self.path}"))

    def _read_json(self) -> dict[str, Any]:
        content_length_str = self.headers.get("Content-Length")
        if not content_length_str:
            raise ValueError("missing Content-Length")
        body = self.rfile.read(int(content_length_str))
        return json.loads(body.decode("utf-8"))

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        pass


def run() -> None:
    server_address = (REGISTRY_HOST, REGISTRY_PORT)
    httpd = ThreadingHTTPServer(server_address, RegistryRequestHandler)
    logger.info(f"Registry Center listening on http://{REGISTRY_HOST}:{REGISTRY_PORT}")
    logger.info(f"Endpoints: POST /register, GET /discover, GET /health")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    run()
