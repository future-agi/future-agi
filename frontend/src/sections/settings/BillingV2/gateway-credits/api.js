// Reads and writes of the gateway's managed-credit ledger, on the existing
// /agentcc/managed-credits/* API (agentcc/urls_billing.py). No other billing
// API is involved. A build without the ledger answers 501
// billing_unavailable: the summary query then errors and nothing about
// gateway credits is shown.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import axios, { endpoints } from "src/utils/axios";

const gw = endpoints.gateway;

export const creditKeys = {
  all: ["gateway-credits"],
  summary: ["gateway-credits", "summary"],
  settings: ["gateway-credits", "settings"],
  entries: (kind) => ["gateway-credits", "entries", kind || "all"],
  statement: (month) => ["gateway-credits", "statement", month],
};

const result = (res) => res.data?.result ?? null;

export function useGatewayCreditSummary({ enabled = true } = {}) {
  return useQuery({
    queryKey: creditKeys.summary,
    queryFn: () => axios.get(gw.managedCredits),
    select: result,
    enabled,
    retry: false,
    staleTime: 30_000,
    // Every caller reports a failed summary itself (Billing's error state,
    // the nav hiding central billing, the models catalog strip), so a 501
    // billing_unavailable never also raises the app-wide toast.
    meta: { errorHandled: true },
  });
}

export async function fetchCreditEntries({ kind, cursor, limit = 50 }) {
  const params = { limit };
  if (kind) params.kind = kind;
  if (cursor) params.cursor = cursor;
  const res = await axios.get(gw.managedCreditEntries, { params });
  return result(res) || { entries: [], next_cursor: null };
}

export function useCreditStatement(month, { enabled = true } = {}) {
  return useQuery({
    queryKey: creditKeys.statement(month),
    queryFn: () => axios.get(gw.managedCreditStatement, { params: { month } }),
    select: result,
    enabled: enabled && !!month,
    retry: false,
  });
}

export function useSaveCreditSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body) => axios.put(gw.managedCreditSettings, body),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: creditKeys.all }),
  });
}

export function useStartCheckout() {
  return useMutation({
    mutationFn: (body) => axios.post(gw.managedCreditCheckout, body),
  });
}

export async function fetchCheckoutStatus(sessionId) {
  const res = await axios.get(gw.managedCreditCheckoutStatus(sessionId));
  return result(res);
}

/** A ledger CSV (entries or a month's statement), saved as a file. */
export async function downloadCreditCsv(kind, params, filename) {
  const url =
    kind === "statement" ? gw.managedCreditStatement : gw.managedCreditEntries;
  const res = await axios.get(url, {
    params: { ...params, format: "csv" },
    responseType: "blob",
  });
  const href = URL.createObjectURL(res.data);
  const link = document.createElement("a");
  link.href = href;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(href);
}
