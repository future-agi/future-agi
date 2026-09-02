import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, IconButton, Tab, Chip, Tooltip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { SegmentedTabs } from "src/components/tabs/tabs";
import { twinById, twinRequestStreamFor } from "../_mock/twins";
import TwinLogo from "../components/TwinLogo";

const TWIN_TINT = "#7857FC";

/**
 * Raw HTTP request stream for one call — the developer's console
 * log of what the agent actually did to the twin sandbox. Rows are
 * expandable to show the JSON request + response payloads, so a
 * failed run can be traced from "why did this fail" to "here's the
 * exact 429 the sandbox returned" without leaving the drawer.
 *
 * The `twinTimelineFor` semantic view (writes/reads with human
 * summaries) lives on the "Twin state" tab; this tab is the raw
 * cut of the same events.
 */
export default function TwinRequestStream({ envState, task }) {
  const services = envState?.twinBacking?.services || [];
  const requests = useMemo(() => twinRequestStreamFor(envState, task), [envState, task]);
  const [filter, setFilter] = useState("all");
  const [openId, setOpenId] = useState(null);

  if (!services.length) {
    return (
      <Box sx={{ p: 3, textAlign: "center" }}>
        <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
          This environment isn&apos;t backed by a service twin.
        </Typography>
      </Box>
    );
  }

  const filtered = requests.filter((r) => {
    if (filter === "all") return true;
    if (filter === "errors") return r.isError;
    if (filter === "writes") return r.isWrite;
    if (filter === "reads") return !r.isWrite;
    return true;
  });

  const errCount = requests.filter((r) => r.isError).length;
  const writeCount = requests.filter((r) => r.isWrite).length;
  const readCount = requests.length - writeCount;

  if (requests.length === 0) {
    return (
      <Box sx={{ p: 3, textAlign: "center" }}>
        <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
          The agent didn&apos;t hit the sandbox during this call.
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ p: 2 }}>
      {/*
        Filter row + counts. Reads like a devtools network panel — the
        counts on the right (writes / reads / errors) give a quick
        gestalt of how noisy the call was without opening each row.
      */}
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 1.5 }}>
        <SegmentedTabs value={filter} onChange={(_, v) => setFilter(v)} sx={{ flexShrink: 0 }}>
          <Tab value="all" label={`All (${requests.length})`} />
          <Tab value="writes" label={`Writes (${writeCount})`} />
          <Tab value="reads" label={`Reads (${readCount})`} />
          <Tab value="errors" label={`Errors (${errCount})`} />
        </SegmentedTabs>
        <Box flex={1} />
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
          {filtered.length} shown · {sumLatency(filtered)}ms total
        </Typography>
      </Stack>

      {filtered.length === 0 ? (
        <Box sx={{ p: 3, textAlign: "center", border: "1px dashed", borderColor: "divider", borderRadius: 1 }}>
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
            No requests match this filter.
          </Typography>
        </Box>
      ) : (
        <Stack spacing={0.5}>
          {filtered.map((r) => (
            <RequestRow
              key={r.id} req={r}
              open={openId === r.id}
              onToggle={() => setOpenId(openId === r.id ? null : r.id)}
            />
          ))}
        </Stack>
      )}
    </Box>
  );
}

TwinRequestStream.propTypes = {
  envState: PropTypes.object,
  task: PropTypes.object,
};

/* ── one row ─────────────────────────────────────────────────────────── */

