import React from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import CustomTooltip from "src/components/tooltip";
import VersionTagChip from "./VersionTagChip";

/** `v6` in the grey version-tag style of Prompts' VersionCard. */
export const VersionNumber = ({ number }) => (
  <Typography
    component="span"
    typography="s2"
    fontWeight="fontWeightMedium"
    sx={{
      bgcolor: "action.hover",
      borderRadius: "2px",
      color: "text.primary",
      px: 0.75,
      pt: 0.25,
      pb: 0.25,
      lineHeight: "16px",
      fontVariantNumeric: "tabular-nums",
      flexShrink: 0,
    }}
  >
    v{number}
  </Typography>
);

VersionNumber.propTypes = { number: PropTypes.number };

const staleText = (staleInfo) => {
  if (!staleInfo?.latest) return null;
  const changes = [
    staleInfo.added ? `${staleInfo.added} added` : null,
    staleInfo.modified ? `${staleInfo.modified} modified` : null,
    staleInfo.deleted ? `${staleInfo.deleted} deleted` : null,
  ].filter(Boolean);
  return `Latest is v${staleInfo.latest}${changes.length ? ` · ${changes.join(", ")} since` : ""}`;
};

/**
 * VersionChip — the dataset version a view or run uses (PRODUCT-SPEC §4,
 * C15): `v6 · latest`, `v5 · golden-v3`, `v7 · latest · golden-v4`, with
 * an amber dot when it is not the latest version.
 *
 * Props (frozen):
 * @param {{number: number, tag?: string|null, is_latest?: boolean}} props.version
 * @param {boolean} [props.asOf]  the page is viewing this past version
 *   (`Viewing v5`, amber outline)
 * @param {{latest: number, added?: number, modified?: number,
 *   deleted?: number}} [props.staleInfo]  what changed since (tooltip)
 * @param {Function} [props.onClick]  makes the chip a button
 * @param {React.ReactNode} [props.tooltip]  R3 (P2, DL#33; additive): the
 *   chip's tooltip (default: what changed since, when not latest)
 * @param {boolean} [props.old]  R3: label `(old)` after the number
 */
const VersionChip = ({
  version,
  asOf = false,
  staleInfo,
  onClick,
  tooltip: tooltipProp,
  old = false,
}) => {
  if (!version || version.number == null) return null;
  const notLatest = version.is_latest === false;
  const tooltip =
    tooltipProp ??
    (notLatest ? staleText(staleInfo) ?? "Not the latest version" : null);

  const content = (
    <Stack direction="row" alignItems="center" gap={0.75} sx={{ minWidth: 0 }}>
      {asOf && (
        <Typography typography="s2" color="text.secondary" noWrap>
          Viewing
        </Typography>
      )}
      <VersionNumber number={version.number} />
      {old && (
        <Typography typography="s2" color="text.secondary" noWrap>
          (old)
        </Typography>
      )}
      {version.is_latest && (
        <Typography typography="s2" color="text.secondary" noWrap>
          latest
        </Typography>
      )}
      {version.tag && <VersionTagChip name={version.tag} size="small" />}
      {notLatest && (
        <Box
          aria-label="Not the latest version"
          sx={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            bgcolor: "amber.500",
            flexShrink: 0,
          }}
        />
      )}
    </Stack>
  );

  const frame = {
    display: "inline-flex",
    alignItems: "center",
    height: 28,
    px: 1,
    border: "1px solid",
    borderColor: asOf ? "amber.500" : "divider",
    borderRadius: 0.5,
    bgcolor: (t) =>
      asOf ? alpha(t.palette.amber[500], 0.12) : t.palette.background.paper,
    maxWidth: "100%",
  };

  return (
    <CustomTooltip
      show={!!tooltip}
      size="small"
      placement="bottom"
      arrow
      title={tooltip ?? ""}
    >
      {onClick ? (
        <ButtonBase
          onClick={onClick}
          aria-label={`Dataset version v${version.number}`}
          sx={{
            ...frame,
            "&:hover": { bgcolor: "action.hover", borderColor: "border.hover" },
          }}
        >
          {content}
        </ButtonBase>
      ) : (
        <Box component="span" sx={frame}>
          {content}
        </Box>
      )}
    </CustomTooltip>
  );
};

VersionChip.propTypes = {
  version: PropTypes.shape({
    number: PropTypes.number,
    tag: PropTypes.string,
    is_latest: PropTypes.bool,
  }),
  asOf: PropTypes.bool,
  staleInfo: PropTypes.shape({
    latest: PropTypes.number,
    added: PropTypes.number,
    modified: PropTypes.number,
    deleted: PropTypes.number,
  }),
  onClick: PropTypes.func,
  tooltip: PropTypes.node,
  old: PropTypes.bool,
};

export default VersionChip;
