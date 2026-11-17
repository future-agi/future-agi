import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import Typography from "@mui/material/Typography";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";

import { relativeTime } from "../lib/format";
import { kindLabel, usageLabel } from "../lib/secretForm";

const mono = { fontFamily: "monospace", fontSize: 13 };
const headCell = {
  typography: "s2",
  fontWeight: 500,
  color: "text.secondary",
  bgcolor: "background.neutral",
  borderColor: "divider",
  py: 1,
};

/** What a row says about the value: never the value, only its version and age. */
function valueLine(secret) {
  const parts = [
    `Version ${secret.version}`,
    `set ${relativeTime(secret.updatedAt)}`,
  ];
  if (secret.lastUsedAt)
    parts.push(`last read ${relativeTime(secret.lastUsedAt)}`);
  return parts.join(" · ");
}

function Actions({ secret, onReplace, onDelete }) {
  return (
    <Box
      sx={{
        display: "flex",
        gap: 0.5,
        justifyContent: "flex-end",
        flexWrap: "wrap",
      }}
    >
      <Button
        size="small"
        color="inherit"
        onClick={() => onReplace(secret)}
        aria-label={`Replace the value of ${secret.name}`}
        data-testid="replace-secret"
      >
        Replace value
      </Button>
      <Button
        size="small"
        color="error"
        onClick={() => onDelete(secret)}
        aria-label={`Delete ${secret.name}`}
        data-testid="delete-secret"
      >
        Delete
      </Button>
    </Box>
  );
}

Actions.propTypes = {
  secret: PropTypes.object.isRequired,
  onReplace: PropTypes.func.isRequired,
  onDelete: PropTypes.func.isRequired,
};

function SecretCard({ secret, canManage, onReplace, onDelete }) {
  return (
    <Box
      data-testid="secret-row"
      data-secret-name={secret.name}
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 0.75,
        p: 1.5,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
      }}
    >
      <Box sx={{ ...mono, color: "text.primary", overflowWrap: "anywhere" }}>
        {secret.name}
      </Box>
      <Typography typography="s2" color="text.secondary">
        {kindLabel(secret.kind)} · {usageLabel(secret.bindingCount)}
      </Typography>
      <Typography typography="s2" color="text.secondary">
        {valueLine(secret)}
      </Typography>
      {canManage && (
        <Actions secret={secret} onReplace={onReplace} onDelete={onDelete} />
      )}
    </Box>
  );
}

SecretCard.propTypes = {
  secret: PropTypes.object.isRequired,
  canManage: PropTypes.bool,
  onReplace: PropTypes.func.isRequired,
  onDelete: PropTypes.func.isRequired,
};

/** The vault's active secrets: names, kinds, versions and use; values never. */
export default function SecretsTable({
  secrets,
  canManage,
  onReplace,
  onDelete,
}) {
  const theme = useTheme();
  const narrow = useMediaQuery(theme.breakpoints.down("md"));

  if (narrow) {
    return (
      <Box
        data-testid="secrets-list"
        sx={{ display: "flex", flexDirection: "column", gap: 1 }}
      >
        {secrets.map((secret) => (
          <SecretCard
            key={secret.credentialId}
            secret={secret}
            canManage={canManage}
            onReplace={onReplace}
            onDelete={onDelete}
          />
        ))}
      </Box>
    );
  }

  const columns = ["Name", "Kind", "Value", "Use", ...(canManage ? [""] : [])];
  return (
    <Box
      data-testid="secrets-list"
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
            {columns.map((column, index) => (
              <TableCell
                key={column || "actions"}
                sx={{
                  ...headCell,
                  width: ["28%", "14%", undefined, "16%", "22%"][index],
                }}
              >
                {column}
              </TableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {secrets.map((secret) => (
            <TableRow
              key={secret.credentialId}
              data-testid="secret-row"
              data-secret-name={secret.name}
              sx={{ "& td": { borderColor: "divider", py: 1.25 } }}
            >
              <TableCell
                sx={{
                  ...mono,
                  color: "text.primary",
                  overflowWrap: "anywhere",
                }}
              >
                {secret.name}
              </TableCell>
              <TableCell sx={{ typography: "s1", color: "text.secondary" }}>
                {kindLabel(secret.kind)}
              </TableCell>
              <TableCell sx={{ typography: "s1", color: "text.secondary" }}>
                {valueLine(secret)}
              </TableCell>
              <TableCell sx={{ typography: "s1", color: "text.secondary" }}>
                {usageLabel(secret.bindingCount)}
              </TableCell>
              {canManage && (
                <TableCell>
                  <Actions
                    secret={secret}
                    onReplace={onReplace}
                    onDelete={onDelete}
                  />
                </TableCell>
              )}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Box>
  );
}

SecretsTable.propTypes = {
  secrets: PropTypes.arrayOf(PropTypes.object).isRequired,
  canManage: PropTypes.bool,
  onReplace: PropTypes.func.isRequired,
  onDelete: PropTypes.func.isRequired,
};
