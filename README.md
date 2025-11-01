# dual-channel-transport-protocol
Hybrid two-channel transport for games: reliable (in-order) + unreliable (low-latency) over UDP, with SR+SACK. This program is dedicated as submission for Assignment 4 CS3103.

## Getting Started
These instructions will help you set up and run the dual-channel transport protocol on your local machine for development and testing purposes.

### Prerequisites
- Python 3.11
- poetry >= 1.7

### Installation
```bash
poetry install
poetry run pre-commit install
```

Quick Checks:
```bash
poetry run python --version
poetry run pre-commit run --all-files
```
