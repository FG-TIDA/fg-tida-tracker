# Render CPU saturation and downtime

For the current 1 CPU / 2 GB web service, use `WEB_CONCURRENCY=2`. More workers
do not create more CPU and can increase contention during document processing.
Do not raise timeouts to disguise a repeatedly failing request. Retain SMTP,
GitHub schedules and `FGTIDA_UPLOAD_SCAN_REQUIRED=true`.

## Changes in this update

- Full file-maintenance walks run every 15 minutes rather than every minute.
  Pending/error originals are retried between those passes. New uploads still
  receive their normal synchronous security checks; nothing unscanned is released.
- Known recent archive digests skip redundant original reads. Due/unknown files
  are still loaded and checked, with the existing one-day rescan threshold.
  Trusted administrator videos no longer need to be loaded and hashed every cycle.
  Download/image routes still check current permissions and actual content hashes.
- Background maintenance runs with lower scheduling priority. Mail/GitHub/files
  start after 60/90/120 seconds to avoid simultaneous cold imports. Mail retains
  its normal retry cadence. Maintenance logs command durations.
- Sanitized document fragments are cached by content and exact URL/image context.
  Safe image validation is cached by exact bytes and type. AV rejection still
  blocks parent packages and images even when a preview was cached earlier.
- The default Gunicorn worker count is two, matching the deployment guide.

No new services, queue instances or schema changes are required for these changes.
These are identified code inefficiencies, not proof of the exact production
failure. Render logs and runtime samples are needed to establish why it restarts.

## Manual production checks

1. In Render select the **web service**, then **Metrics**. Compare CPU and memory
   at the same timestamps as **Events** restarts/deploys. ClamAV has separate metrics.
2. Under **Settings**, verify the health check path is `/health/`, not `/healthz`.
   The application health endpoint checks database connectivity and host validity.
3. Under **Environment**, verify `WEB_CONCURRENCY=2`. Save with the reviewed
   performance commit in one planned deployment. A service with a persistent
   disk has a deployment interruption; avoid repeated trial deployments.
4. After deployment, in the web service **Shell** run:

   ```sh
   python deploy/runtime_report.py
   ```

   Take a sample while CPU is high and another while idle. The script reads Linux
   process/cgroup statistics for one second; it does not load Django, connect to
   PostgreSQL, open documents, scan files, send mail or print arguments/secrets.
   `job:scan_fgtida_files`, `job:sync_fgtida_github`, `parser:*` and `web:gunicorn`
   distinguish common consumers. Processes starting inside the sample are omitted
   from CPU deltas; repeat if a short job was missed. Per-process RSS is not a sum
   of unique memory. Cgroup CPU throttling data is shown when available.
5. In **Logs**, inspect the same time window for `WORKER TIMEOUT`, `SIGKILL`,
   out-of-memory messages and maintenance duration lines. Share those redacted
   lines and runtime samples rather than environment-variable values.

If low traffic still causes saturation after the fix, use the samples to decide
whether a particular renderer, sync operation or queue needs isolation or further
optimization. Do not assume a paid CPU upgrade or a new worker is necessary
without identifying the load first.

Persistent-disk deployments cannot be zero downtime under Render's current model:
https://render.com/docs/deploys and https://render.com/docs/disks.
