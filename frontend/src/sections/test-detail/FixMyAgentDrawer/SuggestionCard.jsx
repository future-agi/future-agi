import React, { useState } from "react";
import { Box, Button, Chip, Typography, useTheme } from "@mui/material";
import SvgColor from "../../../components/svg-color";
import PropTypes from "prop-types";
import { ShowComponent } from "../../../components/show";
import ViewIssueBox from "./ViewIssueBox";
import TruncateText from "../../../components/TruncateText/TruncateText";
import CustomTooltip from "src/components/tooltip";

const SuggestionCard = ({
  title,
  description,
  isPriority = false,
  breakdown = [],
  callExecutionIds,
  isSelected,
  toggleSelection,
  branchCategory = null,
}) => {
  const theme = useTheme();
  const [open, setOpen] = useState(false);

  return (
    <>
      <CustomTooltip
        show={callExecutionIds?.length > 0}
        title="Click on this suggestions to view its associated calls"
        placement="bottom"
        arrow
        size="small"
        type="black"
      >
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 1,
            padding: 2,
            border: "1px solid",
            background: isSelected
              ? theme.palette.action.hover
              : theme.palette.background.paper,
            borderColor: isSelected
              ? theme.palette.primary.main
              : theme.palette.divider,
            borderRadius: 0.5,
            width: "100%",
            cursor: "pointer",
          }}
          onClick={() => toggleSelection()}
        >
          <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
            <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
              <Typography variant="s2_1" fontWeight="fontWeightMedium">
                {title}
              </Typography>
              <ShowComponent condition={isPriority}>
                <Chip
                  label="High Priority"
                  size="small"
                  sx={{
                    borderRadius: 0.5,
                    backgroundColor: theme.palette.red.o10,
                    color: theme.palette.red[700],
                    fontWeight: "fontWeightMedium",
                    ":hover": {
                      backgroundColor: theme.palette.red.o10,
                      color: theme.palette.red[700],
                    },
                  }}
                />
              </ShowComponent>
            </Box>
            <Typography variant="s2" color="text.secondary">
              <TruncateText>{description}</TruncateText>
            </Typography>
          </Box>
          <ShowComponent
            condition={branchCategory && branchCategory !== "Unknown"}
          >
            <Box
              sx={{
                display: "flex",
                flexDirection: "row",
                alignItems: "center",
                gap: 1,
              }}
            >
              <SvgColor src={"/assets/icons/ic_branch.svg"} />
              <Typography typography={"s2"} fontWeight={"fontWeightMedium"}>
                {`Branch Category: ${branchCategory}`}
              </Typography>
            </Box>
          </ShowComponent>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 1,
            }}
          >
            <ShowComponent
              condition={
                callExecutionIds !== undefined && callExecutionIds !== null
              }
            >
              <Box
                sx={{
                  backgroundColor: isSelected
                    ? theme.palette.action.hover
                    : theme.palette.background.neutral,
                  borderRadius: 0.5,
                  display: "flex",
                  alignItems: "center",
                  padding: 0.5,
                  gap: 0.5,
                  border: "1px solid",
                  borderColor: isSelected
                    ? theme.palette.primary.main
                    : theme.palette.background.neutral,
                }}
              >
                <SvgColor
                  src="/assets/icons/ic_phone_call.svg"
                  sx={{
                    width: 16,
                    height: 16,
                    color: isSelected ? theme.palette.primary.main : undefined,
                  }}
                />
                <Typography
                  variant="s3"
                  fontWeight="fontWeightMedium"
                  sx={{
                    color: isSelected ? theme.palette.primary.main : undefined,
                  }}
                >
                  Calls Affected ({callExecutionIds?.length})
                </Typography>
              </Box>
            </ShowComponent>

            <Button
              variant="outlined"
              size="small"
              startIcon={<SvgColor src="/assets/icons/custom/eye.svg" />}
              onClick={(e) => {
                e.stopPropagation();
                setOpen(true);
              }}
            >
              View Issue
            </Button>
          </Box>
        </Box>
      </CustomTooltip>

      <ViewIssueBox
        open={open}
        onClose={() => setOpen(false)}
        breakdown={breakdown}
        description={description}
      />
    </>
  );
};

SuggestionCard.propTypes = {
  title: PropTypes.string,
  description: PropTypes.string,
  isPriority: PropTypes.bool,
  breakdown: PropTypes.arrayOf(PropTypes.string),
  callExecutionIds: PropTypes.arrayOf(PropTypes.string),
  isSelected: PropTypes.bool,
  toggleSelection: PropTypes.func,
  branchCategory: PropTypes.string,
};

export default SuggestionCard;
