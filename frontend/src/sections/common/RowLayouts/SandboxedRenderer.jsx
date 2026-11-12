// Row layouts (W9-4, B4): a layout's custom renderer. The author's HTML runs
// in a frame sandboxed with `allow-scripts` only (opaque origin) under a
// no-network CSP, inside a wrapper frame of ours whose `frame-src 'none'`
// refuses any navigation of the renderer (sandboxDoc.js). It reads the
// row's values and can only propose changes, which become pending changes
// saved with a version. Messages count only from the renderer's window
// (and the wrapper's report of a refused navigation).
// Known limit: a srcdoc frame can share the tab's process, so an endless
// loop can stall the tab; the real build serves renderers from a separate
// origin (W9-SCOPE §9 D6).
import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, TextField, Typography } from "@mui/material";
import Editor from "@monaco-editor/react";
import Iconify from "src/components/iconify";
import { notify } from "src/sections/common/ux/toast";
import { useChangeSet } from "src/sections/develop-detail/DataTab/Versions/changeSetStore";
import {
  RENDERER_HEIGHT,
  RENDERER_LIMIT_BYTES,
  byteSize,
  controlsDisabledReason,
  kb,
  setRenderer,
} from "./layoutModel";
import {
  MSG,
  RENDERER_SANDBOX,
  STARTER_RENDERER,
  buildFrameDoc,
} from "./sandboxDoc";
import {
  acceptWrapper,
  blockedLine,
  createBridge,
  frameHeight,
  rendererWindow,
} from "./rendererBridge";
import { layoutOpId, runUndo, undoPlan } from "./controlOps";
import { consentLine, needsRunConsent } from "./useRowLayouts";

