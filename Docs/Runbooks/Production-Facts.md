# Production, as it actually is

Captured from the box on **2026-08-16** by `bash infra/prod-facts.sh`. Re-run that
script and update this file rather than trusting it indefinitely — it is a
snapshot, and the whole reason it exists is that a stale description of
production is worse than none.

**Where this disagrees with `Deploy-GCP.md`, this file wins.** That runbook was
written before the box existed and describes the shape that was planned; several
of its paths were never true here.

---

## The machine

| | |
|---|---|
| Instance | `app-server` (GCE), project `vidhye`, zone `asia-south1-a` |
| Type | `e2-medium` — 2 vCPU, 3.8 GiB, 29 GB disk (21% used) |
| OS | Ubuntu 22.04.5 LTS |
| External IP | `34.180.8.71` |
| Domain | `primer.yourcaly.com` |
| App directory | **`/home/ullasgowda3523/app`** — *not* `/srv/primer` |
| SSH | `gcloud compute ssh app-server --zone=asia-south1-a` |

### Cloud Shell is not the box

The Cloud Shell Editor's `~/app` is a **tar copy** pulled off the VM:

```bash
gcloud compute ssh app-server --zone=asia-south1-a \
  --command="tar -czf - -C /home/ullasgowda3523 app" | tar -xzf -
```

Editing it changes nothing in production. Same path, two machines — check
`hostname` (`cloudshell` vs `app-server`) whenever it matters. To run something on
the real box from Cloud Shell without copying anything:

```bash
gcloud compute ssh app-server --zone=asia-south1-a --command='bash -s' < script.sh
```

---

## How it runs

**Four Docker containers**, plus **Caddy on the host** (not in compose):

| Container | Image | Bound to |
|---|---|---|
| `primer-web-1` | `primer-web` | `127.0.0.1:8000` |
| `primer-worker-1` | `primer-worker` | — (Celery) |
| `primer-db-1` | `pgvector/pgvector:pg16` | `127.0.0.1:5432` |
| `primer-redis-1` | `redis:7` | `127.0.0.1:6379` |

Only `:80`, `:443` and `:22` are exposed publicly; every service port is bound to
loopback and reached through Caddy. Caddy's admin API is on `127.0.0.1:2019`.

### There is no virtualenv

`~/app/.venv` does not exist. **`make migrate`, `make superuser` and every other
`make` target that shells to `manage.py` will not work here** — they assume a venv
the runbook expected. Run management commands in the container instead:

```bash
docker exec -it primer-web-1 python manage.py <command>
```

Note `docker compose exec` is *also* unreliable from `~/app`: compose derives its
project name from the invoking directory (`app`), but the containers were created
under the project name `primer`, so it finds nothing. Address containers by name.

### Deploys are `git pull` on the box

`~/app` is a real clone — remote `git@github.com:ullasgowda15/Vidhye-ThePrimer.git`
over **SSH, authenticated on the VM** (Cloud Shell has no key; the VM does). So:

```bash
gcloud compute ssh app-server --zone=asia-south1-a --command='cd ~/app && git pull'
```

Nothing auto-deploys — `deploy.yml`'s release step is still a placeholder.

---

## Caddy

Runs as a **systemd service**, v2.11.4, config at `/etc/caddy/Caddyfile`.

```
primer.yourcaly.com {
    handle /api/*   { reverse_proxy 127.0.0.1:8000 }
    handle /admin/* { reverse_proxy 127.0.0.1:8000 }
    handle /admax*  { reverse_proxy 127.0.0.1:8000 }   # added 2026-08-16
    handle {
        root * /home/ullasgowda3523/app/frontend/dist
        file_server
        try_files {path} /index.html
    }
}
```

**Every Django route needs an explicit `handle` block.** The catch-all serves the
built PWA, so an unrouted path returns **HTTP 200 with a React Router 404** — the
request never reaches Django, and the status code hides it. This is what made
`/admax` look broken when Django was serving it correctly all along.

**`/admin/*` is proxied**, so the Django admin *is* reachable in production. It
renders unstyled because the image runs no `collectstatic` and
`staticfiles_urlpatterns()` is DEBUG-only. That is the whole of "the admin isn't
visible in production" — it is not a routing failure. `infra/admax-setup.sh
--close-admin` will close it if wanted; see `Docs/Security-Notes.md`.

---

## Probing it without being lied to

`SECURE_SSL_REDIRECT=1` means `SecurityMiddleware` 301s plain HTTP **before URL
resolution**, so a naive probe returns 301 whether or not the route exists:

```bash
# WRONG — 301 regardless
curl -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/admax

# RIGHT — 302 = route exists, 404 = old code, 000 = nothing listening
curl -o /dev/null -w '%{http_code}' \
  -H 'X-Forwarded-Proto: https' -H 'Host: primer.yourcaly.com' \
  http://127.0.0.1:8000/admax
```

Checking through Caddy tells you a different thing — whether the *route* exists:
`200` there means the PWA answered, i.e. Caddy has no block for that path.

---

## Configuration

`~/app/.env`, 56 keys, `DJANGO_SETTINGS_MODULE=config.settings.prod`, `DEBUG=0`,
`ALLOWED_HOSTS=primer.yourcaly.com,localhost,127.0.0.1`.

Providers as deployed: `AI_PROVIDER_ORDER=google` (Vertex, `GOOGLE_CLOUD_LOCATION=global`),
`MEDIA_STORAGE=s3` (Cloudflare R2 via `AWS_S3_ENDPOINT_URL`), images via Cloudflare
with a Google fallback, TTS Google with ElevenLabs fallback. Reading engine on.

Credentials live in the same `.env`; see `Docs/Security-Notes.md` for the
permissions and sprawl issues found alongside this snapshot.
