async (page) => {
  // Render each scripted scene (scenes/<name>.json) to PNG frames at 12 fps.
  const names = ['trace', 'evals', 'simulate'];
  const fps = 12; const out = [];
  const browser = page.context().browser();
  for (const name of names) {
    const ctx = await browser.newContext({ viewport: { width: 1200, height: 750 }, deviceScaleFactor: 1 });
    const p = await ctx.newPage();
    await p.goto('http://127.0.0.1:8766/gif.html');
    const scene = await (await p.request.get(`http://127.0.0.1:8766/scenes/${name}.json`)).json();
    await p.evaluate(async (s) => { await window.load(s); }, scene);
    await p.evaluate(() => document.fonts.ready);
    const n = Math.round(scene.duration * fps);
    for (let i = 0; i < n; i++) {
      await p.evaluate((t) => window.render(t), i / fps);
      await p.screenshot({ path: `${globalThis.OUT_DIR || './'}frames/${name}/${String(i).padStart(4, '0')}.png` });
    }
    await ctx.close(); out.push(`${name}:${n}`);
  }
  return out.join(' ');
}
