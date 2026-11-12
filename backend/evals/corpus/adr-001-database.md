# ADR 001: Database choice

Date: 2026-03-02. Decided by Priya (tech lead).

## Decision
We use PostgreSQL instead of MongoDB.

## Reasons
- We need multi-row transactions when a delivery changes owner.
- PostGIS gives us geospatial queries (distance, route corridors) out of the box.
- The team already knows SQL well.
