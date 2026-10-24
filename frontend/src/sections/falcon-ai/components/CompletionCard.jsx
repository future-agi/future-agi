import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import Button from "@mui/material/Button";
import Iconify from "src/components/iconify";
import { useRouter } from "src/routes/hooks";
import {
  isHiddenFromRestrictedOrgs,
  useRestrictSimulateToEnvironments,
} from "src/hooks/useRestrictSimulateToEnvironments";

export default function CompletionCard({ card }) {
  const router = useRouter();
  const restrictSimulateToEnvironments = useRestrictSimulateToEnvironments();

  if (!card) return null;

  const path = card.action_path || card.action_url;
  // Falcon links to the scenario, agent definition or test it created; an org
  // restricted to Environments cannot open those pages.
  const showAction =
    card.action_label &&
    path &&
    !(restrictSimulateToEnvironments && isHiddenFromRestrictedOrgs(path));

  const handleNavigate = () => {
    router.push(path);
  };

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        border: 1,
        borderColor: "divider",
        borderRadius: 1.5,
        bgcolor: "background.neutral",
        p: 1.5,
        mt: 1,
      }}
    >
      <Box>
        <Typography variant="body2" fontWeight={600}>
          {card.title}
        </Typography>
        {card.summary && (
          <Box
            sx={{ display: "flex", alignItems: "center", gap: 0.5, mt: 0.5 }}
          >
            <Iconify
              icon="mdi:check-circle"
              width={16}
              sx={{ color: "success.main" }}
            />
            <Typography variant="caption" color="text.secondary">
              {card.summary}
            </Typography>
          </Box>
        )}
      </Box>

      {showAction && (
        <Button
          size="small"
          variant="outlined"
          onClick={handleNavigate}
          startIcon={<Iconify icon="mdi:link-variant" width={16} />}
          sx={{ textTransform: "none", flexShrink: 0, ml: 1 }}
        >
          {card.action_label}
        </Button>
      )}
    </Box>
  );
}

CompletionCard.propTypes = {
  card: PropTypes.shape({
    title: PropTypes.string,
    summary: PropTypes.string,
    action_label: PropTypes.string,
    action_url: PropTypes.string,
    action_path: PropTypes.string,
  }),
};
