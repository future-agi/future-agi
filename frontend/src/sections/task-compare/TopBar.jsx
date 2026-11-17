import React from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import CopyId from "src/sections/common/ux/CopyId";
import KeyHintTooltip from "src/sections/common/ux/KeyHintTooltip";
import { ActionBar } from "src/sections/common/ux/oneview";
import { useRegisterCommands } from "src/sections/common/CommandPalette/commandRegistry";
import OneLine from "./OneLine";
import ExpectedCard from "./ExpectedCard";

export const TOP_BAR_ROW = 40;

const quietLink = {
  p: 0,
  border: 0,
  bgcolor: "transparent",
  cursor: "pointer",
  typography: "s3",
  color: "primary.main",
  whiteSpace: "nowrap",
  "&:hover": { textDecoration: "underline" },
  "&:focus-visible": { outline: "2px solid", outlineColor: "primary.main" },
};

/** Opens the lane picker that the bar renders (⌘K `Edit lanes`). */
const openLanePicker = () =>
  document.querySelector("[data-lane-picker-btn]")?.click();

/**
 * One-line top bar (ONE-VIEW-SPEC §7.1 item 1), 40 px: `← Matrix` · `T26`
 * · title · `⧉` (copy the task id) · `◎ Expected` (hover card) · `From session …
 * ↗` · spacer · ActionBar: `Edit lanes ▾` · `Blind` · `Copy link` (icon) ·
 * `Ask Falcon` (outlined). What does not fit folds into `More ▾`; every
 * action is in ⌘K by its label. Pure presentation.
 */
export default function TopBar({
  row,
  onBack,
  backLabel,
  onCopyLink,
  onAskFalcon,
  blind,
  onBlind,
  onOpenSource,
  lanePicker,
  shownRuns,
  selectionCount,
  onCards,
}) {
  // IT-R1 G4: Blind moved into `Lanes ▾` and the steps `View ▾`; it stays
  // in ⌘K.
  useRegisterCommands(
    "taskCompare.blind",
    onBlind
      ? [
          {
            id: "taskCompare.action.blind",
            group: "Actions",
            label: blind
              ? "Blind review: off (show run names)"
              : "Blind review",
            keywords: ["blind", "hide names"],
            icon: "mdi:eye-off-outline",
            run: onBlind,
          },
        ]
      : [],
    [Boolean(blind), Boolean(onBlind)],
  );
  const actions = [
    lanePicker
      ? {
          id: "lanes",
          label: "Edit lanes",
          icon: "mdi:view-column-outline",
          priority: 1,
          keywords: ["lanes", "runs", "add run", "remove run"],
          run: openLanePicker,
          render: () => lanePicker,
        }
      : null,
    {
      id: "copyLink",
      label: "Copy link",
      icon: "mdi:link-variant",
      priority: 2,
      variant: "icon",
      shortcut: "copyLink",
      run: onCopyLink,
    },
    {
      id: "falcon",
      label: "Ask Falcon",
      icon: "mdi:creation",
      priority: 1,
      variant: "outlined",
      shortcut: "askFalcon",
      run: onAskFalcon,
    },
  ];
  return (
    <Stack
      component="header"
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{
        px: 1.5,
        height: TOP_BAR_ROW,
        flexShrink: 0,
        minWidth: 0,
        borderBottom: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
      }}
    >
      <KeyHintTooltip title={`Back to ${backLabel}`} action="escape">
        <Button
          size="small"
          color="inherit"
          onClick={onBack}
          startIcon={<Iconify icon="mdi:arrow-left" width={16} />}
          sx={{ flexShrink: 0, color: "text.secondary", minWidth: 0 }}
        >
          {backLabel}
        </Button>
      </KeyHintTooltip>
      <Box
        sx={{ width: "1px", height: 20, bgcolor: "divider", flexShrink: 0 }}
      />
      {row ? (
        <>
          <Typography
            typography="m3"
            fontWeight="fontWeightSemiBold"
            sx={{ flexShrink: 0, fontVariantNumeric: "tabular-nums" }}
          >
            {row.label}
          </Typography>
          <Box sx={{ minWidth: 60, flex: "0 1 auto" }}>
            <OneLine
              text={row.title}
              sx={{ typography: "s1", fontWeight: 500 }}
            />
          </Box>
          <Box sx={{ flexShrink: 0, display: "flex" }}>
            {/* L2 G2 R4 (consistency #8): the title says `T20`; the id
                copy stays as an icon (no second `Row 0020` name). */}
            <CopyId
              id={row.id}
              label="Task id"
              short={4}
              sx={{ px: 0.5, "& .MuiTypography-root": { display: "none" } }}
            />
          </Box>
          <ExpectedCard row={row} />
          {row.source?.ref ? (
            <CustomTooltip
              describeChild
              show
              size="small"
              title={`This task came from the production conversation ${row.source.ref} in ${row.source.project_name ?? "Observe"}. Opens its trace.`}
            >
              <Button
                size="small"
                color="inherit"
                onClick={onOpenSource}
                endIcon={<Iconify icon="mdi:open-in-new" width={14} />}
                sx={{
                  flexShrink: 0,
                  typography: "s2",
                  color: "text.secondary",
                  display: { xs: "none", lg: "inline-flex" },
                }}
              >
                From session {row.source.ref}
              </Button>
            </CustomTooltip>
          ) : null}
        </>
      ) : null}
      {selectionCount > shownRuns ? (
        <Typography
          typography="s3"
          color="text.secondary"
          sx={{
            flexShrink: 0,
            whiteSpace: "nowrap",
            display: { xs: "none", xl: "block" },
          }}
        >
          {shownRuns} of {selectionCount} runs
          {selectionCount > 4 ? (
            <>
              {" · "}
              <Box
                component="button"
                type="button"
                onClick={onCards}
                sx={quietLink}
              >
                Cards
              </Box>
            </>
          ) : null}
        </Typography>
      ) : null}
      <ActionBar
        surface="taskCompare"
        actions={actions.filter(Boolean)}
        sx={{ ml: "16px !important", minWidth: 330 }}
      />
    </Stack>
  );
}

TopBar.propTypes = {
  row: PropTypes.object,
  onBack: PropTypes.func.isRequired,
  backLabel: PropTypes.string.isRequired,
  onCopyLink: PropTypes.func.isRequired,
  onAskFalcon: PropTypes.func.isRequired,
  blind: PropTypes.bool,
  onBlind: PropTypes.func,
  onOpenSource: PropTypes.func,
  lanePicker: PropTypes.node,
  shownRuns: PropTypes.number,
  selectionCount: PropTypes.number,
  onCards: PropTypes.func,
};
