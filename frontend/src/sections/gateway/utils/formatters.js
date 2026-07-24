/**
 * Centralized formatting utilities for the gateway dashboard.
 * Single source of truth — do NOT duplicate these in section components.
 */

export function formatNumber(value) {
  if (value == null) return "--";
  const num = Number(value);
  if (Number.isNaN(num)) return "--";
  if (num >= 1_000_000) return `${(num / 1_000_000).toFixed(1)}M`;
  if (num >= 1_000) return `${(num / 1_000).toFixed(1)}K`;
  return num.toLocaleString();
}

export function formatCost(value) {
  if (value == null) return "$0.00";
  const num = Number(value);
  if (Number.isNaN(num)) return "$0.00";

  const abs = Math.abs(num);

  if (abs >= 1000) return `$${(num / 1000).toFixed(1)}K`;
  if (abs >= 1) return `$${num.toFixed(2)}`;
  if (abs >= 0.01) return `$${num.toFixed(4)}`;
  if (abs >= 0.001) return `$${num.toFixed(5)}`;
  if (abs > 0) return `$${num.toFixed(7).replace(/0+$/, "0")}`;

  return "$0.00";
}

export function formatPercent(value, decimals = 1) {
  if (value == null) return "--";
  const num = Number(value);
  if (Number.isNaN(num)) return "--";
  return `${num.toFixed(decimals)}%`;
}

export function formatLatency(ms) {
  if (ms == null) return "--";
  const num = Number(ms);
  if (Number.isNaN(num)) return "--";
  if (num >= 1000) return `${(num / 1000).toFixed(2)}s`;
  return `${Math.round(num)}ms`;
}

export function formatDate(dateStr) {
  if (!dateStr) return "\u2014";
  return new Date(dateStr).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export function formatDateTime(dateStr) {
  if (!dateStr) return "\u2014";
  return new Date(dateStr).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatTokens(value) {
  if (value == null) return "0";
  const num = Number(value);
  if (Number.isNaN(num)) return "0";
  return num.toLocaleString();
}
