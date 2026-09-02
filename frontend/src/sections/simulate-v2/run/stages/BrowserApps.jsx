import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { hashSeed } from "../../_mock/runStream";

/**
 * What the agent is actually looking at.
 *
 * A grey wireframe is fine for ten seconds of a demo and useless for anything
 * else: it cannot show whether the agent typed into the right field, whether
 * the row it clicked was the one the scenario meant, or whether the board it
 * just played is the board it was given. So the viewport renders the app —
 * three of them, because this environment seeds three, and the scenario says
 * which one is on screen.
 *
 * The screen is derived from the steps rather than stored beside them. Replay
 * the same run and the same pixels come back; stop it halfway and the page is
 * in exactly the state those steps left it in.
 */

/* ── state, derived from the steps taken so far ──────────────────────────── */

export const deriveState = (app, steps = []) => {
  if (app === "todo") return todoState(steps);
  if (app === "game") return gameState(steps);
  return adminState(steps);
};

const adminState = (steps) => {
  const s = {
    screen: "login", email: "", password: "", filter: "",
    workspace: null, retried: false, toast: false, exported: false,
  };
  steps.forEach((st) => {
    const t = st.target || "";
    if (st.action === "type" && /email/i.test(t)) s.email = st.value;
    if (st.action === "type" && /password/i.test(t)) s.password = st.value;
    if (st.action === "click" && /submit/i.test(t)) s.screen = "dashboard";
    if (st.action === "wait" && /dashboard/i.test(t)) s.screen = "dashboard";
    if (st.action === "click" && /billing/i.test(t)) s.screen = "billing";
    if (st.action === "type" && /search/i.test(t)) s.filter = st.value;
    if (st.action === "click" && /northwind/i.test(t)) { s.screen = "workspace"; s.workspace = "Northwind Trading"; }
    if (st.action === "click" && /retry/i.test(t)) { s.retried = true; s.toast = true; }
    if (st.action === "click" && /export/i.test(t)) s.exported = true;
  });
  return s;
};

const TODO_SEED = [
  { id: "t1", text: "Renew SSL certificate", done: false, tag: "ops" },
  { id: "t2", text: "Reply to Northwind support thread", done: true, tag: "support" },
  { id: "t3", text: "Rotate staging API keys", done: false, tag: "ops" },
  { id: "t4", text: "Draft Q3 pricing note", done: true, tag: "finance" },
  { id: "t5", text: "Archive 2023 invoices", done: false, tag: "finance" },
];

