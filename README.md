# Waypoint

Waypoint is an interactive 3D office map for planning office improvements and documenting network equipment.

You upload a floor plan image for each floor, draw a box around each room, and give it a room number (required) and a name (optional). Waypoint stacks the floors in a 3D view you can rotate, and it saves everything on your own server.

- **Draw mode**: the floor plan is the background. Pick a type, then drag to draw a box, even on top of other boxes. Click a box to select it, then drag it to move it, or drag its edges or corners to resize it. Edges snap to nearby boxes (hold <kbd>Alt</kbd> to turn snapping off). Press <kbd>Esc</kbd> or right-click while dragging to cancel. A toolbar above the selected box changes its type or deletes it. Undo and redo are supported.
- **Box types**:
  - **Room**: needs a room number. The name is optional.
  - **Walkway**: corridors and paths. Walkway boxes that touch join into one connected path, in both the 2D and 3D views.
  - **Stair / Elevator**: drawn as shafts that reach up to the next floor in 3D.
  - **Empty**: an opening in the floor, such as an atrium or void. It cuts a hole in the floor plan, so the floor below shows through, in 2D and 3D.
- **3D view**: shows all floors stacked, or one floor on its own. Hover over a room to see its details. Double-click a room to edit it.
- **Floors…**: add, rename or delete floors, upload plan images, and set each floor's height and alignment.
- **Export / Import**: download the rooms as JSON, or load them back from a JSON file.

It runs as **one Docker container** (Python standard library plus static files, with no external services) on port **8080**, and stores all maps in **`/data`**.

> **Your data stays out of git.** Real floor plans, room data, `.env`, PDFs and backups are all git-ignored.
> The only map in this repository is the fictional office in [`demo/`](demo/README.md). Every part of it is labelled "DEMO".

## Quick start (any Docker host)

```bash
git clone https://github.com/Asura-T/waypoint.git
cd waypoint
cp .env.example .env          # set WAYPOINT_DATA_PATH to a folder that will hold your maps
mkdir -p data && sudo chown 568:568 data    # only if you keep WAYPOINT_DATA_PATH=./data
WAYPOINT_VERSION=$(git rev-parse --short HEAD) docker compose up -d --build
```

Open `http://<server>:8080`. On the first start Waypoint copies the demo office into the empty data folder. To start with an empty map instead, set `WAYPOINT_SEED_DEMO=false`. Remove the demo floors under **Floors…** once you have added your own.

**Installing on TrueNAS:** see **[docs/TRUENAS.md](docs/TRUENAS.md)**. It covers building the image, installing through the Custom App screen or with Docker Compose, updating, backing up and restoring.

## Configuration

| Variable (in `.env`)       | Default  | Meaning |
|----------------------------|----------|---------|
| `WAYPOINT_DATA_PATH`       | `./data` | Host folder (on TrueNAS, a dataset) mounted at `/data` |
| `WAYPOINT_PORT`            | `8080`   | Host port. The container always listens on 8080 |
| `WAYPOINT_UID` / `WAYPOINT_GID` | `568` | User the container runs as. 568 is TrueNAS's `apps` user |
| `WAYPOINT_SEED_DEMO`       | `true`   | Copy the demo map into an empty data folder on the first start |
| `WAYPOINT_MAX_UPLOAD_MB`   | `40`     | Largest floor plan image you can upload |
| `TZ`                       | `UTC`    | Time zone for logs |

## What is stored in `/data`

```
/data
├── floors.json        floors: name, height, position/size in metres, plan image
├── rooms.json         boxes: floor, type (room/walkway/stair/elevator/empty), number, name, box in metres
├── plans/             uploaded floor plan images
└── *.bak.json         the previous version of each file, rewritten on every save
```

Back up this whole folder, and nothing else. The image can always be rebuilt from git.

## Security note

Waypoint has **no login**. Anyone who can reach port 8080 can view and edit the maps. Keep it on your LAN or VPN. Do not forward the port to the internet.

## Floor plans from AutoCAD PDFs (optional)

If your plans are AutoCAD PDF exports with column-grid labels, `tools/render_plans.py` renders every floor onto one shared metre grid, so the floors line up in 3D:

```bash
pip install pymupdf
cp tools/render_plans.example.json /path/to/data/render_plans.json   # fill in, keep it out of git
python tools/render_plans.py /path/to/data/render_plans.json /path/to/pdfs /path/to/data
```

Otherwise, upload PNG, JPEG or WebP images under **Floors…** and enter each floor's real width in metres.

## Development

Python 3.10 or newer is enough. There is no build step.

```bash
WAYPOINT_DATA_DIR=./data WAYPOINT_PORT=8080 python3 app/server.py
```

- `app/server.py`: static file server and JSON API (`/api/map`, `/api/rooms`, `/api/floors`, `/api/plans/<floor>`)
- `app/static/index.html`: the whole user interface. Three.js is included under `app/static/vendor/three` (MIT), so the app works without internet access
- `demo/`: the fictional demo map, generated by `tools/make_demo.py`
