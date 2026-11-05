"""Waypoint server: serves the web app and stores office maps in DATA_DIR.

Standard library only. Configuration (environment variables):
  WAYPOINT_DATA_DIR   where maps are stored            (default: /data)
  WAYPOINT_HOST       interface to listen on           (default: 0.0.0.0)
  WAYPOINT_PORT       port to listen on                (default: 8080)
  WAYPOINT_SEED_DEMO  add the fictional demo map when there are no maps yet (default: true)
  WAYPOINT_MAX_UPLOAD_MB  largest accepted floor plan image (default: 40)
  WAYPOINT_MAX_IMPORT_MB  largest accepted backup .zip     (default: 300)

Data layout in DATA_DIR (one folder per map):
  maps/<mapId>/map.json     {id, name, created, updated, demo?}
  maps/<mapId>/floors.json  {"floors": [{id, name, elev, extent:[x0,y0,x1,y1], plan, demo?}]}
  maps/<mapId>/rooms.json   {"version": 1, "rooms": [{id, floor, type, no, name, x0, y0, x1, y1, pts?}],
                            pts = corner points [[x, y], ...] for shapes drawn corner by corner (x0..y1 = their bounds)
                             "walls": [{id, floor, x0, y0, x1, y1, t}]}
                            type: room | bathroom | walkway | garden | balcony | stair | elevator | empty (opening to the floor below)
                            walkways, gardens and balconies that touch form one connected outdoor/circulation area
  maps/<mapId>/plans/       floor plan images referenced by floors.json
  maps/<mapId>/*.bak.json   previous version of each file, written before every save
  backups/                  copies taken automatically before an import replaces a map, and deleted maps

A data folder from Waypoint 1.x (floors.json/rooms.json/plans directly in DATA_DIR) is moved into
maps/main/ on the first start; nothing is deleted.

API (all JSON unless noted):
  GET    /api/health                        liveness check + version
  GET    /api/maps                          list of maps with counts
  POST   /api/maps                          create {name} -> {id}
  POST   /api/maps/import?name=...          new map from a backup .zip (raw body)
  GET    /api/maps/<id>                     {map, floors, rooms, walls}
  PUT    /api/maps/<id>                     rename {name}
  DELETE /api/maps/<id>                     move the map to backups/ (recoverable)
  POST   /api/maps/<id>/duplicate           copy {name} -> {id}
  PUT    /api/maps/<id>/rooms               replace rooms (and walls, when "walls" is sent)
  PUT    /api/maps/<id>/floors              replace the floor list
  POST   /api/maps/<id>/plans/<floorId>     upload a plan image (raw PNG/JPEG/WebP) -> {"plan": "plans/<file>"}
  GET    /api/maps/<id>/export              the whole map as one .zip
  POST   /api/maps/<id>/import              replace this map with a backup .zip (old version kept in backups/)
  GET    /files/<mapId>/plans/<file>        plan image
"""
import io, json, os, re, shutil, signal, sys, threading, time, zipfile
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(APP_DIR, 'static')
DEMO = os.path.join(os.path.dirname(APP_DIR), 'demo')
DATA = os.path.abspath(os.environ.get('WAYPOINT_DATA_DIR', '/data'))
MAPS = os.path.join(DATA, 'maps')
BACKUPS = os.path.join(DATA, 'backups')
MAX_UPLOAD = int(float(os.environ.get('WAYPOINT_MAX_UPLOAD_MB', '40')) * 1024 * 1024)
MAX_IMPORT = int(float(os.environ.get('WAYPOINT_MAX_IMPORT_MB', '300')) * 1024 * 1024)
MAX_JSON = 10 * 1024 * 1024
ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$')
FILE_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$')
ROOM_TYPES = ('room', 'bathroom', 'walkway', 'garden', 'balcony', 'stair', 'elevator', 'empty')
IMAGE_TYPES = {b'\x89PNG': 'png', b'\xff\xd8\xff': 'jpg', b'RIFF': 'webp'}
VERSION = os.environ.get('WAYPOINT_VERSION', 'dev')
lock = threading.Lock()


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%S%z')


def map_dir(mid):
    if not ID_RE.match(mid or ''):
        raise ValueError('bad map id')
    return os.path.join(MAPS, mid)


