import json
import logging
import time
from urllib.parse import urlparse, parse_qs
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from common.config import REGISTRY_HOST, REGISTRY_PORT
from common.schemas import success_response, error_response

logger = logging.getLogger("registry")


class Registry:
    def __init__(self) -> None:
        self.agents: dict[str, dict[str, Any]] = {}
        self.AGENT_TTL = 6.0

    def register(self, agent_name: str, payload: dict[str, Any]) -> None:
        payload["last_heartbeat"] = time.time()
        payload["status"] = "healthy"
        self.agents[agent_name] = payload
        protocol = payload.get("protocol", "unknown")
        logger.info(f"Agent registered: {agent_name} at {protocol}://{payload.get('host')}:{payload.get('port')}")

    def heartbeat(self, agent_name: str) -> bool:
        if agent_name in self.agents:
            self.agents[agent_name]["last_heartbeat"] = time.time()
            self.agents[agent_name]["status"] = "healthy"
            return True
        return False

    # 查找所有健康的agents
    def discover(self) -> dict[str, dict[str, Any]]:
        now = time.time()
        healthy_agents = {}
        for name, data in self.agents.items():
            if now - data.get("last_heartbeat", 0) <= self.AGENT_TTL:
                healthy_agents[name] = data
            else:
                data["status"] = "unhealthy"
        return healthy_agents
    
    # 在健康的agent中根据能力查找agents
    def lookup(self, capability: str) -> dict[str, dict[str, Any]]:
        healthy_agents = self.discover()
        return {
            name: data for name, data in healthy_agents.items()
            if capability in data.get("capabilities", [])
        }

    # 查找所有agents
    def get_all_agents(self) -> dict[str, dict[str, Any]]:
        now = time.time()
        for name, data in self.agents.items():
            if now - data.get("last_heartbeat", 0) > self.AGENT_TTL:
                data["status"] = "unhealthy"
        return self.agents


_registry = Registry()


class RegistryRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        if parsed_url.path == "/discover":
            self._send_json(HTTPStatus.OK, success_response({"agents": _registry.discover()}))
            return
        if parsed_url.path == "/agents":
            self._send_json(HTTPStatus.OK, success_response({"agents": _registry.get_all_agents()}))
            return
        if parsed_url.path == "/lookup":
            qs = parse_qs(parsed_url.query)
            capability = qs.get("capability", [""])[0]
            self._send_json(HTTPStatus.OK, success_response({"agents": _registry.lookup(capability)}))
            return
        if parsed_url.path == "/health":
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
        if self.path == "/heartbeat":
            try:
                payload = self._read_json()
                agent_name = payload.get("agent_name")
                if not agent_name:
                    self._send_json(HTTPStatus.BAD_REQUEST, error_response("invalid_request", "agent_name required"))
                    return
                if _registry.heartbeat(agent_name):
                    self._send_json(HTTPStatus.OK, success_response({"status": "ok"}))
                else:
                    self._send_json(HTTPStatus.NOT_FOUND, error_response("not_found", "agent not registered"))
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
    logger.info(f"Endpoints: POST /register, POST /heartbeat, GET /discover, GET /lookup, GET /agents, GET /health")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    run()
