# ADR 002: Event streaming

Date: 2026-04-11.

## Decision
We use Kafka instead of RabbitMQ for events between services.

## Reasons
Kafka lets consumers replay past events, which we need to rebuild the tracking read model.
Events are retained for 7 days. Partitions are keyed by delivery id to keep ordering per delivery.
