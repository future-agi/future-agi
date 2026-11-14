/* eslint-disable react/prop-types */
import React from "react";
import PropTypes from "prop-types";
import { Box, IconButton, Typography } from "@mui/material";
import Section from "src/components/section";
import SvgColor from "src/components/svg-color";
import { ShowComponent } from "src/components/show";
import { format } from "date-fns";

export default function EvalListItem({ evalItem, onEdit, onRemove }) {
  return (
    <Section variant="panel" sx={{ p: 2 }}>
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
              flexDirection: "column",
              gap: 0.5,
            }}
          >
            <ShowComponent condition={!!evalItem?.groupName}>
              <Typography color="text.secondary" fontWeight={400} fontSize={12}>
                {`Group name - ${evalItem?.groupName}.`}
              </Typography>
            </ShowComponent>
            <Typography color="text.secondary" fontWeight={400} fontSize={12}>
              {`Required Columns - ${
                Array.isArray(evalItem.evalRequiredKeys)
                  ? evalItem.evalRequiredKeys.join(", ")
                  : String(evalItem.evalRequiredKeys)
              }`}
            </Typography>
          </Box>
        </Box>
        <IconButton
          size="small"
          onClick={() => onEdit(evalItem)}
          sx={{
            ml: 1,
            border: "1px solid",
            borderColor: "divider",
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
          onClick={() => onRemove(evalItem.eval_id || evalItem.id)}
          sx={{
            ml: 1,
            border: "1px solid",
            borderColor: "divider",
            color: "status.danger.fg",
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
    </Section>
  );
}

EvalListItem.propTypes = {
  evalItem: PropTypes.shape({
    id: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
    evalId: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
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
