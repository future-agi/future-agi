/*
  Demo mode — the hosted prototype (VITE_DEMO_MODE=true). Nobody signs in:
  the app starts as a demo user, and every request to the API is answered
  here with demo data, so nothing leaves the browser. Local runs and the
  product never set the flag, so none of this runs there.
*/
import { OBSERVE_PROJECTS } from "src/sections/simulate-v2/_mock/connectAgent";

export const DEMO_MODE = import.meta.env.VITE_DEMO_MODE === "true";

const ORG = { id: "demo-org", name: "future-agi-demo", display_name: "Future AGI demo" };
const WS = { id: "demo-ws", name: "default", display_name: "Default Workspace" };

const USER = {
  id: "demo-user",
  email: "demo@futureagi.com",
  name: "Demo user",
  organization_role: "Owner",
  organization: { ...ORG, ws_enabled: true },
  created_at: "2026-09-01T09:00:00Z",
  status: "Active",
  role: "Product Manager",
  goals: [],
  remember_me: true,
  get_started_completed: true,
  onboarding_completed: true,
  restrict_simulate_to_environments: false,
  ws_enabled: true,
  default_workspace_id: WS.id,
  default_workspace_name: WS.name,
  default_workspace_display_name: WS.display_name,
  default_workspace_role: "Owner",
  org_level: 15,
  ws_level: 15,
  effective_level: 15,
  has_2fa_enabled: false,
  two_factor_methods: { totp: false, passkey: false },
};

const now = Date.parse("2026-11-07T09:00:00Z");
const PROJECTS = OBSERVE_PROJECTS.map((p, i) => ({
  id: p.id,
  name: p.name,
  last_30_days_vol: 2140 - i * 310,
  daily_volume: Array.from({ length: 30 }, (_, d) => 40 + ((d * 7 + i * 13) % 45)),
  created_at: new Date(now - (40 + i) * 864e5).toISOString(),
  updated_at: new Date(now - i * 36e5).toISOString(),
  last_active: new Date(now - (12 + i * 20) * 6e4).toISOString(),
  activity_query_complete: true,
  activity_error_code: null,
  activity_query_exact: true,
  activity_query_provenance: "demo",
  run_count: 0,
  issues: p.issues || 0,
  tags: [],
}));

/* A numbered page, echoing the page asked for (list readers check it). */
const page = (rows, params = {}) => {
  const size = Number(params.page_size) || 10;
  const number = Number(params.page_number ?? params.current_page_index) || 0;
  return {
    metadata: { total_rows: rows.length, total_count: rows.length, page_number: number, page_size: size, total_pages: Math.ceil(rows.length / size) },
    table: rows.slice(number * size, number * size + size),
  };
};
/* The features the prototype's flows use; the rest stay behind their upgrade screen. */
const features = () => Object.fromEntries(["error_feed", "voice_sim", "optimization"].map((id) => [id, { allowed: true }]));

/* path → result. Anything not listed answers an empty success. */
const ROUTES = [
  [/\/api\/capabilities\/$/, () => ({ features: features() })],
  [/\/accounts\/user-info\/$/, () => USER],
  [/\/accounts\/organizations\/$/, () => ({ status: true, result: { organizations: [{ ...ORG, role: "Owner", level: 15, is_selected: true }] } })],
  [/\/accounts\/organizations\/current\/$/, () => ({ status: true, result: { ...ORG, role: "Owner", level: 15 } })],
  [/\/accounts\/workspace\/list\/$/, () => ({ status: true, result: { workspaces: [{ ...WS, role: "Owner", is_default: true }], ...page([{ ...WS, role: "Owner" }]) } })],
  [/\/tracer\/project\/list_projects\/$/, (q) => ({ status: true, result: page(PROJECTS, q) })],
  [/\/tracer\/feed\/issues\/$/, () => ({ status: true, result: { data: [], total: 0, limit: 25, offset: 0 } })],
  [/\/integrations\/connections\/$/, (q) => ({ status: true, result: page([], q) })],
  [/\/accounts\/key\/get_secret_keys\/$/, (q) => ({ status: true, result: page([], q) })],
  [/\/accounts\/first-checks\/$/, () => ({ status: true, result: { completed: true } })],
];

const answer = (method, url, params = {}) => {
  const u = new URL(url, "https://demo.invalid");
  const q = { ...Object.fromEntries(u.searchParams), ...params };
  const hit = ROUTES.find(([re]) => re.test(u.pathname));
  if (hit) return hit[1](q);
  return method === "get" ? { status: true, result: [] } : { status: true, result: {} };
};

/* An axios adapter: the request never goes out. */
export const demoAdapter = async (config) => ({
  data: answer((config.method || "get").toLowerCase(), `${config.baseURL || ""}${config.url || ""}`, config.params || {}),
  status: 200,
  statusText: "OK",
  headers: {},
  config,
  request: {},
});

/* Before the app mounts: a demo session, and fetch() to the API answered the same way. */
export const installDemoMode = (axiosInstance, axiosPackage, hostApi) => {
  /* The demo opens on Environments, where every flow starts. */
  if (location.pathname === "/" || location.pathname === "/dashboard" || location.pathname === "/dashboard/") history.replaceState(null, "", "/dashboard/simulate/environments");
  localStorage.setItem("accessToken", "demo-token");
  localStorage.setItem("refreshToken", "demo-token");
  axiosInstance.defaults.adapter = demoAdapter;
  axiosPackage.defaults.adapter = demoAdapter;
  const realFetch = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const url = typeof input === "string" ? input : input?.url || "";
    if (hostApi && url.startsWith(hostApi)) {
      const body = JSON.stringify(answer((init?.method || "GET").toLowerCase(), url));
      return Promise.resolve(new Response(body, { status: 200, headers: { "Content-Type": "application/json" } }));
    }
    return realFetch(input, init);
  };
};
