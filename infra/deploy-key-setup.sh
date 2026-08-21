#!/usr/bin/env bash
#
# Wire GitHub Actions to app-server. Run this **once**, from anywhere `gcloud` is
# authenticated — your laptop or Cloud Shell, either is fine, because everything
# it does to the box goes through `infra/remote.sh`, which picks a transport that
# works on this machine.
#
#     bash infra/deploy-key-setup.sh
#
# After it finishes, pushing to `main` deploys. Nothing else to do.
#
# ---------------------------------------------------------------------------
# What it creates, and what each thing can do
# ---------------------------------------------------------------------------
# A dedicated ed25519 key pair whose only job is this deploy:
#
#   * the **public** half is appended to `~/.ssh/authorized_keys` on app-server;
#   * the **private** half becomes the `DEPLOY_SSH_KEY` secret on the repository's
#     `production` environment, and is then deleted from this machine.
#
# It is a full login to the box as your user, so treat it as one: it is scoped to
# the `production` environment (so a workflow on a fork or a pull request cannot
# read it), it is separate from your own SSH key (so revoking it does not lock you
# out), and revoking it is one line, printed at the end.
#
# It does not touch `.env`, the database, Caddy, or anything the app is running.
# The only change to the box is one line in one file.

set -euo pipefail

INSTANCE="${INSTANCE:-app-server}"
ZONE="${ZONE:-asia-south1-a}"
KEY_NAME="${KEY_NAME:-primer-github-deploy}"
REPO="${REPO:-}"

say()  { printf '    %s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
die()  { printf '\nSETUP FAILED: %s\n' "$*" >&2; exit 1; }

HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REMOTE="$HERE/remote.sh"
[ -f "$REMOTE" ] || die "infra/remote.sh is missing; it is how this reaches the box"

# The Windows installer puts gcloud on the *user* PATH, which a shell started
# before the install will not have — the same lookup remote.sh does, for the
# same reason.
if command -v gcloud >/dev/null 2>&1; then
    GCLOUD=gcloud
elif [ -f "${LOCALAPPDATA:-}/Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd" ]; then
    GCLOUD="$LOCALAPPDATA/Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd"
else
    die "gcloud is not installed. Run this from Cloud Shell, or install the SDK."
fi
command -v ssh-keygen >/dev/null 2>&1 \
    || die "ssh-keygen is not on PATH (Git Bash has it; cmd.exe does not)."

WORK="$(mktemp -d)"
# The private key must not outlive this script, including if it fails halfway.
trap 'rm -rf "$WORK"' EXIT INT TERM

# ---------------------------------------------------------------------------
# 1. Find the box
# ---------------------------------------------------------------------------
step "looking up $INSTANCE in $ZONE"
# remote.sh already knows how to find the IP and how to derive the remote
# username — which is *not* your local one, and getting that wrong is how a key
# ends up authorising a user the app does not run as.
TARGET="$(QUIET=1 bash "$REMOTE" --target 2>/dev/null | tr -d '\r\n')" \
    || die "cannot reach $INSTANCE — is gcloud pointed at project 'vidhye'?"
REMOTE_USER="${TARGET%@*}"
IP="${TARGET#*@}"
[ -n "$IP" ] || die "$INSTANCE has no external IP; GitHub's runners cannot reach it"
say "external IP  $IP"
say "ssh user     $REMOTE_USER"

# Prove the login before changing anything on the box.
ACTUAL="$(bash "$REMOTE" 'whoami' 2>/dev/null | tr -d '\r\n')"
[ "$ACTUAL" = "$REMOTE_USER" ] \
    || die "expected to log in as $REMOTE_USER but got '${ACTUAL:-nothing}'"

# OS Login rewrites authorized_keys from directory metadata, so a line appended
# by hand can be silently reverted. Better to say so now than to have the first
# deploy fail with 'Permission denied (publickey)' and no clue why.
OSLOGIN="$("$GCLOUD" compute instances describe "$INSTANCE" --zone "$ZONE" \
    --format='value(metadata.items.filter("key:enable-oslogin").extract("value"))' 2>/dev/null || true)"
case "$OSLOGIN" in
    *TRUE*|*true*)
        say ""
        say "!! OS Login is enabled on this instance."
        say "   It manages authorized_keys, so the line added below may not survive."
        say "   If the first deploy fails with 'Permission denied (publickey)', add"
        say "   the key through OS Login instead:"
        say "     gcloud compute os-login ssh-keys add --key-file=<the .pub>"
        say ""
        ;;
esac

# ---------------------------------------------------------------------------
# 2. Make a key that exists only for this
# ---------------------------------------------------------------------------
step "generating the deploy key pair"
ssh-keygen -t ed25519 -N '' -C "$KEY_NAME" -f "$WORK/key" >/dev/null
say "ed25519, no passphrase (a workflow cannot type one)"
say "comment      $KEY_NAME  — this is how you will recognise it later"

