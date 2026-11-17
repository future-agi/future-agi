// H11 + H11b: the browser settings every walk runs with, and the
// protocol-handler interception for one-click client installs (W12).
//
// H11 (GATEWAY-CLOUD-RECIPE "Firefox profile for strict-kit walks", 643
// addendum): a harness-level Firefox profile with Firefox's own background
// services off, so the only traffic in a walk's window is the product's. Each
// walk writes the exact list into its receipt (walkBrowserSettings). The
// Chromium flags follow the kit README's "Chromium's own traffic" section:
// background networking and component updates off, and a resolver that answers
// NOTFOUND for every name but loopback, so Chromium's own Google calls never
// resolve. Both are harness settings under the adopted kit; the kit is never
// changed. They apply to ASSETS-PENDING runs too, so a strict run and its
// rehearsal see the same browser.
//
// H11b: cursor:, vscode: and claude: links are captured and never launched. An
// init script in every walk page records the href of a clicked link with one of
// those schemes, and of window.open / location.assign / location.replace to one,
// and cancels the navigation; Firefox additionally has those schemes'
// external handlers off (belt, not the mechanism). protocolLinks() reads what
// the page tried. A navigation set through `location.href = ...` cannot be
// intercepted from script (Location is unforgeable); headless browsers on the VM
// have no handler for these schemes, so it opens nothing, and W12's assertion
// reads the link from the button it clicks.

/** Closed loopback port: a pref that must name a URL names one nothing listens on. */
const CLOSED = 'http://127.0.0.1:9/';

export const PROTOCOL_SCHEMES = ['cursor', 'vscode', 'claude'] as const;

export const FIREFOX_STRICT_PREFS: Readonly<Record<string, string | number | boolean>> = {
  // App update and its URL.
  'app.update.enabled': false,
  'app.update.auto': false,
  'app.update.checkInstallTime': false,
  'app.update.disabledForTesting': true,
  'app.update.url': CLOSED,
  'app.update.url.manual': CLOSED,
  'app.update.url.details': CLOSED,
  // toolkit.telemetry and datareporting.
  'toolkit.telemetry.enabled': false,
  'toolkit.telemetry.unified': false,
  'toolkit.telemetry.archive.enabled': false,
  'toolkit.telemetry.server': CLOSED,
  'toolkit.telemetry.newProfilePing.enabled': false,
  'toolkit.telemetry.shutdownPingSender.enabled': false,
  'toolkit.telemetry.firstShutdownPing.enabled': false,
  'toolkit.telemetry.updatePing.enabled': false,
  'toolkit.telemetry.bhrPing.enabled': false,
  'datareporting.healthreport.uploadEnabled': false,
  'datareporting.healthreport.service.enabled': false,
  'datareporting.policy.dataSubmissionEnabled': false,
  // Remote settings.
  'services.settings.server': CLOSED,
  // Captive portal and connectivity checks.
  'network.captive-portal-service.enabled': false,
  'network.connectivity-service.enabled': false,
  'captivedetect.canonicalURL': CLOSED,
  // Normandy and Shield.
  'app.normandy.enabled': false,
  'app.normandy.api_url': CLOSED,
  'app.shield.optoutstudies.enabled': false,
  // Safe Browsing.
  'browser.safebrowsing.malware.enabled': false,
  'browser.safebrowsing.phishing.enabled': false,
  'browser.safebrowsing.blockedURIs.enabled': false,
  'browser.safebrowsing.downloads.enabled': false,
  'browser.safebrowsing.downloads.remote.enabled': false,
  'browser.safebrowsing.provider.google.updateURL': CLOSED,
  'browser.safebrowsing.provider.google4.updateURL': CLOSED,
  'browser.safebrowsing.provider.mozilla.updateURL': CLOSED,
  // dom.push.
  'dom.push.enabled': false,
  'dom.push.connection.enabled': false,
  'dom.push.serverURL': CLOSED,
  // Experiments and messaging.
  'messaging-system.rsexperimentloader.enabled': false,
  'nimbus.debug': false,
  'browser.ping-centre.telemetry': false,
  'browser.newtabpage.activity-stream.feeds.telemetry': false,
  'browser.newtabpage.activity-stream.telemetry': false,
  'browser.newtabpage.activity-stream.feeds.snippets': false,
  'browser.discovery.enabled': false,
  'extensions.getAddons.cache.enabled': false,
  'extensions.update.enabled': false,
  'extensions.blocklist.enabled': false,
  // No speculative DNS or connections: a name an aborted request carried is never resolved.
  'network.dns.disablePrefetch': true,
  'network.prefetch-next': false,
  'network.predictor.enabled': false,
  'network.http.speculative-parallel-limit': 0,
  // H11b belt: no external application for the install schemes.
  ...Object.fromEntries(PROTOCOL_SCHEMES.flatMap((s) => [
    [`network.protocol-handler.external.${s}`, false], [`network.protocol-handler.expose.${s}`, true]])),
};

