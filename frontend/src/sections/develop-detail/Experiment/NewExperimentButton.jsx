import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  ButtonGroup,
  Link,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Typography,
  useTheme,
} from "@mui/material";
import { useParams } from "react-router-dom";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import CustomTooltip from "src/components/tooltip";
import useDatasetAccess from "src/sections/common/Access/useDatasetAccess";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { useRunExperimentStoreShallow } from "../states";
import { usePendingChangesGate } from "../DataTab/Versions/PendingChangesGate";
import {
  cardsForKind,
  kindRecommendation,
  stepCardShown,
} from "src/sections/common/RunSources/catalog";
import { useDatasetKind } from "src/sections/common/Datasets/datasetKind";
import useOpenSource from "../ExperimentTab/useOpenSource";
import { isSurfaceShown } from "src/launch/launchSwitch";

/**
 * The one entry point to the New experiment drawer (RunExperiment.jsx,
 * mounted page-level in DevelopDetailView, so it opens in place on any tab).
 *   placement="header"       dataset header (DatasetsSelectRow), Data tab
 *   placement="experiments"  Experiments tab header (ExperimentTabHeader)
 * `prefill` ({version_id, split_ids, row_ids}) and `firstRunType` pass
 * through to initiateCreateMode. `onBeforeOpen` is kept for callers but the
 * drawer no longer needs a tab switch first, so the Experiments placement
 * ignores it.
 *
 * R4 (lane Rb MUST 8): a split button. The main part opens Level 1; `▾`
 * lists the three kinds with their card line and opens that kind's catalog
 * (`src=<kind>`; Prompt and Agent graph add their R3 run card).
 */
const HEADER_HOVER_TEXT =
  "Run prompts, agent graphs, endpoints, A2A agents or MCP tools on this dataset and compare them";
// Copy follows the same card gates as the picker, including F01-only builds.
const LAUNCH_HOVER_TEXT = "Run prompts on this dataset and compare them";
const hoverText = () => {
  if (stepCardShown("running")) return HEADER_HOVER_TEXT;
  return stepCardShown("agent")
    ? "Run prompts and agent graphs on this dataset and compare them"
    : LAUNCH_HOVER_TEXT;
};
const arrowText = () => {
  if (stepCardShown("running"))
    return "Pick what to run: a prompt, an agent graph, or something already running (an endpoint, an A2A agent or an MCP tool)";
  return stepCardShown("agent")
    ? "Pick what to run: a prompt or an agent graph"
    : "Pick what to run: a prompt";
};
const DOCS_LINK =
  "https://docs.futureagi.com/docs/dataset/features/experiments";

