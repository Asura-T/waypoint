# Installing Waypoint on TrueNAS

## Supported versions

| TrueNAS version | Status |
|---|---|
| **TrueNAS Community Edition 25.04 "Fangtooth"** and **25.10 "Goldeye"** | Supported. These instructions were written for them |
| **TrueNAS SCALE 24.10 "Electric Eel"** | Should work the same way. It was the first release to run apps on Docker. Some menu names may differ slightly |
| TrueNAS SCALE 24.04 "Dragonfish" and older | **Not supported.** Those releases run apps on Kubernetes |
| TrueNAS CORE (FreeBSD) | **Not supported.** It has no Docker |

> These steps have not been tested on a live TrueNAS system. The image itself was built and tested with Podman on Linux.
> If a TrueNAS screen looks different from what is described here, follow what your TrueNAS version shows.

## How images get to your NAS

```
git push to main / tag v1.2.3
        │  GitHub Actions (.github/workflows/docker-image.yml)
        ▼
ghcr.io/asura-t/waypoint  :latest  :sha-1a2b3c4  :1.2.3
        │  you, on the NAS: docker pull + point the app at the tag   ← manual, on purpose
        ▼
TrueNAS app "waypoint"  :8080 ──/data──▶ /mnt/<POOL>/apps/waypoint/data
```

