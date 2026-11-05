"""Waypoint server: serves the web app and stores office maps in DATA_DIR.

Standard library only. Configuration (environment variables):
  WAYPOINT_DATA_DIR   where maps are stored            (default: /data)
  WAYPOINT_HOST       interface to listen on           (default: 0.0.0.0)
  WAYPOINT_PORT       port to listen on                (default: 8080)
  WAYPOINT_SEED_DEMO  copy the demo map into an empty data dir on first start (default: true)
  WAYPOINT_MAX_UPLOAD_MB  largest accepted floor plan image (default: 40)

Data layout in DATA_DIR:
  floors.json   {"floors": [{id, name, elev, extent:[x0,y0,x1,y1], plan, demo?}]}
  rooms.json    {"version": 1, "rooms": [{id, floor, type, no, name, x0, y0, x1, y1}],
                 "walls": [{id, floor, x0, y0, x1, y1, t}]}
                type: room | bathroom | walkway | stair | elevator | empty (opening to the floor below)
                walls are straight segments in metres, t = thickness in metres
  plans/        floor plan images referenced by floors.json
  *.bak.json    previous version of each file, written before every save
  backups/      automatic copies taken before a backup is imported (never exported)

API:
  GET  /api/health          liveness check + version
  GET  /api/map             {"floors": [...], "rooms": [...], "walls": [...]}
  PUT  /api/rooms           replace all rooms (and walls, when "walls" is sent)
  GET  /api/export          download everything (floors, rooms, walls, plan images) as one .zip
  POST /api/import          restore such a .zip (raw body); the current data is copied to backups/ first
  PUT  /api/floors          replace the floor list
  POST /api/plans/<floorId> upload a plan image (raw PNG/JPEG/WebP body) -> {"plan": "plans/<file>"}
  GET  /files/plans/<file>  plan image
"""
import io, json, os, re, shutil, signal, sys, threading, time, zipfile
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(APP_DIR, 'static')
DEMO = os.path.join(os.path.dirname(APP_DIR), 'demo')
DATA = os.path.abspath(os.environ.get('WAYPOINT_DATA_DIR', '/data'))
PLANS = os.path.join(DATA, 'plans')
MAX_UPLOAD = int(float(os.environ.get('WAYPOINT_MAX_UPLOAD_MB', '40')) * 1024 * 1024)
MAX_JSON = 10 * 1024 * 1024
ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$')
FILE_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$')
ROOM_TYPES = ('room', 'bathroom', 'walkway', 'stair', 'elevator', 'empty')
MAX_IMPORT = int(float(os.environ.get('WAYPOINT_MAX_IMPORT_MB', '300')) * 1024 * 1024)
IMAGE_TYPES = {b'\x89PNG': 'png', b'\xff\xd8\xff': 'jpg', b'RIFF': 'webp'}
VERSION = os.environ.get('WAYPOINT_VERSION', 'dev')
lock = threading.Lock()


def read_json(name, default):
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        return default
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def write_json(name, obj):
    """Atomic write that keeps the previous version as <name>.bak.json."""
    path = os.path.join(DATA, name)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    if os.path.exists(path):
        shutil.copy2(path, path.replace('.json', '.bak.json'))
    os.replace(tmp, path)


def seed_demo():
    """First start with an empty data dir: copy the clearly labelled demo map in."""
    if os.path.exists(os.path.join(DATA, 'floors.json')):
        return
    if os.environ.get('WAYPOINT_SEED_DEMO', 'true').lower() in ('0', 'false', 'no'):
        write_json('floors.json', {'floors': []})
        return
    shutil.copytree(os.path.join(DEMO, 'plans'), PLANS, dirs_exist_ok=True)
    for name in ('floors.json', 'rooms.json'):
        shutil.copy2(os.path.join(DEMO, name), os.path.join(DATA, name))
    print('Seeded demo map into', DATA)


def num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) < 1e6


def clean_floor(f):
    if not isinstance(f, dict) or not ID_RE.match(str(f.get('id', ''))):
        raise ValueError('floor id must be 1-40 letters, digits, - or _')
    ext = f.get('extent')
    if not (isinstance(ext, list) and len(ext) == 4 and all(num(v) for v in ext) and ext[2] > ext[0] and ext[3] > ext[1]):
        raise ValueError(f'floor {f["id"]}: bad extent')
    plan = f.get('plan') or ''
    if plan and not (plan.startswith('plans/') and FILE_RE.match(plan[6:])):
        raise ValueError(f'floor {f["id"]}: bad plan path')
    out = {'id': f['id'], 'name': str(f.get('name') or f['id'])[:60], 'elev': float(f.get('elev') or 0),
           'extent': [float(v) for v in ext], 'plan': plan}
    if f.get('demo'):
        out['demo'] = True
    return out


