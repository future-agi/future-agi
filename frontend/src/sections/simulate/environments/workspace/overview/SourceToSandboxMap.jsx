import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { ROW_HEIGHT } from "src/theme/sizes";
import OriginChip from "../../components/OriginChip";
import { ENV_SHAPE } from "./overview.constants";
import {
  MAP_COPY,
  toolMapRows,
  ruleMapRows,
  storeMapRows,
  storeRowsFromStores,
} from "./sourceToSandbox.constants";

const MONO = "ui-monospace, Menlo, monospace";
// Shared grid template so column headers, group headers and every row land on
// the same tracks; min-content right-aligns each origin chip to its widest peer.
// The arrow sits in its own fixed track wide enough that the centred glyph keeps
// clear air on both sides — the origin chip must never read as fused to the
// arrow, however short the row's target is. The name (col 1) and target (col 4)
// are minmax(0, 1fr), so a long value ellipsis-clips inside its cell rather than
// pushing the chip or the arrow.
const MAP_GRID = "minmax(0, 1fr) min-content 44px minmax(0, 1fr)";
const MAP_PX = 1.5;

// A two-column ledger: what was read from source, and what it became in the
// sandbox. Read-only — the read/write classification is a verb-heuristic guess,
// and resolving/overriding it lives on the Contract tab — but this map REFLECTS
// a Contract override: a resolved tool shows the overridden target and a "you
// confirmed" mark. This is the artifact someone returns to weeks later to see
// which decisions a human made and which the reader made itself.
export default function SourceToSandboxMap({ env, envState, stores }) {
  const toolRows = toolMapRows(env, envState);
  const ruleRows = ruleMapRows(env);
  // A backed env passes its real §6 world.stores (may be empty); otherwise fall
  // back to the client/fixture seed tables. Actors were removed — they were a
  // hardcoded 4-row stub, not backed by world.personas.
  const storeRows = stores !== undefined ? storeRowsFromStores(stores) : storeMapRows(env);

  return (
    <Box
      sx={{
        mb: 3, borderRadius: "8px", overflow: "hidden",
        border: "1px solid", borderColor: "divider",
      }}
    >
      <Box sx={{ px: MAP_PX, py: 1, borderBottom: "1px solid", borderColor: "divider" }}>
        <Typography sx={{ typography: "m3", fontWeight: "fontWeightMedium" }}>{MAP_COPY.title}</Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", maxWidth: 720 }}>
          {MAP_COPY.subtitle}
        </Typography>
      </Box>

      <Box
        sx={{
          display: "grid", gridTemplateColumns: MAP_GRID, columnGap: 2,
          alignItems: "center", px: MAP_PX, height: 32,
          borderBottom: "1px solid", borderColor: "divider",
        }}
      >
        <ColHead>{MAP_COPY.readHead}</ColHead>
        <Box />
        <Box />
        <ColHead>{MAP_COPY.sandboxHead}</ColHead>
      </Box>

      <MapGroup
        label={MAP_COPY.groups.tools.label}
        hint={MAP_COPY.groups.tools.hint}
        right={MAP_COPY.toolsRight(toolRows.length)}
        first
      >
        {toolRows.map((r) => (
          <MapRow
            key={r.key}
           
            name={r.name}
            origin={r.origin}
            target={r.target}
            mono
            trailing={r.confirmed ? <ConfirmedChip /> : null}
          />
        ))}
      </MapGroup>

      <MapGroup
        label={MAP_COPY.groups.rules.label}
        hint={MAP_COPY.groups.rules.hint}
        right={MAP_COPY.rulesRight(ruleRows.length)}
      >
        {ruleRows.map((r) => (
          <MapRow key={r.key} name={r.name} origin={r.origin} target={r.target} />
        ))}
      </MapGroup>

      <MapGroup
        label={MAP_COPY.groups.stores.label}
        hint={MAP_COPY.groups.stores.hint}
        right={MAP_COPY.storesRight(storeRows.length)}
      >
        {storeRows.length === 0 ? (
          <EmptyRow>{MAP_COPY.storesEmpty}</EmptyRow>
        ) : (
          storeRows.map((r) => (
            <MapRow key={r.key} name={r.name} origin={r.origin} target={r.target} mono />
          ))
        )}
      </MapGroup>
    </Box>
  );
}
SourceToSandboxMap.propTypes = {
  env: ENV_SHAPE.isRequired,
  envState: PropTypes.shape({ toolResolutions: PropTypes.objectOf(PropTypes.string) }),
  // Real §6 world.stores for a backed env (may be []); undefined = use the
  // client/fixture seed tables.
  stores: PropTypes.array,
};

