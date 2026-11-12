# Omega Hub runner host

The Omega Hub checker (`services/omega-hub-checker`) runs each upload's keyless
test and demo in a container of the runner image, with the network off, a
read-only root, pid, memory and CPU caps, and the upload's code as a non-root
user with no capabilities (the checker's judge in that container keeps only
`CAP_SETUID` and `CAP_SETGID` to start it so).
Those containers run on **this daemon**: Docker in Docker whose default and
only accepted runtime is [gVisor](https://gvisor.dev) (`runsc`), so an upload
that breaks out of its container meets gVisor's kernel, not the host's. The
checker itself holds only its token and the client certificate below; it never
uses the application host's Docker.

| File                 | What it is                                                                       |
| -------------------- | -------------------------------------------------------------------------------- |
| `Dockerfile`         | `docker:27.5.1-dind` pinned by digest, plus gVisor 20261005.0 checked by SHA-512 |
| `daemon.json`        | `default-runtime: runsc`                                                         |
| `docker-compose.yml` | The runner host of a production install, on its own VM                           |

The daemon listens only on TCP 2376 with mutual TLS. On its first start the
`docker:dind` entrypoint makes a CA, a server certificate for the names in
`DOCKER_TLS_SAN`, and one client certificate (`/certs/client`: `ca.pem`,
`cert.pem`, `key.pem`). Only a holder of that client certificate can use the
daemon. It must run privileged (with `userns_mode: host` where the host's
Docker remaps user namespaces): gVisor needs to create namespaces and trap
system calls.

## Where it runs

- **Standalone and Distributed** (`docker-compose.yml`,
  `docker-compose.distributed.yml`): the `hub-runner` service, next to the
  stack, on the stack's network; nothing publishes its port. Both `hub-runner`
  and `hub-checker` are **opt-in**, behind the `hub` profile. Neither starts by
  default. Add `hub` to `COMPOSE_PROFILES` in `.env` (for example,
  `COMPOSE_PROFILES=hub` or `COMPOSE_PROFILES=ml,hub`), then run `./bin/install`
  or `./bin/install --distributed`, respectively. Both installers preserve
  the profile selection; an exported `COMPOSE_PROFILES` takes precedence.
  `hub-checker` mounts the client certificate volume read-only. Turning off
  a profile does not stop already-running containers: stop the checker and
  runner explicitly before removing `hub` from the selection.
- **Production** (`deploy/docker-compose.production.yml`): a VM of its own,
  with this directory's `docker-compose.yml`. The co-located `hub-runner` does
  not start, and the checker dials `OMEGA_HUB_RUNNER_DOCKER_HOST`.
- **Helm**: the same VM; the chart's checker dials `hub.checker.runner.dockerHost`
  with the Secret `hub.checker.runner.tlsSecret` (README.md of the chart, "Omega Hub").

## Set up the production runner host

1. A Linux VM (4 vCPU, 8 GB, 50 GB disk is enough for one checker) with Docker
   Engine 24+ and Compose v2.24+. Nothing else of the install runs on it.
2. Firewall it:
   - inbound: TCP 2376 from the checker's addresses only (the app host, or the
     cluster's node or pod range), plus your own SSH;
   - outbound: only the registry the runner image comes from. Uploads run with
     the network off; the daemon needs egress only to pull that image.
   - never the cloud metadata address from containers (the daemon's containers
     use `--network none`; the firewall is the second line).
3. Start it from a checkout of this repository:

   ```bash
   cd deploy/hub-runner
   OMEGA_HUB_RUNNER_HOST_NAME=hub-runner.example.internal docker compose up -d --build --wait
   ```

   Add `OMEGA_HUB_RUNNER_HOST_SAN_EXTRA=IP:10.20.0.7` when the checker dials an
   address rather than a name.

4. Copy the client certificate to the checker's side, then delete the copy here:

   ```bash
   docker compose cp hub-runner:/certs/client ./client   # ca.pem cert.pem key.pem
   # Compose production: put them in OMEGA_HUB_RUNNER_TLS_DIR on the app host (0440)
   # Helm: kubectl create secret generic futureagi-hub-runner-client \
   #         --from-file=client/ca.pem --from-file=client/cert.pem --from-file=client/key.pem
   rm -r ./client
   ```

5. Check it from the checker's side: `docker info` must say `runsc`.

   ```bash
   DOCKER_HOST=tcp://hub-runner.example.internal:2376 DOCKER_TLS_VERIFY=1 DOCKER_CERT_PATH=./client \
     docker info --format '{{.DefaultRuntime}}'
   ```

The checker's entrypoint (`services/omega-hub-checker/bin/start.sh`) checks the
same before it claims any job, and runs one container of the runner image to
see gVisor's kernel. It refuses (exit 65) a daemon whose default runtime is
not `runsc`, one without `runsc`, and a `runsc` that is not gVisor.

## The runner image

Every upload runs in the runner image the release manifest names
(`scripts/omega-local/release-manifest.json`, `runtimes.hubRunner`), built from
one Omega commit with `services/omega-hub-checker/runner/build.sh`. The checker
pulls it onto the runner daemon when it is missing. Until the release
publishes it, build it and load it onto the daemon yourself:

```bash
C=881b05ba5867e643b3a8af9b3fdfb96df9709825; D=$(mktemp -d)
git -C <omega checkout> archive "$C" | tar -x -C "$D"
services/omega-hub-checker/runner/build.sh "$D" "$C"        # omega-hub-runner:881b05b
# Compose (Standalone or Distributed):
docker save omega-hub-runner:881b05b | docker compose exec -T hub-runner docker load
# A production runner host:
docker save omega-hub-runner:881b05b | DOCKER_HOST=tcp://<runner host>:2376 DOCKER_TLS_VERIFY=1 \
  DOCKER_CERT_PATH=./client docker load
```

In production, name it by digest (`OMEGA_HUB_RUNNER_IMAGE`,
`hub.checker.runner.image`).

## Rotating the certificates

Stop the daemon, delete the `hub-runner-ca`, `hub-runner-server` and
`hub-runner-client` volumes, start it again (a new CA and certificates), and
replace the checker's copy. A check in flight fails and its job is claimed
again when its lease expires.
