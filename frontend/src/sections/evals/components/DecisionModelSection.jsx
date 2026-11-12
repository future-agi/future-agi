import {
  Box,
  Button,
  CircularProgress,
  MenuItem,
  NativeSelect,
  Tooltip,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import { useState } from "react";
import Iconify from "src/components/iconify";
import {
  chooseDecisionConnection,
  decisionCompatibilityError,
  DECISION_OUTPUT_NAMES,
  isJevDecisionModel,
} from "../utils/decisionModels";

export default function DecisionModelSection({
  groups,
  model,
  search,
  outputType,
  choiceCount,
  jevLocked,
  loading,
  error,
  onRetry,
  onSelect,
  onAddKey,
}) {
  const [expanded, setExpanded] = useState(false);
  const selectedGroup = groups.find((group) =>
    group.connections.some((connection) => connection.model_name === model),
  );
  const visible = search || expanded ? groups : groups.slice(0, 6);
  const shown =
    selectedGroup && !visible.includes(selectedGroup)
      ? [...visible, selectedGroup]
      : visible;
  if (!groups.length && !loading && !error) return null;

  return (
    <Box>
      <Box
        sx={{
          px: 1.5,
          pt: 1.5,
          pb: 0.75,
          display: "flex",
          gap: 1,
          alignItems: "center",
        }}
      >
        <Typography
          variant="caption"
          sx={{ fontWeight: 600, color: "text.secondary" }}
        >
          Decision models
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {groups.length > 0 ? groups.length : ""}
        </Typography>
        {loading && (
          <CircularProgress size={12} aria-label="Loading decision models" />
        )}
      </Box>
      {error && (
        <Box sx={{ px: 1.5, pb: 1 }} role="status">
          <Typography variant="caption" color="text.secondary">
            Couldn’t load the decision catalog. Showing legacy native Jev
            options.
          </Typography>
          <Button size="small" onClick={onRetry}>
            Retry catalog
          </Button>
        </Box>
      )}
      {shown.map((group) => {
        const connection = chooseDecisionConnection(
          group,
          model,
          undefined,
          search,
        );
        const selected = connection.model_name === model;
        const compatibility = decisionCompatibilityError(
          group,
          outputType,
          choiceCount,
        );
        const locked = jevLocked && isJevDecisionModel(connection.model_name);
        const disabledReason =
          compatibility || (locked ? "Not included in your plan" : "");
        const choose = () => {
          if (disabledReason) return;
          if (connection.is_available) onSelect(connection.model_name);
          else
            onAddKey({
              model_name: connection.model_name,
              providers: connection.provider,
            });
        };
        return (
          <Box
            key={group.model_name}
            sx={{ mx: 0.5, mb: 0.5, borderRadius: "6px" }}
          >
            <Tooltip
              title={disabledReason || group.notice || ""}
              placement="right"
              arrow
            >
              <span>
                <MenuItem
                  selected={selected}
                  disabled={Boolean(disabledReason)}
                  onClick={choose}
                  onKeyDown={(event) => {
                    if (
                      event.key !== "ArrowRight" ||
                      group.connections.length < 2
                    )
                      return;
                    event.preventDefault();
                    event.stopPropagation();
                    event.currentTarget.parentElement.parentElement
                      .querySelector("select")
                      ?.focus();
                  }}
                  aria-keyshortcuts={
                    group.connections.length > 1 ? "ArrowRight" : undefined
                  }
                  sx={{
                    borderRadius: "6px",
                    py: 0.75,
                    gap: 1,
                    alignItems: "flex-start",
                  }}
                >
                  <Iconify
                    icon="solar:checklist-minimalistic-linear"
                    width={18}
                    sx={{ color: "text.secondary", mt: 0.25 }}
                  />
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Box
                      sx={{
                        display: "flex",
                        alignItems: "baseline",
                        gap: 0.75,
                        flexWrap: "wrap",
                      }}
                    >
                      <Typography
                        variant="body2"
                        sx={{ fontSize: "13px", fontWeight: 500 }}
                      >
                        {group.label}
                      </Typography>
                      {group.version === "latest" && (
                        <Typography variant="caption" color="text.secondary">
                          Moving alias
                        </Typography>
                      )}
                    </Box>
                    <Typography
                      variant="caption"
                      color="text.secondary"
                      sx={{ display: "block", fontSize: "12px" }}
                    >
                      {group.author} ·{" "}
                      {group.question_types
                        .map((type) => DECISION_OUTPUT_NAMES[type])
                        .join(" / ")}
                    </Typography>
                    <Typography
                      variant="caption"
                      color="text.secondary"
                      sx={{ display: "block", fontSize: "12px" }}
                    >
                      {disabledReason ||
                        (connection.is_available
                          ? `via ${connection.label} · Key configured`
                          : `Add your ${connection.label} API key`)}
                    </Typography>
                  </Box>
                  <Iconify
                    icon={
                      disabledReason
                        ? "solar:lock-linear"
                        : selected
                          ? "solar:check-linear"
                          : connection.is_available
                            ? "solar:alt-arrow-right-linear"
                            : "solar:key-linear"
                    }
                    width={16}
                    sx={{ color: "text.secondary", flexShrink: 0, mt: 0.25 }}
                  />
                </MenuItem>
              </span>
            </Tooltip>
            {group.connections.length > 1 && (
              <Box
                sx={{
                  px: 1.5,
                  pb: 0.75,
                  pl: 4.75,
                  display: "flex",
                  alignItems: "center",
                  gap: 1,
                }}
              >
                <Typography variant="caption" color="text.secondary">
                  Connection
                </Typography>
                <NativeSelect
                  value={connection.model_name}
                  inputProps={{ "aria-label": `Connection for ${group.label}` }}
                  onKeyDown={(event) => event.stopPropagation()}
                  onChange={(event) => {
                    const route = group.connections.find(
                      (item) => item.model_name === event.target.value,
                    );
                    if (
                      compatibility ||
                      (jevLocked && isJevDecisionModel(route.model_name))
                    )
                      return;
                    if (route.is_available) onSelect(route.model_name);
                    else
                      onAddKey({
                        model_name: route.model_name,
                        providers: route.provider,
                      });
                  }}
                  sx={{
                    fontSize: "12px",
                    maxWidth: "100%",
                    "& select": { py: 0.25 },
                    "&::before": { borderColor: "divider" },
                  }}
                >
                  {group.connections.map((route) => (
                    <option
                      key={route.model_name}
                      value={route.model_name}
                      disabled={
                        Boolean(compatibility) ||
                        (jevLocked && isJevDecisionModel(route.model_name))
                      }
                    >
                      {route.label}
                      {route.is_available ? " · Key configured" : " · Add key"}
                    </option>
                  ))}
                </NativeSelect>
              </Box>
            )}
            {selected && group.max_state_bytes && (
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: "block", px: 1.5, pl: 4.75, pb: 0.75 }}
              >
                Text/JSON only · Input limit:{" "}
                {group.max_state_bytes.toLocaleString()} UTF-8 bytes
              </Typography>
            )}
            {group.notice && group.notice !== "Text and JSON inputs only." && (
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: "block", px: 1.5, pl: 4.75, pb: 0.75 }}
              >
                {group.notice}
              </Typography>
            )}
          </Box>
        );
      })}
      {!search && groups.length > 6 && (
        <Button
          role="menuitem"
          size="small"
          onClick={() => setExpanded((value) => !value)}
          sx={{ ml: 1.5, mb: 0.5 }}
        >
          {expanded
            ? "Show fewer decision models"
            : `Show all ${groups.length} decision models`}
        </Button>
      )}
    </Box>
  );
}

DecisionModelSection.propTypes = {
  groups: PropTypes.array.isRequired,
  model: PropTypes.string,
  search: PropTypes.string,
  outputType: PropTypes.string,
  choiceCount: PropTypes.number,
  jevLocked: PropTypes.bool,
  loading: PropTypes.bool,
  error: PropTypes.bool,
  onRetry: PropTypes.func,
  onSelect: PropTypes.func.isRequired,
  onAddKey: PropTypes.func.isRequired,
};
