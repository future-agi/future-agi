import { createHash } from 'node:crypto';
import { execFileSync, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test, expect } from '@playwright/test';
import { parse } from 'yaml';
import {
  GATEWAY_PROFILES,
  OTLP_SINK_ENDPOINT,
  PROFILE_DELTAS,
  PROFILE_FILES,
  profileConfig,
  validateProfileSource,
  type WalkGatewayProfile as GatewayProfile,
} from '../lib/profile';
import { allowedPatterns, RedisProbe, RedisProbeRefused, validateRedisCommand } from '../lib/redis-probe';
import { ASSETS_PENDING_HOSTS, classify, isLocal, WalkBoundary } from '../lib/walk/boundary';
import { chromiumStrictArgs, FIREFOX_STRICT_PREFS, protocolInterceptScript, protocolLinks } from '../lib/walk/browsers';
import { configSwitchVerdicts, priceFeedVerdict } from '../lib/walk/census';
import { REFUSAL_FORMAT, writeManifest } from '../lib/value-free-seal.mjs';

// The walk harness's own tests (GATEWAY-WALK-PLAN-706 H1-H17). Every case here
// is offline: no stack, no network (the browser cases render inline content and
// abort everything else). Run: bin/e2e test harness/walk-harness.spec.ts --grep offline
const repo = (rel: string) => fileURLToPath(new URL(`../../${rel}`, import.meta.url));
const read = (rel: string) => readFileSync(repo(rel), 'utf8');
const stockSource = read('e2e/stack/gateway.e2e.yaml');
const ORG_A = '0b5a4e2c-1111-4c3a-9d2e-aaaaaaaaaaaa';
const ORG_B = '0b5a4e2c-2222-4c3a-9d2e-bbbbbbbbbbbb';

test.describe('walk harness offline: H5 gateway profiles', () => {
  const table = JSON.parse(read('e2e/stack/gateway-profiles.json'));
  delete table._comment;

  test('offline: the table names exactly the profiles lib/profile.ts knows, with the same files', () => {
    expect(Object.keys(table)).toEqual([...GATEWAY_PROFILES]);
    for (const p of GATEWAY_PROFILES) expect(table[p].file, p).toBe(PROFILE_FILES[p]);
    for (const p of GATEWAY_PROFILES.filter((x) => x !== 'stock')) expect(table[p].stacks, p).toEqual(['standalone']);
    for (const p of GATEWAY_PROFILES) {
      for (const overlay of table[p].overlays) {
        expect(
          table[p].requires.some((r: { file: string }) => r.file === overlay),
          `${p} requires ${overlay}`,
        ).toBe(true);
      }
    }
  });

  for (const profile of GATEWAY_PROFILES.filter((p) => p !== 'stock' && PROFILE_DELTAS[p] !== null)) {
    test(`offline: the committed ${profile} file is exactly stock plus its delta`, () => {
      const source = read(PROFILE_FILES[profile]);
      validateProfileSource(profile, stockSource, source);
      expect(parse(source)).toEqual(profileConfig(profile, stockSource));
    });
  }

  test('offline: onekey-cat is owed: no file, no delta, refused before anything starts', () => {
    expect(PROFILE_DELTAS['onekey-cat']).toBeNull();
    expect(existsSync(repo(PROFILE_FILES['onekey-cat']))).toBe(false);
    expect(() => profileConfig('onekey-cat', stockSource)).toThrow('has no declared delta yet (owed)');
  });

  const mutations: [GatewayProfile, string, (c: any) => void, string][] = [
    [
      'mcp',
      'an MCP server',
      (c) => {
        c.mcp.servers = { x: { url: 'https://example.invalid/mcp' } };
      },
      'names an MCP upstream',
    ],
    [
      'mcp-catalog',
      'a sign-in',
      (c) => {
        c.mcp.oauth = { enabled: true };
      },
      'names an MCP upstream',
    ],
    [
      'mcp-otel',
      'another exporter endpoint',
      (c) => {
        c.otel.endpoint = 'http://collector.invalid:4318';
      },
      'other than the capture sink',
    ],
    [
      'pricefeed',
      'another feed source',
      (c) => {
        c.public_price_feed.sources[0].provider_id = 'openrouter';
      },
      'not the synthetic one',
    ],
    [
      'mcp-signin',
      'an extra key',
      (c) => {
        c.cache = { enabled: true };
      },
      'is not stock plus the declared delta',
    ],
    [
      'mcp-otel',
      'bodies off',
      (c) => {
        c.otel.include_bodies = false;
      },
      'is not stock plus the declared delta',
    ],
  ];
  for (const [profile, name, mutate, message] of mutations) {
    test(`offline: a ${profile} file with ${name} is refused`, () => {
      const cfg = parse(read(PROFILE_FILES[profile]));
      mutate(cfg);
      expect(() => validateProfileSource(profile, stockSource, JSON.stringify(cfg))).toThrow(message);
    });
  }

  test('offline: mcp-otel exports only to the capture sink service', () => {
    expect(parse(read(PROFILE_FILES['mcp-otel'])).otel.endpoint).toBe(OTLP_SINK_ENDPOINT);
  });

  test('offline: the readiness script refuses a profile whose requirement is missing, and a wrong stack', async () => {
    const { readiness } = await import('../scripts/gateway-profile.mjs');
    const t = {
      p: {
        file: 'f.yaml',
        stacks: ['standalone'],
        overlays: [],
        services: [],
        unblockedBy: 'X',
        requires: [{ file: 'a.go', contains: 'yaml:"k"' }, { file: 'b.yml' }],
      },
    };
    const files: Record<string, string> = { 'a.go': 'type C struct { K int `yaml:"k"` }' };
    const r = (f: string) => files[f] ?? null;
    expect(readiness(t, 'p', 'standalone', r).missing).toEqual(['b.yml']);
    expect(readiness(t, 'p', 'standalone', () => 'nothing').missing).toEqual(['a.go contains yaml:"k"']);
    expect(() => readiness(t, 'p', 'distributed', r)).toThrow('runs on the standalone stack only');
    expect(() => readiness(t, 'q', 'standalone', r)).toThrow('is not one of: p');
    expect(() =>
      execFileSync('node', [repo('e2e/scripts/gateway-profile.mjs'), 'onekey-cat', 'standalone'], { stdio: 'pipe' }),
    ).toThrow();
    const stock = execFileSync('node', [repo('e2e/scripts/gateway-profile.mjs'), 'stock', 'standalone'], {
      encoding: 'utf8',
    });
    expect(stock.trim()).toBe('file=e2e/stack/gateway.e2e.yaml');
  });
});

