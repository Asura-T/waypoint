# Waypoint

Waypoint is an interactive 3D office map for planning office improvements and documenting network equipment.

You upload a floor plan image for each floor, draw a box around each room, and give it a room number (required) and a name (optional). Waypoint stacks the floors in a 3D view you can rotate, and it saves everything on your own server.

- **Draw mode**: the floor plan is the background. Pick a type, then drag to draw a box, even on top of other boxes. Click a box to select it, then drag it to move it, or drag its edges or corners to resize it. Edges snap to nearby boxes (hold <kbd>Alt</kbd> to turn snapping off). Press <kbd>Esc</kbd> or right-click while dragging to cancel. A toolbar above the selected box changes its type or deletes it. Undo and redo are supported.
- **Box types**:
  - **Room**: needs a room number. The name is optional.
  - **Bathroom**: toilets and washrooms. The number is optional.
  - **Walkway**: corridors and paths. Walkway boxes that touch join into one connected path, in both the 2D and 3D views.
  - **Stair / Elevator**: drawn as shafts that reach up to the next floor in 3D.
  - **Empty**: an opening in the floor, such as an atrium or void. It cuts a hole in the floor plan, so the floor below shows through, in 2D and 3D.
- **Walls** (the **▬ Wall** tool, or <kbd>W</kbd>): drag from one end of a wall to the other. Walls stay straight unless you hold <kbd>Shift</kbd>. Their ends snap onto other walls' ends and box corners, so walls join up. With the Wall tool, start the next wall on the end of the last one to keep drawing joined walls. To move a wall or one of its ends, switch to the Box tool, click the wall, then drag it or its round end handles. Set its thickness in the side panel, or delete it. In 3D, walls stand as tall as the rooms.
- **3D view**: shows all floors stacked, or one floor on its own. Hover over a room or wall to see its details. Double-click it to edit it.
- **Floors…**: add, rename or delete floors, upload plan images, and set each floor's height and alignment.
- **Export ▾**:
  - **Full backup (.zip)**: floors, plan images, rooms and walls. Restore it with **Import**.
  - **Rooms & walls (.json)**: without the plan images.
  - **2D floor image (.png)**: the current floor at the plan's full resolution.
  - **3D view image (.png)**: exactly what the 3D camera shows.
  - **3D model (.glb)**: the floors shown in the 3D view (pick **All** for the whole building). Opens in Blender, the Windows 3D Viewer or online glTF viewers. Labels and the floor holes cut by Empty boxes are not included, because glTF can't store them.
- **Import**: accepts a full backup (.zip), which replaces everything after copying the current data to `/data/backups/`, or a rooms & walls file (.json).

It runs as **one Docker container** (Python standard library plus static files, with no external services) on port **8080**, and stores all maps in **`/data`**.

> **Your data stays out of git.** Real floor plans, room data, `.env`, PDFs and backups are all git-ignored.
> The only map in this repository is the fictional office in [`demo/`](demo/README.md). Every part of it is labelled "DEMO".

## Docker images

GitHub Actions ([`.github/workflows/docker-image.yml`](.github/workflows/docker-image.yml)) builds the image for `linux/amd64` and publishes it to **`ghcr.io/asura-t/waypoint`**:

| Event | Tags published |
|---|---|
| Push to `main` | `latest`, `sha-<commit>` |
| Tag `v1.2.3` | `1.2.3`, `1.2`, `sha-<commit>` |
| Pull request | builds the image to check it, **publishes nothing** |

The commit is built into the image as `WAYPOINT_VERSION` and shown next to "Waypoint" in the page header. Publishing is automatic. **Deploying is not**: your server only changes when you pull a new tag and re-create the container.

The package is private unless you change its visibility on GitHub. Its visibility is separate from the repository's. To pull a private image you need a *classic* token with `read:packages`; see [docs/TRUENAS.md](docs/TRUENAS.md#2-private-image-access).

## Quick start (any Docker host)

**Published image** (no build):

```bash
mkdir -p data && sudo chown 568:568 data
docker login ghcr.io -u Asura-T            # only while the package is private
docker run -d --name waypoint --restart unless-stopped -p 8080:8080 \
  -e TZ=Asia/Phnom_Penh -v "$PWD/data:/data" ghcr.io/asura-t/waypoint:latest
```

Or use [`docker-compose.ghcr.yml`](docker-compose.ghcr.yml) with `WAYPOINT_IMAGE_TAG` set in `.env`.

**Build it yourself** from a checkout:

```bash
git clone https://github.com/Asura-T/waypoint.git
cd waypoint
cp .env.example .env          # set WAYPOINT_DATA_PATH to a folder that will hold your maps
mkdir -p data && sudo chown 568:568 data    # only if you keep WAYPOINT_DATA_PATH=./data
WAYPOINT_VERSION=$(git rev-parse --short HEAD) docker compose up -d --build
```

Open `http://<server>:8080`. On the first start Waypoint copies the demo office into the empty data folder. To start with an empty map instead, set `WAYPOINT_SEED_DEMO=false`. Remove the demo floors under **Floors…** once you have added your own.

**Installing on TrueNAS:** see **[docs/TRUENAS.md](docs/TRUENAS.md)**. It covers:
- installing the published image with [`docs/truenas-custom-app.yaml`](docs/truenas-custom-app.yaml), or building the image on the NAS
- access to a private image
- updating to a new tag, and rolling back to an older one
- backing up and restoring

## Releasing a version

```bash
git tag v1.2.3 && git push origin v1.2.3     # publishes :1.2.3 and :1.2
```

## Configuration

| Variable (in `.env`)       | Default  | Meaning |
|----------------------------|----------|---------|
| `WAYPOINT_DATA_PATH`       | `./data` | Host folder (on TrueNAS, a dataset) mounted at `/data` |
| `WAYPOINT_PORT`            | `8080`   | Host port. The container always listens on 8080 |
| `WAYPOINT_UID` / `WAYPOINT_GID` | `568` | User the container runs as. 568 is TrueNAS's `apps` user |
| `WAYPOINT_SEED_DEMO`       | `true`   | Copy the demo map into an empty data folder on the first start |
| `WAYPOINT_MAX_UPLOAD_MB`   | `40`     | Largest floor plan image you can upload |
| `WAYPOINT_IMAGE_TAG`       | `latest` | Image tag used by `docker-compose.ghcr.yml` |
| `TZ`                       | `Asia/Phnom_Penh` | Time zone for log timestamps |

## What is stored in `/data`

```
/data
├── floors.json        floors: name, height, position/size in metres, plan image
├── rooms.json         boxes: floor, type (room/bathroom/walkway/stair/elevator/empty), number, name, box in metres
│                      walls: floor, start/end point and thickness in metres
├── backups/           copies of the data taken automatically before a backup is imported
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
