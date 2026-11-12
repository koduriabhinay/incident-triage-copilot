# API High Error Rate

## Overview
This runbook covers elevated 5xx error rates on public API services behind the
edge load balancer. Typical causes are a bad deploy, a saturated downstream
dependency, or an exhausted connection pool.

## Symptoms
- 5xx rate above 1% sustained for 5 minutes on any public endpoint
- P99 latency climbing alongside the error rate
- Alert: `APIHighErrorRate` firing in PagerDuty

## Immediate mitigation
1. Check the deploy timeline: if errors started within 15 minutes of a release, roll back to the previous version with `deploy rollback <service>`.
2. Shed load by enabling the circuit breaker for the failing downstream in the service config.
3. If a single AZ is affected, drain traffic from it at the load balancer.
4. Scale the service horizontally: `kubectl scale deployment <service> --replicas=<2x current>`.
5. As a last resort, serve stale cached responses for read endpoints while the root cause is fixed.

## Likely causes
1. Bad deploy introducing a regression in request handling
2. Downstream dependency (database, cache) saturated or failing
3. Connection pool exhaustion under traffic spike
4. Misconfigured rate limiter blocking legitimate traffic

## Escalation
Page the owning service team on-call. If the error rate exceeds 10% or checkout/payment paths are affected, declare a SEV1 incident and assign an incident commander.

## Verification
1. Confirm 5xx rate is below 0.1% for 15 consecutive minutes.
2. Confirm P99 latency has returned to baseline.
3. Spot-check checkout and login flows end to end before resolving.
