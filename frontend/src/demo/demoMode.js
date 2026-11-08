/*
  Demo mode — the hosted prototype (VITE_DEMO_MODE=true). Nobody signs in:
  the app starts as a demo user, and every request to the API is answered
  here with demo data, so nothing leaves the browser. Local runs and the
  product never set the flag, so none of this runs there.
*/
import { OBSERVE_PROJECTS } from "src/sections/simulate-v2/_mock/connectAgent";
import { DEMO_PERSONAS, personasPage, savePersona, deletePersona } from "./personas";
import { evalsPage, evalDetail } from "./evals";

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

/* Teammates, API keys and an observability connection for Settings and the Error Feed. */
const TEAM = [
  { id: USER.id, email: USER.email, name: USER.name, organization_role: "Owner", is_active: true, is_staff: false },
  { id: "demo-user-2", email: "priya@futureagi.com", name: "Priya Shah", organization_role: "Admin", is_active: true, is_staff: false },
  { id: "demo-user-3", email: "marcus@futureagi.com", name: "Marcus Reid", organization_role: "Member", is_active: true, is_staff: false },
  { id: "demo-user-4", email: "elena@futureagi.com", name: "Elena Vargas", organization_role: "Member", is_active: true, is_staff: false },
];
const KEYS = [
  { id: "demo-key-1", key_name: "CI pipeline", api_key: "fi_demo_4f2a••••••••", secret_key: "fs_demo_••••••••", created_by: USER.name, created_at: "2026-10-02T10:00:00Z", enabled: true, type: "user" },
  { id: "demo-key-2", key_name: "Local development", api_key: "fi_demo_9c1e••••••••", secret_key: "fs_demo_••••••••", created_by: USER.name, created_at: "2026-10-20T10:00:00Z", enabled: true, type: "user" },
];
const CONNECTIONS = [
  { id: "demo-conn-1", platform: "langfuse", display_name: "Langfuse", host_url: "https://cloud.langfuse.com", status: "active", status_message: "Synced", external_project_name: "support-agents", last_synced_at: "2026-11-07T08:40:00Z", total_traces_synced: 1840, total_spans_synced: 12650, total_scores_synced: 420, backfill_completed: true, backfill_progress: {}, sync_interval_seconds: 300, created_at: "2026-10-10T10:00:00Z" },
];
const drfPage = (rows) => ({ count: rows.length, next: null, previous: null, results: rows, total_pages: 1, current_page: 1 });

/* path → result. Anything not listed answers an empty success. */
const ROUTES = [
  [/\/simulate\/api\/personas\/$/, (q, path, m, body) => ({ status: true, result: m === "post" ? savePersona(body) : personasPage(q) })],
  [/\/simulate\/api\/personas\/[^/]+\/$/, (q, path, m, body) => {
    const id = path.split("/").filter(Boolean).pop();
    if (m === "delete") return { status: true, result: deletePersona(id) };
    if (m === "put" || m === "patch") return { status: true, result: savePersona({ ...body, id }) };
    return { status: true, result: DEMO_PERSONAS.find((p) => p.id === id) || DEMO_PERSONAS[0] };
  }],
  [/\/api\/capabilities\/$/, () => ({ features: features() })],
  [/\/accounts\/user-info\/$/, () => USER],
  [/\/accounts\/organizations\/$/, () => ({ status: true, result: { organizations: [{ ...ORG, role: "Owner", level: 15, is_selected: true }] } })],
  [/\/accounts\/organizations\/current\/$/, () => ({ status: true, result: { ...ORG, role: "Owner", level: 15 } })],
  [/\/accounts\/workspace\/list\/$/, () => ({ status: true, result: { workspaces: [{ ...WS, role: "Owner", is_default: true }], ...page([{ ...WS, role: "Owner" }]) } })],
  [/\/tracer\/project\/list_projects\/$/, (q) => ({ status: true, result: page(PROJECTS, q) })],
  [/\/tracer\/feed\/issues\/$/, () => ({ status: true, result: { data: [], total: 0, limit: 25, offset: 0 } })],
  [/\/integrations\/connections\/$/, () => ({ status: true, result: { metadata: { total_count: CONNECTIONS.length, current_page: 1, page_size: 10, total_pages: 1, next_page: null }, connections: CONNECTIONS } })],
  [/\/model-hub\/organizations\/[^/]+\/users\/$/, () => drfPage(TEAM)],
  [/\/accounts\/key\/get_secret_keys\/$/, (q) => ({ status: true, result: page(KEYS, q) })],
  [/\/accounts\/first-checks\/$/, () => ({ status: true, result: { completed: true } })],
  /* Product screens beside the prototype: the empty shapes their lists read, so they show their own empty states. */
  [/\/model-hub\/eval-templates\/list\/$/, (q) => ({ status: true, result: evalsPage(q) })],
  [/\/model-hub\/eval-templates\/[^/]+\/detail\/$/, (q, path) => ({ status: true, result: evalDetail(path) })],
  [/\/model-hub\/get-eval-template-names\/?$/, () => ({ status: true, result: [] })],
  [/\/tracer\/eval-task\/list_eval_tasks(_with_project_name)?\/$/, (q) => ({ status: true, result: page([], q) })],
  [/\/agentcc\/gateways\/$/, () => ({ status: true, result: [{ id: "demo-gateway", name: "Default gateway", status: "healthy", is_active: true, created_at: "2026-10-01T09:00:00Z" }] })],
];

const answer = (method, url, params = {}, body = {}) => {
  const u = new URL(url, "https://demo.invalid");
  /* A POST list sends its paging and search in the body. */
  const q = { ...Object.fromEntries(u.searchParams), ...(body && typeof body === "object" && !(body instanceof FormData) ? body : {}), ...params };
  const hit = ROUTES.find(([re]) => re.test(u.pathname));
  if (hit) return hit[1](q, u.pathname, method, body);
  /* Requests with no demo answer yet, readable in the console as __demoMisses. */
  (window.__demoMisses ||= new Set()).add(`${method.toUpperCase()} ${u.pathname}`);
  return method === "get" ? { status: true, result: [] } : { status: true, result: {} };
};

const parseBody = (data) => {
  if (typeof data !== "string") return data || {};
  try { return JSON.parse(data); } catch { return {}; }
};

/* An axios adapter: the request never goes out. */
export const demoAdapter = async (config) => ({
  data: answer((config.method || "get").toLowerCase(), `${config.baseURL || ""}${config.url || ""}`, config.params || {}, parseBody(config.data)),
  status: 200,
  statusText: "OK",
  headers: {},
  config,
  request: {},
});

/* Before the app mounts: a demo session, and fetch() to the API answered the same way. */
export const installDemoMode = (axiosInstance, axiosPackage, hostApi) => {
  /* The demo opens on Environments, where every flow starts. */
  if (!/^\/dashboard\/./.test(location.pathname) || /^\/dashboard\/falcon-ai/.test(location.pathname)) history.replaceState(null, "", "/dashboard/simulate/environments");
  localStorage.setItem("accessToken", "demo-token");
  axiosInstance.defaults.adapter = demoAdapter;
  axiosPackage.defaults.adapter = demoAdapter;
  const realFetch = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const url = typeof input === "string" ? input : input?.url || "";
    if (hostApi && url.startsWith(hostApi)) {
      const body = JSON.stringify(answer((init?.method || "GET").toLowerCase(), url, {}, parseBody(init?.body)));
      return Promise.resolve(new Response(body, { status: 200, headers: { "Content-Type": "application/json" } }));
    }
    return realFetch(input, init);
  };
};
