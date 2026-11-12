// Row layouts (W9-4): the Row-tab mount (RowViewBody). `Layout [Default ▾]`,
// then the selected layout through RowLayoutView, or today's Row tab
// (`children`) on Default. While Customize is open the Row tab previews the
// draft; Copy opens CopyLayoutDialog.
import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box } from "@mui/material";
import { notify } from "src/sections/common/ux/toast";
import {
  rowLabel,
  useDatasetKind,
} from "src/sections/common/Datasets/datasetKind";
import { rowNumberOf } from "src/sections/develop-detail/DataTab/DatapointDrawerV2/rowModel";
import {
  defaultLayout,
  layoutColumns,
  rowKindOf,
  validateLayout,
} from "./layoutModel";
import { editableFields, valuesOf } from "./layoutMatch";
import ControlButtons from "./ControlButtons";
import ButtonsEditor from "./ButtonsEditor";
import { availableControlKinds, rowStatusLabels } from "./changeSetKinds";
import CopyLayoutDialog from "./CopyLayoutDialog";
import { RendererEditor, RendererSlot } from "./SandboxedRenderer";
import { rowPayload } from "./rendererBridge";
import LayoutBar from "./LayoutBar";
import LayoutPanel from "./LayoutPanel";
import RowLayoutView from "./RowLayoutView";
import useRowLayouts, {
  canDeleteLayout,
  draftConsentView,
  withRendererAuthor,
} from "./useRowLayouts";

const errText = (err) =>
  err?.response?.data?.result ??
  err?.response?.data?.message ??
  err?.message ??
  "The layout didn't save.";

/**
 * The Row tab with layouts (the mount in RowViewBody): `Layout [Default ▾]`,
 * then the selected layout, or `children` (today's Row tab) on Default.
 * While Customize is open the Row tab previews the draft.
 */
const RowLayoutTab = ({
  datasetId,
  rowId,
  row,
  model,
  edit,
  viewer,
  asOfVersion,
  pendingDelete,
  children,
}) => {
  const layouts = useRowLayouts(datasetId);
  const { kind } = useDatasetKind(datasetId);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState(null);
  const [copying, setCopying] = useState(false);
  const columns = useMemo(() => layoutColumns(model), [model]);
  const { selected } = layouts;
  // Only its author changes a layout in place ("Save changes"); anyone
  // else (an admin too) saves a new one. A viewer may not change a shared
  // layout (saving it would unshare it).
  const editing =
    selected &&
    selected.mine !== false &&
    canDeleteLayout(selected) &&
    !(viewer && selected.shared)
      ? selected
      : null;
  const problems = useMemo(
    () =>
      draft
        ? validateLayout(draft, columns, {
            kinds: availableControlKinds(),
            statuses: rowStatusLabels(),
          })
        : [],
    [draft, columns],
  );
  const spec = draft ?? selected?.state?.layout ?? null;
  const cells = useMemo(() => valuesOf(model), [model]);
  const rowNo = rowNumberOf(row);
  // The row's one name (`T4` in a task suite, else `Row 4`).
  const label = rowNo ? rowLabel(kind, rowNo) : "This row";
  const layout = {
    id: selected?.id ?? "draft",
    name: draft ? "Draft layout" : selected?.name ?? "Layout",
  };
  const payload = useMemo(
    () => rowPayload(model, row, rowId),
    [model, row, rowId],
  );
  const renderer = spec?.renderer?.html ? (
    <RendererSlot
      view={draft ? draftConsentView(draft, selected) : selected}
      trusted={layouts.trusted}
      onTrust={layouts.trust}
      html={spec.renderer.html}
      height={spec.renderer.height}
      payload={payload}
      rowLabel={label}
      columns={columns}
      cells={cells}
      datasetId={datasetId}
      layout={layout}
      viewer={viewer}
      asOfVersion={asOfVersion}
      pendingDelete={pendingDelete}
    />
  ) : null;
  const controls = spec?.controls?.length ? (
    <ControlButtons
      controls={spec.controls}
      columns={columns}
      cells={cells}
      datasetId={datasetId}
      rowId={rowId}
      rowLabel={label}
      layout={layout}
      viewer={viewer}
      asOfVersion={asOfVersion}
      pendingDelete={pendingDelete}
      rowStatus={
        row?.row_meta?.status
          ? {
              status: row.row_meta.status,
              reason: row.row_meta.status_reason ?? null,
            }
          : null
      }
    />
  ) : null;

  const customize = () => {
    const from =
      selected?.state?.layout ?? defaultLayout(columns, rowKindOf(kind));
    setDraft({ ...from, fields: editableFields(from, columns) });
  };
  const save = async (name, shared, replaceIt) => {
    setBusy(true);
    // Someone else's renderer saved unchanged still asks before it runs
    // (and stays agreed to if the user already ran it here).
    const spec = withRendererAuthor(draft, selected);
    const agreed =
      Boolean(spec.renderer?.author) &&
      Boolean(draftConsentView(draft, selected)) &&
      Boolean(layouts.trusted[selected?.id]);
    try {
      const view = replaceIt
        ? await layouts.replace(editing, name, spec, { shared })
        : await layouts.create(name, spec, { shared });
      if (view?.id) layouts.select(view.id);
      if (view?.id && agreed) layouts.trust(view.id);
      setDraft(null);
      notify(
        `Saved the layout “${name}” ${shared ? "for everyone on this dataset" : "for you"}`,
        { variant: "success", key: "layout-saved" },
      );
    } catch (err) {
      notify(errText(err), { variant: "error", key: "layout-save-failed" });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Box data-testid="row-layout-tab">
      <Box sx={{ px: 2, pt: 1 }}>
        <LayoutBar
          layouts={layouts}
          onCustomize={customize}
          onCopy={() => setCopying(true)}
        />
      </Box>
      {spec ? (
        <Box sx={{ px: 2, pb: 2 }}>
          <RowLayoutView
            spec={spec}
            columns={columns}
            model={model}
            edit={edit}
            controls={controls}
            renderer={renderer}
          />
        </Box>
      ) : (
        children
      )}
      {draft && (
        <LayoutPanel
          open
          draft={draft}
          onDraft={setDraft}
          problems={problems}
          viewer={viewer}
          editing={editing}
          saving={busy}
          onSave={save}
          onClose={() => setDraft(null)}
          buttons={
            <ButtonsEditor draft={draft} columns={columns} onDraft={setDraft} />
          }
          renderer={<RendererEditor draft={draft} onDraft={setDraft} />}
        />
      )}
      {copying && selected && (
        <CopyLayoutDialog
          open
          view={selected}
          datasetId={datasetId}
          viewer={viewer}
          onClose={() => setCopying(false)}
        />
      )}
    </Box>
  );
};

RowLayoutTab.propTypes = {
  datasetId: PropTypes.string.isRequired,
  rowId: PropTypes.string.isRequired,
  row: PropTypes.object,
  asOfVersion: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  pendingDelete: PropTypes.bool,
  model: PropTypes.object.isRequired,
  edit: PropTypes.object,
  viewer: PropTypes.bool,
  children: PropTypes.node,
};

export default RowLayoutTab;
