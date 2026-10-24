/*
  Improvements level 1 — the helpers behind one row per environment or dataset.
*/
import { DATASETS } from "../_mock/datasets";

export const MODALITY = {
  voice: { id: "voice", label: "Voice", icon: "solar:microphone-3-linear" },
  chat: { id: "chat", label: "Chat", icon: "solar:chat-round-line-linear" },
  computer: {
    id: "computer",
    label: "Computer use",
    icon: "solar:monitor-linear",
  },
  code: { id: "code", label: "Code", icon: "solar:code-square-linear" },
  tools: { id: "tools", label: "Tools", icon: "solar:widget-6-linear" },
};

/** An environment's agent type, from its channel. */
export const modalityOfSurface = (surface) => {
  const s = String(surface || "").toLowerCase();
  if (s === "voice") return MODALITY.voice;
  if (s === "browser" || s === "computer") return MODALITY.computer;
  if (s === "coding" || s === "code" || s === "cli") return MODALITY.code;
  if (s === "mcp" || s === "tools") return MODALITY.tools;
  return MODALITY.chat;
};

const DAY = 24 * 60 * 60 * 1000;
const daysAgo = (n) => new Date(Date.now() - n * DAY).toISOString();
const rowsOf = (id, fallback) =>
  DATASETS.find((d) => d.id === id)?.rowCount ?? fallback;

/*
  Datasets an agent could be improved against — listed so the option is
  visible, but there's no dataset improvement flow yet: no results, no runs,
  and every place they show says Coming soon.
*/
export const DATASET_SOURCES = [
  {
    id: "dataset:ds-support-transcripts",
    datasetId: "ds-support-transcripts",
    kind: "dataset",
    name:
      DATASETS.find((d) => d.id === "ds-support-transcripts")?.name ||
      "Support transcripts",
    rowCount: rowsOf("ds-support-transcripts", 1284),
    modalityKey: "chat",
    latestAt: daysAgo(4),
  },
  {
    id: "dataset:ds-refund-appeals",
    datasetId: "ds-refund-appeals",
    kind: "dataset",
    name: "Refund appeals",
    rowCount: 240,
    modalityKey: "voice",
    latestAt: daysAgo(11),
  },
];

export const datasetSourceOf = (datasetId) =>
  DATASET_SOURCES.find((d) => d.datasetId === datasetId) || null;

/*
  Level 1's Status — the latest improvement run's, in three states (as the
  Scenarios page words them): still going, finished, or errored out. What it
  found lives on level 2.
*/
export const SOURCE_STATUS = {
  running: { label: "Running…", color: "status.info.fg", animated: true },
  completed: { label: "Completed", color: "status.success.fg" },
  failed: { label: "Failed", color: "status.danger.fg" },
};
export const sourceStatusOf = (latestStatus) => {
  if (!latestStatus) return null;
  if (latestStatus.key === "running") return "running";
  if (latestStatus.key === "failed" && latestStatus.label !== "Reopened")
    return "failed";
  return "completed";
};

export const formatCreatedAt = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
};

/* "3 days ago" — the Environments table's Updated column reads the same way. */
export const relativeTime = (iso) => {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const m = Math.round((Date.now() - then) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min${m === 1 ? "" : "s"} ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} hour${h === 1 ? "" : "s"} ago`;
  const d = Math.round(h / 24);
  if (d < 30) return `${d} day${d === 1 ? "" : "s"} ago`;
  const mo = Math.round(d / 30);
  return `${mo} month${mo === 1 ? "" : "s"} ago`;
};
