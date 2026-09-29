"""Serves the local form fixture. It is not a job application."""

import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import sleep
from urllib.parse import urlparse

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "forms"


class _Handler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:
        if urlparse(self.path).path == "/hang":
            sleep(5)
            self.send_response(204)
            self.end_headers()
            return
        super().do_GET()

    def log_message(self, format: str, *args: object) -> None:
        return


class FormServer:
    def __init__(self, root: Path = FIXTURES) -> None:
        handler = partial(_Handler, directory=str(root))
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        port = self._httpd.server_address[1]
        self.base = f"http://127.0.0.1:{port}"
        self.demo = f"{self.base}/demo.html"
        self.elsewhere = f"{self.base}/elsewhere.html"
        self.hang = f"{self.base}/hang"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def page(self, name: str) -> str:
        return f"{self.base}/{name}"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
