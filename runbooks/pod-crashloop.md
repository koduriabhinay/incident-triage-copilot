# Pod CrashLooping

## Overview
This runbook covers Kubernetes pods stuck in CrashLoopBackOff. The kubelet
restarts the container repeatedly with exponential backoff; the service loses
capacity with each crashing replica.

## Symptoms
- Alert: `PodCrashLooping` firing for a deployment
- `kubectl get pods` shows CrashLoopBackOff with rising restart counts
- Reduced serving capacity; possible elevated latency or errors

## Immediate mitigation
1. Inspect the crash logs: `kubectl logs <pod> --previous`.
2. If the crash started after a deploy, roll back: `kubectl rollout undo deployment/<name>`.
3. Check resource limits: OOMKilled (exit 137) means the memory limit is too low; raise it temporarily.
4. Verify config and secrets mounted correctly; a missing env var is the most common non-code cause.
5. Cordon the bad version by scaling the previous ReplicaSet back up if rollback is slow.

## Likely causes
1. Bad container image from a recent deploy
2. Memory limit too low, causing OOMKills under load
3. Missing or malformed config, secrets, or environment variables
4. Failed readiness probe dependency (e.g., cannot reach the database at startup)

## Escalation
Notify the owning service team on-call. If more than 50% of replicas are crashing, treat as SEV2 and consider declaring an incident.

## Verification
1. Confirm all pods are in Running state with zero restarts over 10 minutes.
2. Confirm readiness probes are passing.
3. Confirm service error rate and latency are back to baseline.
