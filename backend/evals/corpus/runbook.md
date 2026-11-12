# Operations runbook

## On-call
On-call rotates weekly; alerts go to PagerDuty. The rotation changes every Monday at 10:00.

## Restarting the tracking service
Run `kubectl rollout restart deploy/tracking` and watch the rollout with
`kubectl rollout status deploy/tracking`.

## Backups
PostgreSQL is backed up nightly at 02:00 UTC. Backups are kept for 30 days in a separate bucket.
