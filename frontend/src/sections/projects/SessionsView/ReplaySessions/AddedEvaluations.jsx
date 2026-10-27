import React, { useState } from "react";
import { StyledBox } from "./CreateScenariosForm";
import { Box, Collapse, IconButton, Stack, Typography } from "@mui/material";
import SvgColor from "../../../../components/svg-color/svg-color";
import ChipContainer from "../../../../components/ChipContainer/ChipContainer";
import PropTypes from "prop-types";
import { CustomAlert } from "../../../../components/CustomAlert/CustomAlert";

export default function AddedEvaluations({
  evaluationsAdded,
  setEvaluationsAdded,
  // onAddMore,
}) {
  const [collapsed, setCollapsed] = useState(false);

  const handleRemove = (id) => {
    setEvaluationsAdded(evaluationsAdded.filter((e) => e.id !== id));
  };

  return (
    <StyledBox
      sx={{
        width: "100%",
      }}
    >
      <Stack
        sx={{
          mb: 2,
        }}
      >
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
        >
          <Typography
            typography={"m3"}
            color={"text.primary"}
            fontWeight={"fontWeightMedium"}
          >
            Evaluations added{" "}
          </Typography>
          <IconButton
            onClick={() => setCollapsed(!collapsed)}
            sx={{
              color: "text.primary",
              p: 0,
            }}
            size="small"
          >
            <SvgColor
              sx={{
                height: "24px",
                width: "24px",
                transform: `${!collapsed ? "rotate(180deg)" : "rotate(0deg)"}`,
                transition: "transform 0.3s ease",
                transformOrigin: "center",
              }}
              src="/assets/icons/custom/lucide--chevron-down.svg"
            />
          </IconButton>
        </Stack>
        <Collapse in={!collapsed}>
          <Typography
            typography={"s2_1"}
            color={"text.secondary"}
            sx={{
              mr: 5,
              maxWidth: "97%",
            }}
          >
            Evaluations added while creating your trace projects will be visible
            here
          </Typography>
        </Collapse>
      </Stack>
      <Collapse in={!collapsed}>
        {evaluationsAdded?.length > 0 && (
          <ChipContainer
            chips={evaluationsAdded}
            idKey="id"
            labelKey="name"
            onRemove={handleRemove}
            canRemoveKey="fromProject"
            reverse={true}
          />
        )}
        {evaluationsAdded?.length === 0 && (
          <Stack gap={2}>
            <Box
              sx={{
                height: "110px",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                bgcolor: "background.paper",
                borderRadius: 0.5,
              }}
            >
              <Typography
                typography={"s1"}
                fontWeight={"fontWeightRegular"}
                color={"text.primary"}
              >
                No evaluations has been added
              </Typography>
            </Box>
            <CustomAlert
              variant="info"
              message="You can add additional evaluations after the replay."
              type="ghost"
            />
          </Stack>
        )}

        {/* <Box sx={{ display: "flex", mt: 2 }}>
          <Button
            sx={{ ml: "auto", backgroundColor: "background.paper" }}
            variant="outlined"
            size="small"
            startIcon={
              <SvgColor
                sx={{ height: 16, width: 16, color: "text.primary" }}
                src="/assets/icons/ic_add.svg"
              />
            }
            onClick={onAddMore}
          >
            Add More Evaluations
          </Button>
        </Box> */}
      </Collapse>
    </StyledBox>
  );
}

AddedEvaluations.propTypes = {
  evaluationsAdded: PropTypes.array.isRequired,
  setEvaluationsAdded: PropTypes.func.isRequired,
  // onAddMore: PropTypes.func.isRequired,
};
