import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  List,
  ListItem,
  Typography,
  Divider,
  ListItemIcon,
  Link,
} from "@mui/material";
import SvgColor from "src/components/svg-color";
import CustomTooltip from "src/components/tooltip";
import { Evals_Docs_mapping } from "src/sections/evals/constant";
const TooltipForEvals = ({
  children,
  selectedEvalItem,
  heading = "Evaluations",
}) => {
  if (!selectedEvalItem) return null;

  const description = selectedEvalItem?.description ?? "";

  const tooltipContent = Array.isArray(selectedEvalItem) ? (
    <Box sx={{ p: 1.5, maxHeight: 420, overflowY: "auto" }}>
      <Typography sx={{ fontWeight: 600, fontSize: "13px", marginBottom: 0.5 }}>
        {heading}
      </Typography>
      <List
        component="ul"
        sx={{
          listStyleType: "disc",
          pl: 2,
          m: 0,
          fontWeight: 400,
          fontSize: "12px",
        }}
      >
        {selectedEvalItem.map((item, index) => (
          <ListItem
            key={item || index}
            component="li"
            sx={{
              fontSize: "13px",
              py: 0.25,
              display: "list-item",
              pl: 0,
            }}
          >
            {item}
          </ListItem>
        ))}
      </List>
    </Box>
  ) : (
    <Box sx={{ p: 1.5, minWidth: 320, width: "auto" }}>
      <Box
        sx={{
          display: "flex",
          flexDirection: "row",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Typography
          sx={{
            fontWeight: 600,
            fontSize: "13px",
            textTransform: "capitalize",
          }}
        >
          {selectedEvalItem?.name}
        </Typography>
        {Evals_Docs_mapping[selectedEvalItem?.name] && (
          <Link
            href="https://docs.futureagi.com/docs/evaluation/builtin"
            target="_blank"
            sx={{
              fontSize: "12px",
              color: "primary.main",
              textDecoration: "underline",
              "&:hover": {
                fontWeight: "500",
              },
            }}
          >
            View Docs
          </Link>
        )}
      </Box>

      <Typography
        typography={"s3"}
        fontWeight={"fontWeightSemiBold"}
        color={"text.primary"}
      >
        Description
      </Typography>
      <Typography
        sx={{
          fontWeight: "fontWeightRegular",
          typography: "s3",
          mb: 1,
          color: "text.primary",
        }}
      >
        {description}
      </Typography>

      <Divider
        sx={{
          my: 1,
        }}
      />

      <Box sx={{ gap: 0.25 }}>
        <Typography sx={{ fontWeight: 600, fontSize: "12px", mb: 0.25 }}>
          Inputs for evaluations
        </Typography>

        <List
          component="ul"
          sx={{
            fontSize: "12px",
            m: 0,
            display: "flex",
            flexDirection: "row-reverse",
            alignItems: "center",
            padding: 0,
            justifyContent: "flex-end",
            gap: 2,
            rowGap: 1,
            flexWrap: "wrap",
          }}
        >
          {/* Knowledge Base */}
          <ListItem
            component="li"
            disablePadding
            sx={{
              display: "flex",
              alignItems: "center",
              width: "fit-content",
            }}
          >
            <ListItemIcon sx={{ minWidth: 20, mr: 0.5 }}>
              <SvgColor
                sx={{ height: 16, width: 16 }}
                src="/assets/icons/navbar/ic_knowledge_base.svg"
              />
            </ListItemIcon>
            <Typography sx={{ fontSize: "12px", flexShrink: 0 }}>
              Knowledge Base
            </Typography>
          </ListItem>

          {/* FUTURE_EVALS Tag */}
          {selectedEvalItem?.tags?.includes("FUTURE_EVALS") && (
            <ListItem
              component="li"
              disablePadding
              sx={{
                display: "flex",
                flexDirection: "row",
                alignItems: "center",
                width: "fit-content",
              }}
            >
              <ListItemIcon sx={{ minWidth: 20, mr: 0.5 }}>
                <SvgColor
                  sx={{ height: 16, width: 16 }}
                  src="/assets/ic_llm_future_agi.svg"
                />
              </ListItemIcon>
              <Typography sx={{ fontSize: "12px", flexShrink: 0 }}>
                Language Models{" "}
                <Typography
                  sx={{ color: "status.danger.fg" }}
                  component={"span"}
                >
                  *
                </Typography>
              </Typography>
            </ListItem>
          )}
        </List>
      </Box>

      <Divider sx={{ my: 1 }} />

      <Box>
        <Typography
          sx={{ fontWeight: 600, fontSize: "12px", mb: 0.5, paddingTop: 0.125 }}
        >
          Inputs for column mapping
        </Typography>
        <List
          component="ul"
          sx={{
            fontSize: "12px",
            m: 0,
            display: "flex",
            flexDirection: "row",
            alignItems: "flex-end",
            width: "fit-content",
            gap: 2,
            rowGap: 1,
            flexWrap: "wrap",
            padding: 0,
          }}
        >
          {selectedEvalItem?.requiredKeys?.map((key, index) => (
            <ListItem
              key={key || index}
              component="li"
              sx={{
                display: "flex",
                flexDirection: "row",
                alignItems: "center",
                pl: 0,
                py: 0.125,
                flexShrink: 0,
                width: "fit-content",
              }}
            >
              <ListItemIcon sx={{ minWidth: 20, mr: 0.5 }}>
                <SvgColor
                  sx={{
                    height: "16px",
                    width: "16px",
                    color: "text.primary",
                  }}
                  src={"/assets/icons/ic_column_mapping.svg"}
                />
              </ListItemIcon>
              <Typography
                sx={{
                  fontWeight: "fontWeightRegular",
                  typography: "s3",
                  color: "text.primary",
                }}
              >
                {key}{" "}
                <Typography
                  component={"span"}
                  sx={{
                    color: "status.danger.fg",
                    fontWeight: "fontWeightRegular",
                    typography: "s2_1",
                  }}
                >
                  *
                </Typography>
              </Typography>
            </ListItem>
          ))}
          {selectedEvalItem?.optionalKeys?.map((key, index) => (
            <ListItem
              key={key || index}
              component="li"
              sx={{
                display: "flex",
                flexDirection: "row",
                alignItems: "center",
                pl: 0,
                py: 0.125,
                flexShrink: 0,
                width: "fit-content",
              }}
            >
              <ListItemIcon sx={{ minWidth: 20, mr: 0.5 }}>
                <SvgColor
                  sx={{
                    height: "16px",
                    width: "16px",
                    color: "text.primary",
                  }}
                  src={"/assets/icons/ic_column_mapping.svg"}
                />
              </ListItemIcon>
              <Typography
                sx={{
                  fontWeight: "fontWeightRegular",
                  typography: "s3",
                  color: "text.primary",
                }}
              >
                {key}
              </Typography>
            </ListItem>
          ))}
        </List>
      </Box>
      <Typography
        sx={{
          fontSize: "12px",
          fontWeight: "fontWeightRegular",
          color: "text.primary",
          mt: "10px",
        }}
        fontWeight={""}
      >
        Note: Inputs marked with (
        <Typography
          component={"span"}
          sx={{
            color: "red500",
            fontSize: "12px",
            fontWeight: "fontWeightRegular",
          }}
        >
          *
        </Typography>
        ) are required fields
      </Typography>
    </Box>
  );

  return (
    <CustomTooltip
      show={Boolean(selectedEvalItem)}
      title={tooltipContent}
      arrow={false}
      placement="bottom"
      sx={{
        "& .MuiTooltip-tooltip": {
          padding: "0",
          maxWidth: description.length > 100 ? "60vw" : "400px",
          width: "auto",
          whiteSpace: "normal",
          wordBreak: "break-word",
        },
      }}
      componentsProps={{
        tooltip: {
          sx: {
            bgcolor: "background.paper",
            color: "text.primary",
            // eslint-disable-next-line no-restricted-syntax -- ds: overlay shadow
            boxShadow: 3,
            borderRadius: "8px",
          },
        },
      }}
      slotProps={{
        popper: {
          modifiers: [
            {
              name: "offset",
              options: {
                offset: [0, -12],
              },
            },
          ],
        },
      }}
    >
      {children}
    </CustomTooltip>
  );
};

export default TooltipForEvals;
TooltipForEvals.displayName = "TooltipForEvals";

TooltipForEvals.propTypes = {
  children: PropTypes.node.isRequired,
  selectedEvalItem: PropTypes.oneOfType([
    PropTypes.array,
    PropTypes.object,
    PropTypes.oneOf([null]),
  ]),
  heading: PropTypes.string,
};
