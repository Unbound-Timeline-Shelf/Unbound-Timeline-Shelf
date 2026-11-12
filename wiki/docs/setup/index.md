# Setup and deployment

Unnamed Tracking App has a production Docker image and a separate development
Compose stack. The production Compose file is `src/docker-container/compose.yaml`.
It includes PostgreSQL, persistent application data, and a separately isolated
plugin runtime. The runtime is built from the repository checkout.

## Production

Clone the [application repository](https://github.com/Unbound-Timeline-Shelf/Unbound-Timeline-Shelf)
on the Docker host and use its production Compose file. Set `POSTGRES_PASSWORD`,
`SECRET_KEY`, `PRIMARY_USER_PASSWORD`, and `PLUGIN_RUNTIME_TOKEN` in the environment;
see [Production Docker Image](../deployment/production-docker.md) for the value
requirements, volumes, HTTPS and startup behavior. The Compose file defaults to
the moving `main` image; set `UNNAMED_TRACKING_APP_VERSION` to an available release
tag for production. GHCR currently denies anonymous pulls, so the Docker host must
be authenticated with an account that can access the image.

When using Arcane or another Compose manager, provide the repository checkout as
the project context so the plugin runtime's relative build path resolves. Supply
the required environment values through that manager and retain the complete
Compose configuration, including the runtime and database services.

## Development

The root `compose.yaml` runs PostgreSQL, the backend, and the frontend separately.
See [Installation](installation.md) for development startup commands and
[Environment Variables](environment-variables.md) for configuration.
