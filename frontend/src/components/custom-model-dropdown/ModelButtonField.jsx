import React from "react";
import CustomTooltip from "src/components/tooltip";
import ModelHoverState from "./ModelHoverState";
import { Box, Button, IconButton, Typography } from "@mui/material";
import Image from "../image";
import Iconify from "../iconify";
import PropTypes from "prop-types";
import SvgColor from "../svg-color";
import { LOGO_WITH_BLACK_BACKGROUND } from "./common";

const ModelButtonField = ({
  isModalContainer,
  disabledHover,
  openDropdown,
  value,
  modelDetail,
  hoverPlacement,
  disabledClick,
  setOpenDropdown,
  buttonIcon,
  buttonTitle,
  onClick,
}) => {
  return (
    <Box
      sx={{
        height: "24px",
        display: "flex",
        flexDirection: "row",
        justifyContent: "space-between",
        gap: 1,
      }}
      onClick={() => {
        if (!disabledClick) {
          setOpenDropdown(true);
          onClick?.();
        }
      }}
    >
      <CustomTooltip
        card
        show={
          !disabledHover && !openDropdown && (value || modelDetail?.model_name)
        }
        placement={hoverPlacement}
        title={<ModelHoverState modelName={value || modelDetail?.model_name} />}
        enterDelay={300}
        enterNextDelay={300}
        // slotProps={{
        //   popper: {
        //     modifiers: [
        //       {
        //         name: "offset",
        //         options: {
        //           offset: [0, -10],
        //         },
        //       },
        //     ],
        //   },
        // }}
        sx={{
          "& .MuiTooltip-tooltip": {
            padding: 0,
            width: "400px",
          },
        }}
      >
        {isModalContainer ? (
          <Box
            sx={{
              display: "flex",
              flexDirection: "row",
              alignItems: "center",
              gap: 1,
              padding: 0.5,
              cursor: "pointer",
            }}
          >
            {value && modelDetail?.logoUrl ? (
              <Image
                ratio="1/1"
                src={modelDetail?.logoUrl}
                alt={modelDetail?.model_name}
                flexShrink={0}
                disableThemeFilter={
                  !LOGO_WITH_BLACK_BACKGROUND.includes(
                    modelDetail?.providers?.toLowerCase(),
                  )
                }
                style={{
                  width: "16px",
                  height: "16px",
                }}
              />
            ) : (
              buttonIcon || (
                <Iconify
                  icon="solar:graph-linear"
                  width="16px"
                  height="16px"
                  sx={{
                    cursor: "pointer",
                    color: "text.primary",
                  }}
                />
              )
            )}
            <Typography
              typography="s1"
              fontWeight={"fontWeightMedium"}
              color="text.primary"
              sx={{
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                overflow: "hidden",
                minWidth: "200px",
                textAlign: "left",
              }}
            >
              {typeof value === "string" && value ? value : buttonTitle}
            </Typography>
          </Box>
        ) : (
          <Button
            size="small"
            sx={{
              backgroundColor: "background.paper",
              border: "1px solid",
              borderColor: "divider",
              display: "flex",
              // flexDirection: "row",
              // alignItems: "center",
              // justifyContent: "flex-start",
              cursor: "pointer",
            }}
            startIcon={
              value && modelDetail?.logoUrl ? (
                <Image
                  ratio="1/1"
                  src={modelDetail?.logoUrl}
                  alt={modelDetail?.model_name}
                  flexShrink={0}
                  disableThemeFilter={
                    !LOGO_WITH_BLACK_BACKGROUND.includes(
                      modelDetail?.providers?.toLowerCase(),
                    )
                  }
                  style={{
                    width: "16px",
                    height: "16px",
                  }}
                />
              ) : (
                buttonIcon || (
                  <Iconify
                    icon="solar:graph-linear"
                    width="16px"
                    height="16px"
                    sx={{
                      cursor: "pointer",
                      color: "text.primary",
                    }}
                  />
                )
              )
            }
          >
            <Typography
              typography="s3"
              fontWeight={"fontWeightMedium"}
              color="text.primary"
              sx={{
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                overflow: "hidden",
              }}
            >
              {typeof value === "string" && value ? value : buttonTitle}
            </Typography>
          </Button>
        )}
      </CustomTooltip>

      {isModalContainer && (
        <>
          {openDropdown ? (
            <IconButton
              sx={{
                transition: "transform 0.2s ease",
                transform: openDropdown ? "rotate(180deg)" : "rotate(0deg)",
              }}
            >
              <SvgColor
                src="/assets/icons/custom/lucide--chevron-down.svg"
                sx={{ width: "16px", height: "16px" }}
              />
            </IconButton>
          ) : (
            <IconButton
              onClick={() => {
                if (!disabledClick) {
                  setOpenDropdown(true);
                  onClick?.();
                }
              }}
            >
              <SvgColor
                src="/assets/icons/custom/lucide--chevron-down.svg"
                sx={{ width: "16px", height: "16px" }}
              />
            </IconButton>
          )}
        </>
      )}
    </Box>
  );
};

export default ModelButtonField;

ModelButtonField.propTypes = {
  isModalContainer: PropTypes.bool,
  disabledHover: PropTypes.bool,
  openDropdown: PropTypes.bool,
  value: PropTypes.string,
  modelDetail: PropTypes.object,
  hoverPlacement: PropTypes.string,
  disabledClick: PropTypes.bool,
  setOpenDropdown: PropTypes.func.isRequired,
  buttonIcon: PropTypes.node,
  buttonTitle: PropTypes.string.isRequired,
  onClick: PropTypes.func,
};
