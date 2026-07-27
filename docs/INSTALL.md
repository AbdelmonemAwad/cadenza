# Installing Cadenza on a Synology NAS

There are two supported ways to run Cadenza, and they are genuine alternatives
rather than a preferred path and a fallback:

- **A — the native package (`.spk`)**, installed through Package Center. It
  bundles its own Python, its dependencies, and its own `ffmpeg`, `ffprobe` and
  `fpcalc`. It does not use Docker at all.
- **B — Container Manager**, using the `docker-compose.yml` in the repository
  root.

Both end up serving the same application at `http://<nas-address>:8760`.

> **A note on the older documentation.** The top-level `README.md` still says a
> `.spk` cannot start the application on DSM 7 and that Container Manager is the
> only supported path. That was true of an earlier version. The package now runs
> the application itself (see
> `packaging/synology/scripts/start-stop-status`), and this document describes
> the current behaviour. The compose file the installer generates carries the
> same stale sentence in its header comment; ignore it.

---

## 1. System requirements

| | Native `.spk` (path A) | Container Manager (path B) |
|---|---|---|
| **CPU architecture** | `x86_64` only | `x86_64` only |
| **DSM** | 7.2 or newer — the package declares `os_min_ver="7.2-64561"` and DSM refuses to install it below that | 7.2 or newer in practice, because Container Manager itself is a 7.2 package |
| **Docker / Container Manager** | Not used by the service — but Package Center still requires it, see the note below | Required |
| **Free space** | Roughly 300–400 MB (interpreter, libraries and FFmpeg travel inside the package) | Roughly 2 GB for the image and the library index |
| **Extra space** | Whatever your library index and artwork cache grow to, in the config folder | Same |

**ARM models are not supported.** DS220j, DS223, DS423, DS124 and every other
ARM-based unit will not run Cadenza by either path:

- The container image is built `linux/amd64` only.
- The package's `preinst` checks `uname -m` and aborts the install with
  `This package supports x86_64 only. Detected: <arch>`.

There is no ARM build and no workaround. Supported units are the Intel/AMD
models — DS1821+, DS1621+, DS920+, DVA3221 and similar.

**The Container Manager dependency on the native path is real.** The package's
`INFO` file still declares `install_dep_packages="ContainerManager>=20.10"`,
so Package Center will insist Container Manager is installed before it will
install Cadenza — even though the native service never talks to Docker. Install
Container Manager if Package Center asks for it; you do not need to run anything
in it.

**Unsigned package.** The `.spk` is not signed by Synology. Package Center will
warn you, and depending on your settings may refuse outright. If it does:
Package Center → **Settings** → **Trust Level** → **Any publisher**. Set it back
afterwards if you prefer.

---

## 2. Which path should you choose

Neither is a downgrade. Pick on the basis of how your NAS is set up.

**Choose the native package if:**

- You would rather not run Docker, or your model is tight on RAM. One Python
  process, no daemon, no image layer.
- You want DSM to own the lifecycle: start/stop from Package Center, automatic
  start at boot, a desktop icon, and the `cadenza` CLI on `PATH`.
- You are comfortable granting a DSM *system* account access to your shares
  (this is the one real chore of this path — see §6).

**Choose Container Manager if:**

- Container Manager is already part of how you run things.
- You want the service to run as **an ordinary DSM user of your choosing**. This
  is the decisive difference: with `user: "1026:100"` you decide exactly which
  account owns every file Cadenza writes. The native package always runs as its
  own system account.
- You want the CPU and memory limits that the compose file sets, or you want to
  pin, roll back or re-pull an image tag.

**Running both at once is a bad idea.** They would use the same library and the
same quarantine folder with two independent databases and two schedulers. Pick
one. If you switch, stop and remove the other first.

---

## 3. Path A — the native package

### 3.1 What you must prepare before you start the installer

Do these first. The wizard asks for values it cannot create for you, and the
most common failed install is a config folder that does not exist yet.

