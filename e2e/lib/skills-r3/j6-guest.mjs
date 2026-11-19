import { execFile, spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { cp, mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { promisify } from 'node:util';
import { requireValue } from './canonical.mjs';

const run = promisify(execFile);
const TOKEN = /^[a-z0-9][a-z0-9-]{2,40}$/;

// J6 VM container guest (synthetic fixture, NOT an E2B/Simulation template).
//
// Topology on the cloud VM, one pair of containers per run:
//   guest   - installed SDK + application wheels, attached ONLY to an internal
//             docker network (no route off the VM, no host mounts);
//   sidecar - the only reachable peer (alias skills-fakes): authenticated
//             registry relay with an outage switch, deterministic model and an
//             independent business backend with its own idempotent ledger.
// Evidence class everywhere: "synthetic fixture, VM container guest, not E2B/Simulation".
export const GUEST_EVIDENCE_CLASS = 'synthetic fixture, VM container guest, not E2B/Simulation';

const docker = async (args, options = {}) => (await run('docker', args, { maxBuffer: 8 * 1024 * 1024, encoding: 'utf8', ...options })).stdout;

export async function startGuest({ runId, wheelsDir, fixtureDir, upstream = 'http://app:8000', stackNetwork = 'futureagi-e2e-standalone_default' }) {
  requireValue(process.platform === 'linux', 'The guest runs on the approved cloud VM only');
  requireValue(TOKEN.test(runId), 'Invalid run id');
  const label = `ctxhub-j6-${runId}`;
  const names = { network: `${label}-int`, sidecar: `${label}-fakes`, guest: `${label}-app`, volume: `${label}-state`, image: `ctxhub-j6-guest:${runId}` };
  const token = randomBytes(24).toString('hex');
  const build = await mkdtemp(resolve(tmpdir(), 'j6-guest-build-'));
  const created = [];
  const fail = async (error) => { await stopGuestResources(names).catch(() => {}); throw error; };
  try {
    await mkdir(resolve(build, 'wheels'));
    await cp(wheelsDir, resolve(build, 'wheels'), { recursive: true });
    await cp(resolve(fixtureDir, 'Dockerfile'), resolve(build, 'Dockerfile'));
    await cp(resolve(fixtureDir, 'relay.py'), resolve(build, 'relay.py'));
    await docker(['build', '-q', '-t', names.image, build]);
    created.push('image');
    const labels = ['--label', 'purpose=ctxhub-test', '--label', `ctxhub.j6.run=${runId}`];
    await docker(['network', 'create', '--internal', ...labels, names.network]);
    await docker(['volume', 'create', ...labels, names.volume]);
    // The sidecar reaches the product through the stack network; the guest cannot.
    await docker(['run', '-d', '--name', names.sidecar, ...labels, '--network', stackNetwork,
      '-p', '127.0.0.1::8090', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--pids-limit', '256',
      '-e', `J6_REGISTRY_UPSTREAM=${upstream}`, '-e', `J6_CONTROL_TOKEN=${token}`, '-e', 'J6_BUSINESS_DB=/home/guest/business.sqlite',
      '--entrypoint', 'python', names.image, '/opt/relay/relay.py']);
    await docker(['network', 'connect', '--alias', 'skills-fakes', names.network, names.sidecar]);
    // The volume is created root-owned; a one-off container hands exactly that path to the guest user.
    await docker(['run', '--rm', '--user', '0', '-v', `${names.volume}:/state`, '--entrypoint', 'sh', names.image, '-c', 'mkdir -p /state/out /state/requests && chown -R 10001 /state']);
    await docker(['run', '-d', '--name', names.guest, ...labels, '--network', names.network,
      '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--pids-limit', '256', '--memory', '1g',
      '--read-only', '--tmpfs', '/tmp:rw,size=64m', '--tmpfs', '/home/guest:rw,uid=10001,size=64m',
      '-v', `${names.volume}:/state`, '--entrypoint', 'sleep', names.image, 'infinity']);
    const port = (await docker(['port', names.sidecar, '8090/tcp'])).trim().split(':').at(-1);
    const control = `http://127.0.0.1:${port}`;
    const guest = attachGuest({ names, control, token, build });
    for (let attempt = 0; attempt < 50; attempt += 1) {
      try { await guest.business(); break; } catch { await new Promise((done) => setTimeout(done, 200)); }
      requireValue(attempt < 49, 'The sidecar did not become ready');
    }
    return guest;
  } catch (error) { return fail(error); }
}

/** Connect to a guest another process created (for example the registered driver). */
export function attachGuest({ names, control, token, build = null }) {
  requireValue(typeof control === 'string' && /^http:\/\/127\.0\.0\.1:\d+$/.test(control) && typeof token === 'string' && token.length >= 32, 'Guest control is the VM loopback only');
  let envDir = build;
  const call = async (method, path, body) => {
    const response = await fetch(`${control}${path}`, { method, headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body), redirect: 'error', signal: AbortSignal.timeout(15_000) });
    requireValue(response.ok, `Guest control ${path} failed`);
    return response.json();
  };
  const guest = {
    names, evidenceClass: GUEST_EVIDENCE_CLASS,
    // Environment for a command: written to a private env-file, never to argv.
    async envFile(name, values) {
      envDir ??= await mkdtemp(resolve(tmpdir(), 'j6-guest-env-'));
      const path = resolve(envDir, `${name}.env`);
      await writeFile(path, Object.entries(values).map(([key, value]) => `${key}=${value}`).join('\n') + '\n', { mode: 0o600 });
      return path;
    },
    execArgs(envFile, command) { return ['exec', '--env-file', envFile, names.guest, ...command]; },
    async put(path, content) {
      requireValue(path.startsWith('/state/'), 'Guest files live under /state');
      await new Promise((done, reject) => {
        const child = spawn('docker', ['exec', '-i', names.guest, 'sh', '-c', `cat > ${path} && chmod 600 ${path}`], { stdio: ['pipe', 'ignore', 'pipe'] });
        let diagnostics = '';
        child.stderr.on('data', (chunk) => { diagnostics = (diagnostics + String(chunk)).slice(-2048); });
        child.once('error', reject);
        child.once('exit', (code) => (code === 0 ? done() : reject(new Error(`guest write failed: ${diagnostics}`))));
        child.stdin.end(content);
      });
    },
    async read(path) { requireValue(path.startsWith('/state/'), 'Guest files live under /state'); return docker(['exec', names.guest, 'cat', path]); },
    async exists(path) { try { await docker(['exec', names.guest, 'test', '-e', path]); return true; } catch { return false; } },
    async remove(path) { requireValue(path.startsWith('/state/'), 'Guest files live under /state'); await docker(['exec', names.guest, 'rm', '-f', path]); },
    async execute(envFile, command) {
      try { const { stdout, stderr } = await run('docker', guest.execArgs(envFile, command), { encoding: 'utf8', maxBuffer: 4 * 1024 * 1024 }); return { code: 0, stdout, stderr }; }
      catch (error) { return { code: error.code, stdout: error.stdout ?? '', stderr: error.stderr ?? '' }; }
    },
    // Independent controls and ledgers (control token, not an application credential).
    seedOrder: (orderId, age = 30, amount = 25) => call('POST', '/control/orders', { order_id: orderId, age, amount }),
    faults: (body) => call('POST', '/control/faults', body),
    ledger: () => call('GET', '/control/ledger'),
    business: () => call('GET', '/control/business'),
    // Egress attestation: only the sidecar answers; the public internet does not.
    async egress() {
      const probe = `import socket,sys\nfor host,port in (('skills-fakes',8081),('1.1.1.1',443),('8.8.8.8',53)):\n    s=socket.socket(); s.settimeout(3)\n    try: s.connect((host,port)); print(host,'open')\n    except Exception as e: print(host,'closed')\n    finally: s.close()\n`;
      const out = await docker(['exec', names.guest, 'python', '-c', probe]);
      return Object.fromEntries(out.trim().split('\n').map((line) => line.split(' ')));
    },
    describe: () => ({ names, control, token }),
    async stop() { await stopGuestResources(names); if (envDir) await rm(envDir, { recursive: true, force: true }); return verifyGone(names); },
  };
    return guest;
}

async function stopGuestResources(names) {
  for (const args of [['rm', '-f', names.guest], ['rm', '-f', names.sidecar]]) await docker(args).catch(() => {});
  await docker(['network', 'rm', names.network]).catch(() => {});
  await docker(['volume', 'rm', '-f', names.volume]).catch(() => {});
  await docker(['image', 'rm', '-f', names.image]).catch(() => {});
}

// Teardown is qualified only when an exact-name listing proves every resource is gone.
export async function verifyGone(names) {
  const listed = await Promise.all([
    docker(['ps', '-a', '--filter', `name=${names.guest}`, '--filter', `name=${names.sidecar}`, '--format', '{{.Names}}']),
    docker(['network', 'ls', '--filter', `name=${names.network}`, '--format', '{{.Name}}']),
    docker(['volume', 'ls', '--filter', `name=${names.volume}`, '--format', '{{.Name}}']),
    docker(['image', 'ls', '--format', '{{.Repository}}:{{.Tag}}']),
  ]);
  return { qualified: listed[0].trim() === '' && listed[1].trim() === '' && listed[2].trim() === '' && !listed[3].split('\n').includes(names.image) };
}
