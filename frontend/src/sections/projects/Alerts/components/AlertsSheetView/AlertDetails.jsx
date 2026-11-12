import { Box, Divider, Grid, Stack, Typography, useTheme } from "@mui/material";
import React, { useMemo } from "react";
import { format } from "date-fns";
import _ from "lodash";
import SvgColor from "src/components/svg-color";
import PropTypes from "prop-types";
import {
  earlierRuleNote,
  formatConditionText,
  transformAlertToConditions,
} from "../../common";
import { useAlertSheetView } from "../../store/useAlertSheetView";

const AlertRuleDetails = ({ details }) => {
  const isValidDate = (value) => {
    const date = new Date(value);
    return !isNaN(date.getTime());
  };

  const extractedDetails = Object.entries(details).reduce(
    (acc, [key, value]) => {
      const formattedKey = _.capitalize(_.lowerCase(key.replace(/_/g, " ")));

      let formattedValue = value;

      if (
        (key === "created_at" || key === "last_triggered") &&
        typeof value === "string" &&
        isValidDate(value)
      ) {
        formattedValue = format(new Date(value), "dd-MM-yyyy, HH:mm");
      } else if (value instanceof Date && isValidDate(value)) {
        formattedValue = format(value, "dd-MM-yyyy, HH:mm");
      }

      acc[formattedKey] = formattedValue;
      return acc;
    },
    {},
  );

  return (
    <Stack spacing={2}>
      <Typography variant="h6">Alert Rule Details</Typography>
      <Grid container spacing={1}>
        {Object.entries(extractedDetails).map(([label, value]) => (
          <React.Fragment key={label}>
            <Grid item xs={6}>
              <Typography variant="body2" color="text.secondary">
                {label}
              </Typography>
            </Grid>
            <Grid item xs={6} sx={{ textAlign: "right" }}>
              <Typography variant="body2">{value}</Typography>
            </Grid>
          </React.Fragment>
        ))}
      </Grid>
    </Stack>
  );
};

AlertRuleDetails.propTypes = {
  details: PropTypes.shape({
    created_by: PropTypes.string,
    created_at: PropTypes.string,
    last_triggered: PropTypes.string,
  }),
};

const AlertEmails = ({ emails = [] }) => {
  const theme = useTheme();
  return (
    <Stack gap={1.5}>
      <Typography
        variant="m3"
        fontWeight={"fontWeightSemiBold"}
        color={"text.primary"}
      >
        Emails sent to
      </Typography>
      <Stack direction={"row"} gap={0.5} flexWrap={"wrap"}>
        {emails?.map((email, index) => (
          <Typography
            sx={{
              padding: theme.spacing(0.5, 1.5),
              border: `1px solid ${theme.palette.divider}`,
              borderRadius: theme.spacing(0.5),
              backgroundColor: "background.neutral",
            }}
            variant="s3"
            fontWeight={"fontWeightMedium"}
            color={"text.primary"}
            key={index}
          >
            {email}
          </Typography>
        ))}
      </Stack>
    </Stack>
  );
};

AlertEmails.propTypes = {
  emails: PropTypes.arrayOf(PropTypes.string),
};

const ChevronIcon = () => {
  const theme = useTheme();
  return (
    <Box
      sx={{
        bgcolor: "background.default",
        borderRadius: theme.spacing(0.5),
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        height: theme.spacing(3),
        width: theme.spacing(3),
        zIndex: 10,
      }}
    >
      <SvgColor
        sx={{
          height: 12,
          width: 12,
        }}
        src="/assets/icons/custom/lucide--chevron-right.svg"
      />
    </Box>
  );
};

const Condition = ({ condition, description, action }) => {
  const theme = useTheme();
  return (
    <Stack
      sx={{
        gap: theme.spacing(4.45),
        position: "relative",
      }}
    >
      <Stack
        sx={{
          gap: theme.spacing(1.5),
          flexDirection: "row",
        }}
      >
        <ChevronIcon />
        <Typography
          variant="s3"
          color={"text.primary"}
          fontWeight={"fontWeightMedium"}
          sx={{
            border: `1px solid ${theme.palette.divider}`,
            borderRadius: theme.spacing(0.5),
            backgroundColor: "background.neutral",
            padding: theme.spacing(0.5, 1.5),
          }}
        >
          {condition}
        </Typography>
        <Typography
          variant="s1"
          color={"text.primary"}
          fontWeight={"fontWeightRegular"}
        >
          {description}
        </Typography>
      </Stack>
      <Stack
        sx={{
          gap: theme.spacing(1.5),
          flexDirection: "row",
        }}
      >
        <ChevronIcon />
        <Typography
          variant="s1"
          color={"text.primary"}
          fontWeight={"fontWeightRegular"}
        >
          {action}
        </Typography>
      </Stack>
      <Divider
        orientation="vertical"
        sx={{
          position: "absolute",
          left: "10px",
          height: "100%",
          bgcolor: "red",
        }}
      />
    </Stack>
  );
};

Condition.propTypes = {
  condition: PropTypes.string,
  description: PropTypes.string,
  action: PropTypes.string,
};

const AlertConditions = ({ conditions = [] }) => {
  const theme = useTheme();
  return (
    <Stack
      sx={{
        gap: theme.spacing(1.5),
      }}
    >
      {conditions?.length > 0 &&
        conditions?.map((condition, index) => (
          <Stack key={index} gap={1}>
            <Typography
              variant="s1"
              color={"text.primary"}
              fontWeight={"fontWeightMedium"}
            >
              Condition {index + 1}
            </Typography>
            <Condition
              action={condition?.action}
              description={condition?.description}
              condition={formatConditionText(condition?.condition)}
            />
          </Stack>
        ))}
    </Stack>
  );
};

AlertConditions.propTypes = {
  conditions: PropTypes.array,
};

export default function AlertDetails() {
  const { alertRuleDetails } = useAlertSheetView();

  const conditions = useMemo(
    () => transformAlertToConditions(alertRuleDetails),
    [alertRuleDetails],
  );
  const earlierNote = earlierRuleNote(alertRuleDetails);

  return (
    <Stack gap={2.5}>
      <AlertRuleDetails
        details={{
          created_by: alertRuleDetails?.createdBy,
          created_at: alertRuleDetails?.createdAt,
          last_triggered: alertRuleDetails?.lastTriggered,
        }}
      />
      <Divider
        sx={{
          borderColor: "divider",
        }}
      />
      <AlertConditions conditions={conditions} />
      {earlierNote && (
        <Typography
          variant="s1"
          color="text.secondary"
          fontWeight="fontWeightRegular"
          data-alert-earlier-rule=""
        >
          {earlierNote}
        </Typography>
      )}
      <Divider
        sx={{
          borderColor: "divider",
        }}
      />
      <AlertEmails emails={alertRuleDetails?.notificationEmails} />
    </Stack>
  );
}