def clean_room(r):
    if not isinstance(r, dict) or not all(num(r.get(k)) for k in ('x0', 'y0', 'x1', 'y1')):
        raise ValueError('room needs numeric x0, y0, x1, y1')
    kind = r.get('type') or 'room'
    if kind not in ROOM_TYPES:
        raise ValueError(f'room type must be one of {", ".join(ROOM_TYPES)}')
    out = {'id': str(r.get('id', ''))[:40], 'floor': str(r.get('floor', ''))[:40], 'type': kind,
           'no': str(r.get('no', ''))[:40], 'name': str(r.get('name', ''))[:120],
           **{k: float(r[k]) for k in ('x0', 'y0', 'x1', 'y1')}}
    if r.get('demo'):
        out['demo'] = True
    return out


def clean_wall(w):
    if not isinstance(w, dict) or not all(num(w.get(k)) for k in ('x0', 'y0', 'x1', 'y1')):
        raise ValueError('wall needs numeric x0, y0, x1, y1')
    t = w.get('t', 0.2)
    if not num(t) or not 0.02 <= t <= 3:
        raise ValueError('wall thickness must be 0.02-3 m')
    return {'id': str(w.get('id', ''))[:40], 'floor': str(w.get('floor', ''))[:40], 't': float(t),
            **{k: float(w[k]) for k in ('x0', 'y0', 'x1', 'y1')}}


