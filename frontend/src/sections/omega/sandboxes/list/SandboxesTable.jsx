import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import Typography from "@mui/material/Typography";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";
import { useNavigate } from "react-router-dom";

import { RuntimeChip, StateChip, QuietTag } from "../components/SandboxStatus";
import {
  EMPTY,
  formatLimits,
  hibernatesIn,
  shortId,
  timeAgo,
} from "../lib/format";
import { sandboxPath } from "../lib/links";
import useNow from "../lib/useNow";

const COLUMNS = [
  "Name",
  "State",
  "Runtime",
  "Image",
  "CPU · Memory",
  "Last activity",
  "Hibernates",
];

const mono = { fontFamily: "monospace", fontSize: 12 };

function NameCell({ sandbox }) {
  return (
    <Box sx={{ minWidth: 0 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1, minWidth: 0 }}>
        <Typography
          typography="s1"
          fontWeight="fontWeightMedium"
          color="text.primary"
          noWrap
        >
          {sandbox.name}
        </Typography>
        {sandbox.origin === "agent_run" && <QuietTag>Agent run</QuietTag>}
        {sandbox.origin === "restored" && <QuietTag>Restored</QuietTag>}
      </Box>
      <Typography
        typography="s3"
        color="text.secondary"
        sx={mono}
        noWrap
        title={sandbox.id}
      >
        {shortId(sandbox.id, 10)}
      </Typography>
    </Box>
  );
}

NameCell.propTypes = { sandbox: PropTypes.object.isRequired };

function hibernateText(sandbox, now) {
  if (sandbox.state !== "active") return EMPTY;
  return hibernatesIn(sandbox.hibernatesAt, now);
}

function SandboxCard({ sandbox, now, onOpen }) {
  return (
    <Box
      component="button"
      type="button"
      onClick={onOpen}
      data-testid="sandbox-row"
      data-sandbox-id={sandbox.id}
      sx={{
        all: "unset",
        boxSizing: "border-box",
        cursor: "pointer",
        display: "flex",
        flexDirection: "column",
        gap: 1,
        p: 1.5,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
        "&:hover, &:focus-visible": { borderColor: "text.disabled" },
        "&:focus-visible": {
          outline: (theme) => `2px solid ${theme.palette.border.focus}`,
          outlineOffset: -2,
        },
      }}
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          gap: 1,
          minWidth: 0,
        }}
      >
        <NameCell sandbox={sandbox} />
        <StateChip state={sandbox.state} />
      </Box>
      <Box
        sx={{ display: "flex", flexWrap: "wrap", gap: 1, alignItems: "center" }}
      >
        <RuntimeChip provider={sandbox.provider} />
        {sandbox.image && (
          <Typography typography="s2" color="text.secondary" sx={mono}>
            {sandbox.image}
          </Typography>
        )}
      </Box>
      <Typography typography="s2" color="text.secondary">
        {sandbox.limits ? `${formatLimits(sandbox.limits)} · ` : ""}
        active {timeAgo(sandbox.lastActivityAt, now)}
        {sandbox.state === "active" && sandbox.hibernatesAt
          ? ` · hibernates ${hibernateText(sandbox, now)}`
          : ""}
      </Typography>
    </Box>
  );
}

SandboxCard.propTypes = {
  sandbox: PropTypes.object.isRequired,
  now: PropTypes.number.isRequired,
  onOpen: PropTypes.func.isRequired,
};

export default function SandboxesTable({ sandboxes }) {
  const theme = useTheme();
  const narrow = useMediaQuery(theme.breakpoints.down("md"));
  const navigate = useNavigate();
  const now = useNow();
  const open = (sandbox) => navigate(sandboxPath(sandbox.id));

  if (narrow) {
    return (
      <Box
        data-testid="sandboxes-list"
        sx={{ display: "flex", flexDirection: "column", gap: 1 }}
      >
        {sandboxes.map((sandbox) => (
          <SandboxCard
            key={sandbox.id}
            sandbox={sandbox}
            now={now}
            onOpen={() => open(sandbox)}
          />
        ))}
      </Box>
    );
  }

  return (
    <Box
      data-testid="sandboxes-list"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        overflow: "hidden",
      }}
    >
      <Table size="small" sx={{ tableLayout: "fixed" }}>
        <TableHead>
          <TableRow>
            {COLUMNS.map((column, index) => (
              <TableCell
                key={column}
                sx={{
                  typography: "s2",
                  fontWeight: 500,
                  color: "text.secondary",
                  bgcolor: "background.neutral",
                  borderColor: "divider",
                  width: index === 0 ? "26%" : undefined,
                  py: 1,
                }}
              >
                {column}
              </TableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {sandboxes.map((sandbox) => (
            <TableRow
              key={sandbox.id}
              hover
              tabIndex={0}
              data-testid="sandbox-row"
              data-sandbox-id={sandbox.id}
              onClick={() => open(sandbox)}
              onKeyDown={(event) => {
                if (event.key === "Enter") open(sandbox);
              }}
              sx={{
                cursor: "pointer",
                "& td": { borderColor: "divider", py: 1.25 },
                "&:focus-visible": {
                  outline: (theme) => `2px solid ${theme.palette.border.focus}`,
                  outlineOffset: -2,
                },
              }}
            >
              <TableCell>
                <NameCell sandbox={sandbox} />
              </TableCell>
              <TableCell>
                <StateChip state={sandbox.state} />
              </TableCell>
              <TableCell>
                <RuntimeChip provider={sandbox.provider} />
              </TableCell>
              <TableCell sx={{ ...mono, color: "text.secondary" }}>
                <Box
                  component="span"
                  sx={{
                    display: "block",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                  title={sandbox.image || ""}
                >
                  {sandbox.image || EMPTY}
                </Box>
              </TableCell>
              <TableCell
                sx={{
                  typography: "s1",
                  color: "text.primary",
                  whiteSpace: "nowrap",
                }}
              >
                {formatLimits(sandbox.limits)}
              </TableCell>
              <TableCell
                sx={{
                  typography: "s1",
                  color: "text.secondary",
                  whiteSpace: "nowrap",
                }}
              >
                {timeAgo(sandbox.lastActivityAt, now)}
              </TableCell>
              <TableCell
                sx={{
                  typography: "s1",
                  color: "text.secondary",
                  whiteSpace: "nowrap",
                }}
                data-testid="sandbox-hibernates"
              >
                {hibernateText(sandbox, now)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Box>
  );
}

SandboxesTable.propTypes = {
  sandboxes: PropTypes.arrayOf(PropTypes.object).isRequired,
};
