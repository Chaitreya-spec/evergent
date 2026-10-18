# Backup, restore and rollback

Two things hold state: the PostgreSQL database (accounts, projects,
workflows, tasks, events, usage, audit log, encrypted provider keys) and the
file store (uploaded attachments). Generated files live in the database.
Redis holds only transient queue and event-bus traffic and needs no backup.

## Back up

```bash
infrastructure/docker/backup.sh
```

writes `backups/<UTC timestamp>/` with:

- `evergent.dump` — `pg_dump` in custom format,
- `storage.tar.gz` — the local file store (skip if you use S3; version the
  bucket instead),
- `alembic-revision.txt` and `images.json` — the schema revision and image
  tags the data belongs to.

Schedule it (cron, systemd timer) and copy the directory off the host:

```cron
15 3 * * *  cd /srv/evergent && infrastructure/docker/backup.sh && rclone copy backups remote:evergent-backups
```

The dump contains provider keys encrypted with `ENCRYPTION_KEY`. Store
`ENCRYPTION_KEY` separately from the backups (a password manager or secret
store): without it, stored keys cannot be decrypted; with it and a backup,
they can.

## Restore

```bash
infrastructure/docker/restore.sh backups/20261015T031500Z
```

It asks for confirmation, stops the API, worker and frontend, replaces the
database and files, and starts them again. Restore with the same
`ENCRYPTION_KEY` and with images at the schema revision recorded in
`alembic-revision.txt` (or newer — migrations run forward on start).

Rehearse a restore on a scratch host at least once.

## Roll back a release

1. Back up (`backup.sh`).
2. If the release added migrations, downgrade while still on the new image:
   `docker compose run --rm api alembic downgrade <revision from the old release>`.
3. Switch images: `git checkout <old sha>` then
   `IMAGE_TAG=<old sha> docker compose --profile tls up -d`.
4. If a downgrade is not possible (data-destroying migration), restore the
   backup taken before the update instead.

## Rotating secrets

- `SECRET_KEY`: change it and restart the API. Every session ends (users sign
  in again).
- `ENCRYPTION_KEY`: keys stored through the UI were encrypted with the old
  key. Before rotating, note which providers use stored keys (Models &
  providers shows "Stored key"); after rotating, enter those keys again.
- `POSTGRES_PASSWORD`: `ALTER USER evergent PASSWORD '…'` inside Postgres,
  then update `.env` and restart.