**1. Decide where your music lives.** It must be a path under a volume, for
example `/volume1/music`. The wizard rejects anything that does not match
`/volume<N>/<something>` and anything containing a space, colon, quote,
backtick, `$` or backslash.

**2. Create the config folder — it will not be created for you.**

This folder holds the database, the logs, the artwork cache, your settings and
your provider API keys. Back it up; losing it means re-scanning from scratch.

- Default suggestion: `/volume1/docker/cadenza`
- Do **not** put it inside your music library.
- Keep it on a volume you back up.

Create it in File Station, or over SSH:

```sh
sudo mkdir -p /volume1/docker/cadenza
```

**3. Make sure the account the service runs as can write to that folder.**

This is the step that decides whether the package starts. Read §6 before
installing — on the native path the account is *not* the DSM user you type into
the wizard.

**4. Decide on a port**, if 8760 is taken. Anything from 1024 to 65535. Read §7
about the firewall and about Package Center's Open button before choosing
something other than 8760.

**5. Look up a UID if you plan to fill in "Run as".** Enable SSH (Control Panel
→ Terminal & SNMP → Enable SSH service), then:

```sh
$ id musicadmin
uid=1026(musicadmin) gid=100(users) groups=100(users),101(administrators)
```

The `uid=` and `gid=` numbers are what matter. You can disable SSH again
afterwards. See §3.4 for what this field does and does not affect — it is less
than the wizard text suggests.

### 3.2 Installing

1. Package Center → **Manual Install** → select
   `Cadenza-x86_64-1.0.0-0001.spk`.
2. Accept the unsigned-package warning.
3. Fill in the wizard (next section).
4. Finish. DSM starts the package itself; the package is declared `startable`.
5. Open `http://<nas-address>:8760` — or your chosen port. The Cadenza icon on
   the DSM desktop points at the right port; Package Center's **Open** button
   does not (§7).

If the package installs but immediately shows as stopped, go straight to §9.

### 3.3 The wizard fields

The wizard is one screen with six fields.

| Field | Key | What it does |
|---|---|---|
| **Library path** | `wizard_music_path` | The folder Cadenza scans. Becomes `CADENZA_MUSIC_ROOT` for the service, and the `/music` mount in the generated compose file. Required, and validated against `/volume<N>/…`. |
| **Port** | `wizard_http_port` | The port the service listens on. Written to `etc/port.conf`, applied to the desktop icon and to the DSM firewall service definition. 1024–65535. |
| **Config folder** | `wizard_config_path` | Database, logs, artwork cache, settings, API keys. Becomes `CADENZA_CONFIG_DIR`. **Must already exist and be writable by the service account** — see §6. |
| **Timezone** | `wizard_timezone` | A `Region/City` name such as `Europe/London`. See the honesty note below. |
| **Default language** | `wizard_locale` | `en` or `ar`. Anything else silently becomes `en`. Switchable later in the UI. |
| **Run as** | `wizard_run_user` | A DSM username. See §3.4 — on the native path this affects only the compose file the installer writes as a side product. |

Every field is re-validated by `postinst`, not only in the browser. A value that
fails validation is replaced by the default and a message is written to DSM's
installer output rather than failing the install. So if you end up scanning
`/volume1/music` when you asked for something else, check the log (§9) — your
path was probably rejected for a character it did not like.

### 3.4 What the wizard fields actually affect — read this

The installer serves two purposes: it configures the native service, *and* it
writes a ready-to-use `docker-compose.yml` for people who would rather use path
B. Some fields only reach one of those two. This is not documented in the
wizard, and the wizard text for "Run as" is misleading.

| Field | Native service | Generated compose file |
|---|---|---|
| Library path | Yes | Yes |
| Config folder | Yes | Yes |
| Port | Yes | Yes |
| Default language | Yes | Yes |
| Timezone | **No** — `postinst` writes `TZ=` into `etc/cadenza.env`, but `start-stop-status` does not export it, so the service inherits the NAS's own system timezone. Set the NAS timezone in Control Panel → Regional Options and this field does not matter. | Yes |
| **Run as** | **No.** The username is resolved to a `uid:gid` and used for the `user:` line of the compose file. The native service always runs as the package's own DSM system account, whatever you type here. | Yes |

