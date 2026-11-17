import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import ButtonBase from "@mui/material/ButtonBase";
import Typography from "@mui/material/Typography";

import { GatewayStatusChip } from "../../components/frame";
import GatewayAdminOnly from "../../components/GatewayAdminOnly";
import ServerMark from "../components/ServerMark";
import { GATEWAY_MONO } from "../../utils/fonts";

function Meta({ children }) {
  return (
    <Typography
      component="span"
      typography="s2"
      color="text.secondary"
      sx={{
        px: 0.75,
        borderRadius: 0.75,
        border: "1px solid",
        borderColor: "divider",
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </Typography>
  );
}

Meta.propTypes = { children: PropTypes.node };

function credentialNote(auth) {
  if (auth.kind === "none") return null;
  if (auth.state === "not_set") return "Credential not set";
  if (auth.state === "revoked") return "Credential revoked";
  if (auth.state === "expired") return "Credential expired";
  if (auth.state === "needs_reauth") return "Credential needs re-entry";
  return null;
}

// The card's own Edit and Remove (the drawer keeps its pair too). They write
// the org's gateway config: Owner or Admin only, disabled with the reason for
// everyone else, as in the drawer.
function CardActions({ row, canEdit, onEdit, onRemove }) {
  return (
    <Box
      sx={{
        display: "flex",
        gap: 1,
        px: 1.5,
        pb: 1.5,
        flexWrap: "wrap",
      }}
    >
      <GatewayAdminOnly allowed={canEdit}>
        <Button
          size="small"
          variant="outlined"
          aria-label={`Edit ${row.id}`}
          disabled={!canEdit}
          onClick={() => onEdit(row)}
        >
          Edit
        </Button>
      </GatewayAdminOnly>
      <GatewayAdminOnly allowed={canEdit}>
        <Button
          size="small"
          variant="outlined"
          color="error"
          aria-label={`Remove ${row.id}`}
          disabled={!canEdit}
          onClick={() => onRemove(row)}
        >
          Remove
        </Button>
      </GatewayAdminOnly>
    </Box>
  );
}

CardActions.propTypes = {
  row: PropTypes.object.isRequired,
  canEdit: PropTypes.bool,
  onEdit: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired,
};

// One server: its mark, id, endpoint host, health, how it authenticates and
// how many tools it serves; an unhealthy one shows the gateway's last error.
// With `onEdit` and `onRemove` the card also carries Edit and Remove, outside
// the button that opens the server.
export function ServerCard({ row, onOpen, onEdit, onRemove, canEdit = false }) {
  const note = credentialNote(row.auth);
  const actions = Boolean(onEdit && onRemove);
  const open = (
    <ButtonBase
      onClick={() => onOpen(row.id)}
      aria-label={`${row.id} ${row.health.label}`}
      sx={{
        display: "flex",
        flexDirection: "column",
        alignItems: "stretch",
        textAlign: "left",
        gap: 1.25,
        p: 1.5,
        minWidth: 0,
        flex: 1,
        height: actions ? undefined : "100%",
        borderRadius: 1,
        ...(actions
          ? {}
          : {
              border: "1px solid",
              borderColor: "divider",
              bgcolor: "background.paper",
              transition: (theme) =>
                theme.transitions.create(["border-color", "background-color"], {
                  duration: theme.transitions.duration.shortest,
                }),
              "&:hover": {
                borderColor: "border.hover",
                bgcolor: "background.neutral",
              },
            }),
        "&.Mui-focusVisible": {
          outline: "2px solid",
          outlineColor: "primary.main",
        },
      }}
    >
      <Box sx={{ display: "flex", gap: 1.25, alignItems: "flex-start" }}>
        <ServerMark id={row.id} url={row.url} size={40} />
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography
            typography="s1"
            fontWeight="fontWeightMedium"
            color="text.primary"
            noWrap
            title={row.id}
          >
            {row.id}
          </Typography>
          <Typography
            typography="s2"
            color="text.secondary"
            noWrap
            title={row.url}
          >
            {row.host || "—"}
          </Typography>
        </Box>
        <GatewayStatusChip tone={row.health.tone} label={row.health.label} />
      </Box>

      {row.lastError && (
        <Typography
          typography="s2"
          color="error.main"
          sx={{ fontFamily: GATEWAY_MONO, overflowWrap: "anywhere" }}
        >
          {row.lastError}
        </Typography>
      )}

      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: "auto" }}>
        {!row.runtimeOff && (
          <Meta>{`${row.toolCount} ${row.toolCount === 1 ? "tool" : "tools"}`}</Meta>
        )}
        <Meta>{row.auth.label}</Meta>
        <Meta>{row.transport}</Meta>
        {note && <Meta>{note}</Meta>}
      </Box>
    </ButtonBase>
  );
  if (!actions) return open;
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        minWidth: 0,
        height: "100%",
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
        transition: (theme) =>
          theme.transitions.create(["border-color", "background-color"], {
            duration: theme.transitions.duration.shortest,
          }),
        "&:hover": {
          borderColor: "border.hover",
          bgcolor: "background.neutral",
        },
      }}
    >
      {open}
      <CardActions
        row={row}
        canEdit={canEdit}
        onEdit={onEdit}
        onRemove={onRemove}
      />
    </Box>
  );
}

ServerCard.propTypes = {
  row: PropTypes.object.isRequired,
  onOpen: PropTypes.func.isRequired,
  onEdit: PropTypes.func,
  onRemove: PropTypes.func,
  // Owner or Admin (useGatewayAdmin): only they may edit or remove a server.
  canEdit: PropTypes.bool,
};

// The org's servers as a card grid (the Composio-style catalog, over what
// this line has: the org's own servers, no vendor catalog).
export default function ServerCatalog({
  rows,
  onOpen,
  onEdit,
  onRemove,
  canEdit = false,
}) {
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: {
          xs: "1fr",
          sm: "repeat(2, minmax(0, 1fr))",
          lg: "repeat(3, minmax(0, 1fr))",
          xl: "repeat(4, minmax(0, 1fr))",
        },
        gap: 1.5,
      }}
    >
      {rows.map((row) => (
        <ServerCard
          key={row.id}
          row={row}
          onOpen={onOpen}
          onEdit={onEdit}
          onRemove={onRemove}
          canEdit={canEdit}
        />
      ))}
    </Box>
  );
}

ServerCatalog.propTypes = {
  rows: PropTypes.array.isRequired,
  onOpen: PropTypes.func.isRequired,
  onEdit: PropTypes.func,
  onRemove: PropTypes.func,
  canEdit: PropTypes.bool,
};