def export_zip():
    """Everything needed to rebuild the map, as one zip held in memory."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('waypoint-backup.json', json.dumps({'app': 'waypoint', 'format': 1, 'version': VERSION,
                                                       'exported_at': time.strftime('%Y-%m-%dT%H:%M:%S%z')}))
        for name in ('floors.json', 'rooms.json'):
            if os.path.exists(os.path.join(DATA, name)):
                z.write(os.path.join(DATA, name), name)
        for f in sorted(os.listdir(PLANS)):
            if FILE_RE.match(f) and os.path.isfile(os.path.join(PLANS, f)):
                z.write(os.path.join(PLANS, f), 'plans/' + f, compress_type=zipfile.ZIP_STORED)   # images are compressed already
    return buf.getvalue()


def import_zip(raw):
    """Validate a backup zip completely, then swap it in; the current data is moved to backups/ first."""
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise ValueError('not a zip file')
    if sum(i.file_size for i in z.infolist()) > 4 * MAX_IMPORT:
        raise ValueError('backup unpacks to more than the allowed size')
    names = z.namelist()
    if 'floors.json' not in names or 'rooms.json' not in names:
        raise ValueError('not a Waypoint backup (floors.json / rooms.json missing)')
    floors = [clean_floor(f) for f in json.loads(z.read('floors.json')).get('floors', [])]
    rd = json.loads(z.read('rooms.json'))
    rooms = [clean_room(r) for r in rd.get('rooms', [])]
    walls = [clean_wall(w) for w in rd.get('walls', [])]
    plans = {}
    for n in names:
        if n.startswith('plans/') and n != 'plans/':
            f = n[6:]
            if not FILE_RE.match(f):
                raise ValueError(f'bad file name in backup: {n}')
            data = z.read(n)
            if not any(data.startswith(m) for m in IMAGE_TYPES):
                raise ValueError(f'not an image: {n}')
            plans[f] = data
        elif n not in ('floors.json', 'rooms.json', 'waypoint-backup.json') and not n.endswith('/'):
            raise ValueError(f'unexpected file in backup: {n}')
    missing = [f['plan'] for f in floors if f['plan'] and f['plan'][6:] not in plans]
    if missing:
        raise ValueError('plan images missing from backup: ' + ', '.join(missing))
    keep = os.path.join(DATA, 'backups', 'pre-import-' + time.strftime('%Y%m%d-%H%M%S'))
    os.makedirs(keep)
    for name in ('floors.json', 'rooms.json', 'plans'):
        if os.path.exists(os.path.join(DATA, name)):
            shutil.move(os.path.join(DATA, name), os.path.join(keep, name))
    os.makedirs(PLANS)
    for f, data in plans.items():
        with open(os.path.join(PLANS, f), 'wb') as fh:
            fh.write(data)
    write_json('floors.json', {'floors': floors})
    write_json('rooms.json', {'version': 1, 'rooms': rooms, 'walls': walls})
    return {'floors': len(floors), 'rooms': len(rooms), 'walls': len(walls), 'previous_data': os.path.relpath(keep, DATA)}


class Handler(SimpleHTTPRequestHandler):
    server_version = 'Waypoint'

    def __init__(self, *a, **k):
        super().__init__(*a, directory=STATIC, **k)

    def log_message(self, fmt, *args):
        if self.path.startswith('/api/') and self.command != 'GET':
            super().log_message(fmt, *args)

    def end_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        if self.path.startswith('/api/'):
            self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self, limit):
        n = int(self.headers.get('Content-Length') or 0)
        if n <= 0 or n > limit:
            raise ValueError(f'body must be 1 byte to {limit // 1048576} MB')
        return self.rfile.read(n)

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/api/health':
            return self._json(200, {'ok': True, 'version': VERSION})
        if path == '/api/map':
            with lock:
                rd = read_json('rooms.json', {'rooms': []})
                return self._json(200, {'floors': read_json('floors.json', {'floors': []})['floors'],
                                        'rooms': rd['rooms'], 'walls': rd.get('walls', [])})
        if path == '/api/export':
            with lock:
                data = export_zip()
            self.send_response(200)
            self.send_header('Content-Type', 'application/zip')
            self.send_header('Content-Disposition', f'attachment; filename="waypoint-backup-{time.strftime("%Y%m%d-%H%M")}.zip"')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        if path.startswith('/files/plans/'):
            name = path[len('/files/plans/'):]
            full = os.path.join(PLANS, name)
            if not FILE_RE.match(name) or not os.path.isfile(full):
                return self._json(404, {'error': 'not found'})
            ext = name.rsplit('.', 1)[-1].lower()
            with open(full, 'rb') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', {'png': 'image/png', 'jpg': 'image/jpeg', 'webp': 'image/webp'}.get(ext, 'application/octet-stream'))
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'max-age=31536000, immutable')   # uploads get a new file name
            self.end_headers()
            return self.wfile.write(data)
        if path.startswith('/api/'):
            return self._json(404, {'error': 'not found'})
        super().do_GET()

    def do_PUT(self):
        path = self.path.split('?')[0]
        try:
            data = json.loads(self._body(MAX_JSON))
            if path == '/api/rooms':
                rooms = [clean_room(r) for r in data.get('rooms', [])]
                walls = [clean_wall(w) for w in data['walls']] if 'walls' in data else None
                with lock:
                    if walls is None:                      # older clients: keep the walls already saved
                        walls = read_json('rooms.json', {}).get('walls', [])
                    write_json('rooms.json', {'version': 1, 'rooms': rooms, 'walls': walls})
                return self._json(200, {'ok': True, 'count': len(rooms), 'walls': len(walls)})
            if path == '/api/floors':
                floors = [clean_floor(f) for f in data.get('floors', [])]
                if len({f['id'] for f in floors}) != len(floors):
                    raise ValueError('floor ids must be unique')
                with lock:
                    write_json('floors.json', {'floors': floors})
                return self._json(200, {'ok': True, 'count': len(floors)})
        except (ValueError, AttributeError, TypeError) as e:
            return self._json(400, {'error': str(e)})
        self._json(404, {'error': 'not found'})

    def do_POST(self):
        path = self.path.split('?')[0]
        if path == '/api/import':
            try:
                raw = self._body(MAX_IMPORT)
                with lock:
                    result = import_zip(raw)
            except (ValueError, KeyError, TypeError, AttributeError, json.JSONDecodeError) as e:
                return self._json(400, {'error': str(e)})
            return self._json(200, {'ok': True, **result})
        if not path.startswith('/api/plans/'):
            return self._json(404, {'error': 'not found'})
        fid = path[len('/api/plans/'):]
        try:
            if not ID_RE.match(fid):
                raise ValueError('bad floor id')
            data = self._body(MAX_UPLOAD)
            ext = next((e for magic, e in IMAGE_TYPES.items() if data.startswith(magic)), None)
            if not ext or (ext == 'webp' and data[8:12] != b'WEBP'):
                raise ValueError('plan must be a PNG, JPEG or WebP image')
        except ValueError as e:
            return self._json(400, {'error': str(e)})
        name = f'{fid}-{int(time.time() * 1000)}.{ext}'
        with lock:
            with open(os.path.join(PLANS, name), 'wb') as f:
                f.write(data)
        self._json(200, {'plan': f'plans/{name}'})


def main():
    # as PID 1 in a container SIGTERM is ignored unless handled, which makes `docker stop` hang for 10 s
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    os.makedirs(PLANS, exist_ok=True)
    seed_demo()
    host = os.environ.get('WAYPOINT_HOST', '0.0.0.0')
    port = int(os.environ.get('WAYPOINT_PORT', '8080'))
    print(f'Waypoint on http://{host}:{port}  (data: {DATA})', flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == '__main__':
    main()