Two further things the wizard does not ask about:

- **Quarantine location** is fixed at `<library>/.cadenza-quarantine`. This is
  deliberate — it keeps quarantine on the same volume as the library, so a
  "delete" is an instant rename instead of a full file copy. To change it, edit
  `HOST_QUARANTINE_PATH` in `/var/packages/Cadenza/etc/cadenza.env` and restart
  the package. Keep it on the same volume.
- **Worker count.** `cadenza.env` contains `CADENZA_WORKERS=4`, but the service
  always starts `uvicorn` with `--workers 1`, on purpose: the job runner and the
  scheduler keep state in the process, and a second worker would run every
  scheduled job twice.

### 3.5 What the installer writes

| Path | What it is |
|---|---|
| `/var/packages/Cadenza/etc/cadenza.env` | Generated settings. Edit over SSH (`sudo`, mode 640) and restart the package to apply. Preserved across upgrades. |
| `/var/packages/Cadenza/etc/port.conf` | The authoritative port. `postupgrade` re-applies it, so an update does not reset you to 8760. |
| `/var/packages/Cadenza/var/logs/package.log` | Install/upgrade notes. |
| `/var/packages/Cadenza/var/logs/service.log` | Service start/stop plus everything the application prints. |
| `/var/packages/Cadenza/var/docker-compose.yml` | A compose file filled in with your answers, for path B. |
| `<config folder>/docker-compose.yml` | A copy of the same file, if the installer had permission to write it there. If not, `package.log` says so. |
| `/usr/local/bin/cadenza` | Small CLI wrapper: `cadenza health`, `scan`, `dedup`, `report`, `stats`, `jobs`. |

Provider API keys belong in the app's Settings page, not in `cadenza.env`.

---

## 4. Path B — Container Manager

### 4.1 Prepare

**1. Install Container Manager** from Package Center.

**2. Find the UID:GID of the account that owns your music.** Over SSH:

```sh
$ id musicadmin
uid=1026(musicadmin) gid=100(users) groups=100(users),101(administrators)
```

`1026:100` is a common answer for the first user DSM ever created, but it is not
a safe assumption. Check it.

**3. Create the config folder, owned by that account.** The simplest way is to
create it in File Station while signed in to DSM as that user, which makes them
the owner. Over SSH:

```sh
sudo mkdir -p /volume1/docker/cadenza/config
sudo chown 1026:100 /volume1/docker/cadenza/config
```

If this folder does not exist, Docker creates it owned by `root`, the container
cannot write to it, and you get a restart loop (§9).

### 4.2 Edit the compose file

Start from `docker-compose.yml` in the repository root, or — if you installed
the package — from the copy the installer generated with your paths already
filled in.

Three lines need to match your NAS:

| Line | Change it to |
|---|---|
| `- /volume1/music:/music:rw` | your real music share |
| `- /volume1/docker/cadenza/config:/config:rw` | the folder you created above |
| `user: "1026:100"` | the `uid:gid` from `id <your-dsm-user>` |

Keep `/volume1/music/.cadenza-quarantine` on the same volume as the library.

Do not set `user: "0:0"`. The entrypoint warns loudly if it finds itself running
as root, because every file it then writes into your share is root-owned and you
cannot manage it from File Station afterwards.

### 4.3 Start it

Container Manager → **Project** → **Create** → **Set path** to the folder holding
the compose file → **Next** → **Done**.

Then open `http://<nas-address>:8760`.

To change the published port, change the left-hand side of the mapping only:

```yaml
- "9000:8760"
```

The container always listens on 8760 internally — nginx inside the image is
configured for it and that file is root-owned, so it cannot be rewritten at
runtime by a non-root container.

---

## 5. Try it read-only first

Scanning and duplicate analysis never write to your library. Nothing moves until
you explicitly ask for it, and every destructive job supports `dry_run` and
defaults to it. If you would rather prove that than take it on trust:

