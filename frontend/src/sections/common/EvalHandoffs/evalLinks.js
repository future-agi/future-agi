// KH implementation (H-1 · EV-07, EV-NA1): Open in Evals at the run's version.
// Pure helpers; useEvalTemplateRef and OpenInEvalsLink render them.
// The Evals detail page selects a version from `?v=<number>`
// (sections/evals/components/EvalDetailPage.jsx, `searchParams.get("v")`).

export const EVALS_PATH = "/dashboard/evaluations";
export const EVAL_GROUPS_PATH = `${EVALS_PATH}/groups`;

/** "v2" | "V2" | 2 | "2" -> 2; anything else -> null. */
export function versionNumberOf(version) {
  if (version == null || version === "") return null;
  if (typeof version === "number")
    return Number.isInteger(version) && version > 0 ? version : null;
  const m = String(version)
    .trim()
    .match(/^v?(\d+)$/i);
  return m ? Number(m[1]) : null;
}

/**
 * L7c g7: an experiment-level edit's version id ("v2.1": the first edit of
 * v2 made in an experiment) -> {id: "2.1", base: 2}; anything else -> null.
 */
export function editVersionOf(version) {
  const m = String(version ?? "")
    .trim()
    .match(/^v?(\d+)\.(\d+)$/i);
  return m ? { id: `${m[1]}.${m[2]}`, base: Number(m[1]) } : null;
}

/** `/dashboard/evaluations/<id>?v=<n>`; no `v` without a version; null without an id. */
export function evalPageHref({ templateId, version } = {}) {
  if (!templateId) return null;
  const n = versionNumberOf(version);
  const base = `${EVALS_PATH}/${encodeURIComponent(templateId)}`;
  return n ? `${base}?v=${n}` : base;
}

/**
 * The template behind an eval column, in order: the def's own template_id,
 * else the dataset's saved evals (get_evals_list eval_type=user: by
 * metric_key, else by the def's user eval id), else null. It never guesses.
 */
export function resolveTemplateId({ def, metricKey, datasetEvals } = {}) {
  if (def?.template_id) return def.template_id;
  const key = metricKey ?? def?.key ?? null;
  const list = Array.isArray(datasetEvals) ? datasetEvals : [];
  const byKey = key ? list.find((e) => e?.metric_key === key) : null;
  if (byKey?.template_id) return byKey.template_id;
  const uid = def?.user_eval_id ?? null;
  const byId = uid ? list.find((e) => e?.id === uid) : null;
  return byId?.template_id ?? null;
}

const currentOf = (versions) => {
  const def = versions.find((v) => v?.is_default);
  if (def) return Number(def.version_number);
  const nums = versions
    .map((v) => Number(v?.version_number))
    .filter(Number.isFinite);
  return nums.length ? Math.max(...nums) : null;
};

/**
 * The link for a template at a version, checked against the template's
 * version list (GET eval-templates/{id}/versions/ `versions[]`). A version the
 * list no longer has opens the current one, and the tooltip says so.
 * `versions` null = not loaded yet (the run's version is kept).
 */
export function evalLinkOf({ templateId, version, name, versions = null }) {
  if (!templateId) return null;
  const label = name || "this evaluation";
  // L7c g7 (transfer:hover-card, transfer:score-card): "v2.1" is an edit of
  // v2 made in the experiment (its judge, bar, prompt or N/A switch), not a
  // version the template saved: it opens v2, the version it starts from.
  const edit = editVersionOf(version);
  if (edit)
    return {
      href: evalPageHref({ templateId, version: edit.base }),
      tooltip: `This run used v${edit.id}, an edit of v${edit.base} made in this experiment (not a saved version of ${label}). Opens v${edit.base}.`,
      missing: false,
      edited: true,
    };
  const n = versionNumberOf(version);
  if (n == null)
    return {
      href: evalPageHref({ templateId }),
      tooltip: `Open ${label} in Evals`,
      missing: false,
    };
  const list = Array.isArray(versions) ? versions : null;
  const listed = !list || list.some((v) => Number(v?.version_number) === n);
  if (listed)
    return {
      href: evalPageHref({ templateId, version: n }),
      tooltip: `Open ${label} v${n} in Evals`,
      missing: false,
    };
  const current = currentOf(list);
  return {
    href: evalPageHref({ templateId }),
    tooltip: current
      ? `This run used v${n}, which ${label} no longer lists. Opens the current version (v${current}).`
      : `This run used v${n}, which ${label} no longer lists. Opens the current version.`,
    missing: true,
  };
}
