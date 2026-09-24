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
Python as the `qslicer` user.

The mitigations that exist, in the order they matter:

1. **The `allow` / `deny` block in the nginx config.** It ships commented out
   and you should uncomment it. If the site is only ever used from campus or
   the VPN, say so there. This is the control that actually decides who can
   run code.
2. **`IPAddressDeny=any` in the service unit**, so submitted code cannot open
   outbound connections even though it runs.
3. **`MemoryMax` and `TasksMax`**, so a runaway allocation or a fork loop hits
   a wall instead of the machine.
4. **`QSLICER_PARSE_TIMEOUT`**, which kills a worker after 20 seconds.
5. **`ProtectSystem=strict` and `PrivateTmp`**, so the filesystem is read-only
   apart from a private `/tmp`.

Together these bound the damage from a mistake and from casual mischief. They
do not make the box safe to hand to the internet. If the site has to be open to
anyone who finds it, the honest options are to take OpenQASM only — that route
parses text and executes nothing — or to move the parse step into a container.
Ask and I can wire up a switch that refuses Python input at the deployment
level.

## Install

```bash
# 1. Code and a virtualenv, owned by a user that owns nothing else
sudo useradd --system --home /srv/quantum-slicer --shell /usr/sbin/nologin qslicer
sudo git clone <repo> /srv/quantum-slicer
cd /srv/quantum-slicer
sudo python3 -m venv .venv
sudo .venv/bin/pip install -r requirements.txt -r webapp/requirements.txt gunicorn
sudo mkdir -p logs
sudo chown -R qslicer:qslicer /srv/quantum-slicer

# 2. The service
sudo cp deploy/quantum-slicer.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now quantum-slicer
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/     # expect 200

# 3. The site
sudo cp deploy/nginx/quantum-slicing.conf /etc/nginx/sites-available/quantum-slicing
sudo ln -s /etc/nginx/sites-available/quantum-slicing /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

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

The files assume `/srv/quantum-slicer`. If the checkout goes elsewhere, update:

* `alias` in the nginx `/static/` block;
* `WorkingDirectory`, `ExecStart` and `ReadWritePaths` in the service file.

The nginx `alias` must keep its trailing slash, and must point at
`webapp/static/`, not at the repository root.

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
