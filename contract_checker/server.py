"""Local web server (stdlib only).

  GET  /                 -> web UI
  GET  /api/samples      -> demo files from ./samples (for a quick demo; upload works the same way)
  POST /api/analyze      {files: [{name, content_b64}], check_date: "YYYY-MM-DD"} -> table of contracts
  POST /api/ask          {files, check_date, question} -> answer with citations

The server keeps no state: the browser sends the uploaded files with every request.
"""
import base64
import json
import os
import sys
from datetime import date
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from .resolver import analyze
from .questions import answer

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / 'static'
SAMPLES = ROOT / 'samples'
MAX_BODY = 20 * 1024 * 1024
DEFAULT_CHECK_DATE = date(2026, 3, 10)


def decode_text(raw: bytes) -> str:
    for enc in ('utf-8-sig', 'cp1251'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


def _json_default(o):
    if isinstance(o, date):
        return o.isoformat()
    raise TypeError(type(o).__name__)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/':
            return self._file(STATIC / 'index.html')
        if path.startswith('/static/'):
            target = (STATIC / path[len('/static/'):]).resolve()
            if STATIC in target.parents and target.is_file():
                return self._file(target)
        if path == '/api/samples':
            files = [{'name': p.name, 'content_b64': base64.b64encode(p.read_bytes()).decode()}
                     for p in sorted(SAMPLES.glob('*.txt'))]
            return self._json({'files': files})
        self._json({'error': 'not found'}, 404)

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            if length > MAX_BODY:
                return self._json({'error': 'Слишком большой объём файлов'}, 413)
            body = json.loads(self.rfile.read(length) or b'{}')
            files = [(f['name'], decode_text(base64.b64decode(f['content_b64']))) for f in body.get('files', [])]
            check_date = date.fromisoformat(body['check_date']) if body.get('check_date') else DEFAULT_CHECK_DATE
        except (ValueError, KeyError, TypeError) as e:
            return self._json({'error': f'Некорректный запрос: {e}'}, 400)

        if self.path == '/api/analyze':
            return self._json(analyze(files, check_date))
        if self.path == '/api/ask':
            return self._json(answer(files, body.get('question', ''), check_date))
        self._json({'error': 'not found'}, 404)

    def _file(self, path: Path):
        types = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css'}
        data = path.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', types.get(path.suffix, 'application/octet-stream') + '; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False, default=_json_default).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        sys.stderr.write('%s %s\n' % (self.command, self.path))


def main():
    port = int(sys.argv[1] if len(sys.argv) > 1 else os.environ.get('PORT', 8000))
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    # ASCII only: a Windows console with cp1252 cannot print Cyrillic.
    print(f'Open http://127.0.0.1:{port}  (Ctrl+C to stop)', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
