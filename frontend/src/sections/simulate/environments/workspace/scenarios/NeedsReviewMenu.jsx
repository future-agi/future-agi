import PropTypes from "prop-types";
import { Fragment, useState } from "react";
import { enqueueSnackbar } from "notistack";
import { Box, Button, Popover, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import {
  SAMPLE_ENV_LABEL,
  SAMPLE_REVIEW,
  sampleGateOf,
} from "src/api/simulate-environments/_fixtures/detail/scenarioStates";
import SoonChip from "../../components/SoonChip";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import useUiSample from "../../hooks/useUiSample";
import { UI_ONLY_SAVED } from "../gaps/NeedsInputButton";
import { QualityFacts, QualityStateChip } from "./QualityReviewDrawer";

const uiOnly = () => enqueueSnackbar(UI_ONLY_SAVED, { variant: "info" });
const plural = (n, one, many) => (n === 1 ? one : many);

// Vel's notices, built off his sample. Every action but Quality review is
// UI only: it sends nothing and says so.
function sampleNotices(onOpenQualityReview) {
  const { changeReview: cr, proposed, improvements } = SAMPLE_REVIEW;
  const gate = sampleGateOf();
  const counts = [
    cr.outdated > 0 && { n: cr.outdated, label: "Outdated", red: true },
    cr.stale > 0 && { n: cr.stale, label: "Stale" },
    cr.recheck > 0 && { n: cr.recheck, label: "Re-check" },
  ].filter(Boolean);
  return [
    counts.length > 0 && {
      id: "change-review",
      icon: "solar:refresh-circle-linear",
      attention: true,
      title: counts.map((c, i) => (
        <Fragment key={c.label}>
          {i > 0 && (
            <Box
              component="span"
              sx={{ color: "text.disabled", fontWeight: 400 }}
            >
              {" · "}
            </Box>
          )}
          <Box
            component="span"
            sx={{ color: c.red ? "error.main" : "text.primary" }}
          >
            {`${c.n} ${c.label}`}
          </Box>
        </Fragment>
      )),
      sub: `Checked against environment ${SAMPLE_ENV_LABEL}. Flagged ones run but don't count until fixed.`,
      actions: [
        { label: "Review", primary: true, onClick: uiOnly },
        cr.reprovable > 0 && {
          label: `Re-prove ${cr.reprovable}`,
          onClick: uiOnly,
        },
      ].filter(Boolean),
    },
    proposed > 0 && {
      id: "proposed",
      icon: "solar:inbox-linear",
      attention: true,
      title: `${proposed} proposed ${plural(proposed, "scenario", "scenarios")} waiting to be added`,
      sub: "Generated, failing-case, Error Feed and production scenarios join the environment only once you add them. Until then they don't run.",
      actions: [
        { label: "Review", onClick: uiOnly },
        { label: `Add ${proposed}`, primary: true, onClick: uiOnly },
      ],
    },
    improvements > 0 && {
      id: "improvements",
      icon: "solar:magic-stick-3-linear",
      attention: true,
      title: `${improvements} ${plural(improvements, "scenario", "scenarios")} from improvements ${plural(improvements, "isn't", "aren't")} in this environment`,
      sub: "Improvements wrote these to reproduce what they were fixing. They stay with the improvement until you add them.",
      actions: [{ label: "Review", onClick: uiOnly }],
    },
    {
      id: "quality",
      icon: "solar:shield-check-linear",
      attention: gate.state !== "ready",
      title: (
        <Stack direction="row" alignItems="center" spacing={1} component="span">
          <span>Quality review</span>
          <QualityStateChip gate={gate} />
        </Stack>
      ),
      sub: <QualityFacts gate={gate} />,
      actions: [
        {
          label: gate.state === "spot-check" ? "Spot-check" : "Review",
          onClick: onOpenQualityReview,
        },
      ],
    },
  ].filter(Boolean);
}

// The API has no review notices: only the quality review, which opens the
// drawer, and a line saying nothing is flagged yet.
function realNotices(onOpenQualityReview) {
  return [
    {
      id: "nothing",
      icon: "solar:eye-linear",
      title: (
        <Stack direction="row" alignItems="center" spacing={1} component="span">
          <span>Nothing to review yet</span>
          <SoonChip
            label="Review"
            what="Scenarios flagged for review after a build or an agent change"
            size="tiny"
          />
        </Stack>
      ),
      sub: "Outdated, stale and proposed scenarios will be listed here.",
      actions: [],
    },
    {
      id: "quality",
      icon: "solar:shield-check-linear",
      title: "Quality review",
      sub: "The quality gate, a human spot-check and rule coverage.",
      actions: [{ label: "Review", onClick: onOpenQualityReview }],
    },
  ];
}

function NoticeRow({ notice, onAction }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.25}
      sx={{ px: 1.75, py: 1 }}
    >
      <Iconify
        icon={notice.icon}
        width={16}
        sx={{
          color: "text.subtitle",
          flexShrink: 0,
          alignSelf: "flex-start",
          mt: "2px",
        }}
      />
      <Box flex={1} minWidth={0}>
        <Typography component="div" sx={{ typography: "s2", fontWeight: 600 }}>
          {notice.title}
        </Typography>
        {notice.sub && (
          <Typography
            component="div"
            sx={{ typography: "s3", color: "text.secondary", mt: 0.125 }}
          >
            {notice.sub}
          </Typography>
        )}
      </Box>
      {notice.actions.length > 0 && (
        <Stack direction="row" spacing={0.75} sx={{ flexShrink: 0 }}>
          {notice.actions.map((a) => (
            <Button
              key={a.label}
              size="small"
              variant={a.primary ? "contained" : "outlined"}
              color={a.primary ? "primary" : "inherit"}
              onClick={() => onAction(a)}
              sx={{
                whiteSpace: "nowrap",
                ...(!a.primary && {
                  color: "text.primary",
                  borderColor: "divider",
                }),
              }}
            >
              {a.label}
            </Button>
          ))}
        </Stack>
      )}
    </Stack>
  );
}
NoticeRow.propTypes = {
  notice: PropTypes.object.isRequired,
  onAction: PropTypes.func.isRequired,
};

