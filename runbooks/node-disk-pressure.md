# Node Disk Pressure

## Overview
This runbook covers Kubernetes nodes entering DiskPressure, which causes the
kubelet to evict pods and can cascade into service disruption. Common culprits
are runaway container logs, unrotated volumes, or large image layers.

## Symptoms
- Alert: `NodeDiskPressure` firing
- Pods evicted with reason `Evicted: node had disk pressure`
- `df -h` on the node shows usage above 85%

## Immediate mitigation
1. Identify the disk hog: `du -sh /var/lib/docker/* /var/log/containers/*`.
2. Truncate oversized container logs and force log rotation.
3. Run image garbage collection: `crictl rmi --prune` for unused images.
4. Drain the node if pressure persists: `kubectl drain <node> --ignore-daemonsets`, then investigate offline.
5. Move affected workloads by cordoning the node so the scheduler avoids it.

## Likely causes
1. Container writing unbounded logs to stdout without rotation
2. Application writing temp files or caches to the container filesystem
3. Accumulated unused container images on long-lived nodes
4. Persistent volume claim growing beyond its requested size

## Escalation
Notify the infrastructure on-call in the team channel. If multiple nodes are affected, treat as SEV2.

## Verification
1. Confirm node disk usage is below 75% and the DiskPressure condition is cleared.
2. Confirm evicted pods have been rescheduled and are Running.
3. Confirm the log rotation or retention fix is deployed so it does not recur.