const todoState = (steps) => {
  const s = { items: TODO_SEED.map((i) => ({ ...i })), filter: "all", draft: "", added: false, cleared: false };
  steps.forEach((st) => {
    const t = st.target || "";
    if (st.action === "click" && /active/i.test(t)) s.filter = "active";
    if (st.action === "type" && /new-task/i.test(t)) s.draft = st.value;
    if (st.action === "click" && /add/i.test(t) && s.draft) {
      s.items = [...s.items, { id: "new", text: s.draft, done: false, tag: "inbox", fresh: true }];
      s.draft = "";
      s.added = true;
    }
    if (st.action === "click" && /checkbox/i.test(t)) {
      const name = (t.match(/has-text\('([^']+)'\)/) || [])[1];
      s.items = s.items.map((i) => (i.text === name ? { ...i, done: true, justDone: true } : i));
    }
    if (st.action === "click" && /clear completed/i.test(t)) {
      s.items = s.items.filter((i) => !i.done);
      s.cleared = true;
    }
  });
  return s;
};

/* ── 2048, actually played ───────────────────────────────────────────────── */

/**
 * The board moves.
 *
 * Real merge rules rather than a picture of a board: the agent's key presses
 * are the moves, so the tiles have to end up where those moves would put them.
 * A board that ignores the arrows cannot be used to judge whether the agent
 * played well, which is the only reason to show it.
 */
const slide = (row) => {
  const tiles = row.filter(Boolean);
  const out = [];
  for (let i = 0; i < tiles.length; i += 1) {
    if (tiles[i] === tiles[i + 1]) { out.push(tiles[i] * 2); i += 1; } else out.push(tiles[i]);
  }
  while (out.length < 4) out.push(0);
  return out;
};

const rotate = (g) => g[0].map((_, c) => g.map((row) => row[c]));

const move = (grid, dir) => {
  if (dir === "ArrowLeft") return grid.map(slide);
  if (dir === "ArrowRight") return grid.map((r) => slide([...r].reverse()).reverse());
  if (dir === "ArrowUp") return rotate(rotate(grid).map(slide));
  if (dir === "ArrowDown") return rotate(rotate(grid).map((r) => slide([...r].reverse()).reverse()));
  return grid;
};

const gameState = (steps, seedKey = "board") => {
  const h = hashSeed(seedKey);
  /* A seeded board, not a random one — the scenario is "these boards". */
  const start = [
    [2, 4, 0, 0],
    [0, 2, 8, 0],
    [16, 0, 2, 4],
    [0, 32, 0, 2],
  ];
  let grid = start.map((row, r) => row.map((v, c) => (v && (h + r * 4 + c) % 9 === 0 ? v * 2 : v)));
  let moves = 0;
  let score = 0;
  steps.forEach((st) => {
    if (st.action !== "key") return;
    const next = move(grid, st.target);
    score += next.flat().reduce((a, v) => a + v, 0) - grid.flat().reduce((a, v) => a + v, 0) + 4;
    grid = next;
    moves += 1;
  });
  return { grid, moves, score: score + 168, best: Math.max(...grid.flat()) };
};

/* ── which element the agent is on ───────────────────────────────────────── */

export const focusOf = (step) => {
  if (!step) return null;
  const t = step.target || "";
  if (step.action === "navigate") return "url";
  if (/email/i.test(t)) return "email";
  if (/password/i.test(t)) return "password";
  if (/submit/i.test(t)) return "signin";
  if (/billing/i.test(t)) return "nav-billing";
  if (/search/i.test(t)) return "search";
  if (/northwind/i.test(t)) return "row";
  if (/retry/i.test(t)) return "retry";
  if (/export/i.test(t)) return "export";
  if (/new-task/i.test(t)) return "compose";
  if (/has-text\('Add'\)/i.test(t)) return "add";
  if (/checkbox/i.test(t)) return "check";
  if (/clear completed/i.test(t)) return "clear";
  if (/active/i.test(t)) return "tabs";
  if (step.action === "key") return "board";
  if (step.action === "scroll") return "list";
  return null;
};

/** Where the pointer sits for each target, as a share of the viewport. */
export const CURSOR = {
  url: { x: 28, y: 6 },
  email: { x: 50, y: 40 },
  password: { x: 50, y: 52 },
  signin: { x: 50, y: 64 },
  "nav-billing": { x: 9, y: 40 },
  search: { x: 40, y: 16 },
  row: { x: 55, y: 52 },
  retry: { x: 78, y: 55 },
  export: { x: 88, y: 12 },
  compose: { x: 45, y: 24 },
  add: { x: 82, y: 24 },
  check: { x: 22, y: 44 },
  clear: { x: 78, y: 84 },
  tabs: { x: 30, y: 15 },
  board: { x: 50, y: 52 },
  list: { x: 55, y: 66 },
};

export const urlFor = (app, state) => {
  if (app === "todo") return "app.taskly.dev/lists/inbox";
  if (app === "game") return "play.2048.io/seeded/4x4";
  if (state.screen === "workspace") return "app.acme-admin.com/billing/northwind";
  if (state.screen === "billing") return "app.acme-admin.com/billing";
  if (state.screen === "dashboard") return "app.acme-admin.com/dashboard";
  return "app.acme-admin.com/login";
};

/* ── the pages ───────────────────────────────────────────────────────────── */

/**
 * The element the agent is on.
 *
 * A 2px outline set two pixels clear of the element reads as a selection box
 * drawn over the page rather than a highlight of something in it — and around
 * a whole list it swallows the rows. A hugging ring plus a wash of the same
 * colour marks the target the way devtools does: unmistakable, and still
 * clearly part of the page underneath.
 */
const FOCUS = "#7857FC";

/*
  Two weights, because a control and a region are not the same claim. Filling a
  whole invoice list with brand colour buries the rows it is pointing at, so a
  region gets a thin ring and keeps its contents legible; a field or a button —
  the things an agent actually clicks — gets the ring and the wash.
*/
const ring = (on, variant = "control") => {
  if (!on) return {};
  if (variant === "region") {
    /* Clear of the rows rather than hugging them: a ring tight against a list
       clips the corners of everything inside it and reads as a shape rather
       than a highlight. Held off by 3px it reads the way a devtools selection
       does — "this region", not "this box". */
    return {
      /* Pixels, not theme units. `borderRadius: 6` in sx is six *multiples* of
         the theme radius — 48px — which is why the ring around a list came out
         as a capsule slicing through the rows it was meant to point at. */
      borderRadius: "8px",
      outline: `1px solid ${alpha(FOCUS, 0.7)}`,
      outlineOffset: 3,
    };
  }
  return {
    borderRadius: "6px",
    boxShadow: `0 0 0 1.5px ${alpha(FOCUS, 0.85)}`,
    bgcolor: (t) => alpha(FOCUS, t.palette.mode === "dark" ? 0.14 : 0.07),
  };
};

export default function BrowserApp({ app, state, focus }) {
  if (app === "todo") return <TodoApp state={state} focus={focus} />;
  if (app === "game") return <GameApp state={state} focus={focus} />;
  return <AdminApp state={state} focus={focus} />;
}

BrowserApp.propTypes = { app: PropTypes.string, state: PropTypes.object, focus: PropTypes.string };

/* ── admin console ───────────────────────────────────────────────────────── */

const WORKSPACES = [
  { name: "Northwind Trading", plan: "Scale", amount: "£1,248.00", status: "Past due", when: "2 days ago" },
  { name: "Bluebird Labs", plan: "Growth", amount: "£420.00", status: "Past due", when: "4 days ago" },
  { name: "Contoso Ltd", plan: "Scale", amount: "£1,900.00", status: "Paid", when: "yesterday" },
  { name: "Fabrikam", plan: "Starter", amount: "£99.00", status: "Paid", when: "3 days ago" },
  { name: "Tailspin Toys", plan: "Growth", amount: "£640.00", status: "Past due", when: "6 days ago" },
];

function AdminApp({ state, focus }) {
  if (state.screen === "login") {
    return (
      <Shell>
        <Stack alignItems="center" justifyContent="center" sx={{ flex: 1 }}>
          <Stack
            spacing={1.25}
            sx={{ width: 260, p: 2, borderRadius: 1.5, border: "1px solid", borderColor: "divider", bgcolor: "background.paper" }}
          >
            <Typography sx={{ typography: "s1", fontWeight: 700, textAlign: "center" }}>Acme Admin</Typography>
            <Field label="Email" value={state.email} placeholder="you@company.com" focused={focus === "email"} />
            <Field label="Password" value={state.password} placeholder="••••••••" focused={focus === "password"} />
            <Box
              sx={{
                mt: 0.5, py: 0.75, borderRadius: 0.75, textAlign: "center",
                bgcolor: "#2563EB", color: "#fff", typography: "s2", fontWeight: 700,
                ...ring(focus === "signin"),
              }}
            >
              Sign in
            </Box>
          </Stack>
        </Stack>
      </Shell>
    );
  }

  const rows = state.filter
    ? WORKSPACES.filter((w) => w.status.toLowerCase().includes(state.filter.toLowerCase()))
    : WORKSPACES;

  return (
    <Shell>
      <Stack direction="row" sx={{ flex: 1, minHeight: 0 }}>
        <Stack spacing={0.25} sx={{ width: 108, flexShrink: 0, p: 1, borderRight: "1px solid", borderColor: "divider" }}>
          <Typography sx={{ typography: "s3", fontWeight: 700, px: 0.75, pb: 0.5 }}>Acme Admin</Typography>
          {["Overview", "Customers", "Billing", "Usage", "Settings"].map((n) => (
            <Box
              key={n}
              sx={{
                px: 0.75, py: 0.5, borderRadius: 0.5, typography: "s3",
                color: (state.screen !== "dashboard" && n === "Billing") ? "text.primary" : "text.subtitle",
                bgcolor: (state.screen !== "dashboard" && n === "Billing") ? "action.selected" : "transparent",
                ...ring(focus === "nav-billing" && n === "Billing"),
              }}
            >
              {n}
            </Box>
          ))}
        </Stack>

        <Stack sx={{ flex: 1, minWidth: 0, p: 1.25 }} spacing={1}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography sx={{ typography: "s2", fontWeight: 700, flex: 1 }}>
              {state.screen === "workspace" ? state.workspace : state.screen === "billing" ? "Billing" : "Overview"}
            </Typography>
            {state.screen === "billing" && (
              <Box
                sx={{
                  width: 150, px: 1, py: 0.375, borderRadius: 0.75,
                  border: "1px solid", borderColor: "divider", bgcolor: "background.paper",
                  typography: "s3", color: state.filter ? "text.primary" : "text.disabled",
                  ...ring(focus === "search"),
                }}
              >
                {state.filter || "Search workspaces"}
              </Box>
            )}
            <Box
              sx={{
                px: 1, py: 0.375, borderRadius: 0.75, border: "1px solid", borderColor: "divider",
                typography: "s3", color: "text.secondary", ...ring(focus === "export"),
              }}
            >
              {state.exported ? "Exported ✓" : "Export"}
            </Box>
          </Stack>

          {state.screen === "dashboard" && (
            <Stack direction="row" spacing={1}>
              {[
                { k: "MRR", v: "£84,210" },
                { k: "Active workspaces", v: "1,284" },
                { k: "Past due", v: "3" },
              ].map((c) => (
                <Stack key={c.k} sx={{ flex: 1, p: 1, borderRadius: 1, border: "1px solid", borderColor: "divider" }}>
                  <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{c.k}</Typography>
                  <Typography sx={{ typography: "s1", fontWeight: 700 }}>{c.v}</Typography>
                </Stack>
              ))}
            </Stack>
          )}

          {state.screen === "workspace" ? (
            <Stack spacing={0.75} sx={{ ...ring(focus === "list", "region") }}>
              {[
                { id: "INV-2291", amount: "£1,248.00", state: state.retried ? "Paid" : "Failed", note: state.retried ? "retried just now" : "card declined" },
                { id: "INV-2244", amount: "£1,248.00", state: "Paid", note: "last month" },
                { id: "INV-2190", amount: "£1,180.00", state: "Paid", note: "two months ago" },
              ].map((inv, i) => (
                <Stack
                  key={inv.id}
                  direction="row" alignItems="center" spacing={1}
                  sx={{ px: 1, py: 0.75, borderRadius: 0.75, border: "1px solid", borderColor: "divider" }}
                >
                  <Typography sx={{ typography: "s3", fontFamily: "ui-monospace, Menlo, monospace", width: 62 }}>{inv.id}</Typography>
                  <Typography sx={{ typography: "s3", width: 62 }}>{inv.amount}</Typography>
                  <Pill tone={inv.state === "Paid" ? "#16A34A" : "#DC2626"}>{inv.state}</Pill>
                  <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}>{inv.note}</Typography>
                  {i === 0 && (
                    <Box
                      sx={{
                        px: 1, py: 0.25, borderRadius: 0.75, typography: "s3", fontWeight: 700,
                        bgcolor: state.retried ? "action.hover" : "#2563EB",
                        color: state.retried ? "text.subtitle" : "#fff",
                        ...ring(focus === "retry"),
                      }}
                    >
                      {state.retried ? "Retried" : "Retry payment"}
                    </Box>
                  )}
                </Stack>
              ))}
            </Stack>
          ) : (
            <Stack sx={{ borderRadius: 1, border: "1px solid", borderColor: "divider", overflow: "hidden" }}>
              <Stack direction="row" spacing={1} sx={{ px: 1, py: 0.5, bgcolor: "background.neutral" }}>
                {["Workspace", "Plan", "Amount", "Status"].map((h) => (
                  <Typography key={h} sx={{ typography: "s3", color: "text.subtitle", flex: h === "Workspace" ? 2 : 1 }}>{h}</Typography>
                ))}
              </Stack>
              {rows.map((w) => (
                <Stack
                  key={w.name}
                  direction="row" alignItems="center" spacing={1}
                  sx={{
                    px: 1, py: 0.625, borderTop: "1px solid", borderColor: "divider",
                    ...ring(focus === "row" && w.name.startsWith("Northwind")),
                  }}
                >
                  <Typography noWrap sx={{ typography: "s3", flex: 2, fontWeight: w.name.startsWith("Northwind") ? 700 : 400 }}>
                    {w.name}
                  </Typography>
                  <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}>{w.plan}</Typography>
                  <Typography sx={{ typography: "s3", flex: 1 }}>{w.amount}</Typography>
                  <Box sx={{ flex: 1 }}>
                    <Pill tone={w.status === "Paid" ? "#16A34A" : "#DC2626"}>{w.status}</Pill>
                  </Box>
                </Stack>
              ))}
            </Stack>
          )}
        </Stack>
      </Stack>

      {state.toast && (
        <Toast icon="solar:check-circle-bold" tone="#16A34A">
          Payment retried — INV-2291 is paid
        </Toast>
      )}
    </Shell>
  );
}

