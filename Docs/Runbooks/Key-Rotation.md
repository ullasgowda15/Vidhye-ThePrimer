# Key rotation

How to roll a credential on the pilot box, and a record of the ones that have
had to be rolled.

**No secret value is ever written into this file.** Keys are identified by their
first eight and last four characters, which is enough to confirm you are looking
at the right one in a vendor dashboard and not enough to use.

---

## 1. The 15 August 2026 exposure — closed

Both keys were pasted into a chat transcript while debugging the deployment: the
first in `docker compose config` output, which expands every `${VAR}`, and the
second in a `launch_check --live` log line. Both were rotated the same day and
`launch_check --live` reported `credentials.cloudflare` and
`credentials.google-tts` OK on the replacements.

| Setting | Fingerprint (dead) | How it got out | Status |
|---|---|---|---|
| `CLOUDFLARE_API_TOKEN` | `cfut_ePVc…0c1a` | `docker compose config` expanded it | **Rolled** 15 Aug |
| `GOOGLE_TTS_API_KEY` | `AIzaSyDTmM…kxU4` | logged in a request URL by `preflight.probe_google_tts` (§3) | **Replaced** 15 Aug — new key created, cut over, old one deleted |

**The Google key was replaced rather than regenerated, on purpose.** Regenerating
kills the old string the instant you click it, and Google TTS was at that moment
the *only* working voice provider — ElevenLabs was out of credits, so nothing
could have covered the gap between the click and the `.env` update. Create new →
cut over → verify → delete old has no such window. Cloudflare's "Roll" was fine
by contrast, because images fail over between Gemini and Cloudflare.

The replacement Google key is restricted to the Cloud Text-to-Speech API. An
unrestricted `AIza…` key works against every enabled API on the project, which is
how a TTS key turns into a Vertex bill.

> **The step people skip is the last one.** A new key that works while the old
> one still exists is not a rotation. Confirm the old credential is *deleted* at
> the vendor, not merely superseded.

### Not secrets, no action

Named here so nobody spends an afternoon rotating them. All three appear in
ordinary log lines by design and are identifiers rather than credentials:
`CLOUDFLARE_ACCOUNT_ID`, `ELEVENLABS_VOICE_ID`, `GOOGLE_CLOUD_PROJECT`.

### Confirmed *not* exposed

Redacted before they left the box, and verified against the transcript:
`SECRET_KEY`, `MEMORY_ENCRYPTION_KEY`, `ELEVENLABS_API_KEY`,
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_ENDPOINT_URL`, and the
Vertex service-account JSON (which is a mounted file and never an env value).

`MEMORY_ENCRYPTION_KEY` is the one to be most careful with in future: rotating it
does not re-encrypt anything, so a rotation orphans every memory ever written
(§11.10). It is the one credential on this list you cannot simply replace.

---

## 2. The procedure

Do all the keys in one pass — the box restarts once either way.

### Rotate at the vendor

- **Cloudflare** — dashboard → My Profile → API Tokens → **Roll** the Workers AI
  token. Rolling keeps the token's permissions and issues new material, which is
  less error-prone than creating a fresh one and re-scoping it.
- **Google TTS** — Cloud console → APIs & Services → Credentials → the
  Text-to-Speech API key → **Regenerate key**. While there, restrict it to the
  Text-to-Speech API if it is not already; an unrestricted `AIza…` key is usable
  against every enabled API on the project.

### Put them on the box

```sh
gcloud compute ssh app-server --zone=asia-south1-a
cd ~/app
cp .env .env.backup-$(date -u +%Y%m%dT%H%M%SZ)
nano .env          # replace CLOUDFLARE_API_TOKEN and GOOGLE_TTS_API_KEY
```

Or, from a laptop whose own `.env` already holds the new values:

```sh
DRY_RUN=1 sh infra/push-credentials.sh ullasgowda3523@34.180.8.71   # look first
sh infra/push-credentials.sh ullasgowda3523@34.180.8.71
```

### Restart and verify

```sh
docker compose -f infra/docker-compose.yml --profile app up -d --force-recreate web worker
docker compose -f infra/docker-compose.yml exec -T web python manage.py quota --clear all
docker compose -f infra/docker-compose.yml exec -T web python manage.py launch_check --live
```

`quota --clear all` matters: every failure during the outage wrote a provider
cooldown, so a box fixed at 17:59 still refuses to try until 18:04 and looks
unfixed (`apps/media/quota.py`).

Then confirm `credentials.cloudflare` and `credentials.google-tts` both read OK,
and play one beat on the live site — a picture and a voice.

### Clean up

The `.env.backup-*` files on the box still contain the old keys. Once the new
ones are verified, delete the backups from before the rotation. Dead keys are
not dangerous, but a directory of historical credentials is a habit worth not
forming.

---

## 3. Fix the leak, not just the key

`GOOGLE_TTS_API_KEY` did not escape through carelessness. `preflight.py` sends it
as a **query parameter**:

```python
f"https://texttospeech.googleapis.com/v1/voices?key={key}"
```

httpx logs every request URL at INFO, so the key lands in the worker log, in
`launch_check` output, and in anything anyone pastes from either.

The synthesis path already gets this right — `tts.py` puts the key in an
`X-Goog-Api-Key` header, with a comment explaining that a URL ends up in logs.
The preflight probe was written later and did not inherit the lesson.

**Fixed and deployed 15 August 2026**, before the key was rotated — deliberately
in that order, because rotating first would have re-exposed the replacement on
the next `launch_check --live`. The probe now sends `X-Goog-Api-Key`, and
`test_preflight.test_the_google_key_never_reaches_the_url` asserts the key is
absent from the URL and present in the header.

That ordering — **fix the leak, then rotate** — is the general rule, and it is
the one worth carrying to the next incident. A rotation into an unfixed leak
buys nothing but a second dead key.

---

## 4. Habits that would have caught both of these

- **Never paste `docker compose config` output.** It expands every `${VAR}`. Use
  `cat infra/docker-compose.yml` — the raw file holds no values — and only reach
  for `config` when you specifically need to see resolved interpolation, with
  the output piped through a redactor.
- **Prefer `--dry-run` and structural output** over anything that resolves
  secrets.
- **Keep credentials out of URLs**, always, and out of argv — an `ssh` command
  line is visible in `ps` to every user on both machines
  (`infra/push-credentials.sh` already routes its payload over stdin for exactly
  this reason).
- **Delete the paste file.** Terminal output saved to a scratch file in the repo
  is one `git add -A` away from being permanent.