**Container Manager.** Mount the library read-only:

```yaml
- /volume1/music:/music:ro
```

Leave `/quarantine` and `/config` writable — the database and logs need
somewhere to go. Scan, run the duplicate analysis, read the report, then switch
back to `:rw` and restart the project when you are satisfied.

**Native package.** There is no mount to flag, so the equivalent is to grant the
service account **read-only** access to the music share (§6) and leave it there
for the trial. Cadenza will scan and analyse normally. When you later try to move
something to quarantine it will fail with a permission error in
`<config folder>/logs/cadenza.log` — which is the proof you were looking for, and
your cue to grant write access.

Either way, a first scan is the slow part: it hashes and fingerprints every file.
Expect roughly 20–60 minutes for 50,000 tracks. Later scans are incremental.

---

## 6. Permissions — what Cadenza needs, and why

Three folders, three different reasons.

| Folder | Access | Why |
|---|---|---|
| Music library | Read, at minimum | Scanning reads every audio file to hash it, decode it for the audio-stream hash, and fingerprint it. |
| Music library | Write, eventually | Only if you want it to rename, move, retag, convert or organise files. Deduplication does not need this — quarantining a duplicate does. |
| Quarantine (`<library>/.cadenza-quarantine`) | Write | This is where "deleted" files go. Cadenza never unlinks a file during a normal cleanup; it moves it, mirroring the original tree so restores are obvious. Same volume as the library, or every move becomes a copy. |
| Config folder | Write | Database, rotating logs, artwork cache, `settings.json`, `initial-password.txt`. The service refuses to start without it, deliberately, rather than failing on its first write later. |

Cadenza never needs root and never runs as root on either path. The container
runs with `no-new-privileges`, and the package runs as an unprivileged DSM
system account — DSM 7 will not install a third-party package that asks for
root, so this is not something you can override even if you wanted to.

### Which account, on each path

**Container Manager:** whatever you put in `user:`. That is the whole story —
pick a real DSM user that already has access to the music share, and everything
Cadenza writes belongs to them.

**Native package:** a dedicated system account DSM creates for the package. It is
named from `packaging/synology/conf/privilege` and DSM presents these accounts
with an `sc-` prefix (so, `sc-cadenza`). Confirm the exact name on your unit
rather than assuming:

```sh
grep -i cadenza /etc/passwd
```

That account has no access to your shared folders until you give it some. Two
ways:

1. **DSM UI.** Control Panel → **Shared Folder** → select the share → **Edit** →
   **Permissions** → change the dropdown from *Local users* to **System internal
   user** → tick Read/Write (or read-only, for a trial) for the Cadenza account.
2. **Over SSH**, for a folder rather than a whole share:

   ```sh
   sudo chown -R sc-cadenza:sc-cadenza /volume1/docker/cadenza
   sudo chmod 770 /volume1/docker/cadenza
   ```

   On shares where Synology's ACLs are switched on, POSIX ownership can be
   overridden by the ACL and the UI route in (1) is the one that reliably
   sticks.

You do not have to guess whether it worked. Start the package and read
`/var/packages/Cadenza/var/logs/service.log`: if the config folder is not
writable, the service says so by name and stops, instead of half-starting.

---

## 7. Port and firewall

The port is configurable at install time and is applied in three places:
`etc/port.conf`, the DSM desktop shortcut, and the DSM firewall service
definition (`conf/cadenza.sc`, refreshed via `synopkghelper`). `postupgrade`
re-applies it after every package update, so an update will not silently move
you back to 8760.

Two things you should still expect to do yourself:

**The DSM firewall may need opening by hand.** If Control Panel → Security →
Firewall is enabled, the package tries to register its service definition for
your port, but this can fail quietly depending on the DSM build. If you cannot
reach Cadenza from another machine while `service.log` shows it listening, add a
rule for `<your port>/tcp` manually. The installer writes a line to
`package.log` telling you the same thing.

