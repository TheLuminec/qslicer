# Deploying at quantum-slicing.capstone.csi.miamioh.edu

Three files:

| File | Goes to | Does |
|------|---------|------|
| `nginx/quantum-slicing.conf` | `/etc/nginx/sites-available/` | routes the hostname to gunicorn, serves `/static/` itself |
| `quantum-slicer.service` | `/etc/systemd/system/` | runs gunicorn as a locked-down service |
| `../webapp/wsgi.py` | already in the repo | the WSGI object both of them point at |

## Read this first

**Nothing a user submits is executed.** Both front ends the site offers read
the file and interpret it themselves, and the API refuses the old route that
ran Python (`language="qiskit"`) with an `execution_disabled` error. See
[the web app README](../webapp/README.md#how-submitted-circuits-are-handled).

What a submission can still cost is CPU and memory while it is parsed. The
parsers stop after 2,000,000 steps, 200,000 instructions or
`QSLICER_PARSE_TIMEOUT` seconds (default 20), whichever comes first, and the
source is capped at 256 KB. The service unit's hardening stays as defence
in depth:

1. **`MemoryMax` and `TasksMax`**, so a pathological parse hits a wall instead
   of the machine.
2. **`IPAddressDeny=any`**, so the service cannot open outbound connections.
3. **`ProtectSystem=strict`, `ProtectHome=tmpfs` and `PrivateTmp`**, so the
   filesystem is read-only apart from a private `/tmp`, and nothing in
   `/home` is visible except the checkout.
4. **An `allow` / `deny` block in the nginx `server {}`**, if the site should
   only be reachable from campus or the VPN.

## Install

```bash
# 1. Code and a virtualenv in the ubuntu user's home. No service account is
#    needed: the unit uses DynamicUser=, so the files only need to be
#    world-readable, which they are after a normal clone.
cd /home/ubuntu
git clone <repo> slicing-for-quantum-programs
cd slicing-for-quantum-programs
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r webapp/requirements.txt gunicorn -e .

# 2. The service
sudo cp deploy/quantum-slicer.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now quantum-slicer
systemctl status quantum-slicer --no-pager
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/     # expect 200

# 3. The site
sudo cp deploy/nginx/quantum-slicing.conf /etc/nginx/sites-available/quantum-slicing
sudo ln -sf /etc/nginx/sites-available/quantum-slicing /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

After a `git pull`, run `sudo systemctl restart quantum-slicer`. If the unit
file itself changed, copy it again and run `sudo systemctl daemon-reload` first.

### If the service will not start

| Status in `systemctl status` | Cause |
|---|---|
| `status=200/CHDIR` | `WorkingDirectory` is not visible. Check that `BindReadOnlyPaths` matches the checkout path. |
| `status=203/EXEC` | `.venv/bin/gunicorn` does not exist. Install gunicorn into the venv. |
| `status=226/NAMESPACE` | A path in `BindReadOnlyPaths`/`ReadWritePaths` does not exist. |
| `Permission denied` in the journal | The checkout is not world-readable. Run `chmod -R o+rX` on it. |

Then check the whole path end to end:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' http://quantum-slicing.capstone.csi.miamioh.edu/
curl -sS http://quantum-slicing.capstone.csi.miamioh.edu/api/examples | head -c 200
```

## One worker, several threads

`--workers 1 --threads 4` in the service file is deliberate, not a default left
in place. `/api/analyze` builds a QDG, caches it in memory and returns a token;
`/api/slice` looks the token up again. The cache is a module-level dictionary,
so a second worker process would answer some slice requests without ever having
seen the token, and the user would be told to analyse again, seemingly at
random.

If you need real concurrency later, the fix is to move the cache out of process
(a small Redis, or reconstruct the QDG per slice request), not to raise the
worker count.

## Paths to change

The files assume `/home/ubuntu/slicing-for-quantum-programs`. If the checkout
goes elsewhere, update `WorkingDirectory`, `ExecStart` (both paths) and
`BindReadOnlyPaths` in the service file.

## Windows

`gunicorn` does not run on Windows. `waitress` does, is already installed in
this environment, and is single-process by design, which suits the cache:

```bash
waitress-serve --listen=127.0.0.1:8000 --threads=4 --call webapp.wsgi:application
```

…or from inside `webapp/`:

```bash
waitress-serve --listen=127.0.0.1:8000 --threads=4 wsgi:application
```

None of the systemd hardening applies there, which is another reason the Linux
host is the better place for this.

## Logs

```bash
journalctl -u quantum-slicer -f                       # app, including tracebacks
sudo tail -f /var/log/nginx/quantum-slicing.error.log  # proxy and rate limiting
```

A 429 in the nginx log is the analyse rate limit (30 a minute per client, burst
of 10). A 502 with the service running usually means gunicorn is bound
somewhere other than `127.0.0.1:8000`.
