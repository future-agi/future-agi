// Ephemeral, local test CA. No private key is tracked or shipped from the host.
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
if (!existsSync("/certs/cert.pem") || !existsSync("/certs/key.pem")) {
  execFileSync(
    "openssl",
    [
      "req",
      "-x509",
      "-newkey",
      "rsa:2048",
      "-nodes",
      "-days",
      "7",
      "-keyout",
      "/certs/key.pem",
      "-out",
      "/certs/cert.pem",
      "-subj",
      "/CN=Gateway walk fixture CA",
      "-addext",
      "basicConstraints=critical,CA:TRUE",
      "-addext",
      "subjectAltName=DNS:localhost,IP:127.0.0.1,DNS:mcp.e2e-public.test,DNS:k2.e2e-public.test,DNS:siem.e2e-public.test",
    ],
    { stdio: "ignore" },
  );
}
