// Row layouts (W9-4): the layouts of one dataset and the one the user has
// selected. A layout is an r3 saved view (surface `rowLayout`, scope = the
// dataset id, `state.layout` = the spec); W9 A0 adds `shared` / `mine` /
// `can_delete`, persistence per stage and the viewer and delete rules.
// The selected layout and the renderers the user agreed to run are
// per-browser conveniences in layout prefs extras (wrapped storage).
import { useCallback, useMemo } from "react";
import { useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { savedViewsKey, useSavedViews } from "src/api/develop/r3";
import { useLayoutPrefs } from "src/sections/common/ux/oneview/layoutPrefs";
import { LAYOUT_SURFACE } from "./layoutModel";

/** A saved view that holds a layout. */
export const isLayoutView = (v) =>
  Boolean(v?.state?.layout && typeof v.state.layout === "object");

/**
 * Menu groups: shared with everyone on the dataset, then the user's own.
 * @param {object[]} views  saved views (useSavedViews)
 */
export function groupLayouts(views = []) {
  const layouts = views.filter(isLayoutView);
  return {
    shared: layouts.filter((v) => v.shared === true),
    yours: layouts.filter((v) => v.shared !== true),
  };
}

/** Whether the user may delete a layout (A0's rule; own views otherwise). */
export const canDeleteLayout = (v) => v?.can_delete ?? v?.mine ?? true;

const named = (v) => (typeof v === "string" && v.trim() ? v.trim() : null);

/**
 * Who wrote a saved layout's renderer when that isn't the person holding
 * the layout: the author a copy keeps in its spec (`renderer.author`), else
 * the creator of someone else's layout. Null for the user's own code.
 */
export function rendererAuthorOf(view) {
  const r = view?.state?.layout?.renderer;
  if (!r?.html) return null;
  return (
    named(r.author) ??
    (view.mine === false ? named(view.created_by) ?? "another member" : null)
  );
}

/**
 * Whether the user must agree before a layout's renderer runs: code written
 * by someone else (their layout, or a copy of it, the user's own copy too),
 * not yet agreed to for this layout on this browser.
 */
export function needsRunConsent(view, trusted = {}) {
  if (!view?.state?.layout?.renderer?.html) return false;
  if (!rendererAuthorOf(view)) return false;
  return !trusted[view.id];
}

/** The run question: who wrote the code, and who copied it here. */
export function consentLine(view) {
  const author = rendererAuthorOf(view) ?? "another member";
  const by = named(view?.created_by);
  const copied = by && by !== author ? `, copied here by ${by}` : "";
  return `This layout runs code written by ${author}${copied}. It has no network and can only propose changes that you save with a version.`;
}

/**
 * The spec to save from a layout `from` (copied, or saved again from
 * Customize): an unchanged renderer keeps who wrote it, so it still asks
 * before it runs; code the user edited is theirs.
 */
export function withRendererAuthor(spec, from) {
  const r = spec?.renderer;
  if (!r) return spec;
  const { author: _was, ...rest } = r;
  const same =
    Boolean(r.html) && r.html === from?.state?.layout?.renderer?.html;
  const author = same ? rendererAuthorOf(from) : null;
  return { ...spec, renderer: author ? { ...rest, author } : rest };
}

/**
 * The saved layout whose run question still applies to a Customize draft:
 * the draft runs the selected layout's renderer unchanged, so someone
 * else's code is not run by opening Customize. Edited code is the user's.
 */
export function draftConsentView(draft, selected) {
  const html = draft?.renderer?.html;
  return html && html === selected?.state?.layout?.renderer?.html
    ? selected
    : null;
}

/**
 * @param {string} datasetId
 * @returns {{layouts: object[], groups: {shared: object[], yours: object[]},
 *   selected: object|null, select: (id: string|null) => void,
 *   isLoading: boolean, create: Function, remove: Function,
 *   replace: Function, trusted: object, trust: (id: string) => void,
 *   isSaving: boolean}}
 */
export default function useRowLayouts(datasetId) {
  const saved = useSavedViews(LAYOUT_SURFACE, datasetId);
  const queryClient = useQueryClient();
  const [prefs, patch] = useLayoutPrefs(LAYOUT_SURFACE, datasetId);
  const extras = prefs?.extras ?? {};
  const groups = useMemo(() => groupLayouts(saved.views), [saved.views]);
  const layouts = useMemo(() => [...groups.shared, ...groups.yours], [groups]);
  const selectedId =
    typeof extras.selected === "string" ? extras.selected : null;
  const selected = layouts.find((v) => v.id === selectedId) ?? null;
  const trusted = useMemo(
    () =>
      extras.trusted && typeof extras.trusted === "object"
        ? extras.trusted
        : {},
    [extras.trusted],
  );

  const select = useCallback(
    (id) => patch({ extras: { selected: id ?? undefined } }),
    [patch],
  );
  const trust = useCallback(
    (id) => patch({ extras: { trusted: { ...trusted, [id]: true } } }),
    [patch, trusted],
  );

  /** Save a new layout; resolves to the saved view. */
  const create = useCallback(
    (name, layout, { shared = false } = {}) =>
      saved.create(name, { layout }, { shared }),
    [saved],
  );

  /**
   * v2.1 g8 (layouts:customize): Save changes to an existing layout in
   * place (PUT, same id), so its id, author and link stay and no
   * soft-deleted twin is left behind.
   */
  const replace = useCallback(
    async (view, name, layout, { shared = view.shared === true } = {}) => {
      const res = await axios.put(endpoints.develop.r3.savedView(view.id), {
        name,
        state: { ...(view.state ?? {}), layout },
        shared,
      });
      await queryClient.invalidateQueries({
        queryKey: savedViewsKey(LAYOUT_SURFACE, datasetId),
      });
      return res?.data?.result;
    },
    [queryClient, datasetId],
  );

  return {
    layouts,
    groups,
    selected,
    select,
    isLoading: saved.isLoading,
    create,
    remove: saved.remove,
    replace,
    trusted,
    trust,
    isSaving: saved.isSaving,
  };
}
