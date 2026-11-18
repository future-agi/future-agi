/* Docs behaviour: theme switch, light/dark demo pairs, token tables from tokens.js. */
(function () {
  const DS = window.DS;
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const h = (tag, attrs = {}, html = "") => {
    const el = document.createElement(tag);
    Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
    if (html) el.innerHTML = html;
    return el;
  };
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  /* ---------------------------------------------------------------- theme switch */
  const root = document.documentElement;
  function setTheme(mode) {
    if (mode === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", mode);
    try { localStorage.setItem("ds-theme", mode); } catch (e) {}
    $$(".theme-switch button").forEach((b) => b.classList.toggle("is-selected", b.dataset.set === mode));
  }
  let saved = "system";
  try { saved = localStorage.getItem("ds-theme") || "system"; } catch (e) {}
  setTheme(saved);
  $$(".theme-switch button").forEach((b) => b.addEventListener("click", () => setTheme(b.dataset.set)));

  /* ---------------------------------------------------------------- mobile nav */
  const menuBtn = $("#menuBtn");
  menuBtn.addEventListener("click", () => {
    const open = document.body.classList.toggle("nav-open");
    menuBtn.setAttribute("aria-expanded", String(open));
  });
  $$(".side a.lnk").forEach((a) => a.addEventListener("click", () => {
    document.body.classList.remove("nav-open");
    menuBtn.setAttribute("aria-expanded", "false");
  }));

  /* ---------------------------------------------------------------- data swatches inside demos */
  $$('[data-fill="data"], [data-fill="dataMono"]').forEach((row) => {
    const set = row.dataset.fill;
    const cssName = set.replace(/([a-z0-9])([A-Z])/g, "$1-$2").toLowerCase();
    row.innerHTML = [1, 2, 3, 4, 5, 6, 7, 8]
      .map((i) => `<span class="ds-tag"><span class="sw" style="background:var(--ds-${cssName}-${i})"></span>${set}.${i}</span>`)
      .join("");
  });

  /* ---------------------------------------------------------------- demo pairs */
  $$("[data-demo]").forEach((demo) => {
    const opts = (demo.dataset.demo || "").split(/\s+/);
    const surface = opts.includes("surface");
    const pair = h("div", { class: "pair" + (opts.includes("stack") ? " stacked" : "") });
    ["light", "dark"].forEach((theme) => {
      const pane = h("div", { class: "pane ds" + (surface ? " on-surface" : ""), "data-theme": theme });
      pane.append(h("span", { class: "pane-tag" }, theme === "light" ? "Light" : "Dark"));
      const inner = h("div");
      inner.innerHTML = demo.innerHTML;
      pane.append(inner);
      pair.append(pane);
    });
    demo.replaceWith(pair);
  });

  /* ---------------------------------------------------------------- primitives */
  const prim = DS.tokens.primitive;
  const ramps = $("#ramps");
  Object.entries(prim).forEach(([name, steps]) => {
    const row = h("div", { class: "ramp" });
    row.append(h("span", { class: "nm" }, esc(name)));
    Object.entries(steps).forEach(([step, hex]) => {
      row.append(h("div", { class: "st", title: `${name}.${step} ${hex}` }, `<i style="background:${hex}"></i><span>${step}</span>`));
    });
    ramps.append(row);
  });

  /* ---------------------------------------------------------------- semantic table */
  function leaves(node, prefix = []) {
    const out = [];
    Object.entries(node).forEach(([k, v]) => {
      if (k.startsWith("$")) return;
      if (v && typeof v === "object" && "light" in v && "dark" in v) out.push([[...prefix, k].join("."), v]);
      else if (v && typeof v === "object") out.push(...leaves(v, [...prefix, k]));
    });
    return out;
  }
  const sw = (value) => `<span class="sw"><i style="background:${value}"></i>${esc(value.replace(/rgba\(([^)]+)\)/, (m, p) => "rgba(" + p.replace(/\s/g, "") + ")"))}</span>`;
  const semBody = $("#semanticTable tbody");
  leaves(DS.tokens.color)
    .filter(([name]) => !name.startsWith("status.") && !name.startsWith("data.") && !name.startsWith("dataMono."))
    .forEach(([name, leaf]) => {
      const tr = h("tr");
      tr.innerHTML = `<td><span class="mono">${esc(name)}</span><div class="mono" style="color:var(--ds-fg-tertiary);font-size:11px">--ds-${esc(name.replace(/\./g, "-").replace(/([a-z0-9])([A-Z])/g, "$1-$2").toLowerCase())}</div></td>
        <td>${sw(DS.light[name])}</td><td>${sw(DS.dark[name])}</td><td>${esc(leaf.use || "")}</td>`;
      semBody.append(tr);
    });

  /* ---------------------------------------------------------------- status table */
  const meaning = {
    success: "Passed, healthy, improved, connected live",
    warning: "Needs attention or input; close to a limit",
    danger: "Failed, error, regressed, destructive",
    info: "Running, in progress, neutral information",
    neutral: "Not started, draft, stopped, sealed",
    discovery: "AI-suggested, new feature",
  };
  const stBody = $("#statusTable tbody");
  Object.keys(meaning).forEach((role) => {
    const trip = (t) => ["fg", "bg", "solid"].map((k) => `<span class="sw" title="${k}"><i style="background:${DS[t][`status.${role}.${k}`]}"></i></span>`).join(" ");
    const tr = h("tr");
    tr.innerHTML = `<td>${role}</td><td>${meaning[role]}</td><td>${trip("light")}</td><td>${trip("dark")}</td>`;
    stBody.append(tr);
  });

  /* ---------------------------------------------------------------- contrast */
  const cBody = $("#contrastTable tbody");
  DS.contrast.forEach((r) => {
    const cell = (v) => r.min === 0 ? `<span class="mono">${v}</span>` : `<span class="mono ${v >= r.min ? "pass" : "fail"}">${v}</span>`;
    const tr = h("tr");
    tr.innerHTML = `<td>${esc(r.what)}</td><td><span class="mono">${esc(r.fg)}</span> on <span class="mono">${esc(r.bg)}</span></td><td>${cell(r.light)}</td><td>${cell(r.dark)}</td><td class="mono">${r.min ? r.min + ":1" : "exempt"}</td>`;
    cBody.append(tr);
  });

  /* ---------------------------------------------------------------- typography */
  const sample = {
    display: "Simulate before you ship", "title-lg": "Evaluations", "title-md": "Billing copilot environment",
    "title-sm": "How the world was built", "title-xs": "Agent", "body-md": "Customers query charges, dispute transactions and change how they pay.",
    "body-sm": "Storefront assistant · Vapi · 6d ago", "label-md": "Run simulation", "label-sm": "Passed",
    caption: "Phone support for an online storefront", overline: "Read from source", "code-sm": "agt_9f2c41 · b2ca42d",
    "code-md": '{ "passed": false }', "metric-lg": "85%", "metric-md": "1,284",
  };
  const typeSpec = $("#typeSpec");
  Object.entries(DS.tokens.font.role).forEach(([name, r]) => {
    const row = h("div");
    row.innerHTML = `<span class="mono">${name}</span><span class="ds-text-${name}" style="color:var(--ds-fg-default)">${esc(sample[name] || name)}</span><span class="meta">${r.size}/${r.line} · ${r.weight}${r.family === "mono" ? " · mono" : ""}</span><span class="use">${esc(r.use)}</span>`;
    typeSpec.append(row);
  });

  /* ---------------------------------------------------------------- spacing, radius, sizes, motion */
  const space = $("#spaceScale");
  Object.entries(DS.tokens.space).forEach(([k, v]) => {
    space.append(h("div", { class: "space-row" }, `<span>space.${k} · ${v}px</span><i style="width:${Math.max(v, 1) * 4}px"></i>`));
  });
  const radius = $("#radiusScale");
  const rUse = DS.tokens.radius.$use;
  Object.entries(DS.tokens.radius).forEach(([k, v]) => {
    if (k.startsWith("$")) return;
    radius.append(h("div", {}, `<i style="border-radius:${Math.min(v, 22)}px"></i>${k} · ${v === 9999 ? "full" : v + "px"}<span style="font-family:var(--ds-font-sans);max-width:110px;text-align:center">${esc(rUse[k] || "")}</span>`));
  });
  const sizeUse = {
    control: "Buttons, inputs, selects, segmented controls", controlPaddingX: "Horizontal padding per control size",
    icon: "Icons", row: "Table and list rows", tableHeader: "Table header", badge: "Badge and tag height",
    avatar: "Avatar", drawer: "Drawer widths", dialog: "Dialog widths", sidebar: "App sidebar",
  };
  const sizeBody = $("#sizeTable tbody");
  Object.entries(DS.tokens.size).forEach(([group, vals]) => {
    const tr = h("tr");
    tr.innerHTML = `<td class="mono">size.${group}</td><td class="mono">${Object.entries(vals).map(([k, v]) => `${k} ${v}`).join(" · ")}</td><td>${sizeUse[group] || ""}</td>`;
    sizeBody.append(tr);
  });
  const motion = $("#motionRow");
  const m = DS.tokens.motion;
  [["fast", "move"], ["base", "enter"], ["moderate", "move"], ["slow", "exit"]].forEach(([d, e]) => {
    const b = h("button", { type: "button" }, `<i style="transition: transform ${m.duration[d]}ms ${m.easing[e]}"></i>${d} ${m.duration[d]}ms<br>${e}`);
    motion.append(b);
  });

  /* ---------------------------------------------------------------- live toasts (same behaviour as the product's notistack setup) */
  const TOAST_MS = 6000;
  const toastText = {
    success: ["check-circle", "Environment saved as v2"],
    danger: ["x-circle", "Couldn't reach the agent. The endpoint returned 502."],
    warning: ["alert", "Run started with 2 scenarios skipped"],
    info: ["info", "Sync started. We'll tell you when it's done."],
  };
  const stack = h("div", { class: "ds-toasts", "aria-live": "polite" });
  document.body.append(stack);
  function dismiss(t) {
    if (t.classList.contains("is-leaving")) return;
    clearTimeout(t._timer);
    t.classList.add("is-leaving");
    setTimeout(() => t.remove(), 220);
  }
  function arm(t, ms) {
    t._started = Date.now();
    t._left = ms;
    t._timer = setTimeout(() => dismiss(t), ms);
  }
  function showToast(kind, theme) {
    const [icon, text] = toastText[kind];
    if ([...stack.children].some((t) => t.dataset.text === text && !t.classList.contains("is-leaving"))) return;
    const t = h("div", { class: `ds ds-toast ds-toast--${kind}`, "data-theme": theme, "data-text": text, role: "status" },
      `<span class="lead"><svg class="i"><use href="#i-${icon}"/></svg></span><span class="msg">${esc(text)}</span>` +
      (kind === "success" ? '<button class="ds-btn ds-btn--link act" type="button">Undo</button>' : "") +
      '<button class="close" type="button" aria-label="Dismiss"><svg class="i"><use href="#i-x"/></svg></button>');
    t.querySelector(".close").addEventListener("click", () => dismiss(t));
    t.querySelector(".act")?.addEventListener("click", () => dismiss(t));
    t.addEventListener("mouseenter", () => { clearTimeout(t._timer); t._left -= Date.now() - t._started; });
    t.addEventListener("mouseleave", () => arm(t, Math.max(t._left, 600)));
    stack.append(t);
    const live = [...stack.children].filter((x) => !x.classList.contains("is-leaving"));
    if (live.length > 5) dismiss(live[0]);
    arm(t, TOAST_MS);
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-toast]");
    if (!b) return;
    const pane = b.closest("[data-theme]");
    showToast(b.dataset.toast, pane ? pane.getAttribute("data-theme") : (root.getAttribute("data-theme") || "light"));
  });

  /* ---------------------------------------------------------------- current section in nav */
  const links = new Map($$(".side a.lnk").map((a) => [a.getAttribute("href").slice(1), a]));
  const crumb = $("#crumbNow");
  const io = new IntersectionObserver((entries) => {
    entries.forEach((en) => {
      if (!en.isIntersecting) return;
      const id = en.target.id;
      links.forEach((a) => a.classList.remove("is-current"));
      const a = links.get(id);
      if (a) { a.classList.add("is-current"); crumb.textContent = a.textContent; }
    });
  }, { rootMargin: "-80px 0px -70% 0px" });
  $$("section.doc[id]").forEach((s) => io.observe(s));
})();
