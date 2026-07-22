import React, { useState, useEffect } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Link,
  Stack,
  Switch,
  TextField,
  Typography,
  useTheme,
  CircularProgress,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import { useNavigate } from "react-router";
import axios, { endpoints } from "src/utils/axios";
import SvgColor from "src/components/svg-color";

const ORG_2FA_GRACE_PERIOD_MAX_DAYS = 30;

const OrgTwoFactorPolicySection = ({ onStatusChange }) => {
  const theme = useTheme();
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const [require2fa, setRequire2fa] = useState(false);
  const [gracePeriodDays, setGracePeriodDays] = useState(7);
  const [hasChanges, setHasChanges] = useState(false);

  const { data: policy, isLoading: isLoadingPolicy } = useQuery({
    queryKey: ["org-2fa-policy"],
    queryFn: async () => {
      const res = await axios.get(endpoints.orgPolicy.twoFactor);
      return res.data;
    },
  });

  const { data: myStatus } = useQuery({
    queryKey: ["2fa-status"],
    queryFn: async () => {
      const res = await axios.get(endpoints.twoFactor.status);
      return res.data;
    },
  });

  const my2faEnabled = myStatus?.two_factor_enabled || false;

  useEffect(() => {
    if (policy) {
      setRequire2fa(policy.require_2fa || false);
      setGracePeriodDays(policy.require_2fa_grace_period_days ?? 7);
      setHasChanges(false);
    }
  }, [policy]);

  const { mutate: updatePolicy, isPending: isSaving } = useMutation({
    mutationFn: async (data) => {
      const response = await axios.put(endpoints.orgPolicy.twoFactor, {
        require_2fa: data.require2fa,
        require_2fa_grace_period_days: data.gracePeriodDays,
      });
      return response.data;
    },
    onSuccess: () => {
      enqueueSnackbar("Organization security policy updated", {
        variant: "success",
      });
      setHasChanges(false);
      queryClient.invalidateQueries({ queryKey: ["org-2fa-policy"] });
      onStatusChange();
    },
    onError: (error) => {
      enqueueSnackbar(
        error?.response?.data?.error ||
          error?.message ||
          "Failed to update organization policy",
        { variant: "error" },
      );
    },
  });

  const handleToggle = (event) => {
    setRequire2fa(event.target.checked);
    setHasChanges(true);
  };

  const handleGracePeriodChange = (event) => {
    const val = parseInt(event.target.value, 10);
    setGracePeriodDays(
      isNaN(val)
        ? 1
        : Math.min(ORG_2FA_GRACE_PERIOD_MAX_DAYS, Math.max(1, val)),
    );
    setHasChanges(true);
  };

  const handleSave = () => {
    updatePolicy({ require2fa, gracePeriodDays });
  };

  if (isLoadingPolicy) {
    return (
      <Box
        sx={{
          width: "100%",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: theme.spacing(1),
          padding: theme.spacing(2),
          backgroundColor: "background.paper",
          display: "flex",
          justifyContent: "center",
        }}
      >
        <CircularProgress size={24} />
      </Box>
    );
  }

  return (
    <Box
      sx={{
        width: "100%",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: theme.spacing(1),
        padding: theme.spacing(2),
        backgroundColor: "background.paper",
      }}
    >
      <Typography
        variant="s1"
        sx={{
          fontWeight: "fontWeightSemiBold",
          color: "text.primary",
        }}
      >
        Organization Security
      </Typography>
      <Typography
        variant="s2"
        sx={{
          color: "text.secondary",
          fontWeight: "fontWeightRegular",
          marginTop: theme.spacing(0.5),
          marginBottom: theme.spacing(2),
        }}
      >
        Configure security policies for all members of your organization.
      </Typography>

      {/* Require 2FA Toggle */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          paddingY: theme.spacing(1),
        }}
      >
        <Box>
          <Typography
            variant="s1"
            sx={{
              color: "text.primary",
              fontWeight: "fontWeightMedium",
            }}
          >
            Require 2FA for all members
          </Typography>
          <Typography
            variant="s2"
            sx={{
              color: "text.secondary",
              fontWeight: "fontWeightRegular",
              marginTop: theme.spacing(0.25),
            }}
          >
            When enabled, all organization members must set up two-factor
            authentication.
          </Typography>
        </Box>
        <Switch
          checked={require2fa}
          onChange={handleToggle}
          color="primary"
          disabled={!my2faEnabled && !require2fa}
        />
      </Box>

      {/* Warning when admin's own 2FA is not enabled */}
      {!my2faEnabled && !require2fa && (
        <Stack
          direction="row"
          alignItems="center"
          width={"max-content"}
          gap={1.5}
          sx={{
            pl: 1.25,
            pr: 1.5,
            py: 1,
            mt: 2,
            backgroundColor:
              theme.palette.mode === "dark"
                ? "rgba(255, 193, 7, 0.08)"
                : "warning.lighter",
            borderRadius: 1,
            border: "1px solid",
            borderColor:
              theme.palette.mode === "dark"
                ? "rgba(255, 193, 7, 0.3)"
                : "warning.light",
          }}
        >
          <SvgColor
            src="/assets/icons/ic_failed.svg"
            sx={{
              width: "20px",
              height: "20px",
              color:
                theme.palette.mode === "dark" ? "warning.light" : "#7D5E2F",
            }}
          />
          <Typography
            variant="s2"
            sx={{
              color:
                theme.palette.mode === "dark" ? "warning.light" : "#7D5E2F",
              fontWeight: "fontWeightMedium",
              lineHeight: 1.5,
            }}
          >
            You must enable two-factor authentication on your own account before
            requiring it for the organization.{" "}
            <Link
              component="button"
              onClick={() => navigate("/dashboard/settings/profile-settings")}
              sx={{
                fontWeight: "fontWeightSemiBold",
                verticalAlign: "baseline",
                color: "#0066CC",
                textDecoration: "underline",
                cursor: "pointer",
                border: "none",
                background: "none",
                padding: 0,
                "&:hover": {
                  opacity: 0.8,
                },
              }}
            >
              Go to Profile Settings
            </Link>
          </Typography>
        </Stack>
      )}

      {/* Grace Period */}
      {require2fa && (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: theme.spacing(2),
            marginTop: theme.spacing(2),
            paddingTop: theme.spacing(2),
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography
            variant="s1"
            sx={{
              width: "200px",
              color: "text.secondary",
              fontWeight: "fontWeightRegular",
              flexShrink: 0,
            }}
          >
            Grace period (days)
          </Typography>
          <TextField
            type="number"
            value={gracePeriodDays}
            onChange={handleGracePeriodChange}
            size="small"
            inputProps={{ min: 1, max: ORG_2FA_GRACE_PERIOD_MAX_DAYS }}
            sx={{ width: "100px" }}
          />
          <Typography
            variant="s2"
            sx={{
              color: "text.disabled",
              fontWeight: "fontWeightRegular",
            }}
          >
            Members will have this many days to set up 2FA before being locked
            out.
          </Typography>
        </Box>
      )}

      {/* Save Button */}
      {hasChanges && (
        <Box
          sx={{
            display: "flex",
            justifyContent: "flex-end",
            marginTop: theme.spacing(2),
            paddingTop: theme.spacing(2),
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <LoadingButton
            loading={isSaving}
            onClick={handleSave}
            variant="contained"
            color="primary"
            size="small"
          >
            <Typography variant="s2" fontWeight="fontWeightMedium">
              Save
            </Typography>
          </LoadingButton>
        </Box>
      )}
    </Box>
  );
};

OrgTwoFactorPolicySection.propTypes = {
  onStatusChange: PropTypes.func.isRequired,
};

export default OrgTwoFactorPolicySection;
