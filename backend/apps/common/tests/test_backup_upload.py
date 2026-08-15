"""The off-provider copy (PRODUCT.md §16.10, Docs/Runbooks/Backup-Restore.md).

The nightly dump lands on the same disk as the database it copies. This command
is the second layer — and the reason it is tested at the level of *what it
refuses* rather than *what it uploads* is that one of its refusals is the most
expensive mistake available in the whole configuration.

A database dump is every family in the product: parent names, children's names,
their memories. `AWS_STORAGE_BUCKET_NAME` is served publicly through
`MEDIA_CDN_BASE_URL`, because a four-year-old's illustration has to load without
a credential. Setting the two to the same value is one autocomplete away in a
`.env`, produces no error from S3, and would be discovered by somebody who is not
us.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


@pytest.fixture
def dump(tmp_path: Path) -> Path:
    """A file large enough to pass the "is this actually a database" floor."""
    path = tmp_path / "primer-20260815T121805Z.dump"
    path.write_bytes(b"PGDMP" + b"\0" * 4096)
    return path


class _FakeS3:
    """Records what it was asked to do. No network, no credentials."""

    def __init__(self) -> None:
        self.uploaded: list[tuple[str, str, str]] = []
        self.deleted: list[str] = []
        self.objects: list[dict[str, Any]] = []

    def upload_file(self, filename: str, bucket: str, key: str) -> None:
        self.uploaded.append((filename, bucket, key))

    def get_paginator(self, _name: str) -> Any:
        objects = self.objects

        class _Paginator:
            def paginate(self, **_kwargs: Any) -> Any:
                return [{"Contents": objects}]

        return _Paginator()

    # Capitalised parameter names because boto3's are, and this is called by
    # keyword — a lowercase signature here would pass the test and fail the box.
    def delete_objects(self, Bucket: str, Delete: dict[str, Any]) -> None:
        self.deleted.extend(item["Key"] for item in Delete["Objects"])


@pytest.fixture
def s3(monkeypatch: Any) -> _FakeS3:
    from apps.common.management.commands import backup_upload

    fake = _FakeS3()
    monkeypatch.setattr(backup_upload, "_client", lambda: fake)
    return fake


def test_a_dump_in_the_public_media_bucket_is_refused(
    settings: Any, dump: Path, s3: _FakeS3
) -> None:
    """The one this command exists for as much as the upload.

    S3 would accept it happily; the CDN would then serve it. Nothing downstream
    of this point is capable of noticing, so the refusal has to be here.
    """
    settings.AWS_STORAGE_BUCKET_NAME = "primer-media"
    settings.BACKUP_S3_BUCKET = "primer-media"

    with pytest.raises(CommandError, match="served publicly"):
        call_command("backup_upload", str(dump))

    assert s3.uploaded == []


def test_an_unset_bucket_is_a_skip_rather_than_a_failure(
    settings: Any, dump: Path, s3: _FakeS3
) -> None:
    """A deployment without the second layer still has the first one.

    The nightly cron calls this straight after a successful dump. Failing here
    would trade a real backup for a louder complaint about a missing one.
    """
    settings.BACKUP_S3_BUCKET = ""

    call_command("backup_upload", str(dump))

    assert s3.uploaded == []


def test_a_dump_goes_up_under_the_configured_prefix(settings: Any, dump: Path, s3: _FakeS3) -> None:
    settings.AWS_STORAGE_BUCKET_NAME = "primer-media"
    settings.BACKUP_S3_BUCKET = "primer-backups"
    settings.BACKUP_S3_PREFIX = "db/"

    call_command("backup_upload", str(dump))

    assert s3.uploaded == [(str(dump), "primer-backups", "db/primer-20260815T121805Z.dump")]


def test_an_error_message_with_a_dump_extension_is_refused(
    settings: Any, tmp_path: Path, s3: _FakeS3
) -> None:
    """Same floor as `backup.sh`, for the same reason.

    A few hundred bytes is a failed `pg_dump` writing to stdout. Uploaded, it
    becomes the newest off-site copy and the retention sweep eventually removes
    the real ones behind it.
    """
    settings.BACKUP_S3_BUCKET = "primer-backups"
    tiny = tmp_path / "primer-tiny.dump"
    tiny.write_bytes(b"could not connect to server")

    with pytest.raises(CommandError, match="not a database"):
        call_command("backup_upload", str(tiny))

    assert s3.uploaded == []


def test_a_missing_file_is_named_rather_than_traced(settings: Any, s3: _FakeS3) -> None:
    settings.BACKUP_S3_BUCKET = "primer-backups"

    with pytest.raises(CommandError, match="No such dump"):
        call_command("backup_upload", "/nowhere/primer-nope.dump")


class TestRetention:
    """§12.8. The disclosure states how long a copy of a family is kept, and a
    permanent remote copy makes that sentence false in the way that matters —
    the one nobody remembers exists."""

    def _aged(self, name: str, days: int) -> dict[str, Any]:
        import datetime

        return {
            "Key": f"db/{name}",
            "LastModified": datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days),
        }

    def test_expired_dumps_are_swept(self, settings: Any, dump: Path, s3: _FakeS3) -> None:
        settings.BACKUP_S3_BUCKET = "primer-backups"
        settings.BACKUP_S3_PREFIX = "db/"
        settings.BACKUP_RETENTION_DAYS = 7
        s3.objects = [
            self._aged("primer-old.dump", days=30),
            self._aged("primer-recent.dump", days=1),
        ]

        call_command("backup_upload", str(dump), "--prune")

        assert s3.deleted == ["db/primer-old.dump"]

    def test_the_sweep_only_touches_things_this_backup_wrote(
        self, settings: Any, dump: Path, s3: _FakeS3
    ) -> None:
        """Scoped by name as well as by age and prefix.

        A sweep that deleted by age alone would empty a bucket somebody had put
        something else in — and it would do it on a schedule, at night.
        """
        settings.BACKUP_S3_BUCKET = "primer-backups"
        settings.BACKUP_S3_PREFIX = "db/"
        settings.BACKUP_RETENTION_DAYS = 7
        s3.objects = [
            self._aged("primer-old.dump", days=30),
            self._aged("someone-elses-archive.tar.gz", days=400),
            self._aged("notes.txt", days=90),
        ]

        call_command("backup_upload", str(dump), "--prune")

        assert s3.deleted == ["db/primer-old.dump"]

    def test_nothing_expired_deletes_nothing(self, settings: Any, dump: Path, s3: _FakeS3) -> None:
        settings.BACKUP_S3_BUCKET = "primer-backups"
        settings.BACKUP_RETENTION_DAYS = 7
        s3.objects = [self._aged("primer-recent.dump", days=2)]

        call_command("backup_upload", str(dump), "--prune")

        assert s3.deleted == []
