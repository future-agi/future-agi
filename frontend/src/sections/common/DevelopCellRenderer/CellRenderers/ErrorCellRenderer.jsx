/* eslint-disable react/prop-types */
import React from "react";
import PropTypes from "prop-types";
import { Box, Button, IconButton, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { tooltipSlotProp } from "./cellRendererHelper";
import { ORIGIN_OF_COLUMNS } from "src/utils/constants";
import SvgColor from "src/components/svg-color";
import { ShowComponent } from "src/components/show";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { useNavigate } from "react-router-dom";

// Error codes emitted by backend's usage pre-check (see CheckResult.error_code


const USAGE_LIMIT_CTA = {
  FREE_TIER_LIMIT: "Upgrade for more usage",
  RATE_LIMITED: "Upgrade to raise rate limit",
  ENTITLEMENT_LIMIT: "Upgrade to increase limits",
  ENTITLEMENT_DENIED: "Upgrade plan to unlock",
  BUDGET_PAUSED: "Increase budget to resume",
  PAYMENT_REQUIRED: "Add payment method to continue",
  ACCOUNT_SUSPENDED: "Clear dues to reactivate",
  LICENSE_EXPIRED: "Renew license to continue",
  LICENSE_FEATURE_DENIED: "Contact sales to unlock",
  USAGE_LIMIT_EXCEEDED: "Upgrade for more usage",
};

const USAGE_LIMIT_ERROR_CODES = new Set(Object.keys(USAGE_LIMIT_CTA));

const parseValueInfos = (raw) => {
  if (!raw) return null;
  if (typeof raw === "string") {
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  }
  return raw;
};

const ErrorCellRenderer = ({
  valueReason,
  formattedValueReason,
  props,
  onRerun,
}) => {
  const navigate = useNavigate();
  const canRerunAtCell = props?.originOfColumn === ORIGIN_OF_COLUMNS.EXPERIMENT;
  const showRerun = ["experiment", "evaluation"].includes(
    props?.colDef?.col?.originType,
  );

  const columnId = props?.colDef?.col?.id;

  const rowId = props?.data?.rowId;

  const cellData = props?.data?.[props?.column?.colId];
  const valueInfos = parseValueInfos(
    cellData?.value_infos ?? cellData?.valueInfos,
  );
  const errorCode = valueInfos?.error_code;
  const isUsageLimit = USAGE_LIMIT_ERROR_CODES.has(errorCode);
  const upgradeCta = valueInfos?.upgrade_cta || valueInfos?.upgradeCta;
  const ctaText =
    USAGE_LIMIT_CTA[errorCode] || upgradeCta?.text || "Upgrade for more usage";
  const limitMessage =
    valueInfos?.reason ||
    (typeof cellData?.value === "string" ? cellData.value : "Limit reached");

  const handleUpgrade = (e) => {
    e.stopPropagation();
    navigate(paths.dashboard.settings.pricing);
  };

  if (isUsageLimit) {
    return (
      <CustomTooltip
        show={true}
        title={limitMessage}
        enterDelay={300}
        arrow
        type="black"
        size="small"
        slotProps={tooltipSlotProp}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 0.5,
            height: "100%",
            width: "100%",
            padding: "4px 8px",
          }}
        >
          <Iconify
            icon="mdi:lock-outline"
            width={16}
            sx={{ flexShrink: 0, color: "text.primary" }}
          />
          <Typography
           typography="s2"

          >
            {ctaText}
          </Typography>
          <Button
            size="small"
            variant="text"
            onClick={handleUpgrade}
            sx={{
              textTransform: "none",
              minWidth: 0,
              padding: 0,
           
              color: "primary.main",
              textDecoration: "underline",
              whiteSpace: "nowrap",
              "&:hover": {
                backgroundColor: "transparent",
                textDecoration: "underline",
              },
            }}
          >
            Upgrade
          </Button>
        </Box>
      </CustomTooltip>
    );
  }

  if (canRerunAtCell) {
    return (
      <Box
        sx={{
          width: "100%",
          height: "100%",
          border: "1px solid",
          borderColor: "red.100",
          boxSizing: "border-box",
        }}
      >
        <Box
          sx={{
            display: "flex",
            justifyContent: showRerun ? "space-between" : "center",
            alignItems: "center",
            height: "100%",
            padding: 1,
            gap: 1,
          }}
        >
          <CustomTooltip
            show={true}
            title={formattedValueReason()}
            enterDelay={500}
            arrow
            type="black"
            size="small"
            slotProps={tooltipSlotProp}
          >
            <Typography
              variant="subtitle2"
              color="error.main"
              fontWeight={"fontWeightRegular"}
              sx={{
                flex: 1,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                overflow: "auto",
                maxHeight: "100%",
              }}
            >
              {formattedValueReason()}
            </Typography>
          </CustomTooltip>
          <ShowComponent condition={showRerun}>
            <IconButton
              sx={{
                width: "36px",
                height: "28px",
                border: "1px solid",
                color: "primary.main",
                borderRadius: 1,
              }}
              onClick={(e) => {
                window["__reRunClick"] = true;
                e.stopPropagation();
                onRerun?.({ columnId, rowId });
              }}
            >
              <SvgColor src="/assets/icons/navbar/ic_evaluate.svg" />
            </IconButton>
          </ShowComponent>
        </Box>
      </Box>
    );
  }

  return (
    <CustomTooltip
      show={Boolean(valueReason?.length)}
      title={formattedValueReason()}
      enterDelay={500}
      enterNextDelay={500}
      leaveDelay={100}
      arrow
      type="info"
      slotProps={tooltipSlotProp}
    >
      <Box
        sx={{
          color: "error.main",
          opacity: 1,
          flex: 1,
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
        }}
      >
        <Typography variant="body2" align="center">
          Error
        </Typography>
      </Box>
    </CustomTooltip>
  );
};

ErrorCellRenderer.propTypes = {
  valueReason: PropTypes.any,
  formattedValueReason: PropTypes.func.isRequired,
  props: PropTypes.object,
  onRerun: PropTypes.func,
};

export default React.memo(ErrorCellRenderer);
