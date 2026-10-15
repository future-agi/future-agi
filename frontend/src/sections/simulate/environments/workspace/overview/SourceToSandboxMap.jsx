import { useState } from "react";
import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import OriginChip from "../../components/OriginChip";
import { ENV_SHAPE } from "./overview.constants";
import {
  MAP_COPY,
  actorMapRows,
  ruleMapRows,
  startingDataRows,
  storeMapRows,
  storeRowsFromStores,
  toolMapRows,
} from "./sourceToSandbox.constants";

const MONO = "ui-monospace, Menlo, monospace";
const MAP_GRID = "minmax(0, 3fr) 128px 24px minmax(0, 2fr)";
const MAP_PX = 3;

export default function SourceToSandboxMap({ env, stores }) {
  const seededRows = stores !== undefined ? storeRowsFromStores(stores) : storeMapRows(env);
  const storeRows = [...seededRows, ...startingDataRows(env, new Set(seededRows.map((row) => row.name)))];
  const groups = [
    { id: "tools", rows: toolMapRows(env) },
    { id: "rules", rows: ruleMapRows(env) },
    { id: "stores", rows: storeRows, empty: MAP_COPY.storesEmpty },
    { id: "actors", rows: actorMapRows(env) },
  ].filter((group) => group.rows.length || group.empty);

  return (
    <Box
      sx={{
        mb: 3,
        borderRadius: 2,
        overflow: "hidden",
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
      }}
    >
      <Box sx={{ px: MAP_PX, py: 2, borderBottom: "1px solid", borderColor: "divider" }}>
        <Typography sx={{ typography: "s1", fontWeight: "fontWeightBold" }}>{MAP_COPY.title}</Typography>
        <Typography sx={{ typography: "s2", color: "text.subtitle", mt: 0.25, maxWidth: 720 }}>
          {MAP_COPY.subtitle}
        </Typography>
      </Box>

      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: MAP_GRID,
          columnGap: 2,
          alignItems: "center",
          px: MAP_PX,
          py: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
          bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.03 : 0.02),
        }}
      >
        <ColHead>{MAP_COPY.readHead}</ColHead>
        <Box />
        <Box />
        <ColHead>{MAP_COPY.sandboxHead}</ColHead>
      </Box>

      {groups.map((group, groupIndex) => (
        <MapGroup
          key={group.id}
          label={MAP_COPY.groups[group.id].label}
          hint={MAP_COPY.groups[group.id].hint}
          right={MAP_COPY.mapped(group.rows.length)}
          first={groupIndex === 0}
        >
          {group.rows.length === 0 ? (
            <EmptyRow>{group.empty}</EmptyRow>
          ) : (
            group.rows.map((row, index) => <MapRow key={row.key} index={index} row={row} />)
          )}
        </MapGroup>
      ))}
    </Box>
  );
}
SourceToSandboxMap.propTypes = {
  env: ENV_SHAPE.isRequired,
  stores: PropTypes.array,
};

function EmptyRow({ children }) {
  return (
    <Box sx={{ px: MAP_PX, py: 1.25 }}>
      <Typography sx={{ typography: "s3", color: "text.subtitle", fontStyle: "italic" }}>{children}</Typography>
    </Box>
  );
}
EmptyRow.propTypes = { children: PropTypes.node };

function ColHead({ children }) {
  return (
    <Typography
      sx={{
        typography: "s3",
        fontWeight: "fontWeightBold",
        textTransform: "uppercase",
        letterSpacing: 0.6,
        color: "text.subtitle",
      }}
    >
      {children}
    </Typography>
  );
}
ColHead.propTypes = { children: PropTypes.node };

function MapGroup({ label, hint, right, children, first }) {
  return (
    <Box>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: "minmax(0, 1fr) auto",
          columnGap: 2,
          alignItems: "baseline",
          px: MAP_PX,
          pt: first ? 2 : 2.25,
          pb: 1,
          borderTop: first ? "none" : "1px solid",
          borderColor: "divider",
        }}
      >
        <Stack direction="row" alignItems="baseline" spacing={1} sx={{ minWidth: 0 }}>
          <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold" }}>{label}</Typography>
          <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
            {hint}
          </Typography>
        </Stack>
        <Typography sx={{ typography: "s3", fontWeight: "fontWeightBold", color: "text.subtitle" }}>{right}</Typography>
      </Box>
      <Box sx={{ pb: 1 }}>{children}</Box>
    </Box>
  );
}
MapGroup.propTypes = {
  label: PropTypes.string,
  hint: PropTypes.string,
  right: PropTypes.string,
  children: PropTypes.node,
  first: PropTypes.bool,
};

function MapRow({ index, row }) {
  const { name, detail, prose, origin, target, fallback } = row;
  const multiline = Boolean(detail || prose);
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: MAP_GRID,
        columnGap: 2.5,
        alignItems: multiline ? "start" : "center",
        minHeight: 40,
        px: MAP_PX,
        py: multiline ? 1.25 : 0,
        bgcolor: (t) =>
          index % 2 === 1 ? alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.025 : 0.015) : "transparent",
      }}
    >
      {multiline ? (
        <Box sx={{ minWidth: 0 }}>
          <Typography
            sx={{
              typography: "s2",
              fontWeight: "fontWeightSemiBold",
              fontFamily: prose ? undefined : MONO,
              overflowWrap: "anywhere",
            }}
          >
            {name}
          </Typography>
          {detail && (
            <Typography sx={{ typography: "s3", color: "text.secondary", mt: 0.5, maxWidth: 640 }}>
              {detail}
            </Typography>
          )}
        </Box>
      ) : (
        <TruncatedText text={name} sx={{ typography: "s2", fontWeight: "fontWeightSemiBold", fontFamily: MONO }} />
      )}
      <Box sx={{ justifySelf: "end", display: "flex", whiteSpace: "nowrap" }}>
        {origin && (
          <OriginChip origin={origin.kind} file={origin.path || origin.field} line={origin.line ?? undefined} showPath={false} />
        )}
      </Box>
      <Iconify icon="solar:arrow-right-linear" width={14} sx={{ color: "text.subtitle", justifySelf: "center" }} />
      <TruncatedText
        text={target || fallback}
        sx={{
          typography: "s2",
          minWidth: 0,
          color: target ? "text.primary" : "text.subtitle",
          fontStyle: target ? "normal" : "italic",
        }}
      />
    </Box>
  );
}
MapRow.propTypes = {
  index: PropTypes.number,
  row: PropTypes.shape({
    name: PropTypes.string,
    detail: PropTypes.string,
    prose: PropTypes.bool,
    origin: PropTypes.object,
    target: PropTypes.string,
    fallback: PropTypes.string,
  }).isRequired,
};

function TruncatedText({ text, sx }) {
  const [truncated, setTruncated] = useState(false);
  return (
    <CustomTooltip show title={truncated ? text : ""} size="small" placement="top-start" arrow>
      <Typography
        noWrap
        onMouseEnter={(event) =>
          setTruncated(event.currentTarget.scrollWidth > event.currentTarget.clientWidth)
        }
        sx={sx}
      >
        {text}
      </Typography>
    </CustomTooltip>
  );
}
TruncatedText.propTypes = {
  text: PropTypes.string,
  sx: PropTypes.object,
};
