import type { Page } from '@playwright/test';

// Presentation layer for the recorded walkthrough: a caption pill and a cursor highlight.
// Both are pointer-events:none overlays; they never touch product state.
export function overlayInit() {
  const install = () => {
    if (document.getElementById('__demo_caption') || location.pathname.startsWith('/__demo/')) return;
    const style = document.createElement('style');
    style.textContent = `
      #__demo_caption{position:fixed;left:224px;bottom:22px;z-index:2147483647;pointer-events:none;
        max-width:860px;padding:11px 20px;border-radius:24px;background:rgba(17,17,24,.93);color:#fff;
        font:500 18px/1.4 Inter,-apple-system,"Segoe UI",Roboto,sans-serif;letter-spacing:.1px;box-shadow:0 8px 30px rgba(0,0,0,.35);
        display:flex;gap:12px;align-items:center;opacity:0;transition:opacity .25s ease}
      #__demo_caption .chip{flex:none}
      #__demo_caption.on{opacity:1}
      #__demo_caption.top{bottom:auto;top:22px}
      #__demo_caption .chip{font-weight:700;font-size:14px;padding:3px 10px;border-radius:999px;background:#7c5cff;color:#fff;text-transform:uppercase;letter-spacing:.6px}
      #__demo_cursor{position:fixed;left:-100px;top:-100px;width:26px;height:26px;margin:-13px 0 0 -13px;border-radius:50%;
        background:rgba(124,92,255,.28);border:2px solid rgba(124,92,255,.95);z-index:2147483647;pointer-events:none;
        transition:transform .15s ease, background .15s ease;box-shadow:0 0 0 4px rgba(124,92,255,.12)}
      #__demo_cursor.down{transform:scale(.7);background:rgba(124,92,255,.55)}`;
    document.documentElement.appendChild(style);
    const cap = document.createElement('div'); cap.id = '__demo_caption';
    const cur = document.createElement('div'); cur.id = '__demo_cursor';
    document.documentElement.appendChild(cap); document.documentElement.appendChild(cur);
    const w = window as unknown as { __demoCaption?: { chip: string; text: string } };
    try {
      const saved = sessionStorage.getItem('__demo_caption');
      if (saved) w.__demoCaption = JSON.parse(saved);
    } catch { /* storage unavailable */ }
    const render = () => {
      const c = w.__demoCaption;
      if (!c || !c.text) { cap.className = ''; return; }
      cap.innerHTML = '';
      if (c.chip) { const chip = document.createElement('span'); chip.className = 'chip'; chip.textContent = c.chip; cap.appendChild(chip); }
      const t = document.createElement('span'); t.textContent = c.text; cap.appendChild(t);
      cap.className = (w as unknown as { __demoTop?: boolean }).__demoTop ? 'on top' : 'on';
    };
    (window as unknown as { __demoRender: () => void }).__demoRender = render;
    render();
    try {
      const pos = sessionStorage.getItem('__demo_cursor');
      if (pos) { const [x, y] = JSON.parse(pos); cur.style.left = `${x}px`; cur.style.top = `${y}px`; }
    } catch { /* storage unavailable */ }
    window.addEventListener('mousemove', (e) => { cur.style.left = `${e.clientX}px`; cur.style.top = `${e.clientY}px`;
      try { sessionStorage.setItem('__demo_cursor', JSON.stringify([e.clientX, e.clientY])); } catch { /* ignore */ } }, true);
    window.addEventListener('mousedown', () => cur.classList.add('down'), true);
    window.addEventListener('mouseup', () => cur.classList.remove('down'), true);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install); else install();
}

export async function setCaption(page: Page, chip: string, text: string) {
  await page.evaluate(({ chip, text }) => {
    const w = window as unknown as { __demoCaption?: unknown; __demoRender?: () => void };
    w.__demoCaption = { chip, text };
    try { sessionStorage.setItem('__demo_caption', JSON.stringify({ chip, text })); } catch { /* ignore */ }
    w.__demoRender?.();
  }, { chip, text }).catch(() => undefined);
}

// Moves the caption to the top of the frame while the element being shown sits in the bottom band.
export async function setCaptionTop(page: Page, top: boolean) {
  await page.evaluate((top) => {
    const w = window as unknown as { __demoTop?: boolean; __demoRender?: () => void };
    if (w.__demoTop === top) return;
    w.__demoTop = top; w.__demoRender?.();
  }, top).catch(() => undefined);
}

export function cardHtml(title: string, subtitle: string, foot: string) {
  return `<!doctype html><html><head><meta charset="utf-8"><title>${title}</title><style>
  html,body{margin:0;height:100%;background:#0f0f17;color:#fff;font-family:Inter,-apple-system,"Segoe UI",Roboto,sans-serif}
  .wrap{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:22px;text-align:center}
  .badge{font-size:15px;letter-spacing:2px;text-transform:uppercase;color:#a99bff;font-weight:700}
  h1{margin:0;font-size:56px;font-weight:700;letter-spacing:-.5px}
  p{margin:0;font-size:22px;color:#c9c9d6;max-width:900px;line-height:1.5}
  .foot{position:fixed;left:0;right:0;bottom:40px;text-align:center;font-size:15px;color:#8a8aa0}
  .bar{width:120px;height:4px;border-radius:4px;background:linear-gradient(90deg,#7c5cff,#3fd0c9)}
  </style></head><body><div class="wrap"><div class="badge">Future AGI</div><h1>${title}</h1><div class="bar"></div><p>${subtitle}</p></div>
  <div class="foot">${foot}</div></body></html>`;
}