AdminApp.propTypes = { state: PropTypes.object, focus: PropTypes.string };

/* ── todo app ────────────────────────────────────────────────────────────── */

function TodoApp({ state, focus }) {
  const shown = state.filter === "active" ? state.items.filter((i) => !i.done) : state.items;
  const left = state.items.filter((i) => !i.done).length;

  return (
    <Shell>
      <Stack sx={{ flex: 1, minHeight: 0, p: 1.5 }} spacing={1}>
        <Typography sx={{ typography: "s1", fontWeight: 700 }}>Inbox</Typography>

        <Stack direction="row" spacing={1}>
          <Box
            sx={{
              flex: 1, px: 1, py: 0.5, borderRadius: 0.75, border: "1px solid", borderColor: "divider",
              typography: "s3", color: state.draft ? "text.primary" : "text.disabled",
              bgcolor: "background.paper", ...ring(focus === "compose"),
            }}
          >
            {state.draft || "What needs doing?"}
          </Box>
          <Box
            sx={{
              px: 1.25, py: 0.5, borderRadius: 0.75, bgcolor: "#2563EB", color: "#fff",
              typography: "s3", fontWeight: 700, ...ring(focus === "add"),
            }}
          >
            Add
          </Box>
        </Stack>

        <Stack direction="row" spacing={0.5} sx={{ ...ring(focus === "tabs") }}>
          {["All", "Active", "Done"].map((t) => (
            <Box
              key={t}
              sx={{
                px: 1, py: 0.25, borderRadius: 0.75, typography: "s3",
                bgcolor: state.filter === t.toLowerCase() || (state.filter === "all" && t === "All") ? "action.selected" : "transparent",
                color: state.filter === t.toLowerCase() || (state.filter === "all" && t === "All") ? "text.primary" : "text.subtitle",
              }}
            >
              {t}
            </Box>
          ))}
        </Stack>

        <Stack spacing={0.5} sx={{ flex: 1, minHeight: 0, overflow: "hidden", ...ring(focus === "list", "region") }}>
          {shown.map((i) => (
            <Stack
              key={i.id}
              direction="row" alignItems="center" spacing={1}
              sx={{
                px: 1, py: 0.625, borderRadius: 0.75, border: "1px solid",
                borderColor: i.fresh ? alpha("#16A34A", 0.5) : "divider",
                bgcolor: i.fresh ? (t) => alpha("#16A34A", t.palette.mode === "dark" ? 0.1 : 0.05) : "transparent",
                ...ring(focus === "check" && i.justDone),
              }}
            >
              <Box
                sx={{
                  width: 13, height: 13, borderRadius: 0.5, flexShrink: 0,
                  border: "1.5px solid", borderColor: i.done ? "#16A34A" : "text.disabled",
                  bgcolor: i.done ? "#16A34A" : "transparent",
                  display: "grid", placeItems: "center",
                }}
              >
                {i.done && <Iconify icon="mingcute:check-fill" width={9} sx={{ color: "#fff" }} />}
              </Box>
              <Typography
                noWrap
                sx={{
                  typography: "s3", flex: 1,
                  color: i.done ? "text.disabled" : "text.primary",
                  textDecoration: i.done ? "line-through" : "none",
                }}
              >
                {i.text}
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{i.tag}</Typography>
            </Stack>
          ))}
        </Stack>

        <Stack direction="row" alignItems="center" spacing={1}>
          <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}>
            {left} left · {state.items.length - left} done
          </Typography>
          <Box
            sx={{
              px: 1, py: 0.25, borderRadius: 0.75, border: "1px solid", borderColor: "divider",
              typography: "s3", color: "text.secondary", ...ring(focus === "clear"),
            }}
          >
            Clear completed
          </Box>
        </Stack>
      </Stack>

      {state.cleared && (
        <Toast icon="solar:trash-bin-trash-bold" tone="#7857FC">
          Completed tasks cleared
        </Toast>
      )}
    </Shell>
  );
}

