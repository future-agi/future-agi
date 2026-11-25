async (page) => {
  // Renders every job in window-less contexts at 2x. Edit JOBS before running.
  const JOBS = [['integrations', 1000, 800, ''], ['gateway', 1000, 600, '']];
  const browser = page.context().browser();
  const out = [];
  for (const [name, w, h, qs] of JOBS) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: 2 });
    const p = await ctx.newPage();
    for (const th of ['dark', 'light']) {
      await p.goto(`http://127.0.0.1:8766/${name}.html?theme=${th}${qs ? '&' + qs : ''}`);
      await p.waitForLoadState('networkidle');
      await p.evaluate(() => document.fonts.ready);
      await p.waitForTimeout(400);
      const el = await p.$('#root');
      await el.screenshot({ path: `${globalThis.OUT_DIR || './'}out/${name}-${th}.png` });
      const box = await el.boundingBox();
      out.push(`${name}-${th} ${Math.round(box.width)}x${Math.round(box.height)}`);
    }
    await ctx.close();
  }
  return out.join('\n');
}
