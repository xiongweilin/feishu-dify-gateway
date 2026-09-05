# Rebuild configuration

This document records the current non-secret configuration shape and values. Secret values are stored outside Git and are restored from the Bitwarden item ratio-rebuild-local-compose-secrets.

## Source

- Working tree: D:\infrastructure\compose\feishu-dify-gateway
- Compose and application files in this repository remain the service-definition source of truth.
- The active .env file is a local deployment input and is intentionally not committed.

## Compose and bootstrap files

- .pytest_cache/README.md
- compose.yaml
- deploy/cloud/install-cloud-secrets.sh
- deploy/cloud/install-runtime.sh
- Dockerfile
- README.md

## Current environment files

No active .env file exists in this working tree. Use the repository deployment instructions and the documented file-mounted secret mechanism.
## Rebuild rules

1. Restore the repository at the path expected by the compose files and scripts.
2. Restore Docker volumes and SQL dumps from D:\agent\docker备份 before starting dependent application services.
3. Materialize the secret variables from Bitwarden without committing them to Git.
4. Follow the repository README, compose files, and deployment scripts for start order and health checks.
5. A running container is not sufficient; run the project verification checks after startup.