TodoApp.propTypes = { state: PropTypes.object, focus: PropTypes.string };

/* ── 2048 ────────────────────────────────────────────────────────────────── */

const TILE = {
  0: { bg: "rgba(120,120,130,.12)", fg: "transparent" },
  2: { bg: "#EEE4DA", fg: "#776E65" },
  4: { bg: "#EDE0C8", fg: "#776E65" },
  8: { bg: "#F2B179", fg: "#FFF" },
  16: { bg: "#F59563", fg: "#FFF" },
  32: { bg: "#F67C5F", fg: "#FFF" },
  64: { bg: "#F65E3B", fg: "#FFF" },
  128: { bg: "#EDCF72", fg: "#FFF" },
  256: { bg: "#EDCC61", fg: "#FFF" },
  512: { bg: "#EDC850", fg: "#FFF" },
};

function GameApp({ state, focus }) {
  return (
    <Shell>
      <Stack alignItems="center" justifyContent="center" sx={{ flex: 1, p: 1.5 }} spacing={1}>
        <Stack direction="row" spacing={1} sx={{ width: 232 }}>
          <Typography sx={{ typography: "s1", fontWeight: 700, flex: 1 }}>2048</Typography>
          {[{ k: "SCORE", v: state.score }, { k: "BEST", v: state.best }].map((c) => (
            <Stack key={c.k} alignItems="center" sx={{ px: 1, py: 0.25, borderRadius: 0.75, bgcolor: "background.neutral" }}>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{c.k}</Typography>
              <Typography sx={{ typography: "s2", fontWeight: 700 }}>{c.v}</Typography>
            </Stack>
          ))}
        </Stack>

        <Box
          sx={{
            p: 0.75, borderRadius: 1.25, bgcolor: (t) => alpha("#BBADA0", t.palette.mode === "dark" ? 0.25 : 0.7),
            display: "grid", gridTemplateColumns: "repeat(4, 52px)", gap: 0.75,
            ...ring(focus === "board", "region"),
          }}
        >
          {state.grid.flat().map((v, i) => {
            const tone = TILE[v] || TILE[512];
            return (
              <Box
                key={i}
                sx={{
                  height: 52, borderRadius: 0.75, display: "grid", placeItems: "center",
                  bgcolor: tone.bg,
                  transition: "background-color .25s ease",
                }}
              >
                <Typography sx={{ typography: v > 64 ? "s2" : "s1", fontWeight: 800, color: tone.fg }}>
                  {v || ""}
                </Typography>
              </Box>
            );
          })}
        </Box>

        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {state.moves} moves · arrow keys to play
        </Typography>
      </Stack>
    </Shell>
  );
}

