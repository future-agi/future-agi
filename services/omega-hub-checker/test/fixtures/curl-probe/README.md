# A network probe for the sandbox

## What it does

It tries six ways out of the process: `fetch`, a socket, a socket opened in a worker thread, a DNS lookup, `curl` and a Node child process. Each one aims at an address that is never routed, and the probe prints whether it was stopped.

## Run it with no key

```bash
omega hub run curl-probe
```

It prints one line per probe, and each says `stopped`.

## How it works

- `probes.mjs` holds the six attempts and records an error code for each one that fails.
- `main.mjs` prints one line per attempt.
- The test fails if any attempt reaches the network.

## Make it yours

- Add another kind of attempt to `probes.mjs`.
- Point the probes at a server you run on your own machine to see one get through.
