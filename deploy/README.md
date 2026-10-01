# Deploying at quantum-slicing.capstone.csi.miamioh.edu

Three files:

| File | Goes to | Does |
|------|---------|------|
| `nginx/quantum-slicing.conf` | `/etc/nginx/sites-available/` | routes the hostname to gunicorn, serves `/static/` itself |
| `quantum-slicer.service` | `/etc/systemd/system/` | runs gunicorn as a locked-down service |
| `../webapp/wsgi.py` | already in the repo | the WSGI object both of them point at |

## Read this first

**Analysing a Python circuit executes it on the server.** That is not a flaw in
the deployment, it is how the Qiskit front end reaches the `QuantumCircuit` a
program builds — see [the web app README](../webapp/README.md#how-submitted-circuits-are-handled).
Putting the site on a hostname means anyone who can reach that hostname can run
Python as the service's throwaway `DynamicUser`.

The mitigations that exist, in the order they matter:

1. **An `allow` / `deny` block in the nginx `server {}`.** If the site is
   only ever used from campus or the VPN, add one there. This is the control
   that actually decides who can run code.
2. **`IPAddressDeny=any` in the service unit**, so submitted code cannot open
   outbound connections even though it runs.
3. **`MemoryMax` and `TasksMax`**, so a runaway allocation or a fork loop hits
   a wall instead of the machine.
4. **`QSLICER_PARSE_TIMEOUT`**, which kills a worker after 20 seconds.
5. **`ProtectSystem=strict`, `ProtectHome=tmpfs` and `PrivateTmp`**, so the
   filesystem is read-only apart from a private `/tmp`, and nothing in
   `/home` is visible except the checkout.

Together these bound the damage from a mistake and from casual mischief. They
do not make the box safe to hand to the internet. If the site has to be open to
anyone who finds it, the honest options are to take OpenQASM only — that route
parses text and executes nothing — or to move the parse step into a container.
Ask and I can wire up a switch that refuses Python input at the deployment
level.

## Install

```bash
# 1. Code and a virtualenv in the ubuntu user's home. No service account is
#    needed: the unit uses DynamicUser=, so the files only need to be
#    world-readable, which they are after a normal clone.
cd /home/ubuntu
git clone <repo> slicing-for-quantum-programs
cd slicing-for-quantum-programs
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r webapp/requirements.txt gunicorn

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
in place. `/api/analyze` builds a QPDG, caches it in memory and returns a token;
`/api/slice` looks the token up again. The cache is a module-level dictionary,
so a second worker process would answer some slice requests without ever having
seen the token, and the user would be told to analyse again, seemingly at
random.

If you need real concurrency later, the fix is to move the cache out of process
(a small Redis, or reconstruct the QPDG per slice request), not to raise the
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