GameApp.propTypes = { state: PropTypes.object, focus: PropTypes.string };

/* ── shared bits ─────────────────────────────────────────────────────────── */

function Shell({ children }) {
  return (
    <Stack sx={{ position: "absolute", inset: 0, bgcolor: "background.default", overflow: "hidden" }}>
      {children}
    </Stack>
  );
}
Shell.propTypes = { children: PropTypes.node };

function Field({ label, value, placeholder, focused }) {
  return (
    <Box>
      <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 0.25 }}>{label}</Typography>
      <Box
        sx={{
          px: 1, py: 0.5, borderRadius: 0.75, border: "1px solid", borderColor: "divider",
          typography: "s3", color: value ? "text.primary" : "text.disabled",
          fontFamily: "ui-monospace, Menlo, monospace",
          bgcolor: "background.default",
          ...ring(focused),
        }}
      >
        {value || placeholder}
      </Box>
    </Box>
  );
}
Field.propTypes = {
  label: PropTypes.string, value: PropTypes.string, placeholder: PropTypes.string, focused: PropTypes.bool,
};

function Pill({ tone, children }) {
  return (
    <Typography
      component="span"
      sx={{
        px: 0.625, py: 0.125, borderRadius: 0.5, typography: "s3", fontWeight: 700, color: tone,
        bgcolor: (t) => alpha(tone, t.palette.mode === "dark" ? 0.18 : 0.1),
      }}
    >
      {children}
    </Typography>
  );
}
Pill.propTypes = { tone: PropTypes.string, children: PropTypes.node };

function Toast({ icon, tone, children }) {
  return (
    <Stack
      direction="row" alignItems="center" spacing={0.875}
      sx={{
        position: "absolute", right: 12, bottom: 12, zIndex: 4,
        px: 1.25, py: 0.75, borderRadius: 1,
        border: "1px solid", borderColor: alpha(tone, 0.4),
        bgcolor: "background.paper",
        boxShadow: (t) => `0 6px 20px ${alpha("#000", t.palette.mode === "dark" ? 0.5 : 0.12)}`,
      }}
    >
      <Iconify icon={icon} width={14} sx={{ color: tone }} />
      <Typography sx={{ typography: "s3", fontWeight: 600 }}>{children}</Typography>
    </Stack>
  );
}
Toast.propTypes = { icon: PropTypes.string, tone: PropTypes.string, children: PropTypes.node };
