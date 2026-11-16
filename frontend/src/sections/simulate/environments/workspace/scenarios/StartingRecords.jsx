import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Collapse, Stack, Typography } from "@mui/material";

import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import { useStartingRecords } from "src/api/simulate-environments/startingRecords";
import {
  STARTING_RECORDS_COPY as COPY,
  reworkLine,
  seededValueText,
} from "./startingRecordsUtils";
import StartingRecordRework from "./StartingRecordRework";
import { focusBuilderChat } from "../../buildEnvironment/console/builderChatFocusBus";
import ChatMarkdown from "../../buildEnvironment/console/ChatMarkdown";

/** The scenario editor's starting records: the seeded rows its facts name, as the server reads them, and the request
 * to the builder to start the scenario from another one. */
export default function StartingRecords({ envId, scenarioKey }) {
  const { data, isLoading, isError, refetch } = useStartingRecords(
    envId,
    scenarioKey,
  );

  const body = () => {
    if (isLoading) return <ContentSkeleton rows={2} label={COPY.loading} />;
    if (isError) return <ErrorState title={COPY.loadError} onRetry={refetch} />;
    if (data.rows_unavailable)
      return (
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          {data.rows_unavailable.reason}
        </Typography>
      );
    if (!data.records.length)
      return (
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          {COPY.none}
        </Typography>
      );
    return data.records.map((record) => (
      <Stack
        key={record.label}
        spacing={1}
        data-testid={`starting-record-${record.label}`}
        sx={{
          p: 1.5,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
        }}
      >
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          {`${record.label} · ${record.table}`}
        </Typography>
        {record.status === "missing" ? (
          <Typography sx={{ typography: "s1", color: "text.secondary" }}>
            {record.note}
          </Typography>
        ) : (
          <>
            <Typography sx={{ typography: "s1", color: "text.primary" }}>
              {record.summary}
            </Typography>
            <Box
              component="dl"
              sx={{
                m: 0,
                display: "grid",
                gridTemplateColumns: "max-content 1fr",
                columnGap: 1.5,
                rowGap: 0.25,
              }}
            >
              {Object.entries(record.fields).map(([field, value]) => (
                <Box key={field} sx={{ display: "contents" }}>
                  <Typography
                    component="dt"
                    sx={{ typography: "s3", color: "text.tertiary" }}
                  >
                    {field}
                  </Typography>
                  <Typography
                    component="dd"
                    sx={{ typography: "s3", color: "text.secondary", m: 0 }}
                  >
                    {seededValueText(value)}
                  </Typography>
                </Box>
              ))}
            </Box>
          </>
        )}
        <StartingRecordRework
          envId={envId}
          scenarioKey={scenarioKey}
          record={record}
          disabled={["pending", "waiting"].includes(data.rework?.state)}
        />
      </Stack>
    ));
  };

  return (
    <Stack spacing={1.5}>
      <Box>
        <Typography
          sx={{
            typography: "m3",
            fontWeight: "fontWeightMedium",
            color: "text.primary",
          }}
        >
          {COPY.heading}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {COPY.hint}
        </Typography>
      </Box>
      {data?.rework && (
        <Typography
          data-testid="starting-records-rework"
          sx={{
            typography: "s1",
            color:
              data.rework.state === "refused" ? "error.main" : "text.secondary",
          }}
        >
          {reworkLine(data.rework)}
        </Typography>
      )}
      {data?.rework?.builder_reply && (
        <BuilderReply
          text={data.rework.builder_reply}
          messageId={data.rework.reply_message_id}
        />
      )}
      {data?.rework?.state === "waiting" && (
        <Button
          size="small"
          variant="outlined"
          onClick={() => focusBuilderChat(data.rework.confirm_message_id)}
          sx={{ alignSelf: "flex-start" }}
        >
          {COPY.openChat}
        </Button>
      )}
      {body()}
      {data?.setup_note && (
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          {data.setup_note}
        </Typography>
      )}
    </Stack>
  );
}

StartingRecords.propTypes = {
  envId: PropTypes.string.isRequired,
  // The scenario's key, as the server names it.
  scenarioKey: PropTypes.string.isRequired,
};

/** The builder's own reply to a refused request, as it wrote it, shown on asking, with a way to its chat message. */
function BuilderReply({ text, messageId }) {
  const [open, setOpen] = useState(false);
  return (
    <Stack spacing={1} sx={{ alignItems: "flex-start" }}>
      <Button
        size="small"
        color="inherit"
        onClick={() => setOpen((shown) => !shown)}
      >
        {COPY.showReply}
      </Button>
      <Collapse in={open} sx={{ width: "100%" }}>
        <Stack spacing={1}>
          <ChatMarkdown text={text} />
          {messageId && (
            <Button
              size="small"
              variant="outlined"
              onClick={() => focusBuilderChat(messageId)}
              sx={{ alignSelf: "flex-start" }}
            >
              {COPY.openReply}
            </Button>
          )}
        </Stack>
      </Collapse>
    </Stack>
  );
}

BuilderReply.propTypes = {
  text: PropTypes.string.isRequired,
  messageId: PropTypes.string,
};
