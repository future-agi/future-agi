import { defineConfig } from '@playwright/test';
// Cloud-only mock smoke, separate from product acceptance walks (H1/H7b/H11).
export default defineConfig({
  testDir: './harness', testMatch: ['walk-mocks-browser.spec.ts', 'boundary-api.spec.ts'], workers: 2, retries: 0,
  reporter: [['list']], timeout: 30000,
  use: { ignoreHTTPSErrors: true, trace: 'retain-on-failure' },
  projects: [
    { name: 'chromium', use: { browserName: 'chromium', launchOptions: { args: ['--disable-background-networking', '--disable-component-update', '--disable-default-apps', '--no-first-run'] } } },
    { name: 'firefox', use: { browserName: 'firefox', launchOptions: { firefoxUserPrefs: {
      'app.update.enabled': false, 'app.update.url': '', 'toolkit.telemetry.enabled': false,
      'toolkit.telemetry.server': '', 'datareporting.healthreport.uploadEnabled': false,
      'datareporting.policy.dataSubmissionEnabled': false, 'services.settings.server': '',
      'network.captive-portal-service.enabled': false, 'network.connectivity-service.enabled': false,
      'app.normandy.enabled': false, 'app.normandy.api_url': '', 'app.shield.optoutstudies.enabled': false,
      'browser.safebrowsing.malware.enabled': false, 'browser.safebrowsing.phishing.enabled': false,
      'browser.safebrowsing.downloads.enabled': false, 'dom.push.enabled': false,
      'experiments.enabled': false, 'browser.newtabpage.activity-stream.feeds.telemetry': false,
      'browser.newtabpage.activity-stream.asrouter.providers.cfr': '',
    } } } },
  ],
});
