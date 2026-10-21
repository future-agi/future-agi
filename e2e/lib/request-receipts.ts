// Lifecycle-aware receipt acquisition for AUTH-E2E-002.
//
// Playwright Request.allHeaders() waits with no timeout until the browser
// delivers raw headers or the page closes. A pre-prime request that never
// completes (GET /accounts/config/ during browser setup) therefore holds
// Promise.all until the suite timeout, and the post-prime guards never run.
//
// Complete proof still comes only from allHeaders(). request.headers() is not
// an input: it omits browser-added headers and is not equivalent. A request
// that a later main-frame navigation superseded, or that the browser aborted,
// before any response, has no raw header set to prove. That is a receipt with
// lifecycle "canceled-navigation", not a skipped request and not a pass.
// A completed request whose headers cannot be read still fails closed.

export type ReceiptLifecycle = 'complete' | 'canceled-navigation' | 'unreadable';

export interface WireReceipt {
  phase: string;
  method: string;
  path: string;
  afterPrime: boolean;
  organization: string | null;
  workspace: string | null;
  workspaceQuery: string | null;
  sameBearer: boolean;
  headerReadFailed: boolean;
  headersRead: boolean;
  lifecycle: ReceiptLifecycle;
}

export interface ObservedRequestState {
  phase: string;
  method: string;
  pathname: string;
  search: string;
  afterPrime: boolean;
  originalBearer: string;
  sawResponse: boolean;
  finished: boolean;
  navigationSuperseded: boolean;
  failureErrorText: string | null;
  headersSettled: boolean;
  headersRejected: boolean;
  headers: Record<string, string> | null;
}

export function isNavigationCancel(errorText: string | null): boolean {
  if (!errorText) return false;
  return /ERR_ABORTED|ERR_BLOCKED_BY_CLIENT|NS_BINDING_ABORTED|ERR_CANCELED|cancelled|canceled/i.test(errorText);
}

export function proveCanceled(state: Pick<
  ObservedRequestState,
  'navigationSuperseded' | 'failureErrorText' | 'sawResponse' | 'headersSettled' | 'headersRejected'
>): boolean {
  if (state.sawResponse) return false;
  if (state.headersSettled && !state.headersRejected) return false;
  return state.navigationSuperseded || isNavigationCancel(state.failureErrorText);
}

function workspaceQuery(search: string): string | null {
  const query = search.startsWith('?') ? search.slice(1) : search;
  return new URLSearchParams(query).get('workspace_id');
}

function emptyReceipt(
  state: Pick<ObservedRequestState, 'phase' | 'method' | 'pathname' | 'search' | 'afterPrime'>,
  lifecycle: ReceiptLifecycle,
  headerReadFailed: boolean,
): WireReceipt {
  return {
    phase: state.phase,
    method: state.method,
    path: state.pathname,
    afterPrime: state.afterPrime,
    organization: null,
    workspace: null,
    workspaceQuery: workspaceQuery(state.search),
    sameBearer: false,
    headerReadFailed,
    headersRead: false,
    lifecycle,
  };
}

export function receiptFromHeaders(args: {
  phase: string;
  method: string;
  pathname: string;
  search: string;
  afterPrime: boolean;
  originalBearer: string;
  headers: Record<string, string>;
}): WireReceipt {
  // allHeaders() lowercases names. Match that map only; do not fall back to
  // the incomplete request.headers() view.
  return {
    phase: args.phase,
    method: args.method,
    path: args.pathname,
    afterPrime: args.afterPrime,
    organization: args.headers['x-organization-id'] ?? null,
    workspace: args.headers['x-workspace-id'] ?? null,
    workspaceQuery: workspaceQuery(args.search),
    sameBearer: args.headers.authorization === `Bearer ${args.originalBearer}`,
    headerReadFailed: false,
    headersRead: true,
    lifecycle: 'complete',
  };
}

// Returns a receipt when lifecycle already decides the outcome. Returns null
// when the caller must await allHeaders() because a response or finish event
// says a complete header set should exist.
export function settleObservedReceipt(state: ObservedRequestState): WireReceipt | null {
  if (state.headersSettled && state.headers && !state.headersRejected) {
    return receiptFromHeaders({ ...state, headers: state.headers });
  }
  if (proveCanceled(state)) return emptyReceipt(state, 'canceled-navigation', false);
  if (state.headersSettled && state.headersRejected) return emptyReceipt(state, 'unreadable', true);
  if (state.sawResponse || state.finished) return null;
  return emptyReceipt(state, 'unreadable', true);
}

export async function acquireReceipt(
  state: ObservedRequestState,
  allHeaders: () => Promise<Record<string, string>>,
): Promise<WireReceipt> {
  const decided = settleObservedReceipt(state);
  if (decided) return decided;
  try {
    return receiptFromHeaders({ ...state, headers: await allHeaders() });
  } catch {
    if (proveCanceled(state)) return emptyReceipt(state, 'canceled-navigation', false);
    return emptyReceipt(state, 'unreadable', true);
  }
}