// options-truth L3 G8 P1: the menu lists the kinds in the order Level 1
// shows them for this dataset (cardsForKind) with the same `Recommended`
// tag (kindRecommendation) on the first card.
function KindMenu({ anchor, onClose, onPick, datasetKind }) {
  const cards = cardsForKind(datasetKind);
  const rec = kindRecommendation(datasetKind);
  return (
    <Menu
      anchorEl={anchor}
      open={Boolean(anchor)}
      onClose={onClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
      transformOrigin={{ vertical: "top", horizontal: "right" }}
      slotProps={{ paper: { sx: { width: 360, mt: 0.5 } } }}
      MenuListProps={{ "aria-label": "What to run", dense: true }}
    >
      {cards.map((card, i) => (
        <MenuItem
          key={card.id}
          data-testid={`new-experiment-kind-${card.id}`}
          onClick={() => {
            onClose();
            onPick(card.id);
          }}
          sx={{ alignItems: "flex-start", gap: 0, py: 0.75 }}
          autoFocus={i === 0}
        >
          <ListItemIcon sx={{ mt: 0.25 }}>
            <Iconify icon={card.icon} width={18} />
          </ListItemIcon>
          <ListItemText
            primary={
              <Box
                component="span"
                sx={{ display: "inline-flex", alignItems: "center", gap: 0.75 }}
              >
                {card.title}
                {rec?.card === card.id ? (
                  <CustomTooltip
                    show={Boolean(rec.reason)}
                    title={rec.reason ?? ""}
                    size="small"
                    placement="top"
                  >
                    <Typography
                      component="span"
                      typography="s3"
                      data-testid="new-experiment-recommended-tag"
                      sx={{
                        px: 0.75,
                        borderRadius: 0.5,
                        bgcolor: "purple.o10",
                        color: "primary.main",
                        fontWeight: "fontWeightMedium",
                        whiteSpace: "nowrap",
                      }}
                    >
                      Recommended
                    </Typography>
                  </CustomTooltip>
                ) : null}
              </Box>
            }
            secondary={card.line}
            primaryTypographyProps={{
              typography: "s1",
              fontWeight: "fontWeightMedium",
            }}
            secondaryTypographyProps={{
              typography: "s3",
              color: "text.secondary",
              sx: { whiteSpace: "normal" },
            }}
          />
        </MenuItem>
      ))}
    </Menu>
  );
}

KindMenu.propTypes = {
  anchor: PropTypes.any,
  onClose: PropTypes.func.isRequired,
  onPick: PropTypes.func.isRequired,
  datasetKind: PropTypes.string,
};

const ArrowPart = ({ onOpen, disabled, variant, color, height }) => (
  <CustomTooltip
    show={!disabled}
    title={arrowText()}
    size="small"
    placement="top"
  >
    <Box component="span" sx={{ display: "inline-flex" }}>
      <Button
        size="small"
        variant={variant}
        color={color}
        disabled={disabled}
        aria-label="New experiment: pick what to run"
        aria-haspopup="menu"
        data-testid="new-experiment-kinds"
        onClick={(e) => onOpen(e.currentTarget)}
        sx={{ px: 0.5, minWidth: 0, height }}
      >
        <Iconify icon="mdi:chevron-down" width={18} />
      </Button>
    </Box>
  </CustomTooltip>
);

ArrowPart.propTypes = {
  onOpen: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
  variant: PropTypes.string,
  color: PropTypes.string,
  height: PropTypes.number,
};

// J09: classic prompt creation is independent of new experiment enhancements.
const NewExperimentButton = (props) =>
  isSurfaceShown("exp.classic") ? (
    <NewExperimentButtonInner {...props} />
  ) : null;

const NewExperimentButtonInner = ({
  placement = "header",
  disabled = false,
  onBeforeOpen,
  prefill,
  firstRunType,
}) => {
  const theme = useTheme();
  // L6: the ▾ kinds (agent graph, already running) only once a lane ships them.
  const kindMenuShown = isSurfaceShown("exp.new.kindMenu");
  const { role } = useAuthContext();
  const gate = usePendingChangesGate();
  const { initiateCreateMode } = useRunExperimentStoreShallow((state) => ({
    initiateCreateMode: state.initiateCreateMode,
  }));
  const canEdit = Boolean(RolePermission.DATASETS[PERMISSIONS.UPDATE][role]);
  const { dataset: datasetId } = useParams();
  const { kind: r2Kind, dataset } = useDatasetKind(datasetId);
  const datasetKind = dataset?.kind ?? r2Kind;
  // Unsaved dataset edits first go through the pending-changes interstitial:
  // an experiment runs on a saved version.
  const open = () =>
    gate("experiment", () => initiateCreateMode({ prefill, firstRunType }));
  const openSource = useOpenSource({ prefill });
  const [kindAnchor, setKindAnchor] = useState(null);
  // W4 §9 (M6): a run needs run access on the dataset; the server's reason
  // is the tooltip of the disabled button.
  const access = useDatasetAccess(datasetId);
  const runReason = canEdit && !access.can.run ? access.reason("run") : null;
  const off = disabled || !canEdit || !access.can.run;
  const kinds = (
    <KindMenu
      anchor={kindAnchor}
      datasetKind={datasetKind}
      onClose={() => setKindAnchor(null)}
      onPick={(cardId) => {
        onBeforeOpen?.();
        openSource({ cardId });
      }}
    />
  );

  if (placement === "experiments") {
    return (
      <>
        <CustomTooltip show={Boolean(runReason)} title={runReason ?? ""}>
          <Box component="span" sx={{ display: "inline-flex" }}>
            <ButtonGroup
              size="small"
              variant="outlined"
              color="primary"
              disableElevation
              sx={{ flexShrink: 0 }}
            >
              <Button
                startIcon={<Iconify icon="mdi:plus" width={18} />}
                disabled={off}
                onClick={open}
                // The toolbar's 32 px row height. R4: outlined, so the answer's
                // `Compare latest vs previous` is the one primary (budget §C1.2).
                sx={{ whiteSpace: "nowrap", height: 32, px: 1.5 }}
              >
                New experiment
              </Button>
              {kindMenuShown ? (
                <ArrowPart
                  onOpen={setKindAnchor}
                  disabled={off}
                  variant="outlined"
                  color="primary"
                  height={32}
                />
              ) : null}
            </ButtonGroup>
          </Box>
        </CustomTooltip>
        {kindMenuShown ? kinds : null}
      </>
    );
  }

  return (
    <>
      <ButtonGroup size="small" variant="outlined" sx={{ flexShrink: 0 }}>
        <CustomTooltip
          show
          title={
            runReason ? (
              <Box data-testid="new-experiment-denied">{runReason}</Box>
            ) : (
              <Box>
                {hoverText()}.{" "}
                <Link
                  href={DOCS_LINK}
                  underline="always"
                  color="blue.500"
                  target="_blank"
                  rel="noopener noreferrer"
                  fontWeight="fontWeightMedium"
                  onClick={(e) => e.stopPropagation()}
                  sx={{ whiteSpace: "nowrap" }}
                >
                  Read more
                </Link>
              </Box>
            )
          }
          placement="bottom"
          arrow
          size="small"
          type="black"
          slotProps={{ tooltip: { sx: { maxWidth: "220px !important" } } }}
        >
          {/* A disabled button fires no events; the span keeps the tooltip. */}
          <Box component="span" sx={{ display: "inline-flex" }}>
            <Button
              size="small"
              variant="outlined"
              startIcon={
                <SvgColor src="/assets/icons/action_buttons/ic_experiment.svg" />
              }
              disabled={off}
              onClick={() => {
                onBeforeOpen?.();
                open();
              }}
              sx={{
                "&:hover": {
                  backgroundColor: `${theme.palette.divider} !important`,
                },
              }}
            >
              New experiment
            </Button>
          </Box>
        </CustomTooltip>
        {kindMenuShown ? (
          <ArrowPart onOpen={setKindAnchor} disabled={off} variant="outlined" />
        ) : null}
      </ButtonGroup>
      {kindMenuShown ? kinds : null}
    </>
  );
};

NewExperimentButtonInner.propTypes = {
  placement: PropTypes.oneOf(["header", "experiments"]),
  disabled: PropTypes.bool,
  onBeforeOpen: PropTypes.func,
  prefill: PropTypes.shape({
    version_id: PropTypes.string,
    split_ids: PropTypes.arrayOf(PropTypes.string),
    row_ids: PropTypes.arrayOf(PropTypes.string),
  }),
  firstRunType: PropTypes.oneOf(["prompt", "agent", "external", "http"]),
};

NewExperimentButton.propTypes = NewExperimentButtonInner.propTypes;

export default NewExperimentButton;