**Package Center's own "Open" button always goes to 8760.** It reads `adminport`
from the package's `INFO` file, which is fixed when the `.spk` is built. No
install-time script can change it. If you chose a different port, use the DSM
desktop icon (which does follow your choice) or browse to
`http://<nas-address>:<your port>` directly. If the desktop icon is also wrong,
`package.log` will contain a warning saying the rewrite failed.

For the Container Manager path the port is simply the left-hand side of the
`ports:` mapping, and no DSM service definition is registered at all — add the
firewall rule yourself if the firewall is on.

---

## 8. First sign-in

There is no default password. One is generated the first time Cadenza starts,
because a shared default would look like protection while providing none.

1. Read it from **`initial-password.txt` in your config folder** — the same
   folder you named in the wizard or mounted at `/config`. It is written mode
   `0600`. The application also logs *where* it is (never the password itself)
   in `<config folder>/logs/cadenza.log`.

   ```sh
   sudo cat /volume1/docker/cadenza/initial-password.txt
   ```

2. Sign in with it. The username is the administrator account created on first
   run.
3. **You are required to change it immediately.** This is enforced server-side,
   not by the sign-in screen, so refreshing or navigating away does not skip it.
4. Once you set your own password, `initial-password.txt` is deleted
   automatically. Changing your password also ends every other session.

Sessions last 14 days. **Sign out everywhere** is available if you think a
session leaked.

### Before you expose this to anything

Keep Cadenza on a trusted LAN. It speaks plain HTTP; there is no TLS on either
path. For remote access, put it behind DSM's reverse proxy with its own
authentication — do not port-forward it.