test.describe('walk harness offline: H3 RedisProbe', () => {
  const ok = (args: string[]) => expect(() => validateRedisCommand(args, [ORG_A])).not.toThrow();
  const no = (args: string[]) => expect(() => validateRedisCommand(args, [ORG_A])).toThrow(RedisProbeRefused);

  test('offline: only allowlisted reads of owned keys pass', () => {
    ok(['GET', `agentcc:mc:{${ORG_A}}:acct`]);
    ok(['HGETALL', `agentcc:mc:{${ORG_A}}:acct`]);
    ok(['HGET', `agentcc:mc:{${ORG_A}}:acct`, 'spent']);
    ok(['SMEMBERS', 'agentcc:mc:outbox_orgs']);
    ok(['EXISTS', `agentcc:mc:{${ORG_A}}:holds`, 'agentcc:mc:needs_push']);
    ok(['PTTL', `agentcc:approval:{${ORG_A}}:x1`]);
    ok(['XRANGE', `agentcc:mc:{${ORG_A}}:outbox`, '-', '+']);
    ok(['XINFO', 'GROUPS', `agentcc:mc:{${ORG_A}}:outbox`]);
    ok(['SCAN', '0', 'MATCH', `agentcc:mc:{${ORG_A}}*`, 'COUNT', '500']);
    for (const cmd of ['SET', 'DEL', 'HSET', 'SADD', 'EVAL', 'FLUSHDB', 'CONFIG', 'KEYS', 'TYPE', 'DUMP', 'MONITOR']) {
      no([cmd, `agentcc:mc:{${ORG_A}}:acct`]);
    }
    no(['GET', `agentcc:mc:{${ORG_B}}:acct`]);
    no(['GET', 'agentcc:session:abc']);
    no(['EXISTS', `agentcc:mc:{${ORG_A}}:acct`, 'agentcc:other']);
    no(['SCAN', '0', 'MATCH', '*', 'COUNT', '500']);
    no(['SCAN', '0', 'MATCH', 'agentcc:mc:*', 'COUNT', '500']);
    no(['SCAN', '0', 'COUNT', '500']);
    no(['XINFO', 'STREAM', `agentcc:mc:{${ORG_A}}:outbox`]);
    no(['HGET', `agentcc:mc:{${ORG_A}}:acct`]);
    no(['GET', `agentcc:mc:{${ORG_A}}:acct`, 'extra']);
    expect(() => validateRedisCommand(['GET', 'x'], ['not-an-org'])).toThrow('is not an organization id');
    expect(allowedPatterns([ORG_A])).toEqual([
      `agentcc:mc:{${ORG_A}}*`,
      `agentcc:cap:{${ORG_A}}*`,
      `agentcc:approval:{${ORG_A}}*`,
    ]);
  });

  test('offline: an empty redis-cli SCAN response has no owned keys', () => {
    // redis-cli raw output encodes the empty nested key array as a blank line.
    // Do not strip blank lines from generic GET replies: empty string values
    // are data, but no empty key can match an owned SCAN pattern.
    const probe = new RedisProbe([ORG_A], () => '0\n\n');
    expect(probe.keys(ORG_A)).toEqual([]);
    expect(probe.snapshot(ORG_A)).toEqual([]);
    expect(probe.log.every((row) => row.args[0] === 'SCAN')).toBe(true);
  });

  test('offline: keys, sets and snapshots read through the transport; a refused read never reaches it', () => {
    const store: Record<string, string[]> = {
      [`SCAN 0 MATCH agentcc:mc:{${ORG_A}}* COUNT 500`]: ['7', `agentcc:mc:{${ORG_A}}:acct`],
      [`SCAN 7 MATCH agentcc:mc:{${ORG_A}}* COUNT 500`]: ['0', `agentcc:mc:{${ORG_A}}:holds`],
      [`PTTL agentcc:mc:{${ORG_A}}:acct`]: ['-1'],
      [`PTTL agentcc:mc:{${ORG_A}}:holds`]: ['5000'],
      [`GET agentcc:mc:{${ORG_A}}:acct`]: ['WRONGTYPE Operation against a key holding the wrong kind of value'],
      [`HGETALL agentcc:mc:{${ORG_A}}:acct`]: ['granted', '1000000', 'spent', '0'],
      [`GET agentcc:mc:{${ORG_A}}:holds`]: ['WRONGTYPE Operation against a key holding the wrong kind of value'],
      [`HGETALL agentcc:mc:{${ORG_A}}:holds`]: ['WRONGTYPE Operation against a key holding the wrong kind of value'],
      [`SMEMBERS agentcc:mc:{${ORG_A}}:holds`]: ['h2', 'h1'],
      'SMEMBERS agentcc:mc:outbox_orgs': [ORG_B],
    };
    const sent: string[] = [];
    const probe = new RedisProbe([ORG_A], (args) => {
      sent.push(args.join(' '));
      return `${(store[args.join(' ')] ?? []).join('\n')}\n`;
    });
    expect(probe.keys(ORG_A)).toEqual([`agentcc:mc:{${ORG_A}}:acct`, `agentcc:mc:{${ORG_A}}:holds`]);
    expect(probe.inSet('mc:outbox_orgs', ORG_A)).toBe(false);
    expect(probe.snapshot(ORG_A)).toEqual([
      { key: `agentcc:mc:{${ORG_A}}:acct`, pttl: -1, kind: 'hash', value: { granted: '1000000', spent: '0' } },
      { key: `agentcc:mc:{${ORG_A}}:holds`, pttl: 5000, kind: 'set', value: ['h1', 'h2'] },
    ]);
    const before = sent.length;
    expect(() => probe.read('DEL', `agentcc:mc:{${ORG_A}}:acct`)).toThrow(RedisProbeRefused);
    expect(sent.length).toBe(before);
    const failing = new RedisProbe([ORG_A], () => {
      throw Object.assign(new Error('x'), { status: 1, stderr: 'boom\nmore' });
    });
    expect(() => failing.keys(ORG_A)).toThrow('redis transport failed (status 1): boom');
  });
});

