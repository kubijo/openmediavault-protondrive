# Backing up Immich and Audiobookshelf

The plugin copies the directories selected in a backup set. Its archive checksum verifies the copy after capture; it
does not make live application files a consistent snapshot. Let each application create its own database backup, then
include that backup and its media in the set. Keep the same volume paths when restoring, or update the applications'
mounts before starting them.

## Immich

Enable Immich's automatic database dumps in **Administration → Settings → Database Dump Settings**, or create a dump in
**Administration → Job Queues** before a manual backup. Select the host directory mapped to `UPLOAD_LOCATION`; this
includes `backups/` and the uploaded photos and videos. Add any external library paths separately, including paths
mounted outside `UPLOAD_LOCATION`. If Immich data folders were moved to other host locations, select those too. Do not
rely on a live copy of `DB_DATA_LOCATION` as the database backup.

Pair the database dump with the media copy from the same backup run. Immich recommends copying the database first and
the files second if the server remains active; pausing writes during capture gives a stronger match. A daily dump can be
much older than the media capture, so inspect its date before trusting the pair. Immich's restore flow needs both the
dump and the files it references. See
[Immich's backup and restore guide](https://docs.immich.app/administration/backup-and-restore/).

## Audiobookshelf

Enable Audiobookshelf backups on its **Backups** settings page. By default, it writes them to `/metadata/backups`; the
location can be changed. Select the host directory that contains those backups, plus every host directory mounted for
books, podcasts, and other media. Include the host directory mapped to `/metadata` if you also want its other metadata
and covers. The application backup contains its SQLite database and covers in `metadata/`, but no media files or covers
stored alongside library items. A copy of the live `/config` SQLite file is not a substitute for the application backup.
Check the newest backup's date and size before trusting it.

Restore the media to its expected paths, then use Audiobookshelf's backup restore flow. Its backup documentation warns
that restoring between installation methods can lose progress or metadata. See
[Audiobookshelf's backup guide](https://audiobookshelf.org/docs/documentation/server-management/backups/) and
[Docker mount guide](https://audiobookshelf.org/docs/documentation/install/docker/).

## Current verification limit

The plugin does not yet require a fresh application backup file or prove that it matches the copied media. The status
therefore reports capture consistency as unverified. If using an OMV Compose backup job, also inspect that job's log:
its copy failures are not always reflected in the job's exit status.
