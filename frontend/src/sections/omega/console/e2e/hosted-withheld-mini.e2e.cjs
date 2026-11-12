/* eslint-env node */
// jsdom visibility cannot prove the real NavMini's clipping.
// Existing console-frame stand-in harness: playwright.config.cjs + run-e2e.sh.
// No rail/renderer/theme/font mocks or geometry-changing test styles.
const { expect, test } = require("playwright/test");
const { FAKE, control, keepLocal } = require("./helpers.cjs");

// Measure every non-whitespace character, not just the element's box (which
// stays inside the rail even when nowrap/ellipsis clips its actual text).
async function textGeometry(locator) {
  return locator.evaluate((element) => {
    const failures = [];
    const clips = [];
    let bounds = { left: 0, top: 0, right: innerWidth, bottom: innerHeight };
    const clippedOverflow = /^(hidden|clip|auto|scroll)$/;
    const rail = document.querySelector('[data-testid="harness-nav-mini"]').firstElementChild;
    for (let node = element; node; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.visibility !== "visible" || Number(style.opacity) === 0 || style.display === "none") {
        failures.push(`hidden ancestor: ${node.tagName}`);
      }
      if (style.textOverflow === "ellipsis" || Number.parseInt(style.webkitLineClamp, 10) > 0) {
        failures.push(`truncated text: ${node.tagName}`);
      }
      const clipX = node === rail || clippedOverflow.test(style.overflowX);
      const clipY = node === rail || clippedOverflow.test(style.overflowY);
      if (!clipX && !clipY) continue;
      const rect = node.getBoundingClientRect();
      const left = rect.left + node.clientLeft;
      const top = rect.top + node.clientTop;
      bounds = {
        left: clipX ? Math.max(bounds.left, left) : bounds.left,
        right: clipX ? Math.min(bounds.right, left + node.clientWidth) : bounds.right,
        top: clipY ? Math.max(bounds.top, top) : bounds.top,
        bottom: clipY ? Math.min(bounds.bottom, top + node.clientHeight) : bounds.bottom,
      };
      clips.push({ tag: node.tagName, overflowX: style.overflowX, overflowY: style.overflowY });
    }
    const characters = [];
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    let text;
    while ((text = walker.nextNode())) {
      for (let i = 0; i < text.length; i += 1) {
        const char = text.data[i];
        if (/\s/.test(char)) continue;
        const range = document.createRange();
        range.setStart(text, i);
        range.setEnd(text, i + 1);
        const rects = Array.from(range.getClientRects());
        if (!rects.length) failures.push(`no painted range: ${char}`);
        for (const rect of rects) {
          const epsilon = 0.75; // fractional CSS pixel rounding, not a glyph
          if (
            rect.width <= 0 || rect.height <= 0 ||
            rect.left < bounds.left - epsilon || rect.right > bounds.right + epsilon ||
            rect.top < bounds.top - epsilon || rect.bottom > bounds.bottom + epsilon
          ) {
            failures.push(`clipped character: ${char}`);
          }
          const hit = document.elementFromPoint(
            (rect.left + rect.right) / 2,
            (rect.top + rect.bottom) / 2,
          );
          if (!hit || !element.contains(hit)) failures.push(`occluded character: ${char}`);
          characters.push({ char, left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom });
        }
      }
    }
    return { text: element.textContent, bounds, clips, characters, failures };
  });
}

// Only scroll ancestors that are user-scrollable. scrollIntoView also scrolls
// overflow:hidden ancestors, which could conceal the very bug we are testing.
async function scrollRowIntoRail(row) {
  return row.evaluate(async (element) => {
    const scrolled = [];
    for (let node = element.parentElement; node; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (!/^(auto|scroll)$/.test(style.overflowY) || node.scrollHeight <= node.clientHeight) continue;
      const before = node.scrollTop;
      const rowRect = element.getBoundingClientRect();
      const viewport = node.getBoundingClientRect();
      node.scrollTop += (rowRect.top + rowRect.bottom) / 2 -
        (viewport.top + node.clientTop + node.clientHeight / 2);
      scrolled.push({ before, after: node.scrollTop });
    }
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    return scrolled;
  });
}

async function loadedFont(locator) {
  return locator.evaluate(async (element) => {
    const style = getComputedStyle(element);
    const font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
    const faces = await document.fonts.load(font, element.textContent);
    await document.fonts.ready;
    return {
      font,
      // fonts.check alone also returns true for a missing web font/fallback.
      faces: faces.map((face) => ({ family: face.family, status: face.status })),
      ready: document.fonts.status,
      checked: document.fonts.check(font, element.textContent),
    };
  });
}

