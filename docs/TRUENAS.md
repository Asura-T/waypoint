# Installing Waypoint on TrueNAS

## Supported versions

| TrueNAS version | Status |
|---|---|
| **TrueNAS Community Edition 25.04 "Fangtooth"** and **25.10 "Goldeye"** | Supported. These instructions were written for them |
| **TrueNAS SCALE 24.10 "Electric Eel"** | Should work the same way. It was the first release to run apps on Docker. Some menu names may differ slightly |
| TrueNAS SCALE 24.04 "Dragonfish" and older | **Not supported.** Those releases run apps on Kubernetes |
| TrueNAS CORE (FreeBSD) | **Not supported.** It has no Docker |

> These steps have not been tested on a live TrueNAS system. The Docker build and the container itself were tested on Linux.
> If a TrueNAS screen looks different from what is described here, follow what your TrueNAS version shows.

**How updates reach TrueNAS:** pushing to GitHub does **not** update your server. TrueNAS never pulls from GitHub on its own, and the image is not published to any registry. Every update is a manual pull and rebuild on the NAS (see [Updating](#updating)).

## Overview

```
GitHub repo ──git clone/pull──▶ /mnt/<pool>/apps/waypoint/src   (source code)
                                    │ docker build
                                    ▼
                              image waypoint:local  (exists only on this NAS)
                                    │ run (Custom App or docker compose)
                                    ▼
                              container :8080 ──/data──▶ /mnt/<pool>/apps/waypoint/data  (your maps)
```

In these steps, replace `tank` with your pool name.

## 1. Create the datasets

In **Datasets**, create:

- `tank/apps/waypoint/src`: the git checkout. Any dataset works.
- `tank/apps/waypoint/data`: your maps. This is the dataset to snapshot and back up.

Give the data dataset to the `apps` user (UID/GID 568). Either use **Datasets → data → Edit Permissions** (owner `apps`, group `apps`), or run this in the shell:

```bash
sudo chown -R 568:568 /mnt/tank/apps/waypoint/data
```

Keep the source code on a dataset. The TrueNAS system disk is replaced on every OS update.

## 2. Get the code onto the NAS

Open **System → Shell**, or connect over SSH as an admin user. Docker commands need `sudo`.

```bash
cd /mnt/tank/apps/waypoint
sudo git clone https://github.com/<your-user>/waypoint.git src
```

- **Private repository:** create a GitHub *fine-grained personal access token* with read-only "Contents" access to this one repo. Use it as the password when git asks. You can also add an SSH deploy key to the repo.
- **If `git` is not available** on your TrueNAS, run it from a throw-away container:
  ```bash
  sudo docker run --rm -it -v /mnt/tank/apps/waypoint:/w -w /w alpine/git clone https://github.com/<your-user>/waypoint.git src
  ```
  For later updates, use the same command with `-w /w/src` and `pull` in place of `clone …`.

## 3. Build the image

```bash
cd /mnt/tank/apps/waypoint/src
sudo docker build --build-arg WAYPOINT_VERSION=$(git rev-parse --short HEAD) -t waypoint:local .
```

The image exists only on this NAS. It is stored in the apps pool, so it survives reboots and TrueNAS updates. If it ever goes missing, run this command again.

Then pick **one** of the two install methods below. Do not run both at the same time, because they would both use port 8080.

## 4a. Install through the TrueNAS Custom App screen (recommended)

With this method, TrueNAS shows, starts, stops and monitors the app on the **Apps** page.

**Option 1: the form.** Go to **Apps → Discover Apps → Custom App** and fill in:

| Field | Value |
|---|---|
| Application name | `waypoint` |
| Image repository / tag | `waypoint` / `local` |
| Pull policy | **Never pull** (or "Only pull if not present"). The image was built locally, and there is nothing to pull |
| Container user / group (if shown) | `568` / `568` |
| Environment | `WAYPOINT_SEED_DEMO=true` (optional) |
| Port forwarding | container port `8080` → host port `8080`, TCP |
| Storage | Host path `/mnt/tank/apps/waypoint/data` → mount path `/data` |
| Restart policy | Unless stopped |

**Option 2: YAML.** Go to **Apps → Discover Apps → ⋮ → Install via YAML**, and paste the contents of [`truenas-custom-app.yaml`](truenas-custom-app.yaml) after editing the host path.

The Custom App screen **cannot build images**. It only runs an image that already exists. That is why step 3 builds `waypoint:local` first, and why the pull policy must not be "Always pull".

## 4b. Install with Docker Compose from the shell

This method uses the `docker-compose.yml` in the repo, and the build and run happen in one command:

```bash
cd /mnt/tank/apps/waypoint/src
sudo cp .env.example .env          # first time only
sudo nano .env                     # set WAYPOINT_DATA_PATH=/mnt/tank/apps/waypoint/data
sudo WAYPOINT_VERSION=$(git rev-parse --short HEAD) docker compose up -d --build
```

### How the two methods differ

| | Custom App screen | Docker Compose in the shell |
|---|---|---|
| Builds the image | No. Build it first (step 3) | Yes (`--build`) |
| Visible on the Apps page, with start/stop/logs in the UI | Yes | Usually not, or only as an unmanaged/external container. Manage it with `docker` commands |
| Where settings live | In TrueNAS's app configuration | `.env` + `docker-compose.yml` in the source dataset |
| Starts after a reboot | Yes, TrueNAS starts it | Yes, Docker restarts it (`restart: unless-stopped`) |
| Updating | Rebuild the image, then **Stop → Start** the app | `git pull` + `docker compose up -d --build` |
| Risk on TrueNAS upgrades | Low. Standard TrueNAS app | Containers TrueNAS doesn't manage are not officially supported and may need re-running after major upgrades |

Both methods mount the **same** data dataset at `/data`, so you can switch from one to the other later without losing maps. Stop the old one before starting the new one.

## 5. Open it

Go to `http://<truenas-ip>:8080`. The header shows the version (the git commit) next to "Waypoint". Waypoint has no login, so keep it on your LAN or VPN.

## Updating

Pushing to GitHub does **not** update the NAS. To update by hand:

```bash
cd /mnt/tank/apps/waypoint/src
sudo git pull
sudo docker build --build-arg WAYPOINT_VERSION=$(git rev-parse --short HEAD) -t waypoint:local .
```

Then recreate the container:

- **Custom App:** on **Apps → waypoint**, click **Stop**, then **Start**. If the version in the page header has not changed, use **Edit → Save** to force TrueNAS to recreate the container.
- **Compose:** `sudo WAYPOINT_VERSION=$(git rev-parse --short HEAD) docker compose up -d --build`

Your maps in `/data` are untouched by updates. Take a snapshot first anyway (see below). Old image layers can be removed with `sudo docker image prune`.

## Backing up

All state is in the data dataset (`floors.json`, `rooms.json`, `plans/`). Pick any of these:

1. **ZFS snapshots (recommended):** **Data Protection → Periodic Snapshot Tasks → Add**, dataset `tank/apps/waypoint/data`, for example daily with 30-day retention. Add a **Replication** or **Cloud Sync** task to keep a copy off the NAS.
2. **Manual snapshot** before an update: `sudo zfs snapshot tank/apps/waypoint/data@before-update-$(date +%F)`
3. **Archive file:**
   ```bash
   sudo tar czf /mnt/tank/backups/waypoint-$(date +%F).tar.gz -C /mnt/tank/apps/waypoint data
   ```
4. **Export** in the app downloads the rooms as JSON. It does **not** include the plan images, so use it as an extra copy, not a full backup.

Each save also writes the previous version to `rooms.bak.json` / `floors.bak.json`. This only lets you undo the last save.

## Restoring

1. **Stop** the app (Custom App: **Stop**; Compose: `sudo docker compose stop`).
2. Restore the data, using one of:
   - **Snapshot:** **Datasets → data → Snapshots → Rollback**, or `sudo zfs rollback tank/apps/waypoint/data@<snapshot>`. Rolling back discards everything saved after that snapshot.
   - **Archive:**
     ```bash
     cd /mnt/tank/apps/waypoint
     sudo mv data data.old && sudo mkdir data
     sudo tar xzf /mnt/tank/backups/waypoint-YYYY-MM-DD.tar.gz -C /mnt/tank/apps/waypoint
     ```
   - **Only the last save:** `sudo cp data/rooms.bak.json data/rooms.json`
3. Fix ownership: `sudo chown -R 568:568 /mnt/tank/apps/waypoint/data`
4. **Start** the app again and reload the page.

## Troubleshooting

- **The page says "Save failed" or "Cannot reach the server":** the container is not writing to `/data`. Check the ownership in step 1, and check the logs (Apps → waypoint → Logs, or `sudo docker logs waypoint`).
- **TrueNAS reports "image not found" or "pull access denied":** the pull policy is trying to pull `waypoint:local` from a registry. Set it to **Never pull**, and make sure step 3 has been run on this NAS (`sudo docker images | grep waypoint`).
- **Port 8080 is already in use:** change the *host* port, for example to 8090. The container port stays 8080.
- **You see the demo office instead of your own map:** the container is pointing at an empty data folder. Check the host path of the `/data` mount.
