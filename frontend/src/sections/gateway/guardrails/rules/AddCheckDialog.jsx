import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Dialog from "@mui/material/Dialog";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import IconButton from "@mui/material/IconButton";
import List from "@mui/material/List";
import ListItemButton from "@mui/material/ListItemButton";
import Typography from "@mui/material/Typography";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";

import FormSearchField from "src/components/FormSearchField/FormSearchField";

import { GatewayEmptyState } from "../../components/frame";
import { GatewayIcon } from "../../shell/gateway-icons";
import { OFFERED_CATALOG, STAGE_LABELS, isShownCheck } from "../catalog";

// A partner or Future AGI check calls a service outside the gateway, and is
// offered only once the organization's config holds that service's account
// (catalog isShownCheck), which a check not yet added never does. So the
// catalog lists the checks built into the gateway.
const GROUPS = [
  {
    id: "built-in",
    title: "Built into the gateway",
    match: (entry) => entry.kind === "built-in",
  },
];

const matchesSearch = (entry, query) => {
  if (!query) return true;
  const text =
    `${entry.label} ${entry.name} ${entry.description}`.toLowerCase();
  return text.includes(query.toLowerCase());
};

/**
 * The catalog of checks this org has not added yet. Picking one opens its
 * settings (the rule drawer) as a new rule.
 */
export default function AddCheckDialog({ open, onClose, onPick, configured }) {
  const theme = useTheme();
  const fullScreen = useMediaQuery(theme.breakpoints.down("sm"));
  const [query, setQuery] = useState("");

  const groups = useMemo(() => {
    const available = OFFERED_CATALOG.filter(
      (entry) =>
        !configured.has(entry.name) &&
        isShownCheck(entry, null) &&
        matchesSearch(entry, query.trim()),
    );
    return GROUPS.map((group) => ({
      ...group,
      entries: available.filter(group.match),
    })).filter((group) => group.entries.length > 0);
  }, [configured, query]);

  const close = () => {
    setQuery("");
    onClose();
  };

  return (
    <Dialog
      open={open}
      onClose={close}
      fullWidth
      maxWidth="sm"
      fullScreen={fullScreen}
      aria-labelledby="guardrail-add-check-title"
    >
      <DialogTitle
        id="guardrail-add-check-title"
        sx={{ display: "flex", alignItems: "center", gap: 1, pb: 1 }}
      >
        <Box component="span" sx={{ flex: 1 }}>
          Add a check
        </Box>
        <IconButton aria-label="Close" size="small" onClick={close}>
          <GatewayIcon name="close" size={16} />
        </IconButton>
      </DialogTitle>
      <DialogContent
        sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}
      >
        <FormSearchField
          size="small"
          placeholder="Search checks"
          searchQuery={query}
          onChange={(event) => setQuery(event.target.value)}
          inputProps={{ "aria-label": "Search checks" }}
          autoFocus
        />
        {groups.length === 0 && (
          <GatewayEmptyState
            variant="no-results"
            title={query ? "No check matches" : "Every check is already added"}
            description={
              query
                ? "Try another name, or clear the search."
                : "Open a rule from the list to change it."
            }
            onAction={query ? () => setQuery("") : undefined}
            actionLabel="Clear search"
          />
        )}
        {groups.map((group) => (
          <Box key={group.id} component="section" aria-label={group.title}>
            <Typography
              typography="s2"
              color="text.secondary"
              fontWeight="fontWeightMedium"
              sx={{ px: 1, pb: 0.5 }}
            >
              {group.title}
            </Typography>
            <List dense disablePadding>
              {group.entries.map((entry) => (
                <ListItemButton
                  key={entry.name}
                  onClick={() => {
                    setQuery("");
                    onPick(entry);
                  }}
                  sx={{ borderRadius: 1, alignItems: "flex-start", gap: 1 }}
                >
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Typography typography="s1" color="text.primary">
                      {entry.label}
                    </Typography>
                    <Typography typography="s2" color="text.secondary">
                      {entry.description}
                    </Typography>
                  </Box>
                  {entry.stage === "response" && (
                    <Typography
                      typography="s3"
                      color="text.secondary"
                      sx={{ flexShrink: 0, pt: 0.25 }}
                    >
                      {`${STAGE_LABELS.response} · reads the answer`}
                    </Typography>
                  )}
                </ListItemButton>
              ))}
            </List>
          </Box>
        ))}
      </DialogContent>
    </Dialog>
  );
}

AddCheckDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onPick: PropTypes.func.isRequired,
  configured: PropTypes.instanceOf(Set).isRequired,
};