# ---------------------------------------------------------------------------
# 3. Install the public half on the box
# ---------------------------------------------------------------------------
step "authorising it on $INSTANCE"
PUB="$(cat "$WORK/key.pub")"
# Over stdin, and idempotent: re-running this script replaces the previous line
# for the same comment rather than growing the file.
bash "$REMOTE" <<REMOTE_SCRIPT >/dev/null
set -eu
mkdir -p ~/.ssh && chmod 700 ~/.ssh
touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys
grep -v ' $KEY_NAME\$' ~/.ssh/authorized_keys > ~/.ssh/authorized_keys.next || true
echo '$PUB' >> ~/.ssh/authorized_keys.next
mv ~/.ssh/authorized_keys.next ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
REMOTE_SCRIPT
say "added to $REMOTE_USER@$INSTANCE:~/.ssh/authorized_keys"

step "checking the key actually logs in"
if ssh -i "$WORK/key" -o BatchMode=yes -o StrictHostKeyChecking=no \
       -o ConnectTimeout=15 "$REMOTE_USER@$IP" 'echo ok' 2>/dev/null | grep -q ok; then
    say "connected, and ran a command. This is the same path the workflow takes."
else
    die "the key was installed but cannot log in. Port 22 may be closed to the
    internet — GitHub's runners come from arbitrary addresses. Check:
      gcloud compute firewall-rules list --filter='allowed.ports:22'"
fi

# ---------------------------------------------------------------------------
# 4. Pin the host key, so the workflow is not trusting-on-first-use forever
# ---------------------------------------------------------------------------
step "recording the host key"
KNOWN="$(ssh-keyscan -H "$IP" 2>/dev/null)"
[ -n "$KNOWN" ] || say "!! ssh-keyscan returned nothing; the workflow will fall back to TOFU"

# ---------------------------------------------------------------------------
# 5. Hand the private half to GitHub
# ---------------------------------------------------------------------------
step "storing the secrets on the repository"

if [ -z "$REPO" ] && command -v git >/dev/null 2>&1; then
    REPO="$(git config --get remote.origin.url 2>/dev/null \
        | sed -E 's#^.*github\.com[:/]##; s#\.git$##')" || true
fi

if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1 && [ -n "$REPO" ]; then
    say "using the gh CLI on $REPO"
    # Create the environment first. `gh secret set --env production` 404s against
    # an environment that does not exist yet, and this is a fresh repository as
    # far as environments go — the workflow references `production` but nothing
    # has ever created it. Idempotent: PUT on an existing environment is a no-op,
    # and it sets no protection rules, so it does not quietly add an approval
    # step nobody asked for.
    gh api -X PUT "repos/$REPO/environments/production" >/dev/null 2>&1         || say "!! could not create the 'production' environment; secrets may fail"

    # `--env production` rather than a repository secret. An environment secret is
    # only readable by a job that declares `environment: production`, which is the
    # release job and nothing else — so a workflow added later, or one running from
    # a fork's pull request, cannot read a live production login.
    gh secret set DEPLOY_SSH_KEY     --repo "$REPO" --env production < "$WORK/key"
    printf '%s' "$IP"          | gh secret set DEPLOY_HOST        --repo "$REPO" --env production
    printf '%s' "$REMOTE_USER" | gh secret set DEPLOY_USER        --repo "$REPO" --env production
    if [ -n "$KNOWN" ]; then
        printf '%s' "$KNOWN"   | gh secret set DEPLOY_KNOWN_HOSTS --repo "$REPO" --env production
    fi
    say "set: DEPLOY_SSH_KEY, DEPLOY_HOST, DEPLOY_USER, DEPLOY_KNOWN_HOSTS"
    say ""
    say "Push to main and it deploys."
else
    say "no usable gh CLI (install it, or run: gh auth login) — paste these by hand."
    say ""
    say "GitHub -> Settings -> Environments -> production -> Environment secrets"
    say "(create the environment if it does not exist; leave 'Required reviewers'"
    say "unticked unless you want to approve every deploy)"
    printf '\n--- DEPLOY_HOST ---\n%s\n' "$IP"
    printf '\n--- DEPLOY_USER ---\n%s\n' "$REMOTE_USER"
    printf '\n--- DEPLOY_KNOWN_HOSTS ---\n%s\n' "$KNOWN"
    printf '\n--- DEPLOY_SSH_KEY ---\n'
    cat "$WORK/key"
    printf -- '--- end ---\n\n'
    say "Copy the private key including both BEGIN and END lines."
    say "It is not written anywhere on this machine and is gone when this exits,"
    say "so if you lose it, re-run this script — it is safe to run again."
fi

cat <<CLOSING

Done. What now exists:

  * pushing to main runs the eval gate, and deploys if it is green
  * a failed health check on the box reverts the deploy by itself
  * nothing on your laptop is involved in a deploy any more

To revoke this key later — it is one line, identified by its comment:

    bash infra/remote.sh "sed -i '/ $KEY_NAME\$/d' ~/.ssh/authorized_keys"

Your own SSH access is untouched by that.
CLOSING
