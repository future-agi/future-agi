import { once } from 'node:events';
import { spawn, type ChildProcessByStdio } from 'node:child_process';
import { createInterface } from 'node:readline';
import type { Readable } from 'node:stream';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import { E2E } from './env';

interface ReadyMessage {
  ready: true;
  base_url: string;
  metadata_url: string;
  metadata: string;
}

export interface LocalSamlIdp {
  baseUrl: string;
  metadataUrl: string;
  metadata: string;
  setIdentity(email: string): Promise<void>;
  stop(): Promise<void>;
}

const E2E_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const REPO_ROOT = path.dirname(E2E_DIR);
const SERVER = path.join(E2E_DIR, 'lib', 'saml_idp_server.py');
const DEFAULT_PYTHON = path.join(REPO_ROOT, 'futureagi', '.venv', 'bin', 'python');
type IdpProcess = ChildProcessByStdio<null, Readable, Readable>;

function readReady(child: IdpProcess): Promise<ReadyMessage> {
  return new Promise((resolve, reject) => {
    const lines = createInterface({ input: child.stdout });
    const timeout = setTimeout(() => reject(new Error('local SAML IdP did not become ready')), 15_000);
    const fail = (message: string) => {
      clearTimeout(timeout);
      lines.close();
      reject(new Error(message));
    };
    child.once('error', error => fail(`could not start local SAML IdP: ${error.message}`));
    child.once('exit', (code, signal) => fail(`local SAML IdP exited before ready (${code ?? signal})`));
    lines.on('line', line => {
      try {
        const message = JSON.parse(line) as ReadyMessage;
        if (message.ready && message.base_url && message.metadata_url && message.metadata) {
          clearTimeout(timeout);
          lines.close();
          resolve(message);
        }
      } catch {
        // The Python server emits exactly one JSON readiness line. Ignore any
        // incidental library diagnostics until it either becomes ready or exits.
      }
    });
  });
}

/**
 * Start the Python fixture signer on loopback.  The Python side owns key
 * generation and calls the same pysaml2+xmlsec signer as backend acceptance
 * tests, so browser and S-layer signatures cannot drift apart.
 */
export async function startLocalSamlIdp(): Promise<LocalSamlIdp> {
  const python = process.env.E2E_SAML_PYTHON ?? DEFAULT_PYTHON;
  const child = spawn(
    python,
    [SERVER, '--port', '0', '--acs-url', `${E2E.apiUrl}/saml2_auth/acs/`, '--audience', E2E.apiUrl],
    {
      cwd: REPO_ROOT,
      env: {
        ...process.env,
        PYTHONPATH: [path.join(REPO_ROOT, 'futureagi'), process.env.PYTHONPATH]
          .filter(Boolean)
          .join(path.delimiter),
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  );
  const ready = await readReady(child);

  return {
    baseUrl: ready.base_url,
    metadataUrl: ready.metadata_url,
    metadata: ready.metadata,
    async setIdentity(email: string): Promise<void> {
      const response = await fetch(`${ready.base_url}/__identity`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      });
      if (!response.ok) throw new Error(`local SAML IdP identity setup failed: ${response.status}`);
    },
    async stop(): Promise<void> {
      if (child.exitCode !== null || child.signalCode !== null) return;
      try {
        await fetch(`${ready.base_url}/__shutdown`, { method: 'POST' });
        await Promise.race([
          once(child, 'exit'),
          new Promise((_, reject) => setTimeout(() => reject(new Error('local SAML IdP did not stop')), 5_000)),
        ]);
      } catch {
        child.kill('SIGTERM');
        await once(child, 'exit');
      }
    },
  };
}
