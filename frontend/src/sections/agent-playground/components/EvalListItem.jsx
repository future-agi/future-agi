/* eslint-disable react/prop-types */
import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  Chip,
  IconButton,
  Paper,
  Typography,
  useTheme,
} from "@mui/material";
import SvgColor from "src/components/svg-color";
import { ShowComponent } from "src/components/show";
import { format } from "date-fns";
import { getEvaluatorId } from "../utils/evaluationUtils";

export default function EvalListItem({ evalItem, onEdit, onRemove }) {
  const theme = useTheme();

  return (
    <Paper
      sx={{
        p: 2,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
      }}
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
        }}
      >
        <Box sx={{ flex: 1 }}>
          <Typography
            typography="s1_2"
            fontWeight="fontWeightMedium"
            color="text.primary"
          >
            {evalItem.name}
          </Typography>
          <Typography color="text.secondary" fontWeight={400} fontSize={12}>
            Updated at - {format(new Date(), "MM/dd/yyyy, HH:mm")}
          </Typography>

          <Box
            sx={{
              mt: 1,
              display: "flex",
              gap: 1,
              flexWrap: "wrap",
            }}
          >
            <ShowComponent condition={!!evalItem?.groupName}>
              <Chip
                label={`Group name - ${evalItem?.groupName}.`}
                size="small"
                sx={{
                  height: "24px",
                  backgroundColor: "whiteScale.200",
                  borderColor: "whiteScale.500",
                  fontSize: "11px",
                  borderRadius: "2px",
                  paddingX: "12px",
                  lineHeight: "16px",
                  fontWeight: 400,
                  color: "text.primary",
                  "& .MuiChip-label": {
                    padding: 0,
                  },
                  ".MuiChip-icon ": {
                    marginRight: "6px",
                  },
                  "&:hover": {
                    backgroundColor: "whiteScale.200",
                    borderColor: "whiteScale.500",
                  },
                }}
                icon={
                  <SvgColor
                    src="/assets/icons/ic_dashed_square.svg"
                    sx={{ width: 16, height: 16, mr: 1 }}
                    style={{
                      color: "var(--text-primary)",
                    }}
                  />
                }
              />
            </ShowComponent>
            <Chip
              label={`Required Columns - ${
                Array.isArray(evalItem.evalRequiredKeys)
                  ? evalItem.evalRequiredKeys.join(", ")
                  : String(evalItem.evalRequiredKeys)
              }`}
              size="small"
              icon={
                <SvgColor
                  src="/assets/icons/custom/eval_columns.svg"
                  sx={{ width: 16, height: 16 }}
                  style={{ color: theme.palette.black[1000] }}
                />
              }
              sx={{
                height: "24px",
                backgroundColor: "whiteScale.200",
                border: "1px solid",
                borderColor: "whiteScale.400",
                fontSize: "11px",
                borderRadius: "2px",
                paddingX: 1,
                paddingY: 0.5,
                color: "text.primary",
                "&:hover": {
                  backgroundColor: "whiteScale.200",
                  borderColor: "whiteScale.500",
                },
              }}
            />
          </Box>
        </Box>
        <IconButton
          size="small"
          onClick={() => onEdit(evalItem)}
          sx={{
            ml: 1,
            border: "1px solid",
            borderColor: "whiteScale.500",
            borderRadius: 0.25,
            color: "text.primary",
          }}
        >
          <SvgColor
            src="/assets/icons/ic_edit.svg"
            sx={{
              width: 20,
              height: 20,
            }}
          />
        </IconButton>
        <IconButton
          size="small"
          onClick={() => onRemove(getEvaluatorId(evalItem))}
          sx={{
            ml: 1,
            border: "1px solid",
            borderColor: "whiteScale.500",
            borderRadius: 0.25,
            color: "red.500",
          }}
        >
          <SvgColor
            src="/assets/icons/ic_delete.svg"
            sx={{
              height: 20,
              width: 20,
            }}
          />
        </IconButton>
      </Box>
    </Paper>
  );
}

EvalListItem.propTypes = {
  evalItem: PropTypes.shape({
    id: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
    evalId: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
    eval_id: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
    templateId: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
    template_id: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
    name: PropTypes.string,
    groupName: PropTypes.string,
    evalRequiredKeys: PropTypes.oneOfType([
      PropTypes.arrayOf(PropTypes.string),
      PropTypes.string,
    ]),
  }).isRequired,
  onEdit: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired,
};