Version 1.0 has known security gaps that are documented in the README's Security
section (issues #5, #6 and #8). The short version: treat an authenticated
session as equivalent to shell access on the NAS, and leave "keep original"
switched on in the conversion settings.

---

## 9. Troubleshooting

### Where the logs are

| Log | Path | Covers |
|---|---|---|
| Package install/upgrade | `/var/packages/Cadenza/var/logs/package.log` | Port applied, firewall note, whether the compose file could be copied |
| Native service | `/var/packages/Cadenza/var/logs/service.log` | Start/stop, the config-writable check, and all application stdout/stderr including Python tracebacks |
| Application | `<config folder>/logs/cadenza.log` | Scans, jobs, providers, file operations. Rotating. Same file on both paths. |
| DSM installer output | `/var/log/packages/Cadenza.log` | What the install scripts printed, including rejected wizard values |
| Container | Container Manager → Container → `cadenza` → **Log** | Everything the container printed, including the entrypoint's checks |

Reading them over SSH needs `sudo` for the first four.

### The package installs, then shows as "Stopped"

`start-stop-status` waits three seconds and verifies the process is still alive
before reporting success, so this means it really did fail. Read
`service.log`. The realistic causes:

- **`ERROR: <folder> is not writable by <account>`** — the config folder does
  not exist, or the service account has no write access to it. This is the most
  common one by a wide margin. Fix per §6, then start the package again. The
  message names the account, which saves you guessing what it is called on your
  DSM version.
- **`ERROR: the bundled interpreter is missing at …`** — you installed a package
  built with `NATIVE=0`, which contains the DSM scripts but no runtime. It
  installs and cannot start, by design. Rebuild with `NATIVE=1` (the default) or
  download a release build.
- **`[Errno 98] Address already in use`** — something else has your port.
  Change it in `/var/packages/Cadenza/etc/port.conf` and
  `/var/packages/Cadenza/etc/cadenza.env`, then restart the package. Check with
  `sudo netstat -tlnp | grep <port>`.
- **A Python traceback** — everything the app prints lands in `service.log`. The
  last few lines are the real error.

### The install failed immediately on an ARM NAS

`This package supports x86_64 only. Detected: armv8` in
`/var/log/packages/Cadenza.log`. There is no ARM build. See §1.

### Package Center will not install it without Container Manager

Expected — the package's `INFO` still declares that dependency even though the
native service does not use Docker. Install Container Manager and continue; you
do not have to create a project in it.

### The container restarts in a loop

Check the container log. If it says:

```
ERROR: /config is not writable - check the folder permissions on the NAS
       (the container runs as UID:GID 1026:100)
```

the config folder either does not exist or is owned by someone else — very often
`root`, because Docker created it for you when the path was missing. It prints
the UID it is actually running as, which is what you compare against
`ls -ln /volume1/docker/cadenza`. Fix the ownership and restart the project.

### Cadenza writes files my own account cannot touch

The `user:` line does not match the account that owns your music share. Check
with `id <your-dsm-user>`, correct the compose file, restart the project, and
fix the existing files with `chown`.

On the native path this cannot be fixed with the "Run as" wizard field — see
§3.4. Files are owned by the package's system account; give your own user access
through the share permissions instead.

### The web page is blank, or the API answers but there is no UI

`cadenza.log` will contain `no frontend bundle at …`. The package was built
without `frontend/dist`. The build script normally refuses to produce a package
in that state, so this points at a hand-assembled build.

### The desktop icon or Open button goes to the wrong place

See §7. Package Center's Open button is hardcoded to 8760 and cannot be changed
after the build. If the *desktop icon* is also wrong, `package.log` will contain
a warning that the rewrite failed; browse to the port directly.

### Quarantining files is unexpectedly slow

The quarantine folder is on a different volume from the library, so every move
is a full copy. Keep it under the library (`<library>/.cadenza-quarantine`,
which is the default) or at least on the same volume.

### Duplicate detection is missing cross-format duplicates

Add an [AcoustID key](https://acoustid.org/new-application) in **Settings →
Provider API keys**. Without it the acoustic layer cannot run, and matching a
FLAC against an MP3 of the same recording is exactly what that layer is for.
Also check `service.log` for `fpcalc` errors.

### After an upgrade the port went back to 8760

It should not: `preupgrade` saves `etc/port.conf` and `postupgrade` re-applies
it. If it happened anyway, check `package.log` for
`Upgrade WARNING: the desktop icon reset to 8760` — the service is still on your
port even when the icon is not; browse to it directly and re-run the fix by
reinstalling, or edit `target/ui/config` by hand.

---

## 10. Upgrading and removing

**Native package.** Install the newer `.spk` over the old one through Package
Center. `preupgrade` preserves `etc/cadenza.env` and `etc/port.conf`; everything
under `target/` is wiped and recreated by DSM, and `postupgrade` re-applies your
port. Your database and settings are in the config folder and are untouched. The
package's own scripts explicitly preserve only `etc/` — do not assume anything
else inside `/var/packages/Cadenza` survives, and keep the config folder
somewhere you back up.

**Container Manager.** Stop the project, pull the new image, start it again.

**Uninstalling** removes the package directory. It deliberately does not touch
your music, your quarantine folder or your config folder — the uninstall scripts
do nothing at all, on the principle that your data outlives the package. Delete
the config folder yourself if you really want it gone; that is where the
database and your API keys are.

---

## 11. Known limitations

Stated plainly so you do not discover them by experiment:

- **ARM is not supported**, by either path, and there is no plan in this release.
- **No TLS on either path.** Use DSM's reverse proxy for anything beyond the LAN.
- **The native path's "Run as" wizard field does not change what account the
  service runs as.** It only fills in the generated compose file. §3.4.
- **The native path ignores the wizard's timezone.** The service inherits the
  NAS system timezone. §3.4.
- **The native package requires Container Manager to be installed** even though
  it never uses it, because of a leftover dependency declaration in `INFO`.
- **Package Center's Open button is stuck on 8760** whatever port you choose,
  because `adminport` is fixed at build time.
- **The DSM firewall entry may need to be added by hand.** §7.
- **Provider API keys are stored in plain text** in `settings.json` in the config
  folder. Protect that folder accordingly.
- **The generated compose file's header comment is out of date** — it claims the
  package cannot start the application, which was true of the previous version.
- **The quarantine path is not exposed in the wizard**; change it in
  `cadenza.env` if you must, keeping it on the library's volume.
- **One worker, always.** Not a bug — the scheduler and job runner are
  single-process by design.