// Vel's "Needs review" toolbar button: the notices that used to stack above
// the scenario table, one compact row each with its own actions. The count
// shows only with ?uiSample=review, since the API flags nothing yet.
export default function NeedsReviewMenu({ onOpenQualityReview }) {
  const sample = useUiSample("review");
  const [anchor, setAnchor] = useState(null);
  const notices = sample
    ? sampleNotices(onOpenQualityReview)
    : realNotices(onOpenQualityReview);
  const count = notices.filter((n) => n.attention).length;

  // Every action closes the list first, then runs.
  const onAction = (a) => {
    setAnchor(null);
    a.onClick?.();
  };

  return (
    <>
      <Button
        size="small"
        variant="outlined"
        aria-haspopup="true"
        aria-expanded={!!anchor}
        onClick={(e) => setAnchor(e.currentTarget)}
        startIcon={<Iconify icon="solar:eye-linear" width={14} />}
        sx={{
          flexShrink: 0,
          whiteSpace: "nowrap",
          color: "text.primary",
          borderColor: anchor ? "text.primary" : "divider",
          bgcolor: anchor ? "action.selected" : "transparent",
        }}
      >
        Needs review
        {count > 0 && (
          <Box
            component="span"
            data-testid="needs-review-count"
            sx={{
              ml: 0.75,
              px: 0.625,
              minWidth: 18,
              height: 18,
              borderRadius: "4px",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              typography: "s3",
              fontWeight: 600,
              fontVariantNumeric: "tabular-nums",
              color: "text.secondary",
              bgcolor: "action.selected",
            }}
          >
            {count}
          </Box>
        )}
      </Button>
      <Popover
        open={!!anchor}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{
          paper: {
            sx: {
              width: 460,
              maxWidth: "calc(100vw - 32px)",
              mt: 0.5,
              py: 0.5,
            },
          },
        }}
      >
        {sample && (
          <Stack
            direction="row"
            justifyContent="flex-end"
            sx={{ px: 1.75, pt: 0.5 }}
          >
            <UiOnlyFlag feature="scenarioStates" />
          </Stack>
        )}
        <Stack
          divider={
            <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
          }
        >
          {notices.map((n) => (
            <NoticeRow key={n.id} notice={n} onAction={onAction} />
          ))}
        </Stack>
      </Popover>
    </>
  );
}

NeedsReviewMenu.propTypes = {
  onOpenQualityReview: PropTypes.func,
};