test("actual NavMini keeps both hosted-withheld titles and labels unclipped without hover or focus", async ({
  page, context, request,
}, testInfo) => {
  await keepLocal(context);
  const host = control(request, FAKE);
  await host.reset();
  await host.mode({ config: { VITE_OMEGA_CONSOLE: "1" } });
  await context.route(`${FAKE}/omega-rollout/early-access/`, (route) =>
    route.fulfill({ json: { can_request: true, products: [] } }),
  );
  let capabilityReads = 0;
  await context.route(`${FAKE}/api/capabilities/`, async (route) => {
    const response = await route.fetch();
    const answer = await response.json();
    answer.deployment_flavor = "cloud_image";
    answer.display_mode = "cloud";
    for (const product of ["sandboxes", "deployments"]) {
      for (const feature of [`omega_${product}`, `omega_part_hosted_${product}`]) {
        answer.features[feature] = {
          display_name: product,
          allowed: false,
          reason_code: "PRODUCT_NOT_ADMITTED",
          requires_network: null,
          oss_baseline: false,
        };
      }
    }
    capabilityReads += 1;
    await route.fulfill({ response, json: answer });
  });
  await page.goto(`${FAKE}/dashboard/nav-mini`);
  const rail = page.getByTestId("harness-nav-mini").locator(":scope > div");
  // The second NavSectionMini is the app rail, not dashboard/resources/settings.
  const nav = rail.locator("nav#nav-section-mini").nth(1);
  await expect(rail).toBeVisible();
  await expect(nav).toBeVisible();
  const evidence = [];
  let didScroll = false;
  for (const height of [800, 480]) {
    await page.setViewportSize({ width: 1280, height });
    await page.mouse.move(1200, height - 20); // never hover an entry or tooltip
    const railBox = await rail.boundingBox();
    expect(railBox.width).toBeCloseTo(52, 0);
    expect(railBox.height).toBeLessThanOrEqual(height);
    for (const title of ["Sandboxes", "Deployments"]) {
      const titleNode = nav.getByText(title, { exact: true });
      await expect(titleNode).toHaveCount(1);
      const row = titleNode.locator("..");
      const label = row.getByText("Not available in this deployment", { exact: true });
      await expect(label).toHaveCount(1);
      const fonts = [];
      for (const text of [titleNode, label]) {
        const font = await loadedFont(text);
        expect(font.faces.length, font.font).toBeGreaterThan(0);
        expect(font.faces.every((face) => face.status === "loaded")).toBe(true);
        expect(font.ready).toBe("loaded");
        expect(font.checked).toBe(true);
        fonts.push(font);
      }
      const scrolling = await scrollRowIntoRail(row);
      didScroll ||= scrolling.some(({ before, after }) => before !== after);
      // Both strings must fit simultaneously, not separately scroll into view.
      for (const [text, expected] of [[titleNode, title], [label, "Not available in this deployment"]]) {
        await expect(text).toHaveText(expected);
        await expect(text).toBeVisible();
        const geometry = await textGeometry(text);
        expect(geometry.characters.map(({ char }) => char).join("")).toBe(expected.replace(/\s/g, ""));
        expect(geometry.failures, JSON.stringify(geometry)).toEqual([]);
        expect(await text.evaluate((element) => element.matches(":hover"))).toBe(false);
        evidence.push({ height, title, fonts, scrolling, geometry });
      }
      const interactive = "a, [href], button, input, select, textarea, [tabindex], [role='link'], [role='button'], [contenteditable='true']";
      expect(await row.evaluate((element, selector) => element.matches(selector) || Boolean(element.querySelector(selector)), interactive)).toBe(false);
      await testInfo.attach(`mini-${height}-${title}`, {
        body: await rail.screenshot(), contentType: "image/png",
      });
    }
  }
  expect(capabilityReads).toBeGreaterThan(0);
  expect(didScroll, "exercise the actual rail's scrolling, not a tall unconstrained nav").toBe(true);

  // Exercise a full real keyboard cycle, with the ordinary controls as positive
  // controls. No entry may receive focus directly or through a descendant.
  const first = rail.getByRole("button", { name: "Expand sidebar" });
  await first.focus();
  await expect(first).toBeFocused();
  const focusBudget = await page.locator("a, button, input, select, textarea, [tabindex]").count() + 2;
  let cycled = false;
  let focusedWorkingLink = false;
  // macOS WebKit only moves keyboard focus onto links with Option-Tab (the platform's
  // "highlight each item" convention); Chromium and Firefox use plain Tab.
  const tabKey = page.context().browser()?.browserType().name() === "webkit" ? "Alt+Tab" : "Tab";
  for (let step = 0; step < focusBudget; step += 1) {
    await page.keyboard.press(tabKey);
    for (const title of ["Sandboxes", "Deployments"]) {
      const row = nav.getByText(title, { exact: true }).locator("..");
      expect(await row.evaluate((element) => element.contains(document.activeElement))).toBe(false);
    }
    focusedWorkingLink ||= await nav.evaluate((element) =>
      element.contains(document.activeElement) && document.activeElement.matches("a[href]"));
    if (await first.evaluate((element) => element === document.activeElement)) {
      cycled = true;
      break;
    }
  }
  expect(focusedWorkingLink).toBe(true);
  expect(cycled, "complete the tab cycle rather than stopping before the entries").toBe(true);
  await testInfo.attach("mini-text-geometry", {
    body: JSON.stringify(evidence, null, 2), contentType: "application/json",
  });
});