// A muted full-width row for a group the backend returned empty.
function EmptyRow({ children }) {
  return (
    <Box sx={{ px: MAP_PX, py: 1 }}>
      <Typography sx={{ typography: "s3", color: "text.subtitle", fontStyle: "italic" }}>
        {children}
      </Typography>
    </Box>
  );
}
EmptyRow.propTypes = { children: PropTypes.node };

// A small "you confirmed" pill for a tool row whose read/write effect was
// overridden by a human on the Contract tab — the map reflects that decision.
function ConfirmedChip() {
  return <StatusBadge status="confirmed" role="success" label={MAP_COPY.confirmed} size="sm" dot={false} />;
}

function ColHead({ children }) {
  return (
    <Typography
      sx={{
        typography: "s3", fontWeight: "fontWeightMedium", color: "text.subtitle",
      }}
    >
      {children}
    </Typography>
  );
}
ColHead.propTypes = { children: PropTypes.node };

function MapGroup({ label, hint, right, toAnswer, children, first }) {
  return (
    <Box>
      <Box
        sx={{
          display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto", columnGap: 2,
          alignItems: "baseline", px: MAP_PX, pt: first ? 1.5 : 2, pb: 1,
          borderTop: first ? "none" : "1px solid", borderColor: "divider",
        }}
      >
        <Stack direction="row" alignItems="baseline" spacing={1} sx={{ minWidth: 0 }}>
          <Typography sx={{ typography: "m3", fontWeight: "fontWeightMedium" }}>{label}</Typography>
          <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{hint}</Typography>
        </Stack>
        <Typography sx={{ typography: "s3", fontWeight: "fontWeightMedium", color: toAnswer ? "status.warning.fg" : "text.subtitle" }}>
          {right}
        </Typography>
      </Box>
      <Box>{children}</Box>
    </Box>
  );
}
MapGroup.propTypes = {
  label: PropTypes.string, hint: PropTypes.string, right: PropTypes.string,
  toAnswer: PropTypes.number, children: PropTypes.node, first: PropTypes.bool,
};

function MapRow({ name, origin, target, mono, highlight, derived, trailing, below }) {
  return (
    <Box
      sx={{
        position: "relative",
        bgcolor: highlight ? "action.hover" : "transparent",
        borderTop: "1px solid",
        borderColor: "divider",
      }}
    >
      <Box
        sx={{
          display: "grid", gridTemplateColumns: MAP_GRID, columnGap: 2,
          alignItems: "center",
          minHeight: highlight ? ROW_HEIGHT.twoLine : ROW_HEIGHT.default,
          pl: MAP_PX, pr: MAP_PX,
        }}
      >
        {name ? (
          <Typography
            noWrap
            sx={{
              typography: "s1",
              fontWeight: mono ? "fontWeightRegular" : "fontWeightMedium",
              fontFamily: mono ? MONO : undefined,
              color: derived ? "text.subtitle" : "text.primary",
            }}
          >
            {name}
          </Typography>
        ) : <Box />}

        <Box sx={{ justifySelf: "end", display: "flex" }}>
          {origin && <OriginChip origin={origin} showPath={false} />}
        </Box>

        <Iconify
          icon="solar:arrow-right-linear"
          width={14}
          sx={{ color: derived ? "text.disabled" : "text.subtitle", justifySelf: "center" }}
        />

        <Stack direction="row" alignItems="center" spacing={0.75} sx={{ minWidth: 0 }}>
          {/* String targets get the row's own text styling; a node target (the
              "Needs your answer" marker) styles itself, so it renders as-is
              rather than nested inside a <p>. */}
          {typeof target === "string" && (
            <Typography
              noWrap
              sx={{
                typography: "s1",
                color: derived ? "text.subtitle" : "text.primary",
                fontStyle: derived ? "italic" : "normal",
                minWidth: 0,
              }}
            >
              {target}
            </Typography>
          )}
          {typeof target !== "string" && target}
          {trailing}
        </Stack>
      </Box>

      {below && <Box sx={{ pl: MAP_PX, pr: MAP_PX, pb: 1.5, pt: 0.25 }}>{below}</Box>}
    </Box>
  );
}
MapRow.propTypes = {
  name: PropTypes.node, origin: PropTypes.string,
  target: PropTypes.node, mono: PropTypes.bool, highlight: PropTypes.bool,
  derived: PropTypes.bool, trailing: PropTypes.node, below: PropTypes.node,
};