- **Every push to `main`** publishes `latest` and a commit tag `sha-<7 characters>`.
- **Every version tag** (`git tag v1.2.3 && git push origin v1.2.3`) publishes `1.2.3`, `1.2` and the commit tag.
- **Pull requests** are built to check them, but nothing is published.
- **Deployment is manual.** The NAS never updates itself. You choose when to pull a new image and switch the app to it (see [Updating](#updating)).

The version shown next to "Waypoint" in the page header is the commit, or `tag (commit)` for releases. Use it to confirm which image is running.

In this guide, **`<POOL>`** is your pool name (for example `tank`), and **`<IMAGE_TAG>`** is the image tag you run (for example `sha-1a2b3c4`).

## 1. Create the data dataset

In **Datasets**, create `<POOL>/apps/waypoint/data`. It holds all maps, plan images and `.bak.json` backups. This is the dataset to snapshot.

Give it to the `apps` user (UID/GID 568). Either use **Datasets → data → Edit Permissions** (owner `apps`, group `apps`), or run this in the shell:

```bash
sudo chown -R 568:568 /mnt/<POOL>/apps/waypoint/data
```

## 2. Private image access

**The repository and the container image have separate visibility settings.** The repo `Asura-T/waypoint` is private. The image `ghcr.io/asura-t/waypoint` is a *package* with its own setting. A newly published package is **private** by default. Making the repo public or private does not change the package, and the other way round.

You have two choices. These docs never change visibility for you.

- **Keep the image private (default).** The NAS has to log in to pull it. Follow the steps below.
- **Make the image public.** Go to GitHub → your profile → **Packages → waypoint → Package settings → Change visibility**. After that, anyone can pull the image without logging in, and the code repository stays private. Only do this if you are happy for the built app to be public. Your maps are never in the image either way.

### Create a token for pulling (private image)

1. GitHub → **Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token (classic)**.
   GHCR only accepts *classic* tokens. Fine-grained tokens cannot pull packages.
2. Select **only** the `read:packages` scope. Set an expiry date.
3. The account the token belongs to must be allowed to read the package.
   - As the owner (`Asura-T`) you already are.
   - For a different account, open **Packages → waypoint → Package settings → Manage access** and add that account with the **Read** role.

### Log in on the NAS, pull, log out

```bash
sudo docker login ghcr.io -u Asura-T     # paste the token when asked for the password
sudo docker pull ghcr.io/asura-t/waypoint:<IMAGE_TAG>
sudo docker logout ghcr.io               # optional: removes the saved credential
```

- **The token is never typed on the command line.** `docker login` asks for it, so it does not end up in your shell history.
- **Never put the token in a file in this repo, in the app YAML, in an image, or in a URL** (no `https://user:token@…`).
- **While you stay logged in**, Docker keeps the credential in `/root/.docker/config.json` as plain, base64-encoded text. That is why the guide suggests logging out after pulling.
- **The app doesn't need the token.** The app YAML uses `pull_policy: missing` and a fixed tag. Once an image is pulled, TrueNAS runs the local copy and needs no credentials. You only log in again when you pull a new tag.

## 3. Install

### Method A: the published image (recommended)

1. **Pick a tag.** Go to GitHub → **Packages → waypoint**, or open the summary of the latest successful "Docker image" workflow run. Use a fixed tag such as `sha-1a2b3c4` or `1.2.0`.
2. **Pull it** (step 2 above). With a public image, skip `docker login`.
3. **Install the app.** Go to **Apps → Discover Apps → ⋮ → Install via YAML**. Paste [`truenas-custom-app.yaml`](truenas-custom-app.yaml) with `<POOL>` and `<IMAGE_TAG>` replaced.
   If you prefer the **Custom App** form, use these values:

| Field | Value |
|---|---|
| Application name | `waypoint` |
| Image repository / tag | `ghcr.io/asura-t/waypoint` / `<IMAGE_TAG>` |
| Pull policy | Only pull if not present |
| Container user / group (if shown) | `568` / `568` |
| Environment | `TZ=Asia/Phnom_Penh` (optional: `WAYPOINT_SEED_DEMO=true`) |
| Port forwarding | container `8080` → host `8080`, TCP |
| Storage | Host path `/mnt/<POOL>/apps/waypoint/data` → mount path `/data` |
| Restart policy | Unless stopped |

**From the shell instead (Docker Compose):** this needs a copy of the repo on a dataset, for `docker-compose.ghcr.yml` and `.env`.

```bash
cd /mnt/<POOL>/apps/waypoint/src
sudo cp .env.example .env && sudo nano .env    # WAYPOINT_DATA_PATH, WAYPOINT_IMAGE_TAG
sudo docker compose -f docker-compose.ghcr.yml pull
sudo docker compose -f docker-compose.ghcr.yml up -d
```

### Method B: build on the NAS (alternative, no registry)

Use this if you don't want to use GHCR at all.

```bash
cd /mnt/<POOL>/apps/waypoint
sudo git clone https://github.com/Asura-T/waypoint.git src     # private repo: use a token as the password
cd src
sudo docker build --build-arg WAYPOINT_VERSION=$(git rev-parse --short HEAD) -t waypoint:local .
```

- **To clone the private repo,** create a *fine-grained* token with read-only **Contents** access to `Asura-T/waypoint`, and enter it when git asks for the password.
- **If `git` is missing on TrueNAS,** run git from a container:
  ```bash
  sudo docker run --rm -it -v /mnt/<POOL>/apps/waypoint:/w -w /w alpine/git clone https://github.com/Asura-T/waypoint.git src
  ```

Then install with [`truenas-custom-app.local-build.yaml`](truenas-custom-app.local-build.yaml) (image `waypoint:local`, `pull_policy: never`), or run `sudo docker compose up -d --build` in `src/`. The Custom App screen cannot build images. It only runs images that already exist on the NAS.

### Which method to choose

| | A: Published image | B: Built on the NAS |
|---|---|---|
| Needs the source code on the NAS | No (only for Compose) | Yes |
| Build time on the NAS | None | About a minute per update |
| Credentials on the NAS | Only while pulling a private image | Only while cloning or pulling the private repo |
| Rollback | Switch to an older `sha-…` tag | `git checkout <commit>` and rebuild |
| Shown on the Apps page | Yes, if installed via YAML or the form | Yes, if installed via YAML or the form. Compose-in-shell containers usually show as unmanaged |

Both methods use the same `/data` dataset. To switch methods, stop the old app first, because only one app can use port 8080.

## 4. Open it

Go to `http://<truenas-ip>:8080` and check the version next to "Waypoint". Waypoint has **no login**, so keep it on your LAN or VPN.

## Updating

Pushing to GitHub only **publishes** a new image. It does not change what runs on the NAS.

**A container keeps running the image it was created from.** Restarting it, or stopping and starting it, does **not** switch it to a newer image, even if you pulled a newer `latest`. The update only happens when the container is **re-created** with the new image. Changing the tag in the app and saving does that.

1. **Snapshot the data first** (recommended):
   ```bash
   sudo zfs snapshot <POOL>/apps/waypoint/data@before-update-$(date +%F)
   ```
2. **Find the new tag.** It is the `sha-…` of the commit you want, or a release such as `1.3.0`.
3. **Pull it:**
   ```bash
   sudo docker login ghcr.io -u Asura-T                 # private image only
   sudo docker pull ghcr.io/asura-t/waypoint:<NEW_TAG>
   sudo docker logout ghcr.io
   ```
4. **Point the app at it and re-create the container:**
   - **App installed via YAML or the form:** go to **Apps → waypoint → Edit**, change the image tag to `<NEW_TAG>`, and click **Save**. TrueNAS re-creates the container from the new image.
   - **Compose:** set `WAYPOINT_IMAGE_TAG=<NEW_TAG>` in `.env`, then run `sudo docker compose -f docker-compose.ghcr.yml up -d`. Compose re-creates the container because the image changed.
   - **Method B:** run `git pull`, rebuild `waypoint:local`, then run `sudo docker rm -f waypoint` and start the app again (Apps → waypoint → Start), so the container is re-created.
5. **Confirm the update.** The version in the page header should now match the new commit. You can also run `curl -s http://localhost:8080/api/health`.

**About `latest`:** if you run `latest`, then `docker pull …:latest` downloads the new image, but saving the app *without changing anything* may not re-create the container. Fixed tags avoid that problem. They also make every update and rollback a visible change of one value.

**Your data:** updates replace only the container. `/data` lives on your dataset, and Waypoint never deletes files there. Each save keeps the previous version as `*.bak.json`. The demo map is only copied in when `/data` is empty, so it never overwrites your maps.

## Rolling back

Rolling back is the same as updating, using the **previous** tag. You can find it in GitHub → Packages → waypoint → *All versions*, or in your notes from the last update.

```bash
sudo docker pull ghcr.io/asura-t/waypoint:sha-<previous>
```

Then go to **Apps → waypoint → Edit**, set the tag back to `sha-<previous>`, and click **Save**. If the newer version had already saved maps in a form the older one doesn't understand, roll the data back too, using the snapshot from step 1 of [Updating](#updating) (see [Restoring](#restoring)).

Old image versions stay in GHCR until you delete them, so rollback keeps working for as long as you keep those versions. To free space on the NAS, run `sudo docker image prune`. It only removes images that no container uses.

## Backing up

All state is in the data dataset (`floors.json`, `rooms.json`, `plans/`). Pick any of these:

1. **ZFS snapshots (recommended):** **Data Protection → Periodic Snapshot Tasks → Add**, dataset `<POOL>/apps/waypoint/data`, for example daily with 30-day retention. Add a **Replication** or **Cloud Sync** task to keep a copy off the NAS.
2. **Manual snapshot** before each update, as shown in [Updating](#updating).
3. **Archive file:**
   ```bash
   sudo tar czf /mnt/<POOL>/backups/waypoint-$(date +%F).tar.gz -C /mnt/<POOL>/apps/waypoint data
   ```
4. **Export** in the app downloads the rooms as JSON. It does **not** include the plan images, so use it as an extra copy, not a full backup.

## Restoring

1. **Stop** the app (Apps → waypoint → Stop, or `sudo docker compose … stop`).
2. Restore the data, using one of:
   - **Snapshot:** **Datasets → data → Snapshots → Rollback**, or `sudo zfs rollback <POOL>/apps/waypoint/data@<snapshot>`. This discards everything saved after that snapshot.
   - **Archive:**
     ```bash
     cd /mnt/<POOL>/apps/waypoint
     sudo mv data data.old && sudo mkdir data
     sudo tar xzf /mnt/<POOL>/backups/waypoint-YYYY-MM-DD.tar.gz -C /mnt/<POOL>/apps/waypoint
     ```
   - **Only the last save:** `sudo cp data/rooms.bak.json data/rooms.json`
3. Fix ownership: `sudo chown -R 568:568 /mnt/<POOL>/apps/waypoint/data`
4. **Start** the app again and reload the page.

## Troubleshooting

- **`denied`, `unauthorized` or `403` when pulling:** the image is private and you are not logged in, or the token lacks `read:packages`, has expired, or its account has no read access to the package. Log in again, as described in step 2.
- **`manifest unknown`:** that tag doesn't exist. Check the tag name in GitHub → Packages. Also check that the "Docker image" workflow finished successfully for that commit (repo → **Actions**).
- **The app shows "image not found" after a reboot or TrueNAS update:** pull the tag again. The app's `pull_policy: missing` will also try to pull it itself, which only works for a public image or while logged in.
- **The app keeps restarting and the logs show `PermissionError: … '/data/plans'`:** the data dataset is not writable by 568:568. Fix it as described in step 1.
- **"Save failed" in the page:** the container cannot write `/data`. Check the ownership from step 1, and check the logs (Apps → waypoint → Logs, or `sudo docker logs waypoint`).
- **Port 8080 is already in use:** change only the *host* port, for example to `8090:8080`.
- **You see the demo office instead of your maps:** the `/data` host path points at an empty folder. Check the path in the app's storage settings.
