"""Copy a dump off the box that made it (PRODUCT.md §16.10).

    manage.py backup_upload <dump-file>
    manage.py backup_upload <dump-file> --prune

The nightly dump lands in `<app>/backups`, on the same disk as the database it
is a copy of. `Docs/Runbooks/Backup-Restore.md` is explicit that this is only the
first of two layers: it survives a dropped table, a bad migration and a corrupted
index, and none of the failures where the disk, the VM or the cloud account goes
away. This is the second layer, and it is the one that makes "we have backups" a
true sentence about a fire rather than about a typo.

**Written as a management command rather than a shell script with `aws` in it**
for one reason: boto3 is already in the image, and asking an operator to install
and configure a second CLI on the box is how the off-provider copy stays a
to-do. `infra/backup/cron-backup.sh` calls this straight after a successful dump,
through the container that already holds the credentials.

**Retention is not optional here either.** `apps/accounts/privacy.py` tells
parents how long a copy of their family is kept, and a dump that expires on the
VM while a permanent one sits in a bucket makes that sentence false in the way
that matters — the copy that outlives the promise is the one nobody remembers
exists. `--prune` sweeps the same `BACKUP_RETENTION_DAYS` window over the remote
prefix, and only over that prefix.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Upload a database dump to the off-provider backup bucket."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("dump", help="Path to the .dump file to upload.")
        parser.add_argument(
            "--prune",
            action="store_true",
            help=(
                "After uploading, delete remote dumps older than "
                "BACKUP_RETENTION_DAYS. Only ever under BACKUP_S3_PREFIX."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        bucket = str(settings.BACKUP_S3_BUCKET or "").strip()
        if not bucket:
            # Not an error. A deployment that has not set this up yet still has
            # its local dump, and failing the nightly cron over a missing second
            # layer would trade a real backup for a louder complaint about a
            # missing one.
            self.stdout.write("BACKUP_S3_BUCKET is not set — skipping the off-site copy.")
            return

        # The guard this command exists to enforce as much as the upload itself.
        # The media bucket is served publicly through MEDIA_CDN_BASE_URL; a dump
        # is every family in the product. These two must never be the same name,
        # and the mistake is one autocomplete away in a `.env`.
        if bucket == str(settings.AWS_STORAGE_BUCKET_NAME or "").strip():
            raise CommandError(
                f"BACKUP_S3_BUCKET is the media bucket ({bucket!r}). That bucket is "
                "served publicly over the CDN, and a database dump in it would put "
                "every family's data behind a public URL. Use a separate, private "
                "bucket."
            )

        path = Path(options["dump"])
        if not path.is_file():
            raise CommandError(f"No such dump: {path}")

        size = path.stat().st_size
        if size < 1024:
            # Same floor as `backup.sh`. An error message with a `.dump`
            # extension must not become the newest off-site copy.
            raise CommandError(f"Refusing a {size}-byte dump: that is not a database.")

        client = _client()
        key = f"{str(settings.BACKUP_S3_PREFIX or '').lstrip('/')}{path.name}"

        client.upload_file(str(path), bucket, key)
        self.stdout.write(self.style.SUCCESS(f"Uploaded {path.name} ({size} bytes) to {key}"))

        if options["prune"]:
            removed = _prune(client, bucket)
            self.stdout.write(
                f"Retention: kept {settings.BACKUP_RETENTION_DAYS} days, "
                f"removed {removed} expired remote dump(s)"
            )


def _client() -> Any:
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=settings.BACKUP_S3_ENDPOINT_URL,
        aws_access_key_id=settings.BACKUP_S3_ACCESS_KEY_ID,
        aws_secret_access_key=settings.BACKUP_S3_SECRET_ACCESS_KEY,
        # R2 ignores the region but boto3 insists on one being present.
        region_name=getattr(settings, "AWS_S3_REGION_NAME", None) or "auto",
    )


def _prune(client: Any, bucket: str) -> int:
    """Delete remote dumps past the retention window. Returns how many.

    Scoped three ways on purpose: to `BACKUP_S3_PREFIX`, to objects whose name
    matches the one `backup.sh` produces, and to objects older than the window.
    A sweep that deleted by age alone would empty a bucket somebody had put
    something else in.
    """
    prefix = str(settings.BACKUP_S3_PREFIX or "").lstrip("/")
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
        days=settings.BACKUP_RETENTION_DAYS
    )

    expired: list[dict[str, str]] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            name = str(obj["Key"]).rsplit("/", 1)[-1]
            if not (name.startswith("primer-") and name.endswith(".dump")):
                continue
            if obj["LastModified"] < cutoff:
                expired.append({"Key": obj["Key"]})

    if not expired:
        return 0

    # `delete_objects` caps at 1000 keys per call. At one dump a day the loop is
    # theatre, and it is three lines cheaper than finding out it was not.
    for start in range(0, len(expired), 1000):
        client.delete_objects(Bucket=bucket, Delete={"Objects": expired[start : start + 1000]})
    return len(expired)