function RequestRow({ req, open, onToggle }) {
  const twin = twinById(req.service);
  const methodC = methodColor(req.method);
  const statusC = req.isError ? "#DC2626" : "#16A34A";
  return (
    <Box sx={{
      borderRadius: 1, border: "1px solid",
      borderColor: req.isError ? alpha("#DC2626", 0.35) : "divider",
      bgcolor: (t) => req.isError
        ? alpha("#DC2626", t.palette.mode === "dark" ? 0.06 : 0.03)
        : "background.paper",
      overflow: "hidden",
    }}>
      <Stack
        direction="row" alignItems="center" spacing={1.25}
        onClick={onToggle}
        sx={{
          px: 1.5, py: 1, cursor: "pointer",
          "&:hover": { bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.03 : 0.02) },
        }}
      >
        <Iconify
          icon={open ? "solar:alt-arrow-down-linear" : "solar:alt-arrow-right-linear"}
          width={12} sx={{ color: "text.subtitle", flexShrink: 0 }}
        />
        <Typography sx={{
          typography: "s3", fontWeight: 700, color: "text.subtitle",
          fontFamily: "ui-monospace, Menlo, monospace",
          fontVariantNumeric: "tabular-nums", minWidth: 32,
        }}>
          T{req.turn + 1}
        </Typography>
        <TwinLogo twin={twin} width={13} />
        <Chip
          label={req.method}
          size="small"
          sx={{
            height: 18, borderRadius: 0.5, fontFamily: "ui-monospace, Menlo, monospace",
            bgcolor: (t) => alpha(methodC, t.palette.mode === "dark" ? 0.16 : 0.09),
            color: methodC,
            "& .MuiChip-label": { px: 0.75, fontSize: 10, fontWeight: 700, letterSpacing: 0.3 },
          }}
        />
        <Typography noWrap sx={{
          typography: "s2", flex: 1, minWidth: 0,
          fontFamily: "ui-monospace, Menlo, monospace",
          color: "text.primary",
        }}>
          {req.path}
        </Typography>
        <Tooltip title={req.isError ? "Rate limited or sandbox failure" : "OK"} arrow>
          <Typography sx={{
            typography: "s3", fontWeight: 700, color: statusC,
            fontFamily: "ui-monospace, Menlo, monospace",
            fontVariantNumeric: "tabular-nums",
          }}>
            {req.status}
          </Typography>
        </Tooltip>
        <Typography sx={{
          typography: "s3", color: "text.subtitle",
          fontVariantNumeric: "tabular-nums", minWidth: 42, textAlign: "right",
        }}>
          {req.latencyMs}ms
        </Typography>
        <IconButton size="small" onClick={(e) => { e.stopPropagation(); copy(payloadFor(req)); }}>
          <Iconify icon="solar:copy-linear" width={12} sx={{ color: "text.subtitle" }} />
        </IconButton>
      </Stack>

      {open && (
        <Box sx={{ px: 1.5, pb: 1.5, pt: 0.5, borderTop: "1px dashed", borderColor: "divider" }}>
          {req.requestBody != null && (
            <PayloadBlock label="Request body" body={req.requestBody} />
          )}
          <PayloadBlock label={req.isError ? "Response body · error" : "Response body"} body={req.responseBody} error={req.isError} />
          <Typography sx={{
            typography: "s3", color: "text.subtitle", mt: 0.75, fontStyle: "italic",
          }}>
            {req.summary}
          </Typography>
        </Box>
      )}
    </Box>
  );
}
RequestRow.propTypes = { req: PropTypes.object, open: PropTypes.bool, onToggle: PropTypes.func };

function PayloadBlock({ label, body, error }) {
  return (
    <Box sx={{ mt: 1 }}>
      <Typography sx={{
        typography: "s3", fontWeight: 700, color: "text.subtitle",
        textTransform: "uppercase", letterSpacing: 0.4, mb: 0.5,
      }}>
        {label}
      </Typography>
      <Box sx={{
        p: 1, borderRadius: 0.75, border: "1px solid",
        borderColor: error ? alpha("#DC2626", 0.35) : "divider",
        bgcolor: (t) => error
          ? alpha("#DC2626", t.palette.mode === "dark" ? 0.06 : 0.03)
          : "background.neutral",
        maxHeight: 200, overflow: "auto",
      }}>
        <Typography component="pre" sx={{
          typography: "s3", fontFamily: "ui-monospace, Menlo, monospace",
          color: error ? "#DC2626" : "text.primary",
          whiteSpace: "pre", m: 0,
        }}>
          {JSON.stringify(body, null, 2)}
        </Typography>
      </Box>
    </Box>
  );
}
PayloadBlock.propTypes = { label: PropTypes.string, body: PropTypes.any, error: PropTypes.bool };

/* ── bits ────────────────────────────────────────────────────────────── */

function methodColor(m) {
  return ({
    GET: "#0EA5E9",
    POST: "#16A34A",
    PUT: "#F59E0B",
    PATCH: TWIN_TINT,
    DELETE: "#DC2626",
  })[m] || "#6B7280";
}

function sumLatency(rows) {
  return rows.reduce((a, r) => a + (r.latencyMs || 0), 0);
}

function payloadFor(req) {
  return JSON.stringify({
    method: req.method,
    path: req.path,
    status: req.status,
    latency_ms: req.latencyMs,
    request: req.requestBody,
    response: req.responseBody,
  }, null, 2);
}

function copy(text) {
  navigator.clipboard?.writeText(text);
}
