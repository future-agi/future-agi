// `/` fallback: focus the page's first visible search field.

const visible = (el) => {
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
};

/** Focus the page's first visible search field. Returns true when found. */
export function focusPageSearch() {
  const main = document.querySelector("main") ?? document.body;
  const candidates = main.querySelectorAll(
    'input[type="search"], input[aria-label*="earch" i], input[placeholder*="earch" i]',
  );
  const el = [...candidates].find(visible);
  if (!el) return false;
  el.focus();
  el.select?.();
  return true;
}