def read_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def write_json(path, obj):
    """Atomic write that keeps the previous version as <name>.bak.json."""
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    if os.path.exists(path):
        shutil.copy2(path, path.replace('.json', '.bak.json'))
    os.replace(tmp, path)


def touch(mid):
    p = os.path.join(map_dir(mid), 'map.json')
    meta = read_json(p, {'id': mid, 'name': mid, 'created': now()})
    meta['updated'] = now()
    write_json(p, meta)


def new_map_id(name):
    base = re.sub(r'[^A-Za-z0-9]+', '-', name).strip('-').lower()[:24] or 'map'
    mid, n = base, 2
    while os.path.exists(os.path.join(MAPS, mid)):
        mid, n = f'{base}-{n}', n + 1
    return mid


def create_map(name, demo=False):
    mid = new_map_id(name)
    d = os.path.join(MAPS, mid)
    os.makedirs(os.path.join(d, 'plans'))
    meta = {'id': mid, 'name': name[:80] or 'Untitled map', 'created': now(), 'updated': now()}
    if demo:
        meta['demo'] = True
    write_json(os.path.join(d, 'map.json'), meta)
    write_json(os.path.join(d, 'floors.json'), {'floors': []})
    write_json(os.path.join(d, 'rooms.json'), {'version': 1, 'rooms': [], 'walls': []})
    return mid


def migrate_and_seed():
    """Move a 1.x single-map data folder into maps/main, then add the demo map if there are no maps."""
    os.makedirs(MAPS, exist_ok=True)
    old = [n for n in ('floors.json', 'rooms.json', 'floors.bak.json', 'rooms.bak.json', 'plans') if os.path.exists(os.path.join(DATA, n))]
    if 'floors.json' in old and not os.path.exists(os.path.join(MAPS, 'main')):
        d = os.path.join(MAPS, 'main')
        os.makedirs(d)
        for n in old:
            shutil.move(os.path.join(DATA, n), os.path.join(d, n))
        os.makedirs(os.path.join(d, 'plans'), exist_ok=True)
        floors = read_json(os.path.join(d, 'floors.json'), {'floors': []})['floors']
        meta = {'id': 'main', 'name': 'My map', 'created': now(), 'updated': now()}
        if floors and all(f.get('demo') for f in floors):
            meta.update(name='Demo office (fictional)', demo=True)
        write_json(os.path.join(d, 'map.json'), meta)
        print('Moved the existing map into', d, flush=True)
    if os.listdir(MAPS):
        return
    if os.environ.get('WAYPOINT_SEED_DEMO', 'true').lower() in ('0', 'false', 'no'):
        return
    mid = create_map('Demo office (fictional)', demo=True)
    d = os.path.join(MAPS, mid)
    shutil.copytree(os.path.join(DEMO, 'plans'), os.path.join(d, 'plans'), dirs_exist_ok=True)
    for name in ('floors.json', 'rooms.json'):
        shutil.copy2(os.path.join(DEMO, name), os.path.join(d, name))
    print('Added the demo map', mid, flush=True)


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
    if not isinstance(r, dict):
        raise ValueError('room must be an object')
    pts = r.get('pts')
    if pts is not None:                    # shape drawn corner by corner; x0..y1 become its bounding box
        if not (isinstance(pts, list) and 3 <= len(pts) <= 500 and all(isinstance(p, list) and len(p) == 2 and num(p[0]) and num(p[1]) for p in pts)):
            raise ValueError('room pts must be 3-500 [x, y] points')
        pts = [[float(x), float(y)] for x, y in pts]
        r = {**r, 'x0': min(p[0] for p in pts), 'y0': min(p[1] for p in pts), 'x1': max(p[0] for p in pts), 'y1': max(p[1] for p in pts)}
    if not all(num(r.get(k)) for k in ('x0', 'y0', 'x1', 'y1')):
        raise ValueError('room needs numeric x0, y0, x1, y1')
    kind = r.get('type') or 'room'
    if kind not in ROOM_TYPES:
        raise ValueError(f'room type must be one of {", ".join(ROOM_TYPES)}')
    out = {'id': str(r.get('id', ''))[:40], 'floor': str(r.get('floor', ''))[:40], 'type': kind,
           'no': str(r.get('no', ''))[:40], 'name': str(r.get('name', ''))[:120],
           **{k: float(r[k]) for k in ('x0', 'y0', 'x1', 'y1')}}
    if pts is not None:
        out['pts'] = pts
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


