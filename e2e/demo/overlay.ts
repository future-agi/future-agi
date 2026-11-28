// Presentation layer for the recorded walkthrough. Captions are not drawn in the page: postprocess.py
// renders them in a band below the app frame, so they never cover product UI. In the page there is only a
// pointer-events:none cursor highlight, plus a loading-placeholder probe that reports to the recorder.
export function overlayInit() {
  const install = () => {
    if (document.getElementById('__demo_cursor') || location.pathname.startsWith('/__demo/')) return;
    const style = document.createElement('style');
    style.textContent = `
      #__demo_cursor{position:fixed;left:-100px;top:-100px;width:26px;height:26px;margin:-13px 0 0 -13px;border-radius:50%;
        background:rgba(124,92,255,.28);border:2px solid rgba(124,92,255,.95);z-index:2147483647;pointer-events:none;
        transition:transform .15s ease, background .15s ease;box-shadow:0 0 0 4px rgba(124,92,255,.12)}
      #__demo_cursor.down{transform:scale(.7);background:rgba(124,92,255,.55)}`;
    document.documentElement.appendChild(style);
    const cur = document.createElement('div'); cur.id = '__demo_cursor';
    document.documentElement.appendChild(cur);
    try {
      const pos = sessionStorage.getItem('__demo_cursor');
      if (pos) { const [x, y] = JSON.parse(pos); cur.style.left = `${x}px`; cur.style.top = `${y}px`; }
    } catch { /* storage unavailable */ }
    window.addEventListener('mousemove', (e) => { cur.style.left = `${e.clientX}px`; cur.style.top = `${e.clientY}px`;
      try { sessionStorage.setItem('__demo_cursor', JSON.stringify([e.clientX, e.clientY])); } catch { /* ignore */ } }, true);
    window.addEventListener('mousedown', () => cur.classList.add('down'), true);
    window.addEventListener('mouseup', () => cur.classList.remove('down'), true);
    // Loading placeholders (skeletons, "Loading …" regions, busy containers) on screen are reported to the
    // recorder, which marks them so the edit drops those frames. Detection runs every 100 ms.
    const report = (on: boolean, what: string) => (window as unknown as { __demoPlaceholder?: (on: boolean, what: string) => void }).__demoPlaceholder?.(on, what);
    let on = false;
    const visible = (el: Element) => {
      const r = el.getBoundingClientRect();
      if (r.width < 4 || r.height < 4 || r.bottom < 0 || r.top > innerHeight) return false;
      const s = getComputedStyle(el);
      return s.visibility !== 'hidden' && s.display !== 'none' && Number(s.opacity) > 0.05;
    };
    setInterval(() => {
      const hit = [...document.querySelectorAll('.MuiSkeleton-root, [aria-busy="true"], [aria-label^="Loading"]')].find(visible);
      if (Boolean(hit) !== on) { on = Boolean(hit); report(on, hit ? (hit.getAttribute('aria-label') || hit.className.toString().slice(0, 60)) : ''); }
    }, 100);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install); else install();
}

const esc = (value: string) => value.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]!));

export function cardHtml(title: string, subtitle: string, foot: string) {
  return `<!doctype html><html><head><meta charset="utf-8"><title>${esc(title)}</title><style>
  html,body{margin:0;height:100%;background:#0f0f17;color:#fff;font-family:Inter,-apple-system,"Segoe UI",Roboto,sans-serif}
  .wrap{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:22px;text-align:center}
  .badge{font-size:15px;letter-spacing:2px;text-transform:uppercase;color:#a99bff;font-weight:700}
  h1{margin:0;font-size:56px;font-weight:700;letter-spacing:-.5px}
  p{margin:0;font-size:22px;color:#c9c9d6;max-width:900px;line-height:1.5}
  .foot{position:fixed;left:0;right:0;bottom:40px;text-align:center;font-size:17px;color:#a2a2b8;line-height:1.6}
  .bar{width:120px;height:4px;border-radius:4px;background:linear-gradient(90deg,#7c5cff,#3fd0c9)}
  </style></head><body><div class="wrap"><div class="badge">Future AGI</div><h1>${esc(title)}</h1><div class="bar"></div><p>${esc(subtitle)}</p></div>
  <div class="foot">${foot.split('\n').map(esc).join('<br>')}</div></body></html>`;
}

// A short interstitial naming who is signed in, for the switch between the author and the reviewer.
export function personHtml(name: string, role: string, workspace: string) {
  const initials = name.split(/\s+/).map((part) => part[0]).join('').slice(0, 2);
  return `<!doctype html><html><head><meta charset="utf-8"><title>${esc(name)}</title><style>
  html,body{margin:0;height:100%;background:#f6f6fa;font-family:Inter,-apple-system,"Segoe UI",Roboto,sans-serif;color:#17171f}
  .wrap{height:100%;display:flex;align-items:center;justify-content:center}
  .card{display:flex;gap:22px;align-items:center;padding:28px 40px;border-radius:16px;background:#fff;box-shadow:0 10px 40px rgba(20,20,40,.12)}
  .avatar{width:72px;height:72px;border-radius:50%;background:#7c5cff;color:#fff;font-size:28px;font-weight:700;display:flex;align-items:center;justify-content:center}
  .label{font-size:15px;color:#6b6b80;letter-spacing:.3px}.name{font-size:30px;font-weight:700;margin:4px 0}.role{font-size:17px;color:#45455a}
  </style></head><body><div class="wrap"><div class="card"><div class="avatar">${esc(initials)}</div>
  <div><div class="label">Signed in as</div><div class="name">${esc(name)}</div><div class="role">${esc(role)} · ${esc(workspace)}</div></div></div></div></body></html>`;
}

// A terminal frame for a command that really ran during the take; `lines` is its captured output.
export function terminalHtml(prompt: string, command: string, lines: string[], highlight: string) {
  const mark = (line: string) => (highlight && line.includes(highlight)
    ? esc(line).replace(esc(highlight), `<span class="hl">${esc(highlight)}</span>`) : esc(line));
  return `<!doctype html><html><head><meta charset="utf-8"><title>Terminal</title><style>
  html,body{margin:0;height:100%;background:#e9e9f1;font-family:Inter,-apple-system,"Segoe UI",Roboto,sans-serif}
  .wrap{height:100%;display:flex;align-items:center;justify-content:center}
  .term{width:1180px;border-radius:12px;overflow:hidden;box-shadow:0 18px 60px rgba(20,20,40,.25);background:#14141c}
  .bar{height:36px;background:#24242f;display:flex;align-items:center;gap:8px;padding:0 14px;color:#9a9ab0;font-size:14px}
  .dot{width:12px;height:12px;border-radius:50%}.title{margin-left:12px}
  pre{margin:0;padding:22px 26px;color:#e6e6f0;font:16px/1.65 "DejaVu Sans Mono",Menlo,Consolas,monospace;white-space:pre-wrap;word-break:break-all}
  .p{color:#7fd7c4}.c{color:#c9c9ff}.hl{background:#3d2f8f;color:#fff;border-radius:3px;padding:0 3px;outline:2px solid #8f7bff}
  </style></head><body><div class="wrap"><div class="term"><div class="bar"><span class="dot" style="background:#ff5f57"></span><span class="dot" style="background:#febc2e"></span><span class="dot" style="background:#28c840"></span><span class="title">${esc(prompt)}</span></div>
  <pre><span class="p">$</span> <span class="c">${esc(command)}</span>\n${lines.map(mark).join('\n')}</pre></div></div></body></html>`;
}