const SandboxedRenderer = ({
  html,
  height,
  payload,
  rowLabel,
  columns,
  cells,
  datasetId,
  layout,
  viewer,
  asOfVersion,
  pendingDelete,
}) => {
  const frame = useRef(null);
  const changeSet = useChangeSet(datasetId);
  // The last height message; the max height applies when drawing, so a new
  // max height (Customize) takes effect without the frame reloading.
  const [fit, setFit] = useState({ px: RENDERER_HEIGHT.start, auto: true });
  const px = frameHeight(fit, height);
  const [lines, setLines] = useState([]);
  const reason = controlsDisabledReason({ viewer, asOfVersion, pendingDelete });
  const reasonRef = useRef(reason);
  reasonRef.current = reason;
  const bridge = useMemo(
    () => createBridge({ columns, disabledReason: () => reasonRef.current }),
    // One session per Row view: the limits span rows.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );
  bridge.setColumns(columns);
  const title = `Custom renderer of ${layout.name}`;
  const srcDoc = useMemo(
    () => buildFrameDoc(html, payload, title),
    [html, payload, title],
  );
  // A new row or new code starts with no lines.
  useEffect(() => setLines([]), [payload?.id, html]);

  const say = (line) =>
    setLines((ls) => (ls.includes(line) ? ls : [...ls, line]));
  const handlerRef = useRef(null);
  handlerRef.current = (event) => {
    const msg =
      bridge.accept(event, rendererWindow(frame.current)) ??
      acceptWrapper(event, frame.current?.contentWindow);
    if (!msg) return;
    if (msg.type === MSG.height) setFit(msg);
    else if (msg.type === MSG.csp) say(blockedLine(msg.blocked, msg.directive));
    else if (msg.type === MSG.error)
      say(`The renderer stopped: ${msg.message}`);
    else if (msg.type === MSG.propose) {
      const res = bridge.propose(msg);
      if (res.refused) {
        say(res.refused);
        return;
      }
      const op = {
        op: "update_cell",
        id: layoutOpId(),
        row_id: payload.id,
        column_id: res.column.id,
        value: res.value,
        before: cells?.[res.column.id]?.original,
        source: { kind: "layout", layout_id: layout.id, name: layout.name },
      };
      const plan = undoPlan(changeSet.ops, op);
      changeSet.addOp(op);
      const label =
        rowLabel ?? (payload.number ? `Row ${payload.number}` : "This row");
      notify(
        `${label} · ${res.column.name}: proposed by “${layout.name}” · in pending changes`,
        {
          variant: "success",
          key: "renderer-propose",
          ...(plan ? { undo: { onUndo: () => runUndo(changeSet, plan) } } : {}),
        },
      );
    }
  };
  useEffect(() => {
    const on = (e) => handlerRef.current?.(e);
    window.addEventListener("message", on);
    return () => window.removeEventListener("message", on);
  }, []);

  return (
    <Box data-testid="layout-renderer">
      <Box
        component="iframe"
        ref={frame}
        title={title}
        sandbox={RENDERER_SANDBOX}
        referrerPolicy="no-referrer"
        srcDoc={srcDoc}
        data-testid="layout-renderer-frame"
        sx={{
          display: "block",
          width: "100%",
          // The height is the content's; the border goes outside it.
          boxSizing: "content-box",
          height: px,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          bgcolor: "common.white",
        }}
      />
      {lines.length > 0 && (
        <Stack gap={0.25} sx={{ mt: 0.75 }} data-testid="layout-renderer-lines">
          {lines.map((l) => (
            <Typography
              key={l}
              typography="s3"
              color={l.startsWith("Blocked") ? "text.secondary" : "error.main"}
            >
              {l}
            </Typography>
          ))}
        </Stack>
      )}
    </Box>
  );
};

SandboxedRenderer.propTypes = {
  html: PropTypes.string.isRequired,
  height: PropTypes.number,
  payload: PropTypes.object.isRequired,
  rowLabel: PropTypes.string,
  columns: PropTypes.array.isRequired,
  cells: PropTypes.object,
  datasetId: PropTypes.string.isRequired,
  layout: PropTypes.shape({ id: PropTypes.string, name: PropTypes.string })
    .isRequired,
  viewer: PropTypes.bool,
  asOfVersion: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  pendingDelete: PropTypes.bool,
};

export default SandboxedRenderer;

/**
 * The renderer behind its first-run question: someone else's renderer runs
 * only after the user says so (remembered per layout on this browser).
 */
export const RendererSlot = ({ view, trusted, onTrust, ...props }) => {
  if (needsRunConsent(view, trusted))
    return (
      <Stack
        gap={1}
        sx={{
          p: 1.5,
          border: "1px dashed",
          borderColor: "divider",
          borderRadius: 1,
        }}
        data-testid="layout-renderer-consent"
      >
        <Typography typography="s2">{consentLine(view)}</Typography>
        <Box>
          <Button
            size="small"
            variant="outlined"
            onClick={() => onTrust(view.id)}
            data-testid="layout-renderer-run"
          >
            Run it
          </Button>
        </Box>
      </Stack>
    );
  return <SandboxedRenderer {...props} />;
};

RendererSlot.propTypes = {
  view: PropTypes.object,
  trusted: PropTypes.object,
  onTrust: PropTypes.func.isRequired,
};

/** The Custom renderer section of the Customize panel. */
export const RendererEditor = ({ draft, onDraft }) => {
  const r = draft.renderer;
  const bytes = r ? byteSize(r.html) : 0;
  return (
    <Box sx={{ px: 2, py: 1.5 }} data-testid="panel-renderer">
      <Typography typography="s1" fontWeight="fontWeightMedium">
        Custom renderer
      </Typography>
      <Typography typography="s3" color="text.secondary" component="p">
        HTML and script that draw the row (fa.row, fa.onRow, fa.proposeChange).
        It runs with no network and can only propose changes. Fields set to
        Custom show here. The frame fits its content up to the max height.
      </Typography>
      {r ? (
        <Stack gap={1} sx={{ mt: 1 }}>
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              overflow: "hidden",
            }}
          >
            <Editor
              height="240px"
              language="html"
              value={r.html}
              onChange={(v) =>
                onDraft({ ...draft, renderer: { ...r, html: v ?? "" } })
              }
              options={{
                minimap: { enabled: false },
                fontSize: 12,
                wordWrap: "on",
                scrollBeyondLastLine: false,
              }}
            />
          </Box>
          <Stack direction="row" alignItems="center" gap={1}>
            <Typography
              typography="s3"
              color={
                bytes > RENDERER_LIMIT_BYTES ? "error.main" : "text.secondary"
              }
              sx={{ flex: 1 }}
              data-testid="renderer-size"
            >
              {`${kb(bytes)} of ${kb(RENDERER_LIMIT_BYTES)}`}
            </Typography>
            <TextField
              size="small"
              type="number"
              label="Max height (px)"
              value={r.height}
              onChange={(e) =>
                onDraft({
                  ...draft,
                  renderer: { ...r, height: Number(e.target.value) },
                })
              }
              inputProps={{
                min: RENDERER_HEIGHT.min,
                max: RENDERER_HEIGHT.max,
              }}
              sx={{ width: 120 }}
            />
            <Button
              size="small"
              color="inherit"
              startIcon={<Iconify icon="mdi:delete-outline" width={16} />}
              onClick={() => onDraft(setRenderer(draft, null))}
            >
              Remove
            </Button>
          </Stack>
        </Stack>
      ) : (
        <Button
          size="small"
          startIcon={<Iconify icon="mdi:plus" width={16} />}
          onClick={() =>
            onDraft(
              setRenderer(draft, {
                html: STARTER_RENDERER,
                height: RENDERER_HEIGHT.start,
              }),
            )
          }
          data-testid="renderer-add"
          sx={{ mt: 0.5 }}
        >
          Add a custom renderer
        </Button>
      )}
    </Box>
  );
};

RendererEditor.propTypes = {
  draft: PropTypes.object.isRequired,
  onDraft: PropTypes.func.isRequired,
};