def map_summary(mid):
    d = os.path.join(MAPS, mid)
    meta = read_json(os.path.join(d, 'map.json'), {'id': mid, 'name': mid})
    floors = read_json(os.path.join(d, 'floors.json'), {'floors': []})['floors']
    rd = read_json(os.path.join(d, 'rooms.json'), {'rooms': []})
    return {**meta, 'id': mid, 'floors': len(floors), 'rooms': len(rd.get('rooms', [])), 'walls': len(rd.get('walls', [])),
            'thumb': next((f'files/{mid}/{f["plan"]}' for f in floors if f.get('plan')), '')}


def export_zip(mid):
    """Everything needed to rebuild one map, as a zip held in memory."""
    d, buf = map_dir(mid), io.BytesIO()
    meta = read_json(os.path.join(d, 'map.json'), {})
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('waypoint-backup.json', json.dumps({'app': 'waypoint', 'format': 2, 'version': VERSION,
                                                       'name': meta.get('name', mid), 'exported_at': now()}))
        for name in ('floors.json', 'rooms.json'):
            if os.path.exists(os.path.join(d, name)):
                z.write(os.path.join(d, name), name)
        for f in sorted(os.listdir(os.path.join(d, 'plans'))):
            if FILE_RE.match(f) and os.path.isfile(os.path.join(d, 'plans', f)):
                z.write(os.path.join(d, 'plans', f), 'plans/' + f, compress_type=zipfile.ZIP_STORED)
    return buf.getvalue(), meta.get('name', mid)


def read_backup(raw):
    """Validate a backup zip completely; returns (floors, rooms, walls, plans{name: bytes}, name)."""
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
    info = json.loads(z.read('waypoint-backup.json')) if 'waypoint-backup.json' in names else {}
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
    return floors, rooms, walls, plans, str(info.get('name') or 'Imported map')


def write_map_contents(mid, floors, rooms, walls, plans):
    d = map_dir(mid)
    os.makedirs(os.path.join(d, 'plans'), exist_ok=True)
    for f, data in plans.items():
        with open(os.path.join(d, 'plans', f), 'wb') as fh:
            fh.write(data)
    write_json(os.path.join(d, 'floors.json'), {'floors': floors})
    write_json(os.path.join(d, 'rooms.json'), {'version': 1, 'rooms': rooms, 'walls': walls})
    touch(mid)


def stash(mid, why):
    """Move a whole map folder into backups/ (used before replacing or deleting it)."""
    os.makedirs(BACKUPS, exist_ok=True)
    dest = os.path.join(BACKUPS, f'{why}-{mid}-{time.strftime("%Y%m%d-%H%M%S")}')
    shutil.move(map_dir(mid), dest)
    return os.path.relpath(dest, DATA)


