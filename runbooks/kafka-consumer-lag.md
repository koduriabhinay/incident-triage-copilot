# Kafka Consumer Lag

## Overview
This runbook covers sustained consumer lag on Kafka topics feeding the order
processing and notification pipelines. Lag means consumers are falling behind
producers; if it grows unbounded, downstream SLAs break.

## Symptoms
- Alert: `KafkaConsumerLagHigh` firing (lag above 100k messages for 10 minutes)
- Order confirmation emails delayed
- Consumer group lag dashboard trending upward

## Immediate mitigation
1. Identify the lagging consumer group: `kafka-lag --group order-processor`.
2. Check whether consumers are crashlooping or stuck; restart unhealthy consumer pods.
3. Temporarily scale up consumer replicas: `kubectl scale deployment order-consumer --replicas=<2x current>`.
4. If a poison-pill message is blocking a partition, skip it via the dead-letter tool after confirming with the owning team.
5. Pause non-critical producers (marketing batch) to let consumers catch up.

## Likely causes
1. Consumer pods crashlooping on a poison-pill message
2. Traffic spike exceeding provisioned consumer throughput
3. Slow downstream dependency (database) throttling consume speed
4. Rebalance storm from unstable consumer group membership

## Escalation
Notify the data platform on-call in the team channel. If order processing delay exceeds 30 minutes, declare a SEV2 incident.

## Verification
1. Confirm consumer lag is trending down and below 10k messages.
2. Confirm no partitions are stuck (all partitions have a live consumer).
3. Confirm downstream order confirmation latency is back to baseline.
