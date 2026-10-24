import React from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Card,
  CardContent,
  FormControlLabel,
  Stack,
  Switch,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import { useUpdateConfig } from "../providers/hooks/useGatewayConfig";
import GatewayAdminOnly from "../components/GatewayAdminOnly";
import { useGatewayAdmin } from "../hooks/useGatewayAdmin";
import { budgetsEnabled, budgetsEnabledIsExplicit } from "./budgetLevels";

// The org's budgets switch (the same budgets.enabled as Org Config >
// Budgets) and whether this gateway enforces budgets at all, from the
// gateway's org_sections_enforced (absent: unknown). The switch changes the
// org's gateway config: Owner or Admin only (useGatewayAdmin).
const BudgetEnforcementCard = ({ gatewayId, budgets, sectionsEnforced }) => {
  const updateConfig = useUpdateConfig();
  const { canEdit } = useGatewayAdmin();
  const enabled = budgetsEnabled(budgets);
  const explicit = budgetsEnabledIsExplicit(budgets);

  const handleToggle = (event) => {
    if (!canEdit) return;
    const next = event.target.checked;
    updateConfig.mutate(
      { gatewayId, config: { budgets: { enabled: next } } },
      {
        onSuccess: () =>
          enqueueSnackbar(next ? "Budgets turned on" : "Budgets turned off", {
            variant: "success",
          }),
        onError: (error) =>
          enqueueSnackbar(
            `Failed to update budgets${error?.message ? `: ${error.message}` : ""}`,
            { variant: "error" },
          ),
      },
    );
  };

  let caption = "Off until a budget level is set or you switch it on.";
  if (explicit) {
    caption = "Set for this organization here or in Org Config > Budgets.";
  } else if (enabled) {
    caption =
      "On because a budget level is set; switch it off to stop enforcing every level.";
  }

  const reported = Array.isArray(sectionsEnforced);
  const notEnforced = reported && !sectionsEnforced.includes("budgets");

  return (
    <Card>
      <CardContent>
        <Stack spacing={1.5}>
          <GatewayAdminOnly allowed={canEdit}>
            <FormControlLabel
              control={
                <Switch
                  checked={enabled}
                  onChange={handleToggle}
                  disabled={updateConfig.isPending || !canEdit}
                />
              }
              label="Enforce budgets"
            />
          </GatewayAdminOnly>
          <Typography variant="body2" color="text.secondary">
            {caption}
          </Typography>
          {explicit && !enabled && (
            <Alert severity="warning">
              Budgets are off for this organization: no level below is enforced
              until you switch them on.
            </Alert>
          )}
          {!reported && (
            <Alert severity="info">
              Enforcement unknown: this gateway does not report which org config
              sections it enforces.
            </Alert>
          )}
          {notEnforced && (
            <Alert severity="error">
              Not enforced on this gateway: it does not run the budget plugin,
              so these budgets are stored but no request is checked against
              them.
            </Alert>
          )}
        </Stack>
      </CardContent>
    </Card>
  );
};

BudgetEnforcementCard.propTypes = {
  gatewayId: PropTypes.string,
  budgets: PropTypes.object,
  sectionsEnforced: PropTypes.arrayOf(PropTypes.string),
};

export default BudgetEnforcementCard;
