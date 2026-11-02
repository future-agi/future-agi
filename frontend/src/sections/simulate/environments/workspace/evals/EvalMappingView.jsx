import PropTypes from "prop-types";
import { useId } from "react";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { sampleEvalHistory } from "src/api/simulate-environments/_fixtures/detail/evalHistory";
import SideDrawer from "../../components/SideDrawer";
import SoonChip from "../../components/SoonChip";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import useUiSample from "../../hooks/useUiSample";
import { EvalInputs } from "./evalEntryCells";
import { inputRowsOf } from "./evalEntry";

const MONO = "ui-monospace, Menlo, monospace";

const NOTHING_MAPPED =
  "Nothing mapped. This evaluation reads the run as a whole.";

const headingSx = {
  typography: "s3",
  fontWeight: "fontWeightSemiBold",
  color: "text.subtitle",
  letterSpacing: 0.4,
};

// A run test's config sends its mapping as raw `variable → value` pairs, not
// the labelled `inputs[]` an environment's eval row carries. Shown as stored.
function RawMapping({ mapping }) {
  return (
    <Stack spacing={0.75}>
      {Object.entries(mapping).map(([variable, value]) => (
        <Stack key={variable} direction="row" alignItems="center" spacing={1}>
          <Typography
            noWrap
            sx={{ typography: "s3", fontFamily: MONO, color: "text.secondary" }}
          >
            {variable}
          </Typography>
          <Iconify
            icon="solar:arrow-right-linear"
            width={12}
            sx={{ color: "text.disabled", flexShrink: 0 }}
          />
          <Typography
            noWrap
            sx={{
              typography: "s3",
              fontFamily: MONO,
              color: "text.primary",
              flex: 1,
              minWidth: 0,
            }}
          >
            {typeof value === "string" ? value : JSON.stringify(value)}
          </Typography>
        </Stack>
      ))}
    </Stack>
  );
}
RawMapping.propTypes = { mapping: PropTypes.object.isRequired };

// An eval's mapping, from whichever shape the row carries: the environment's
// labelled `inputs[]` first, else a run test's raw `mapping`.
export function EvalMapping({ item }) {
  if (inputRowsOf(item).length > 0) {
    return <EvalInputs entry={item} heading={null} />;
  }
  const mapping =
    item?.mapping && typeof item.mapping === "object" ? item.mapping : {};
  if (Object.keys(mapping).length > 0) return <RawMapping mapping={mapping} />;
  return (
    <Typography sx={{ typography: "s2", color: "text.secondary" }}>
      {NOTHING_MAPPED}
    </Typography>
  );
}
EvalMapping.propTypes = { item: PropTypes.object };

// Pass threshold and judge model. Neither is on an environment's eval row,
// and no endpoint sends a threshold, so each missing one is named as coming.
function ScoringLine({ item }) {
  const threshold = Number.isFinite(item?.threshold) ? item.threshold : null;
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{ flexWrap: "wrap", rowGap: 0.75 }}
    >
      {threshold != null ? (
        <Typography
          sx={{
            typography: "s2",
            color: "text.secondary",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {`Pass ≥ ${(threshold * 100).toFixed(0)}%`}
        </Typography>
      ) : (
        <SoonChip label="Pass threshold" what="The pass threshold" />
      )}
      {item?.model ? (
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          {`judged by ${item.model}`}
        </Typography>
      ) : (
        <SoonChip label="Judge model" what="The judge model" />
      )}
    </Stack>
  );
}
ScoringLine.propTypes = { item: PropTypes.object };

function EvalHistory({ name }) {
  const rows = sampleEvalHistory(name);
  const headingId = useId();
  return (
    <Box component="section" aria-labelledby={headingId}>
      <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 1 }}>
        <Typography id={headingId} sx={headingSx}>
          History
        </Typography>
        <UiOnlyFlag feature="evalHistory" />
      </Stack>
      <Stack spacing={1}>
        {rows.map((row) => (
          <Stack key={row.label} direction="row" spacing={1.25}>
            <Typography
              sx={{
                typography: "s3",
                fontFamily: MONO,
                color: "text.subtitle",
                flexShrink: 0,
                width: 20,
              }}
            >
              {row.label}
            </Typography>
            <Box flex={1} minWidth={0}>
              <Typography sx={{ typography: "s2", color: "text.primary" }}>
                {row.detail}
              </Typography>
              {row.note && (
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {row.note}
                </Typography>
              )}
            </Box>
            <Typography
              sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}
            >
              {row.ago}
            </Typography>
          </Stack>
        ))}
      </Stack>
    </Box>
  );
}
EvalHistory.propTypes = { name: PropTypes.string };

/**
 * An added eval, read-only: what fills each of its variables and how it is
 * scored. Vel's view of an eval; the Edit button opens our full eval config
 * (the caller's `onEdit`), where the mapping and the judge are changed.
 *
 * `item` is an environment eval row (`evaluations.selected[]`) or a run
 * test's eval config; both carry `id` and `name`.
 */
export default function EvalMappingView({
  open,
  item,
  onClose,
  onEdit,
  canEdit = false,
  editTooltip = "",
}) {
  const showHistory = useUiSample("evalHistory");
  const blurb = item?.description || item?.blurb || "";

  return (
    <SideDrawer open={Boolean(open && item)} onClose={onClose} width={480}>
      {item && (
        <Stack sx={{ height: "100%" }}>
          <Box
            sx={{
              px: 2.5,
              py: 2,
              pr: 6,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography
              sx={{ typography: "m3", fontWeight: "fontWeightSemiBold" }}
            >
              {item.name}
            </Typography>
            {blurb && (
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                {blurb}
              </Typography>
            )}
          </Box>

          <Stack spacing={2.5} sx={{ p: 2.5, flex: 1, overflowY: "auto" }}>
            <Box>
              <Typography sx={{ ...headingSx, mb: 1.25 }}>
                Variable mapping
              </Typography>
              <EvalMapping item={item} />
            </Box>
            <Box>
              <Typography sx={{ ...headingSx, mb: 0.75 }}>Scoring</Typography>
              <ScoringLine item={item} />
            </Box>
            {showHistory && <EvalHistory name={item.name} />}
          </Stack>

          <Stack
            direction="row"
            spacing={1.5}
            sx={{
              px: 2.5,
              py: 2,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            <Box flex={1} />
            <Button
              size="small"
              onClick={onClose}
              sx={{ color: "text.secondary" }}
            >
              Close
            </Button>
            <CustomTooltip
              show={Boolean(editTooltip)}
              arrow
              size="small"
              title={editTooltip}
            >
              <span>
                <Button
                  size="small"
                  variant="contained"
                  color="primary"
                  disabled={!canEdit}
                  onClick={() => onEdit?.(item)}
                  startIcon={<Iconify icon="solar:pen-linear" width={14} />}
                >
                  Edit
                </Button>
              </span>
            </CustomTooltip>
          </Stack>
        </Stack>
      )}
    </SideDrawer>
  );
}

EvalMappingView.propTypes = {
  open: PropTypes.bool,
  item: PropTypes.object,
  onClose: PropTypes.func.isRequired,
  // Opens the full eval config (mapping, model) for this eval.
  onEdit: PropTypes.func,
  canEdit: PropTypes.bool,
  // Why Edit is held, when it is.
  editTooltip: PropTypes.string,
};
