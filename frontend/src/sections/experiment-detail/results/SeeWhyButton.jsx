import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Button,
  Divider,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Typography,
} from "@mui/material";
import { useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";
import { fmtCount } from "src/sections/common/format/fmt";
import { useWhyFails } from "./WhyFailsStrip";
import { causeMethodLabel } from "./causeMethod";

const BTN_SX = { height: 24, typography: "s2", whiteSpace: "nowrap" };

/** The three items of `Why ▾` (seam S6; pure, exported for tests). */
export function whyMenuItems({ noun = "row", worse = 0, canWhy, why }) {
  return [
    {
      key: "worst",
      label: `Open the worst ${noun}`,
      icon: "mdi:compare-horizontal",
      disabled: !canWhy,
      reason: canWhy ? null : `No ${noun} got worse`,
    },
    {
      key: "worse",
      label: `Show the ${fmtCount(worse)} that got worse`,
      icon: "mdi:filter-variant",
      disabled: !(worse > 0),
      reason: worse > 0 ? null : `No ${noun} got worse`,
    },
    {
      key: "explain",
      label: "Explain",
      icon: "mdi:format-list-checks",
      disabled: !why || !(why.traceHref ?? why.fixHref),
      reason: why ? null : "No finding for these runs yet",
    },
  ];
}

/**
 * IT-R5 G3 (P1, seam S6): ONE label, `Why ▾`, right of the verdict. Its
 * items are the same on every surface: `Open the worst <task|row>`,
 * `Show the N that got worse` and `Explain` (the Falcon finding, opened as
 * a trace side by side). The finding's sentence heads the menu.
 * @param {object} props  WhyFailsStrip's props + `onWhy()`, `canWhy`,
 *   `worse`, `onShowWorse()`, `noun`
 */
export default function SeeWhyButton({
  onWhy,
  canWhy = false,
  worse = 0,
  onShowWorse,
  noun = "row",
  ...props
}) {
  const navigate = useNavigate();
  const why = useWhyFails(props);
  const anchor = useRef(null);
  const [open, setOpen] = useState(false);
  if (!why && !canWhy && !(worse > 0)) return null;
  const items = whyMenuItems({ noun, worse, canWhy, why });
  const run = (key) => {
    setOpen(false);
    if (key === "worst") onWhy?.();
    else if (key === "worse") onShowWorse?.();
    else if (key === "explain" && (why?.traceHref ?? why?.fixHref))
      navigate(why.traceHref ?? why.fixHref);
  };
  return (
    <>
      <Button
        ref={anchor}
        size="small"
        variant="outlined"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(true)}
        data-testid="results-see-why"
        endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
        sx={{
          ...BTN_SX,
          px: 1,
          flexShrink: 0,
          "& .MuiButton-endIcon": { ml: 0.25 },
        }}
      >
        Why
      </Button>
      <Menu
        anchorEl={anchor.current}
        open={open}
        onClose={() => setOpen(false)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { maxWidth: 380 } } }}
      >
        {why ? (
          <Typography
            typography="s2"
            color="text.secondary"
            data-testid="results-why-fails"
            sx={{ px: 2, py: 1, display: "flex", gap: 0.75 }}
          >
            <Iconify
              icon="mdi:format-list-checks"
              width={16}
              sx={{ color: "amber.600", flexShrink: 0, mt: 0.25 }}
            />
            <span>
              {`${why.text}.`}
              <Typography
                component="span"
                typography="s3"
                color="text.disabled"
                data-testid="results-why-method"
                sx={{ display: "block", mt: 0.25 }}
              >
                {causeMethodLabel(why.cause)}
              </Typography>
            </span>
          </Typography>
        ) : null}
        {why ? <Divider /> : null}
        {items.map((it) => (
          <MenuItem
            key={it.key}
            disabled={it.disabled}
            title={it.reason ?? undefined}
            data-testid={`results-why-${it.key}`}
            onClick={() => run(it.key)}
          >
            <ListItemIcon sx={{ minWidth: 28 }}>
              <Iconify icon={it.icon} width={16} />
            </ListItemIcon>
            <ListItemText
              primary={it.label}
              secondary={it.disabled ? it.reason : null}
              primaryTypographyProps={{ typography: "s1" }}
              secondaryTypographyProps={{ typography: "s3" }}
            />
          </MenuItem>
        ))}
        {/* options-truth L2 G9: the finding's rows go straight to Test a fix
            (the dock opens on exactly those rows). */}
        {why?.fixHref && why.rows?.length ? (
          <MenuItem
            data-testid="results-why-fix"
            onClick={() => {
              setOpen(false);
              navigate(why.fixHref);
            }}
          >
            <ListItemIcon sx={{ minWidth: 28 }}>
              <Iconify icon="mdi:source-branch" width={16} />
            </ListItemIcon>
            <ListItemText
              primary={why.fixLabel}
              secondary="Change something, rerun only these rows, compare"
              primaryTypographyProps={{ typography: "s1" }}
              secondaryTypographyProps={{ typography: "s3" }}
            />
          </MenuItem>
        ) : null}
      </Menu>
    </>
  );
}

SeeWhyButton.propTypes = {
  onWhy: PropTypes.func,
  canWhy: PropTypes.bool,
  worse: PropTypes.number,
  onShowWorse: PropTypes.func,
  noun: PropTypes.string,
  datasetId: PropTypes.string,
  runIds: PropTypes.arrayOf(PropTypes.string),
  baselineId: PropTypes.string,
  focusRunId: PropTypes.string,
  candidateId: PropTypes.string,
  labelOf: PropTypes.func.isRequired,
  winning: PropTypes.object,
  nameOf: PropTypes.func,
};