/** Loopback names a walk's Chromium may resolve; H7b adds MAP rules for e2e-public aliases. */
export function chromiumStrictArgs(extraResolverRules: readonly string[] = []): string[] {
  const rules = ['MAP * ~NOTFOUND', ...extraResolverRules, 'EXCLUDE localhost', 'EXCLUDE 127.0.0.1'];
  return ['--disable-background-networking', '--disable-component-update', '--disable-domain-reliability',
    '--disable-sync', '--no-pings', '--disable-client-side-phishing-detection', '--metrics-recording-only',
    '--disable-features=OptimizationHints,MediaRouter,Translate,AutofillServerCommunication,' +
      'CertificateTransparencyComponentUpdater,InterestFeedContentSuggestions',
    `--host-resolver-rules=${rules.join(', ')}`];
}

/** Whether the strict browser settings apply (default on; E2E_WALK_STRICT_BROWSER=0 turns them off). */
export const strictBrowser = (value = process.env.E2E_WALK_STRICT_BROWSER) => value !== '0';

/** What a walk receipt records of its browser (H11: the exact list). */
export function walkBrowserSettings(browserName: string,
  launchOptions: { args?: string[]; firefoxUserPrefs?: Record<string, string | number | boolean> } = {}) {
  const args = [...(launchOptions.args ?? [])];
  const prefs = { ...(launchOptions.firefoxUserPrefs ?? {}) };
  const strict = browserName === 'chromium' ? chromiumStrictArgs().every(arg => args.includes(arg))
    : browserName === 'firefox' && Object.entries(FIREFOX_STRICT_PREFS).every(([k, v]) => prefs[k] === v);
  return { browserName, strict,
    firefoxPrefs: browserName === 'firefox' ? prefs : null,
    chromiumArgs: browserName === 'chromium' ? args : null,
    source: 'resolved Playwright launchOptions fixture (user-supplied overrides, not engine defaults)',
    protocolInterception: [...PROTOCOL_SCHEMES] };
}

/** Init script (runs in the page before app code): capture install links, never launch them. */
export function protocolInterceptScript(schemes: readonly string[]): void {
  const w = window as unknown as { __e2eProtocolLinks?: { href: string; via: string }[] };
  const prefixes = schemes.map((s) => `${s}:`);
  const hit = (url: unknown) => typeof url === 'string' && prefixes.some((p) => url.toLowerCase().startsWith(p));
  const record = (href: string, via: string) => { (w.__e2eProtocolLinks ??= []).push({ href, via }); };
  document.addEventListener('click', (event) => {
    const target = event.target as Element | null;
    const link = target && typeof target.closest === 'function' ? target.closest('a[href]') as HTMLAnchorElement | null : null;
    if (link && hit(link.getAttribute('href'))) {
      event.preventDefault();
      event.stopImmediatePropagation();
      record(link.getAttribute('href')!, 'click');
    }
  }, true);
  const open = window.open;
  window.open = function (this: Window, url?: string | URL, ...rest: unknown[]) {
    if (hit(String(url ?? ''))) { record(String(url), 'window.open'); return null; }
    return (open as (...a: unknown[]) => Window | null).call(this, url, ...rest);
  } as typeof window.open;
  for (const method of ['assign', 'replace'] as const) {
    const original = Location.prototype[method];
    Location.prototype[method] = function (this: Location, url: string | URL) {
      if (hit(String(url))) { record(String(url), `location.${method}`); return; }
      return original.call(this, url);
    };
  }
}

/** What the page tried to open with an install scheme, oldest first. */
export async function protocolLinks(page: import('@playwright/test').Page): Promise<{ href: string; via: string }[]> {
  return page.evaluate(() => (window as unknown as { __e2eProtocolLinks?: { href: string; via: string }[] })
    .__e2eProtocolLinks ?? []);
}
