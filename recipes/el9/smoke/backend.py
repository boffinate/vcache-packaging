#!/usr/bin/env python3
"""Trivial tagging backend for the installed-package smoke test.

Every response carries a Cache-Tag header and a body that names the generation
it was produced in. The generation increments on every backend hit, so a cached
response and a freshly fetched one are distinguishable by content alone: a warm
hit and a post-purge refetch cannot be confused with each other.
"""

import http.server

GENERATION = 0


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802 - http.server API
        global GENERATION
        GENERATION += 1
        body = ("generation=%d\n" % GENERATION).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=120")
        self.send_header("Cache-Tag", "article:1, section:news")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print("backend: " + fmt % args, flush=True)


if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("127.0.0.1", 8080), Handler).serve_forever()
