# Helm chart validation record

This file records the validation performed while introducing the chart. It is
not a substitute for the repeatable CI checks in `deploy/helm/test-chart.sh`.

## Static validation

The chart passes strict Helm linting and rendering for:

- the default bundled installation;
- the reduced Minikube profile;
- the production AWS S3 profile;
- ingress enabled;
- the privileged code executor with a pre-created Secret; and
- all datastores configured as external services.

Negative rendering verifies that production mode cannot use bundled MinIO.

The test script also renders and packages the chart. CI runs the same checks for
every pull request that changes the chart.

## Minikube validation

Tested on 2026-08-26 with Minikube 1.38.1, Kubernetes 1.34.0, the Docker driver,
and containerd. Kubernetes admitted the release resources. The frontend,
AgentCC gateway, PostgreSQL, ClickHouse, Redis, RabbitMQ, and MinIO reached their
ready state. A no-op `helm upgrade` preserved every generated Secret value.

The complete smoke test could not finish on the test machine because Docker
Desktop's shared 50 GB virtual disk filled while containerd unpacked the backend
image. Temporal also encountered DNS upstream timeouts in the rootless Docker
network. These were host-runtime constraints rather than manifest admission
errors. Re-run the commands in the chart README on a Minikube runtime with at
least 15 GB free before installation and working cluster DNS; the backend image
is the dominant disk consumer.

The disposable test profile was removed after validation. No pre-existing
Minikube profiles, Docker images, or Docker volumes were deleted.
