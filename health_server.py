import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.getenv('PORT', '8080'))

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ('/', '/health'):
            self.send_response(404)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            return
        body = json.dumps({'status': 'online', 'service': 'pocket-option-discord-signal-bot'}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass

HTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