class Handler(SimpleHTTPRequestHandler):
    server_version = 'Waypoint'
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, '.mjs': 'text/javascript', '.js': 'text/javascript'}

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

    def _bytes(self, data, ctype, filename=None, cache=None):
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        if filename:
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        if cache:
            self.send_header('Cache-Control', cache)
        self.end_headers()
        self.wfile.write(data)

    def _body(self, limit):
        n = int(self.headers.get('Content-Length') or 0)
        if n <= 0 or n > limit:
            raise ValueError(f'body must be 1 byte to {limit // 1048576} MB')
        return self.rfile.read(n)

    def _route(self):
        u = urlparse(self.path)
        return [p for p in u.path.split('/') if p], parse_qs(u.query)

    def _existing(self, mid):
        d = map_dir(mid)
        if not os.path.isdir(d):
            raise LookupError('map not found')
        return d

    def _handle(self, fn):
        try:
            return fn()
        except LookupError as e:
            return self._json(404, {'error': str(e)})
        except (ValueError, KeyError, TypeError, AttributeError, json.JSONDecodeError) as e:
            return self._json(400, {'error': str(e)})

    # ---------- GET ----------
    def do_GET(self):
        parts, _ = self._route()
        if parts[:1] == ['files'] and len(parts) == 4 and parts[2] == 'plans':
            mid, name = parts[1], parts[3]
            if not ID_RE.match(mid) or not FILE_RE.match(name):
                return self._json(404, {'error': 'not found'})
            full = os.path.join(MAPS, mid, 'plans', name)
            if not os.path.isfile(full):
                return self._json(404, {'error': 'not found'})
            with open(full, 'rb') as f:
                data = f.read()
            ext = name.rsplit('.', 1)[-1].lower()
            return self._bytes(data, {'png': 'image/png', 'jpg': 'image/jpeg', 'webp': 'image/webp'}.get(ext, 'application/octet-stream'),
                               cache='max-age=31536000, immutable')        # uploads get a new file name
        if parts[:1] != ['api']:
            return super().do_GET()
        self._handle(lambda: self._get_api(parts[1:]))

    def _get_api(self, p):
        if p == ['health']:
            return self._json(200, {'ok': True, 'version': VERSION})
        if p == ['maps']:
            with lock:
                maps = [map_summary(m) for m in sorted(os.listdir(MAPS)) if ID_RE.match(m) and os.path.isdir(os.path.join(MAPS, m))]
            maps.sort(key=lambda m: m.get('updated', ''), reverse=True)
            return self._json(200, {'maps': maps})
        if len(p) == 2 and p[0] == 'maps':
            with lock:
                d = self._existing(p[1])
                rd = read_json(os.path.join(d, 'rooms.json'), {'rooms': []})
                return self._json(200, {'map': read_json(os.path.join(d, 'map.json'), {'id': p[1], 'name': p[1]}),
                                        'floors': read_json(os.path.join(d, 'floors.json'), {'floors': []})['floors'],
                                        'rooms': rd['rooms'], 'walls': rd.get('walls', [])})
        if len(p) == 3 and p[0] == 'maps' and p[2] == 'export':
            with lock:
                self._existing(p[1])
                data, name = export_zip(p[1])
            safe = re.sub(r'[^A-Za-z0-9_-]+', '-', name).strip('-') or p[1]
            return self._bytes(data, 'application/zip', filename=f'waypoint-{safe}-{time.strftime("%Y%m%d-%H%M")}.zip')
        raise LookupError('not found')

    # ---------- PUT ----------
    def do_PUT(self):
        parts, _ = self._route()
        self._handle(lambda: self._put_api(parts[1:]) if parts[:1] == ['api'] else self._json(404, {'error': 'not found'}))

    def _put_api(self, p):
        data = json.loads(self._body(MAX_JSON))
        if len(p) == 2 and p[0] == 'maps':
            name = str(data.get('name') or '').strip()[:80]
            if not name:
                raise ValueError('name is required')
            with lock:
                d = self._existing(p[1])
                meta = read_json(os.path.join(d, 'map.json'), {'id': p[1]})
                meta.update(name=name, updated=now())
                meta.pop('demo', None)
                write_json(os.path.join(d, 'map.json'), meta)
            return self._json(200, {'ok': True})
        if len(p) == 3 and p[0] == 'maps' and p[2] == 'rooms':
            rooms = [clean_room(r) for r in data.get('rooms', [])]
            walls = [clean_wall(w) for w in data['walls']] if 'walls' in data else None
            with lock:
                d = self._existing(p[1])
                if walls is None:                      # older clients: keep the walls already saved
                    walls = read_json(os.path.join(d, 'rooms.json'), {}).get('walls', [])
                write_json(os.path.join(d, 'rooms.json'), {'version': 1, 'rooms': rooms, 'walls': walls})
                touch(p[1])
            return self._json(200, {'ok': True, 'count': len(rooms), 'walls': len(walls)})
        if len(p) == 3 and p[0] == 'maps' and p[2] == 'floors':
            floors = [clean_floor(f) for f in data.get('floors', [])]
            if len({f['id'] for f in floors}) != len(floors):
                raise ValueError('floor ids must be unique')
            with lock:
                d = self._existing(p[1])
                write_json(os.path.join(d, 'floors.json'), {'floors': floors})
                touch(p[1])
            return self._json(200, {'ok': True, 'count': len(floors)})
        raise LookupError('not found')

    # ---------- POST ----------
    def do_POST(self):
        parts, q = self._route()
        self._handle(lambda: self._post_api(parts[1:], q) if parts[:1] == ['api'] else self._json(404, {'error': 'not found'}))

    def _post_api(self, p, q):
        if p == ['maps']:
            name = str(json.loads(self._body(MAX_JSON)).get('name') or '').strip()[:80]
            if not name:
                raise ValueError('name is required')
            with lock:
                mid = create_map(name)
            return self._json(200, {'ok': True, 'id': mid})
        if p == ['maps', 'import']:
            floors, rooms, walls, plans, name = read_backup(self._body(MAX_IMPORT))
            name = (q.get('name') or [name])[0].strip()[:80] or name
            with lock:
                mid = create_map(name)
                write_map_contents(mid, floors, rooms, walls, plans)
            return self._json(200, {'ok': True, 'id': mid, 'floors': len(floors), 'rooms': len(rooms), 'walls': len(walls)})
        if len(p) == 3 and p[0] == 'maps' and p[2] == 'duplicate':
            name = str(json.loads(self._body(MAX_JSON)).get('name') or '').strip()[:80]
            with lock:
                src = self._existing(p[1])
                meta = read_json(os.path.join(src, 'map.json'), {'name': p[1]})
                mid = new_map_id(name or meta['name'] + ' copy')
                shutil.copytree(src, os.path.join(MAPS, mid), ignore=shutil.ignore_patterns('*.bak.json', '*.tmp'))
                meta.update(id=mid, name=name or meta.get('name', p[1]) + ' (copy)', created=now(), updated=now())
                meta.pop('demo', None)
                write_json(os.path.join(MAPS, mid, 'map.json'), meta)
            return self._json(200, {'ok': True, 'id': mid})
        if len(p) == 3 and p[0] == 'maps' and p[2] == 'import':
            floors, rooms, walls, plans, _ = read_backup(self._body(MAX_IMPORT))
            with lock:
                d = self._existing(p[1])
                meta = read_json(os.path.join(d, 'map.json'), {'id': p[1], 'name': p[1]})
                kept = stash(p[1], 'pre-import')
                os.makedirs(d)
                write_json(os.path.join(d, 'map.json'), meta)
                write_map_contents(p[1], floors, rooms, walls, plans)
            return self._json(200, {'ok': True, 'floors': len(floors), 'rooms': len(rooms), 'walls': len(walls), 'previous_data': kept})
        if len(p) == 4 and p[0] == 'maps' and p[2] == 'plans':
            mid, fid = p[1], p[3]
            if not ID_RE.match(fid):
                raise ValueError('bad floor id')
            data = self._body(MAX_UPLOAD)
            ext = next((e for magic, e in IMAGE_TYPES.items() if data.startswith(magic)), None)
            if not ext or (ext == 'webp' and data[8:12] != b'WEBP'):
                raise ValueError('plan must be a PNG, JPEG or WebP image (PDFs are converted in the browser)')
            name = f'{fid}-{int(time.time() * 1000)}.{ext}'
            with lock:
                d = self._existing(mid)
                with open(os.path.join(d, 'plans', name), 'wb') as f:
                    f.write(data)
            return self._json(200, {'plan': f'plans/{name}'})
        raise LookupError('not found')

    # ---------- DELETE ----------
    def do_DELETE(self):
        parts, _ = self._route()
        def go():
            p = parts[1:]
            if parts[:1] != ['api'] or len(p) != 2 or p[0] != 'maps':
                raise LookupError('not found')
            with lock:
                self._existing(p[1])
                kept = stash(p[1], 'deleted')
            return self._json(200, {'ok': True, 'kept_in': kept})
        self._handle(go)


def main():
    # as PID 1 in a container SIGTERM is ignored unless handled, which makes `docker stop` hang for 10 s
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    migrate_and_seed()
    host = os.environ.get('WAYPOINT_HOST', '0.0.0.0')
    port = int(os.environ.get('WAYPOINT_PORT', '8080'))
    print(f'Waypoint on http://{host}:{port}  (data: {DATA})', flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == '__main__':
    main()
