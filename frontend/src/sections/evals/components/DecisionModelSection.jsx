import {
  Box,
  Button,
  CircularProgress,
  Link,
  NativeSelect,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import { useState } from "react";
import { PickerRow, PickerSectionLabel } from "./ModelPickerParts";
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
  // A section with no matches disappears, but a catalog error stays visible
  // (with Retry) even while searching.
  if (!groups.length && !error && (search || !loading)) return null;

  return (
    <Box>
      <PickerSectionLabel
        count={groups.length}
        adornment={
          loading ? (
            <CircularProgress size={10} aria-label="Loading decision models" />
          ) : null
        }
      >
        Decision models
      </PickerSectionLabel>
      {error && (
        <Box sx={{ px: 1.5, pb: 0.75 }} role="status">
          <Typography
            sx={{ fontSize: 11, lineHeight: "16px", color: "text.disabled" }}
          >
            Couldn’t load the catalog, showing built-in Jev models.{" "}
            <Link
              component="button"
              aria-label="Retry catalog"
              onClick={onRetry}
              underline="always"
              color="text.secondary"
              sx={{
                font: "inherit",
                fontWeight: 500,
                verticalAlign: "baseline",
              }}
            >
              Retry
            </Link>
          </Typography>
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
          <Box key={group.model_name} sx={{ mb: 0.25 }}>
            <PickerRow
              icon="solar:checklist-minimalistic-linear"
              label={group.label}
              badge={group.version === "latest" ? "Moving alias" : null}
              meta={[
                group.author,
                group.question_types
                  .map((type) => DECISION_OUTPUT_NAMES[type])
                  .join(" / "),
              ]
                .filter(Boolean)
                .join(" · ")}
              // Key and plan state get their own line so they never truncate.
              status={
                disabledReason ||
                (!connection.is_available
                  ? `Add your ${connection.label} API key`
                  : connection.label === group.author
                    ? "Key configured"
                    : `via ${connection.label} · Key configured`)
              }
              selected={selected}
              disabled={Boolean(disabledReason)}
              tooltip={disabledReason || group.notice || ""}
              trailing={
                disabledReason
                  ? "lock"
                  : selected
                    ? "check"
                    : connection.is_available
                      ? undefined
                      : "key"
              }
              onClick={choose}
              menuItemProps={{
                onKeyDown: (event) => {
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
                },
                "aria-keyshortcuts":
                  group.connections.length > 1 ? "ArrowRight" : undefined,
              }}
            />
            {group.connections.length > 1 && (
              <Box
                sx={{
                  pr: 1.5,
                  pb: 0.75,
                  pl: 4.5,
                  display: "flex",
                  alignItems: "center",
                  gap: 1,
                }}
              >
                <Typography
                  sx={{
                    fontSize: 11,
                    lineHeight: "16px",
                    color: "text.disabled",
                  }}
                >
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
                    fontSize: 11,
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
                sx={{
                  display: "block",
                  pl: 4.5,
                  pr: 1.5,
                  pb: 0.75,
                  fontSize: 11,
                  lineHeight: "16px",
                  color: "text.disabled",
                }}
              >
                Text/JSON only · Input limit:{" "}
                {group.max_state_bytes.toLocaleString()} UTF-8 bytes
              </Typography>
            )}
            {group.notice && group.notice !== "Text and JSON inputs only." && (
              <Typography
                sx={{
                  display: "block",
                  pl: 4.5,
                  pr: 1.5,
                  pb: 0.75,
                  fontSize: 11,
                  lineHeight: "16px",
                  color: "text.disabled",
                }}
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
          sx={{ ml: 1, mb: 0.5, color: "text.secondary" }}
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
