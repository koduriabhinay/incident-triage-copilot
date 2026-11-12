# Database Failover

## Overview
This runbook covers primary database failover events for the managed Postgres
clusters. A failover can be triggered by primary node failure, AZ evacuation,
or a manual switchover. Expect a brief write outage during promotion.

## Symptoms
- Alert: `PostgresFailoverInitiated` firing
- Application logs show connection refused or read-only transaction errors
- Write latency spikes followed by a short write outage (typically 30-90 seconds)
- Replication lag alert on the old primary

## Immediate mitigation
1. Confirm which node is the new primary: `pg-status --cluster orders-db`.
2. Verify application connection pools have drained stale connections; restart app pods if they hold onto the old primary.
3. If writes are still failing after 3 minutes, check that the new primary has been promoted out of recovery mode.
4. Throttle non-critical write workloads (analytics backfill, batch jobs) until replication is healthy.
5. Do NOT fail back to the old primary without DBA approval.

## Likely causes
1. Primary node hardware failure or kernel panic
2. AZ evacuation by the cloud provider
3. Primary disk full, causing Postgres to stop accepting writes
4. Network partition isolating the primary from replicas

## Escalation
Page the database on-call immediately. If write downtime exceeds 5 minutes, declare a SEV1 incident.

## Verification
1. Confirm the new primary accepts writes: run a canary insert through the app.
2. Confirm at least one replica is streaming with lag under 5 seconds.
3. Confirm application error rates are back to baseline before resolving.
