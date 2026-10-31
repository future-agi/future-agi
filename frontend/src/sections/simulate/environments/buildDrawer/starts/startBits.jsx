import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import {
  Box,
  Button,
  ButtonBase,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { copyToClipboard } from "src/utils/utils";
import CopyField from "../../components/CopyField";
import { HintLine, MONO } from "../forms/formBits";
import {
  BTN_SX,
  PANEL_SX,
  T12,
  T13,
  T14,
  chipSx,
  focusRing,
} from "../drawerStyles";

/*
  The pieces the starts share that the connect forms don't have, at the
  drawer's sizes.
*/

/** A quiet outlined tag: a world's agent type, "Any agent". */
export function QuietTag({ children }) {
  return (
    <Box
      component="span"
      sx={{
        ...T12,
        fontWeight: 500,
        color: "text.secondary",
        px: 0.75,
        height: 20,
        display: "inline-flex",
        alignItems: "center",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
        flexShrink: 0,
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </Box>
  );
}
QuietTag.propTypes = { children: PropTypes.node };

export function SectionHead({ title, subtitle, action }) {
  return (
    <Stack
      direction="row"
      alignItems="flex-end"
      justifyContent="space-between"
      spacing={1.5}
      sx={{ mb: 1.75 }}
    >
      <Box minWidth={0}>
        <Typography component="h2" sx={{ ...T14, fontWeight: 600 }}>
          {title}
        </Typography>
        {subtitle && (
          <Typography sx={{ ...T13, color: "text.secondary", mt: 0.25 }}>
            {subtitle}
          </Typography>
        )}
      </Box>
      {action && <Box sx={{ flexShrink: 0 }}>{action}</Box>}
    </Stack>
  );
}
SectionHead.propTypes = {
  title: PropTypes.node,
  subtitle: PropTypes.node,
  action: PropTypes.node,
};

/** A bordered card with a titled head, as Vel's SectionCard at drawer sizes. */
export function StartCard({ title, subtitle, action, children, sx }) {
  return (
    <Box sx={{ ...PANEL_SX, overflow: "hidden", ...sx }}>
      {(title || action) && (
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          spacing={2}
          sx={{
            px: 2,
            py: 1.25,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box minWidth={0}>
            <Typography component="div" sx={{ ...T13, fontWeight: 600 }}>
              {title}
            </Typography>
            {subtitle && (
              <Typography
                component="div"
                sx={{ ...T12, color: "text.secondary" }}
              >
                {subtitle}
              </Typography>
            )}
          </Box>
          {action}
        </Stack>
      )}
      {children}
    </Box>
  );
}
StartCard.propTypes = {
  title: PropTypes.node,
  subtitle: PropTypes.node,
  action: PropTypes.node,
  children: PropTypes.node,
  sx: PropTypes.object,
};

/** An icon and a line, with the rest in a tooltip. */
export function IconLine({ icon, text, hint }) {
  return (
    <Stack direction="row" spacing={1.25} alignItems="flex-start">
      <Iconify
        icon={icon}
        width={16}
        sx={{ color: "text.secondary", flexShrink: 0, mt: "2px" }}
      />
      <Typography component="div" sx={{ ...T13, color: "text.secondary" }}>
        {hint ? <HintLine text={text} hint={hint} /> : text}
      </Typography>
    </Stack>
  );
}
IconLine.propTypes = {
  icon: PropTypes.string.isRequired,
  text: PropTypes.node,
  hint: PropTypes.string,
};

/**
 * The start's own bottom row: why it can't go yet, then its action. It
 * sticks to the drawer's bottom edge.
 */
export function StartFooter({
  hint,
  disabled,
  onClick,
  label = "Build environment",
  icon = "solar:magic-stick-3-linear",
  sticky = true,
  sx,
}) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      justifyContent="space-between"
      spacing={2}
      sx={{
        px: 2.5,
        py: 1.5,
        borderTop: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
        ...(sticky && { position: "sticky", bottom: 0, zIndex: 2 }),
        ...sx,
      }}
    >
      {disabled && hint ? (
        <Typography sx={{ ...T12, color: "text.secondary" }}>{hint}</Typography>
      ) : (
        <Box />
      )}
      <Button
        variant="contained"
        color="primary"
        disabled={disabled}
        onClick={onClick}
        startIcon={<Iconify icon={icon} width={14} />}
        endIcon={<Iconify icon="solar:alt-arrow-right-linear" width={14} />}
        sx={{ ...BTN_SX, height: 32, px: 1.5, ...T13, fontWeight: 500 }}
      >
        {label}
      </Button>
    </Stack>
  );
}
StartFooter.propTypes = {
  hint: PropTypes.node,
  disabled: PropTypes.bool,
  onClick: PropTypes.func.isRequired,
  label: PropTypes.node,
  icon: PropTypes.string,
  sticky: PropTypes.bool,
  sx: PropTypes.object,
};

/** A pickable chip that can be inert with its own quiet tag. */
export function TagChip({ icon, label, tag, on, disabled, onClick }) {
  return (
    <ButtonBase
      aria-pressed={on}
      disabled={disabled}
      onClick={onClick}
      sx={{ ...chipSx(on), "&.Mui-focusVisible": focusRing }}
    >
      {icon && <Iconify icon={icon} width={14} />}
      <Box
        component="span"
        sx={{ color: disabled ? "text.disabled" : "inherit" }}
      >
        {label}
      </Box>
      {tag && <QuietTag>{tag}</QuietTag>}
    </ButtonBase>
  );
}
TagChip.propTypes = {
  icon: PropTypes.string,
  label: PropTypes.node,
  tag: PropTypes.node,
  on: PropTypes.bool,
  disabled: PropTypes.bool,
  onClick: PropTypes.func,
};

const COPIED_RESET_MS = 1500;

/** Code in a muted box with a copy button at its top right. */
export function CodeBlock({ code, wrap = false }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);
  const copy = async () => {
    const ok = await copyToClipboard(code);
    if (!ok) return;
    setCopied(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), COPIED_RESET_MS);
  };
  const label = copied ? "Copied" : "Copy code";
  return (
    <Box
      sx={{
        position: "relative",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "6px",
        bgcolor: "action.hover",
      }}
    >
      <CustomTooltip show title={copied ? "Copied" : "Copy"} arrow size="small">
        <IconButton
          size="small"
          onClick={copy}
          aria-label={label}
          sx={{ position: "absolute", top: 6, right: 6 }}
        >
          <Iconify
            icon={copied ? "solar:check-read-linear" : "solar:copy-linear"}
            width={16}
          />
        </IconButton>
      </CustomTooltip>
      <Box
        component="pre"
        sx={{
          m: 0,
          p: 1.5,
          pr: 5,
          fontFamily: MONO,
          fontSize: 12,
          lineHeight: "18px",
          color: "text.primary",
          overflowX: "auto",
          whiteSpace: wrap ? "pre-wrap" : "pre",
        }}
      >
        {code}
      </Box>
    </Box>
  );
}
CodeBlock.propTypes = {
  code: PropTypes.string.isRequired,
  wrap: PropTypes.bool,
};

/**
 * Numbered terminal steps: a title, a line on what it does and the command
 * to copy, joined by a rule.
 */
export function LocalSteps({ steps }) {
  return (
    <Stack spacing={0}>
      {steps.map((s, i) => (
        <Stack key={s.title} direction="row" spacing={1.75}>
          <Stack alignItems="center" sx={{ flexShrink: 0 }}>
            <Box
              sx={{
                width: 24,
                height: 24,
                borderRadius: "50%",
                display: "grid",
                placeItems: "center",
                border: "1px solid",
                borderColor: "divider",
                ...T12,
                fontWeight: 600,
                color: "text.secondary",
              }}
            >
              {i + 1}
            </Box>
            {i < steps.length - 1 && (
              <Box
                sx={{
                  flex: 1,
                  width: "1px",
                  bgcolor: "divider",
                  my: 0.75,
                  minHeight: 24,
                }}
              />
            )}
          </Stack>
          <Box
            sx={{ flex: 1, minWidth: 0, pb: i < steps.length - 1 ? 2.25 : 0 }}
          >
            <Typography sx={{ ...T13, fontWeight: 600 }}>{s.title}</Typography>
            <Typography
              component="div"
              sx={{ ...T12, color: "text.secondary", mb: 1 }}
            >
              {s.hint ? <HintLine text={s.body} hint={s.hint} /> : s.body}
            </Typography>
            {s.extra}
            <CopyField value={s.cmd} wrap />
          </Box>
        </Stack>
      ))}
    </Stack>
  );
}
LocalSteps.propTypes = {
  // [{ title, body, hint?, cmd, extra? }]; `extra` sits above the command.
  steps: PropTypes.arrayOf(PropTypes.object).isRequired,
};
