import React, { useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { ConfirmDialog } from "src/components/custom-dialog";

import { MODERATION_CONSEQUENCES, MODERATION_NOTE_FIELDS } from "./constants";
import {
  useHubMe,
  usePublisherAction,
  useReviewPublishers,
} from "./api/queries";
import {
  HubEmpty,
  HubError,
  HubSection,
  HubSkeletonCards,
} from "./components/HubStates";
import MaintainerGate from "./components/MaintainerGate";
import { SortTabsLite } from "./components/SortTabsLite";
import TierBadge from "./components/TierBadge";

function Actions({ publisher }) {
  const [note, setNote] = useState("");
  const [message, setMessage] = useState("");
  const act = usePublisherAction();
  const [confirming, setConfirming] = useState(null);
  const disabled = !note.trim() || act.isPending;
  const verificationDisabled = !message.trim() || act.isPending;
  const verificationField = MODERATION_NOTE_FIELDS.ask_publisher;
  const noteField = publisher.suspended
    ? MODERATION_NOTE_FIELDS.unsuspend_publisher
    : MODERATION_NOTE_FIELDS.private;
  const run = async (action) => {
    const value = (
      action === "verify" || action === "unverify" ? message : note
    ).trim();
    if (!value || act.isPending) return;
    try {
      await act.mutateAsync({
        handle: publisher.handle,
        action,
        note: value,
      });
      if (action === "verify" || action === "unverify") setMessage("");
      else setNote("");
      setConfirming(null);
    } catch {
      // A failed confirmation stays open with its error and can be retried.
    }
  };
  const actionError = act.isError && (
    <Alert severity="error" sx={{ width: "100%" }}>
      {act.error.message}
    </Alert>
  );
  return (
    <Box
      sx={{
        display: "flex",
        gap: 1,
        alignItems: "flex-start",
        flexWrap: "wrap",
      }}
    >
      {(publisher.tier === "community" || publisher.tier === "verified") && (
        <Box
          sx={{
            display: "flex",
            gap: 1,
            flexWrap: "wrap",
            width: "100%",
            alignItems: "flex-start",
          }}
        >
          <TextField
            size="small"
            label={verificationField.label}
            helperText={verificationField.helperText}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            sx={{ flex: 1, minWidth: 200 }}
          />
          <Button
            size="small"
            variant={publisher.tier === "community" ? "contained" : "outlined"}
            disabled={verificationDisabled}
            onClick={() => {
              if (publisher.tier === "community") run("verify");
              else {
                act.reset();
                setConfirming("unverify");
              }
            }}
          >
            {publisher.tier === "community" ? "Verify" : "Remove verification"}
          </Button>
        </Box>
      )}
      {publisher.tier !== "official" && (
        <Box
          sx={{
            display: "flex",
            gap: 1,
            flexWrap: "wrap",
            width: "100%",
            alignItems: "flex-start",
          }}
        >
          <TextField
            size="small"
            label={noteField.label}
            helperText={noteField.helperText}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            sx={{ flex: 1, minWidth: 200 }}
          />
          {publisher.suspended ? (
            <Button
              size="small"
              variant="outlined"
              disabled={disabled}
              onClick={() => run("unsuspend")}
            >
              Unsuspend
            </Button>
          ) : (
            <Button
              size="small"
              variant="outlined"
              color="inherit"
              disabled={disabled}
              onClick={() => {
                act.reset();
                setConfirming("suspend");
              }}
            >
              Suspend
            </Button>
          )}
        </Box>
      )}
      {!confirming && actionError}
      <ConfirmDialog
        open={Boolean(confirming)}
        title={
          confirming === "unverify"
            ? `Remove verification from @${publisher.handle}?`
            : `Suspend publisher @${publisher.handle}?`
        }
        onClose={() => !act.isPending && setConfirming(null)}
        closeDisabled={act.isPending}
        content={
          <>
            <Typography typography="s2">
              {confirming === "unverify"
                ? "The publisher returns to the Community tier without a verified badge, and your message is emailed to them."
                : MODERATION_CONSEQUENCES.suspend_publisher}
            </Typography>
            {actionError}
          </>
        }
        action={
          <Button
            variant="contained"
            color="error"
            disabled={
              confirming === "unverify" ? verificationDisabled : disabled
            }
            onClick={() => run(confirming)}
          >
            {confirming === "unverify"
              ? "Remove verification"
              : "Suspend publisher"}
          </Button>
        }
      />
    </Box>
  );
}

Actions.propTypes = { publisher: PropTypes.object.isRequired };

// Publisher verification and suspension (SPEC §6.5, §5.5).
export default function HubPublishersReviewPage() {
  const me = useHubMe();
  const [filter, setFilter] = useState("requested");
  const publishers = useReviewPublishers(
    filter ? { verification: filter } : {},
    Boolean(me.data?.isMaintainer),
  );
  const rows = publishers.data || [];
  return (
    <MaintainerGate
      title="Publishers"
      documentTitle="Publisher review"
      description="Verification requests, verified badges and suspensions."
      testId="hub-publishers-review"
    >
      <SortTabsLite
        value={filter}
        onChange={setFilter}
        options={[
          ["requested", "Verification requests"],
          ["", "All publishers"],
        ]}
      />
      {publishers.isLoading ? (
        <HubSkeletonCards count={2} />
      ) : publishers.isError ? (
        <HubError
          error={publishers.error}
          onRetry={() => publishers.refetch()}
        />
      ) : rows.length === 0 ? (
        <HubEmpty
          title={filter ? "No verification requests" : "No publishers"}
        />
      ) : (
        rows.map((p) => (
          <HubSection
            key={p.handle}
            title={`${p.displayName} (@${p.handle})`}
            action={<TierBadge tier={p.tier} verifiedAt={p.verifiedAt} />}
            testId="hub-publisher-review-row"
          >
            {p.verificationRequest && (
              <Typography
                typography="s2"
                sx={{ whiteSpace: "pre-wrap" }}
              >{`Evidence: ${p.verificationRequest}`}</Typography>
            )}
            {p.suspended && (
              <Typography typography="s2" color="error.main">
                Suspended
              </Typography>
            )}
            <Actions publisher={p} />
          </HubSection>
        ))
      )}
    </MaintainerGate>
  );
}
