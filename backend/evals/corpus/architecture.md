# Atlas Architecture

## Services
Atlas is a delivery tracking platform. The API gateway is written in Python with FastAPI.
The tracking service is written in Go and computes live delivery positions.
Data lives in PostgreSQL 16; Redis is used as a cache in front of it.
Events between services go through Kafka.

## Public API
We expose a REST API rather than gRPC because REST has much wider client support:
customers integrate from browsers, low-code tools and shell scripts.

## Deployment
All services run on Kubernetes in Google Kubernetes Engine (GKE), region europe-west3.