test.describe('walk harness offline: H12 boundary and H11 browser settings', () => {
  test('offline: local and in-browser URLs pass; the four asset hosts are ASSETS-PENDING; anything else fails', () => {
    for (const url of [
      'http://localhost:3100/x',
      'http://127.0.0.1:28082/y',
      'ws://localhost:8100/ws',
      'data:text/plain,a',
      'blob:http://localhost:3100/1',
      'about:blank',
    ])
      expect(isLocal(url), url).toBe(true);
    for (const url of ['https://fonts.googleapis.com/css', 'http://localhost.example.invalid/', 'https://10.0.0.1/']) {
      expect(isLocal(url), url).toBe(false);
    }
    expect(ASSETS_PENDING_HOSTS).toEqual([
      'fonts.googleapis.com',
      'api.iconify.design',
      'api.simplesvg.com',
      'api.unisvg.com',
    ]);
    for (const host of ASSETS_PENDING_HOSTS) expect(classify(host)).toBe('ASSETS-PENDING');
    for (const host of ['fonts.gstatic.com', 'api.openai.com', 'update.googleapis.com'])
      expect(classify(host)).toBe('FAIL');
  });

  test('offline: a guarded context aborts and records non-local requests and fails only on non-asset ones', async ({
    browser,
  }) => {
    const boundary = new WalkBoundary('selftest');
    const context = await browser.newContext({ serviceWorkers: 'block' });
    await boundary.install(context, 'ctx');
    await expect(boundary.install(context, 'ctx')).rejects.toThrow('already installed');
    const page = await context.newPage();
    await page.setContent(
      '<img src="https://api.iconify.design/mdi/plus.svg"><link rel="stylesheet" ' +
        'href="https://fonts.googleapis.com/css2?family=Inter">',
    );
    await expect.poll(() => boundary.receipt().attempts.length).toBe(2);
    expect(boundary.iconHostsAborted('ctx')).toBe(true);
    expect(() => boundary.assert()).not.toThrow();
    await page.evaluate(() => fetch('https://collector.example.invalid/v1/traces').catch(() => undefined));
    await expect.poll(() => boundary.receipt().ledger.filter((r) => r.classification === 'FAIL').length).toBe(1);
    expect(() => boundary.assert()).toThrow('https://collector.example.invalid x1');
    const receipt = boundary.receipt();
    expect(receipt.attempts.every((a) => !('url' in a))).toBe(true);
    expect(JSON.stringify(receipt)).not.toContain('/v1/traces');
    await context.close();
    const late = new WalkBoundary('late');
    const used = await browser.newContext();
    await used.newPage();
    await expect(late.install(used, 'x')).rejects.toThrow('before any page opens');
    await used.close();
  });

  test('offline: install links are captured and never opened', async ({ browser }) => {
    const context = await browser.newContext();
    // The page is fulfilled in the browser (never resolved); everything else is aborted.
    const html =
      '<a id="c" href="cursor://anysphere.cursor-deeplink/mcp/install?x=1">c</a>' +
      '<button id="v" onclick="window.open(\'vscode:mcp/install?y\')">v</button>';
    await context.route(
      () => true,
      (route) =>
        route.request().url() === 'http://walk.invalid/'
          ? route.fulfill({ contentType: 'text/html', body: html })
          : route.abort(),
    );
    await context.addInitScript(protocolInterceptScript, ['cursor', 'vscode', 'claude']);
    const page = await context.newPage();
    await page.goto('http://walk.invalid/');
    await page.click('#c');
    await page.click('#v');
    expect(await protocolLinks(page)).toEqual([
      { href: 'cursor://anysphere.cursor-deeplink/mcp/install?x=1', via: 'click' },
      { href: 'vscode:mcp/install?y', via: 'window.open' },
    ]);
    expect(page.url()).toBe('http://walk.invalid/');
    await context.close();
  });

  test('offline: the Firefox strict profile covers every 643 category and Chromium resolves loopback only', () => {
    const keys = Object.keys(FIREFOX_STRICT_PREFS);
    for (const prefix of [
      'app.update.',
      'toolkit.telemetry.',
      'datareporting.',
      'services.settings.server',
      'network.captive-portal-service.',
      'network.connectivity-service.',
      'app.normandy.',
      'app.shield.',
      'browser.safebrowsing.',
      'dom.push.',
      'messaging-system.',
    ]) {
      expect(
        keys.some((k) => k.startsWith(prefix)),
        prefix,
      ).toBe(true);
    }
    for (const [k, v] of Object.entries(FIREFOX_STRICT_PREFS)) {
      if (typeof v === 'string') expect(v, k).toMatch(/^http:\/\/127\.0\.0\.1:9\//);
    }
    const resolver = chromiumStrictArgs().find((a) => a.startsWith('--host-resolver-rules='));
    expect(resolver).toBe('--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1');
    expect(chromiumStrictArgs()).toEqual(
      expect.arrayContaining(['--disable-background-networking', '--disable-component-update']),
    );
  });
});

test.describe('walk harness offline: runner, overlays and copies', () => {
  test('offline: the walk config caps workers at 3 and runs Chromium and Firefox projects', async () => {
    const config = (await import('../playwright.walk.config')).default;
    expect(config.workers).toBeLessThanOrEqual(3);
    expect(config.projects?.map((p) => [p.name, p.use?.browserName])).toEqual([
      ['chromium', 'chromium'],
      ['firefox', 'firefox'],
    ]);
    expect(config.retries).toBe(0);
    expect(config.testMatch).toEqual(['flows/walks/**/*.spec.ts']);
  });

  test('offline: the walk pins keep the networks as shipped and bind every port on loopback (H12)', () => {
    const src = read('e2e/stack/docker-compose.walk-pins.yml');
    expect(src).not.toMatch(/^\s*internal:\s*true/m);
    expect(src).not.toMatch(/extra_hosts/);
    const env = parse(src.replace('!override', '')).services.app.environment;
    expect(env).toMatchObject({
      FUTURE_AGI_LICENSE_URL: 'http://127.0.0.1:9',
      FUTURE_AGI_TELEMETRY_URL: 'http://127.0.0.1:9',
      OLLAMA_HOST: '127.0.0.1:9',
      STRIPE_SECRET_KEY: '',
      STRIPE_API_KEY: '',
      APP_URL: 'http://localhost:3100',
    });
    const ports: string[] = parse(src.replace('!override', '')).services.app.ports;
    expect(ports.length).toBe(6);
    for (const port of ports) expect(port).toMatch(/^127\.0\.0\.1:/);
  });

  test('offline: the replica shares the app network namespace, config file and Redis (H8)', () => {
    const cfg = parse(read('e2e/stack/docker-compose.mcp-replica.yml'));
    const replica = cfg.services['agentcc-gateway-2'];
    expect(replica.network_mode).toBe('service:app');
    expect(replica.environment).toMatchObject({
      AGENTCC_PORT: '8081',
      AGENTCC_REDIS_ADDRESS: '127.0.0.1:6379',
      AGENTCC_CONTROL_PLANE_URL: 'http://127.0.0.1:8000',
    });
    expect(cfg.services.app.environment.AGENTCC_REDIS_ADDRESS).toBe(replica.environment.AGENTCC_REDIS_ADDRESS);
    expect(cfg.services.app.environment.AGENTCC_REDIS_DB).toBe(replica.environment.AGENTCC_REDIS_DB);
    expect(replica.volumes).toEqual([
      './${AGENTCC_CONFIG_PATH:-e2e/stack/gateway.e2e.yaml}:/etc/futureagi/secrets/agentcc.yaml:ro',
    ]);
    expect(replica.restart).toBe('no');
  });

  test('offline: the export sinks are gw/RF-UITRUTH-PLAT2 d3bcfd30a2 byte for byte (H15)', () => {
    const blob = (rel: string) => {
      const b = readFileSync(repo(rel));
      return createHash('sha1').update(`blob ${b.length}\0`).update(b).digest('hex');
    };
    const PLAT2_BLOBS = {
      'e2e/lib/export-sinks.ts': '1591141d309797b552631d4ffcca29be65c0e9e9',
      'e2e/lib/integration-export.ts': '5f1b9edb6a10202f9c8e5ea0039dcf321d30c664',
    };
    for (const [rel, sha] of Object.entries(PLAT2_BLOBS)) expect(blob(rel), rel).toBe(sha);
  });

  test('offline: every group launcher carries the recipe flags (H16)', () => {
    const groups = JSON.parse(read('e2e/walks/launch/groups.json'));
    delete groups._comment;
    const head = execFileSync('git', ['-C', repo(''), 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
    const env: NodeJS.ProcessEnv = { ...process.env, WALK_SHA: head };
    // The enclosing cloud stage may intentionally share one custom lane. This
    // test checks group defaults, not that inherited invocation override.
    delete env.WALK_LANE;
    // Off: the enclosing run may itself be value-free (HR-JRN-01's main-launcher runs).
    delete env.E2E_VALUE_FREE;
    for (const group of Object.keys(groups)) {
      const argv = execFileSync('bash', [repo('e2e/walks/launch/launch.sh'), group, 'print'], { encoding: 'utf8', env })
        .trim()
        .split('\n');
      const flag = (name: string) => argv[argv.indexOf(name) + 1];
      expect(argv, group).toEqual(expect.arrayContaining(['--standard', '--skip-nested']));
      expect(flag('--machine'), group).toBe('n2d-standard-32');
      expect(flag('--on-lost'), group).toBe('fail');
      expect(flag('--life'), group).toBe('4h');
      expect(Number(flag('--timeout').replace(/m$/, '')), group).toBeGreaterThan(60);
      expect(Number(flag('--keep').replace(/m$/, '')), group).toBeGreaterThanOrEqual(30);
      expect(flag('--repo'), group).toMatch(new RegExp(`@${head}$`));
      expect(flag('--lane'), group).toBe(groups[group].lane);
      for (const install of [
        'uv sync --locked',
        'go mod download all',
        'cd frontend && yarn install --frozen-lockfile',
        'cd e2e && yarn install --frozen-lockfile',
        'playwright install chromium firefox',
      ]) {
        expect(flag('--prepare'), `${group} prepare`).toContain(install);
      }
      const pulls = argv.flatMap((arg, i) => (arg === '--pull' ? [argv[i + 1]] : []));
      const lands = [
        'e2e/walk-results/**/steps.txt',
        'e2e/walk-results/**/summary.json',
        'e2e/walk-results/**/counts.json',
        'e2e/walk-results/**/walk-receipt.json',
        'e2e/walk-results/**/command-exit.txt',
        'e2e/FLOWS.md',
        'e2e/walk-results/**/evidence-audit.json',
      ];
      // Every pull is rooted in this launch's landing directory, never in
      // the tree the job wrote; land.sh audits exactly the LANDS globs and lands only
      // verified copies there (scripts/walk-landing.test.mjs runs it).
      const command = argv.slice(argv.indexOf('--') + 1);
      expect(command.slice(0, 2), group).toEqual(['bash', 'e2e/walks/vm/land.sh']);
      const id = command[2];
      expect(id, group).toMatch(/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/);
      expect(pulls, group).toEqual(lands.map((g) => `e2e/walk-landed/${id}/${g}`));
      expect(command[3], group).toBe(String(lands.length));
      expect(command.slice(4, 4 + lands.length), group).toEqual(lands);
      expect(command[4 + lands.length], group).toBe('--');
      expect(flag('--prepare'), group).toContain('go version');
      for (const pin of [
        'FUTURE_AGI_LICENSE_URL=http://127.0.0.1:9',
        'OLLAMA_HOST=127.0.0.1:9',
        'LITELLM_LOCAL_MODEL_COST_MAP=True',
      ])
        expect(argv, group).toContain(pin);
      expect(argv.join(' '), group).not.toMatch(/futureagi\.com|Secret|secrets /);
    }
  });

  test("offline: a value-free launch pulls only each profile's checked export of its publication, value-free reporter first (HR-JRN-01)", () => {
    const groups = JSON.parse(read('e2e/walks/launch/groups.json'));
    delete groups._comment;
    const head = execFileSync('git', ['-C', repo(''), 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
    const env: NodeJS.ProcessEnv = { ...process.env, WALK_SHA: head, E2E_VALUE_FREE: '1' };
    delete env.WALK_LANE;
    for (const [group, g] of Object.entries(groups) as [string, { job: string; profiles?: string[] }][]) {
      const run = () =>
        execFileSync('bash', [repo('e2e/walks/launch/launch.sh'), group, 'print'], {
          encoding: 'utf8',
          env,
          stdio: 'pipe',
        });
      if (g.job !== 'walks') {
        // A gate or hosted job assembles no publish directory: a value-free launch of it is refused, never pulled empty.
        let status: number | null = null;
        try {
          run();
        } catch (error) {
          status = (error as { status: number | null }).status;
        }
        expect(status, `${group}: refused`).toBe(64);
        continue;
      }
      const argv = run().trim().split('\n');
      const pulls = argv.flatMap((arg, i) => (arg === '--pull' ? [argv[i + 1]] : []));
      // Each profile's export directory is `vfout`, a name shorter than any registered value can be.
      expect(pulls, group).toEqual([
        ...g.profiles!.map((p) => `e2e/walk-results/${group}/${p}/vfout/**`),
        'e2e/walk-results/command/command-exit.txt',
      ]);
      expect(argv, group).toEqual(expect.arrayContaining(['--env', 'E2E_VALUE_FREE=1']));
      expect(argv.join(' '), group).not.toMatch(/walk-receipt|summary\.json|steps\.txt|counts\.json|FLOWS\.md/);
      // The job runs under the value-free job wrapper, which lands the exit file and then runs the final boundary check
      // over exactly these pulls (the same list, the exit file among them).
      expect(argv.slice(argv.indexOf('--') + 1), group).toEqual([
        'bash',
        'e2e/walks/vm/value-free-job.sh',
        'e2e/walk-results/command/command-exit.txt',
        ...pulls,
        '--',
        'bash',
        'e2e/walks/vm/run-walks.sh',
        group,
        head,
      ]);
    }
    // On the VM: the reporter override names the value-free reporter first, each profile publishes into its own directory
    // with its own registry below the job's, and the seal check exports what it vouches for before the job ends; off, the
    // override is exactly what it was.
    const vm = read('e2e/walks/vm/run-walks.sh');
    expect(vm).toContain('--reporter=./lib/value-free-reporter.ts,line,./walks/vm/counts-reporter.cjs');
    expect(vm).toContain('--reporter=line,./walks/vm/counts-reporter.cjs');
    expect(vm).toContain('export E2E_VALUE_FREE_PUBLISH="$P/value-free-publish"');
    expect(vm).toContain('export E2E_VALUE_FREE_REGISTRY="$E2E_VALUE_FREE_REGISTRIES/$profile"');
    // Through the publication choke point, its output neither captured nor echoed (it joins the held job output).
    expect(vm).toContain(
      'node e2e/lib/value-free-boundary.mjs export "$E2E_VALUE_FREE_PUBLISH" "$P/vfout" || seal_rc=$?',
    );
    // The walk config turns trace, screenshot and video off in value-free mode (the ScopeActors fixture refuses them).
    expect(read('e2e/playwright.walk.config.ts')).toMatch(/use: withValueFreeUse\(\{/);
  });

  // The VM's own seal step (run-walks.sh, run as written) on a walk-results tree, and the
  // launch's own pulls (launch.sh print): an intact publication or an intact refusal record is transferred; nothing from a
  // rejected publication, whose files (sources included) stay where they are on the machine, and the walk fails.
  test('offline: a value-free walk transfers nothing from a rejected publication and leaves its sources intact (HR-JRN-01)', ({}, testInfo) => {
    const groups = JSON.parse(read('e2e/walks/launch/groups.json'));
    const group = Object.keys(groups).find(
      (g) => !g.startsWith('_') && groups[g].job === 'walks' && groups[g].profiles?.length,
    )!;
    const profile: string = groups[group].profiles[0];
    const head = execFileSync('git', ['-C', repo(''), 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
    const env: NodeJS.ProcessEnv = { ...process.env, WALK_SHA: head, E2E_VALUE_FREE: '1' };
    delete env.WALK_LANE;
    const argv = execFileSync('bash', [repo('e2e/walks/launch/launch.sh'), group, 'print'], {
      encoding: 'utf8',
      env,
      stdio: 'pipe',
    })
      .trim()
      .split('\n');
    const pulls = argv.flatMap((arg, i) => (arg === '--pull' ? [argv[i + 1]] : []));
    const lines = read('e2e/walks/vm/run-walks.sh').split('\n');
    const from = lines.findIndex((l) => l.trim() === 'seal_rc=0');
    const to = lines.findIndex((l, i) => i > from && l.trim() === 'unset E2E_VALUE_FREE_PUBLISH');
    expect(from > 0 && to > from, 'the VM seal step').toBe(true);
    const sealStep = lines.slice(from, to + 1).join('\n');
    const filesBelow = (dir: string): string[] =>
      readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
        e.isDirectory() ? filesBelow(path.join(dir, e.name)) : [path.join(dir, e.name)],
      );
    /** The seal step on a profile's simulated walk results, then every file there a pull transfers (its path below the pull). */
    const walk = (name: string, make: (pub: string, profileDir: string) => void) => {
      const root = testInfo.outputPath(name);
      const profileDir = path.join(root, 'e2e', 'walk-results', group, profile);
      const pub = path.join(profileDir, 'value-free-publish');
      mkdirSync(pub, { recursive: true });
      make(pub, profileDir);
      const r = spawnSync(
        'bash',
        ['--noprofile', '--norc', '-uo', 'pipefail', '-c', `note() { :; }\nfail=0\n${sealStep}\necho "fail=$fail"`],
        {
          cwd: repo(''),
          encoding: 'utf8',
          env: { ...process.env, P: profileDir, profile, E2E_VALUE_FREE_PUBLISH: pub },
        },
      );
      const transferred: string[] = [];
      for (const file of filesBelow(root)) {
        const rel = path.relative(root, file).split(path.sep).join('/');
        for (const p of pulls) {
          if (p.endsWith('/**') ? rel.startsWith(p.slice(0, -2)) : rel === p)
            transferred.push(p.endsWith('/**') ? rel.slice(p.length - 2) : rel);
        }
      }
      return { fail: /^fail=(\d+)$/m.exec(r.stdout)?.[1], transferred: transferred.sort(), pub };
    };
    const sealed = (dir: string) => {
      writeFileSync(path.join(dir, 'runner.log'), 'clean report\n');
      writeManifest(dir);
    };
    // Control: an intact publication is transferred, and the walk does not fail on it.
    const intact = walk('intact', sealed);
    expect([intact.fail, intact.transferred], 'an intact publication').toEqual(['0', ['MANIFEST.json', 'runner.log']]);
    // Files written after assembly, source-like (a late .js and a package.json): the check leaves the directory untouched.
    const late = 'console.log("synthetic late write after the seal");\n';
    const rejected = walk('rejected', (pub) => {
      sealed(pub);
      writeFileSync(path.join(pub, 'late.js'), late);
      writeFileSync(path.join(pub, 'package.json'), '{ "private": true }\n');
    });
    expect(rejected.transferred, 'nothing from a rejected publication is transferred').toEqual([]);
    expect(rejected.fail, 'the walk fails').toBe('1');
    expect(readFileSync(path.join(rejected.pub, 'late.js'), 'utf8'), 'its sources stay intact on the machine').toBe(
      late,
    );
    expect(readFileSync(path.join(rejected.pub, 'package.json'), 'utf8')).toBe('{ "private": true }\n');
    // An intact refusal: its record alone, and the walk fails.
    const record = JSON.stringify({
      format: REFUSAL_FORMAT,
      status: 'refused',
      reasons: [{ reason: 'short-value', kind: 'value', length: 5 }],
    });
    const refused = walk('refused', (pub) => writeFileSync(path.join(pub, 'REFUSED.json'), record));
    expect([refused.fail, refused.transferred], 'an intact refusal').toEqual(['1', ['REFUSED.json']]);
    // An earlier job's export at the path (a reused machine) and a broken seal now: nothing is transferred for this job.
    // The export directory the launch pulls for this profile (`vfout`).
    const exportName = pulls
      .find((p) => p.endsWith('/**') && p.includes(`/${profile}/`))!
      .split('/')
      .slice(-2)[0];
    const stale = walk('stale', (pub, profileDir) => {
      const earlier = path.join(profileDir, exportName);
      mkdirSync(earlier);
      sealed(earlier);
      sealed(pub);
      writeFileSync(path.join(pub, 'late.txt'), 'late\n');
    });
    expect([stale.fail, stale.transferred], 'an earlier export is never transferred').toEqual(['1', []]);
  });
});

test.describe('walk harness offline: H13 census readers', () => {
  test('offline: config switches read OFF when absent or false and ON when set; mcp.search must be absent', () => {
    const row = (cfg: any) => configSwitchVerdicts(cfg, 'storage', 't').map((v) => [v.feature, v.verdict]);
    expect(row(null)).toEqual([
      ['managed_models.enabled', 'OFF'],
      ['budgets.admission_caps_enabled', 'OFF'],
      ['privacy.trace_content', 'OFF'],
      ['mcp.search', 'OFF'],
      ['mcp.enabled', 'OFF'],
      ['mcp.servers', 'OFF'],
      ['mcp.virtual_servers', 'OFF'],
    ]);
    expect(
      row({ managed_models: { enabled: false }, budgets: {}, privacy: { trace_content: false }, mcp: {} }).every(
        ([, v]) => v === 'OFF',
      ),
    ).toBe(true);
    expect(row({ managed_models: { enabled: true } })[0]).toEqual(['managed_models.enabled', 'ON']);
    expect(row({ budgets: { admission_caps_enabled: true } })[1][1]).toBe('ON');
    expect(row({ privacy: { trace_content: true } })[2][1]).toBe('ON');
    expect(row({ mcp: { search: { enabled: false } } })[3][1]).toBe('ON');
  });

  test('offline: the price feed reads against the declaration', () => {
    expect(priceFeedVerdict({}, false).verdict).toBe('ABSENT');
    expect(priceFeedVerdict({ public_price_feed: { enabled: false } }, false).verdict).toBe('OFF');
    expect(priceFeedVerdict({ public_price_feed: { enabled: true } }, false).verdict).toBe('ON');
    expect(priceFeedVerdict({ public_price_feed: { enabled: true } }, true).verdict).toBe('RECORDED');
    expect(priceFeedVerdict({ public_price_feed: { enabled: false } }, true).verdict).toBe('ON');
  });
});

test.describe('walk harness offline: H4 ledger fixtures, H14 hosted shape, H17 strict kit', () => {
  test('offline: fixture entries are labelled, validated and never a real session', async () => {
    const { FIXTURE_NOTE, syntheticSession, checkoutSessionId } = await import('../lib/ledger-seed');
    expect(FIXTURE_NOTE).toBe('walk fixture, not a purchase');
    expect(syntheticSession('w02-a')).toBe('cs_test_e2e_walk_w02_a');
    // R-LIT synthetic fixture: literal output of checkoutSessionId('w02-a').
    expect(checkoutSessionId('w02-a')).toBe('cs_test_E2Ewalkf2a989353324cf7b30fdd5d184e4c5bc');
    expect(checkoutSessionId('w02-a')).toMatch(/^cs_(test|live)_[A-Za-z0-9]{24,}$/);
    expect(() => checkoutSessionId('bad label!')).toThrow('label');
    expect(() => syntheticSession('bad label!')).toThrow('label');
  });

  test('offline: the hosted env fills every required name, shapes the Fernet key and refuses outside images', async () => {
    const { fernetKey, renderHostedEnv, requiredNames } = await import('../walks/hosted/hosted-env.mjs');
    const compose = 'a: ${SECRET_KEY:?x}\nb: ${AGENTCC_REDIS_PASSWORD:?y}\nc: ${PG_HOST:?z}\nd: ${OPTIONAL:-q}\n';
    expect(requiredNames(compose)).toEqual(['AGENTCC_REDIS_PASSWORD', 'PG_HOST', 'SECRET_KEY']);
    const images = {
      BACKEND_IMAGE: '127.0.0.1:5000/futureagi-backend',
      BACKEND_DIGEST: `sha256:${'a'.repeat(64)}`,
      GATEWAY_IMAGE: '127.0.0.1:5000/agentcc-gateway',
      GATEWAY_DIGEST: `sha256:${'b'.repeat(64)}`,
      FRONTEND_IMAGE: '127.0.0.1:5000/futureagi-frontend:walk',
    };
    const env = renderHostedEnv(compose, images);
    expect(env.SECRET_KEY).toMatch(/^[0-9a-f]{64}$/);
    expect(env.PG_HOST).toBe('postgres');
    expect(env.REDIS_URL).toBe(`redis://:${env.AGENTCC_REDIS_PASSWORD}@redis:6379/0`);
    expect(Buffer.from(fernetKey().replace(/-/g, '+').replace(/_/g, '/'), 'base64').length).toBe(32);
    expect(fernetKey()).toMatch(/^[A-Za-z0-9_-]{43}=$/);
    expect(renderHostedEnv(compose, images).SECRET_KEY).not.toBe(env.SECRET_KEY);
    expect(() => renderHostedEnv(compose, { ...images, BACKEND_IMAGE: 'ghcr.io/x/backend' })).toThrow(
      'loopback registry',
    );
    expect(() => renderHostedEnv(compose, { ...images, GATEWAY_DIGEST: 'latest' })).toThrow('not a sha256 digest');
    const overlay = parse(read('e2e/walks/hosted/docker-compose.hosted-walk.yml').replace('!override', ''));
    for (const [name, svc] of Object.entries<any>(overlay.services)) {
      for (const port of svc.ports ?? []) expect(String(port), name).toMatch(/^127\.0\.0\.1:/);
      expect(svc.restart, name).toBe('no');
    }
    expect(overlay.services['agentcc-gateway'].volumes).toEqual([
      '../e2e/stack/gateway.e2e.yaml:/etc/agentcc/config.yaml:ro',
    ]);
  });

  test('offline: the strict-kit wrapper re-verifies the archive and wraps only a walk phase', () => {
    const kit = repo('e2e/walks/kit/strict-kit.sh');
    const source = read('e2e/walks/kit/strict-kit.sh');
    expect(source).toContain('KIT_SHA256=31a52264e27c63626caf3120d4935e64ee5bdb841edab0b0351cee5a430849f7');
    const walk = ['yarn', 'playwright', 'test', '--config', 'playwright.walk.config.ts', '--grep', '@walk-W01'];
    const plan = execFileSync(
      'bash',
      [
        kit,
        '--print',
        '/tmp/k.tar',
        '/tmp/o',
        'futureagi-e2e-standalone_default',
        'futureagi-e2e-standalone_e2e-public',
        '--',
        ...walk,
      ],
      { encoding: 'utf8' },
    )
      .trim()
      .split('\n');
    expect(plan[3]).toBe(
      'lockdown.sh run --network futureagi-e2e-standalone_default --network ' +
        'futureagi-e2e-standalone_e2e-public --ttl 3600 -- yarn playwright test --config playwright.walk.config.ts --grep @walk-W01',
    );
    const status = (args: string[]) => {
      try {
        execFileSync('bash', [kit, ...args], { stdio: 'pipe' });
        return 0;
      } catch (error) {
        return (error as { status: number }).status;
      }
    };
    expect(status(['--print', '/tmp/k.tar', '/tmp/o', 'net', '--', 'yarn', 'vitest', 'run'])).toBe(64);
    expect(status(['--print', '/tmp/k.tar', '/tmp/o', 'net', '--', 'go', 'test', './...'])).toBe(64);
    expect(status(['--print', '/tmp/k.tar', '/tmp/o', '--', ...walk])).toBe(64);
    const fake = test.info().outputPath('not-the-kit.tar');
    writeFileSync(fake, 'not the kit');
    expect(status([fake, test.info().outputPath('out'), 'net', '--', ...walk])).toBe(70);
  });

  test('offline: WALK_STRICT ships the kit archive alone and tells the VM where it is', () => {
    const head = execFileSync('git', ['-C', repo(''), 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
    const argv = execFileSync('bash', [repo('e2e/walks/launch/launch.sh'), 'vm-1', 'print'], {
      encoding: 'utf8',
      env: { ...process.env, WALK_SHA: head, WALK_STRICT: '1', WALK_PULL_ROOT: '/tmp/walk-pulls' },
    })
      .trim()
      .split('\n');
    expect(argv[argv.indexOf('--ship') + 1]).toBe('/tmp/walk-pulls/vm-1/kit-ship');
    expect(argv).toEqual(
      expect.arrayContaining([
        'E2E_WALK_STRICT=1',
        'E2E_KIT_ARCHIVE=/tmp/walk-pulls/vm-1/kit-ship/egress-sinkhole-r574b.tar',
      ]),
    );
  });
});
