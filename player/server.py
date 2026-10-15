from __future__ import annotations

import logging
import mimetypes
import re
import socket
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from player.index import MusicLibrary

logger = logging.getLogger("xiaogpt.player")

_SONG_PATH = re.compile(r"^/song/([0-9a-f]+)(?:\.\w+)?$")
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
_CHUNK = 64 * 1024


def _make_handler(library: MusicLibrary) -> type[BaseHTTPRequestHandler]:
    class SongHandler(BaseHTTPRequestHandler):
        def do_HEAD(self) -> None:
            self._serve(send_body=False)

        def do_GET(self) -> None:
            self._serve(send_body=True)

        def _serve(self, send_body: bool) -> None:
            match = _SONG_PATH.match(self.path.split("?", 1)[0])
            song = library.get(match.group(1)) if match else None
            if song is None or not song.path.is_file():
                self.send_error(404)
                return

            size = song.path.stat().st_size
            start, end = 0, size - 1
            status = 200
            if range_match := _RANGE.match(self.headers.get("Range", "")):
                first, last = range_match.groups()
                if first:
                    start = int(first)
                    end = min(int(last), size - 1) if last else size - 1
                elif last:
                    start = max(size - int(last), 0)
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                status = 206

            content_type = mimetypes.guess_type(song.path.name)[0] or "audio/mpeg"
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if not send_body:
                return

            remaining = end - start + 1
            try:
                with song.path.open("rb") as f:
                    f.seek(start)
                    while remaining > 0:
                        chunk = f.read(min(_CHUNK, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError, socket.error):
                pass

        def log_message(self, format: str, *args) -> None:
            logger.debug(f"{self.address_string()} - {format}", *args)

    return SongHandler


class _Server(ThreadingHTTPServer):
    def server_bind(self) -> None:
        # HTTPServer.server_bind 会做一次 getfqdn 反查，部分网络下要卡好几秒
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "localhost", self.server_address[1]


class MusicServer:
    def __init__(self, library: MusicLibrary, port: int) -> None:
        self.library = library
        self.port = port
        self._httpd: ThreadingHTTPServer | None = None

    def start(self) -> None:
        self._httpd = _Server(("", self.port), _make_handler(self.library))
        self.port = self._httpd.server_address[1]
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        logger.info("音乐 HTTP 服务已启动，端口 %s", self.port)

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
