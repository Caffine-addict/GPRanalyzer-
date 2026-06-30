# ADR-0001: Platform Architecture

## Status

Accepted

## Context

The project started as an ICT analytics application but is intended to evolve into a complete manufacturing intelligence platform supporting multiple machine types, assembly-line analytics, predictive maintenance, and enterprise integrations.

## Decision

Adopt a modular platform architecture based on independent domains:

- Core
- Factory
- Machine Types
- Analytics
- Services
- Integrations
- ML
- Digital Twin
- Presentation

Machine-specific implementations will be isolated from shared platform functionality.

## Consequences

### Positive

- Easy to extend
- Supports multiple machine types
- Cleaner testing
- Reduced coupling
- Enterprise-ready architecture

### Negative

- Slightly more boilerplate
- Additional packages during early development