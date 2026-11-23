/*
  Add scenarios' sources, as links and imports see them. Every source of
  evidence — production (Observe), an Error Feed issue, a dataset and an
  uploaded transcript or file — sits in the one Add scenarios drawer and ends
  in the same review of imported evidence.
*/
import { asProposal } from "../../_mock/scenarioSources";

/* Sources that follow the workspace's "scenarios from production" switch. */
export const PRODUCTION_ROUTES = new Set(["error-feed", "observe", "files"]);

/**
 * The drawer route a link opens on. `?add=production` predates the Error
 * Feed source and lands there; `?add=replay` was the old combined import —
 * from Observe (`&from=observe`) it is From production, else the upload.
 */
export const routeOfLink = (add, searchParams) => {
  if (!add) return null;
  if (add === "production") return "error-feed";
  if (add === "replay")
    return searchParams?.get?.("from") === "observe" ? "observe" : "files";
  return add;
};

/** The provenance id an add is recorded under in Recent imports. */
export const addSourceOf = (route, rows = []) => {
  const kind = rows[0]?.traceImport?.sourceKind;
  if (route === "observe")
    return kind === "simCall" ? "simulation" : "production";
  if (route === "files")
    return kind === "dataset" ? "dataset-import" : "transcript";
  if (route === "dataset") return "dataset-import";
  return route;
};

/**
 * Picked dataset rows as one import, reviewed like every other evidence:
 * each draft keeps its row as its source and joins only through the review.
 */
export const datasetImportOf = (
  rows,
  dataset,
  {
    at = new Date().toISOString(),
    id = `imp_ds_${Date.now().toString(36)}`,
  } = {},
) => {
  /* "#12" — the row as the dataset numbers it — else its id. */
  const rowIdOf = (r) =>
    r.datasetRow?.row ? `#${r.datasetRow.row}` : r.datasetRow?.rowId || r.id;
  const ids = rows.map(rowIdOf);
  return rows.map((r) => {
    const rowId = rowIdOf(r);
    return asProposal(
      {
        ...r,
        traceImport: {
          sourceKind: "dataset",
          id,
          at,
          project: dataset?.name || null,
          ids,
          goal: "reproduce",
          sourceIds: [rowId],
          sources: [
            {
              id: `${id}_${rowId}`,
              traceId: rowId,
              at,
              channel: "Dataset row",
              outcome: "unknown",
              topic: r.title,
              callerName: "[customer]",
              turns: [{ role: "caller", text: r.task || r.title || "" }],
              expected: r.expected || null,
              evidence: {
                transcript: false,
                tools: false,
                startingState: !!r.fixtures?.length,
              },
              sourceKind: "dataset",
              sourceName: dataset?.name || null,
            },
          ],
        },
      },
      "dataset-import",
      at,
    );
  });
};
