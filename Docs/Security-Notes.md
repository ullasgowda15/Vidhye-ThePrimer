# Security notes from the 2026-08-16 production snapshot

Found by reading `infra/prod-facts.sh` output and the deploy tree. None of these
is an active compromise; all are the conditions one needs. Ordered by what to do
first.

---

## 1. `.env` is group- and world-readable — **fix now**

```
/home/ullasgowda3523/app/.env  (664 ullasgowda3523:ullasgowda3523)
```

`664` means every account on the box can read it. That file holds `SECRET_KEY`,
`MEMORY_ENCRYPTION_KEY` (children's memories are encrypted with it),
`AWS_SECRET_ACCESS_KEY`, `CLOUDFLARE_API_TOKEN`, `ELEVENLABS_API_KEY`,
`GOOGLE_TTS_API_KEY` and the backup bucket's credentials. `DEPLOY.md` already
specifies `600`; the box does not match it.

```bash
chmod 600 ~/app/.env ~/app/.env.backup*
```

Single-operator box, so the practical exposure is low — but it is one added user,
one misconfigured container mount, or one careless `tar` from being real.

## 2. Eleven `.env` backups, all holding live secrets

```
.env.backup  .env.backup-final  .env.backup-tls
.env.backup-20260815T111659Z … .env.backup-20260815T160251Z   (7 more)
```

Each is a full copy of the credentials above, and they multiply the surface for
the problem in §1 by eleven while making it harder to know which file is
authoritative. They *are* matched by `.gitignore`'s `.env.*`, so they are not a
git risk — they are a disk risk.

Keep the newest one, `chmod 600` it, delete the rest once the current `.env` is
confirmed good.

## 3. Key filenames that `.gitignore` missed — **fixed 2026-08-16**

`vertex-sa_(1).json` (a browser-download rename) and `env-for-server.txt` were
sitting untracked in the tree. `*-sa.json` does not match the first; `.env*` does
not match the second. Both were one `git add -A` from a history that cannot be
rewritten.

The patterns are now prefix-anchored and suffix-free (`vertex-sa*`,
`env-for-server*`, `*service-account*`), because every leak of this kind starts
with a rename — a `(1)`, a `.bak`, a timestamp.

**Still to verify by hand.** Ignoring a file does nothing about a file already
committed. Run in a full clone:

```bash
git log --all --full-history --oneline -- '*vertex-sa*' '*gemini-key*' '*service*account*' 'env-for-server*'
```

Any output means a credential is in history and must be **rotated** — the Vertex
service account key regenerated in GCP IAM — not merely deleted. Ignoring is not
remediation.

## 4. The Django admin is exposed with password-only auth

The deployed Caddyfile proxies `/admin/*`, so `https://primer.yourcaly.com/admin/`
reaches Django. It renders unstyled (no `collectstatic`), which is why it *looks*
broken, but it is functional and behind a single factor.

`/admax` now shows the same operational data behind two factors. That makes the
admin the weakest door to the same building — and the one with the write access:
it can edit children, users and companions, which `/admax` deliberately cannot.

```bash
bash infra/admax-setup.sh --close-admin
```

comments the block out (reversibly) and reloads. It is **opt-in** because
`DEPLOY.md`'s own checklist uses `/admin` to hand-create a Child and Companion —
closing it removes that workflow, and only the operator knows if they rely on it.

## 5. Stray directory from a mangled command

```
~/app/backupsDATABASE_URL=postgresql:
```

A shell invocation where an intended assignment became an argument, creating a
directory named after it. Harmless in itself, but it means **a backup command did
not do what its author believed**, and backups are a top SLO here (§16.10 — losing
a child's history breaks the core promise). Worth confirming
`Docs/Runbooks/Backup-Restore.md`'s restore drill actually passes before deleting
the directory.

---

## Not problems, recorded so they are not re-investigated

* **Only `:22`, `:80`, `:443` are public.** Postgres, Redis and the app are all
  bound to loopback and reached through Caddy. This is right.
* **`DEBUG=0`, `SECURE_SSL_REDIRECT=1`, `ALLOWED_HOSTS` scoped to the real
  domain.** Also right.
* **`/admax` sessions grant nothing on `/admin`.** Different auth path, no
  `_auth_user_id` — asserted by `test_admax.py::TestItIsNotTheDjangoAdmin`.
