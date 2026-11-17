// W7-4 remote scorers: the detail (Evals › <scorer> › Details). What the
// scorer is, how calls are signed, Rotate the secret, Score a run, and the
// editor with Test on one row. Mounted by TypedEvalDetail ({templateId,
// evalData}). A scorer just created opens here with its signing secret, shown
// once (the editor hands it over in page memory; a reload never shows it).
import React, { useCallback, useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  Typography,
} from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { evalDetailQuery } from "../hooks/useEvalDetail";
import {
  AUTH_KINDS,
  REMOTE_OUTPUT_TYPES,
  SIGNATURE_LABEL,
  maskedToken,
  secondsText,
} from "src/sections/common/EvalTypes/remoteModel";
import RemoteScorerEditor from "./RemoteScorerEditor";
import ScoreRunDialog from "./ScoreRunDialog";
import SigningSecretDialog from "./SigningSecretDialog";
import { takeHandedSecret } from "./remoteScorerForm";

const label = (list, v) => list.find((x) => x.value === v)?.label ?? v;

export default function RemoteScorerDetail({ templateId }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);
  const [rotateAsk, setRotateAsk] = useState(false);
  const [secret, setSecret] = useState(null);
  const [scoreRun, setScoreRun] = useState(false);
  const [rev, setRev] = useState(0);
  const queryClient = useQueryClient();

  const load = useCallback(() => {
    axios
      .get(endpoints.develop.w7.remote(templateId))
      .then((res) => {
        setDetail(res?.data?.result ?? null);
        setRev((n) => n + 1);
        const handed = takeHandedSecret(templateId);
        if (handed) setSecret(handed);
      })
      .catch((e) => setError(e?.result ?? "Couldn't load the scorer"));
  }, [templateId]);
  useEffect(load, [load]);

  const rotate = async () => {
    setRotateAsk(false);
    try {
      const res = await axios.patch(endpoints.develop.w7.remote(templateId), {
        rotate_secret: true,
      });
      setSecret(res?.data?.result?.signing_secret ?? null);
      setDetail(res?.data?.result ?? detail);
    } catch (e) {
      setError(e?.result ?? "Couldn't rotate the secret");
    }
  };

  if (error) return <Alert severity="error">{String(error)}</Alert>;
  if (!detail) return null;
  const r = detail.remote;
  const auth = r.auth?.kind;
  const authText =
    auth === "hmac"
      ? `${SIGNATURE_LABEL}${detail.signing?.previous_valid_until ? ` · signing with the old secret too until ${new Date(detail.signing.previous_valid_until).toLocaleString()}` : ""}`
      : auth === "bearer"
        ? `Bearer token ${maskedToken(r.auth.token_last4)}`
        : auth === "oauth"
          ? `OAuth client credentials · ${r.auth.oauth?.client_id} at ${r.auth.oauth?.token_url}`
          : label(AUTH_KINDS, auth);
  return (
    <Stack spacing={2} data-testid="remote-scorer-detail">
      <Box sx={{ border: 1, borderColor: "divider", borderRadius: 1, p: 1.5 }}>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="flex-start"
        >
          <Box>
            <Typography variant="subtitle2" data-testid="remote-detail-host">
              Remote scorer · {detail.host}
            </Typography>
            <Typography
              variant="caption"
              component="div"
              color="text.secondary"
            >
              POST {r.url} · {detail.version}
            </Typography>
            <Typography
              variant="caption"
              component="div"
              data-testid="remote-detail-auth"
            >
              {authText}
            </Typography>
            <Typography
              variant="caption"
              component="div"
              color="text.secondary"
            >
              Timeout {secondsText(r.timeout_ms)} · {r.retries}{" "}
              {r.retries === 1 ? "retry" : "retries"}
              {r.retries ? ` on ${r.retry_on.join(", ")}` : ""} ·{" "}
              {r.calls_at_a_time} at a time ·{" "}
              {r.send === "trace"
                ? "row, output and trace steps"
                : "row and output"}{" "}
              · {label(REMOTE_OUTPUT_TYPES, r.output?.type)}
            </Typography>
            <Typography
              variant="caption"
              component="div"
              color="text.secondary"
            >
              Billed by your service · 0 Future AGI credits
            </Typography>
          </Box>
          <Stack direction="row" spacing={1}>
            {auth === "hmac" && (
              <Button
                size="small"
                variant="outlined"
                onClick={() => setRotateAsk(true)}
                data-testid="remote-rotate"
              >
                Rotate secret
              </Button>
            )}
            <Button
              size="small"
              variant="contained"
              onClick={() => setScoreRun(true)}
              data-testid="remote-score-run"
            >
              Score a run
            </Button>
          </Stack>
        </Stack>
      </Box>
      <RemoteScorerEditor
        key={rev}
        templateId={templateId}
        detail={detail}
        onSaved={() => {
          load();
          // The page header names the version: a saved change is a new one.
          queryClient.invalidateQueries({
            queryKey: evalDetailQuery(templateId).queryKey,
          });
          queryClient.invalidateQueries({
            queryKey: ["evals", "versions", templateId],
          });
          queryClient.invalidateQueries({ queryKey: ["evals", "list"] });
        }}
      />
      <Dialog open={rotateAsk} onClose={() => setRotateAsk(false)}>
        <DialogTitle sx={{ fontSize: 16 }}>
          Rotate the signing secret?
        </DialogTitle>
        <DialogContent>
          <Typography variant="body2">
            Future AGI makes a new secret and signs with both the new and the
            old one for 24 hours, so your scorer can accept either while you
            switch.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRotateAsk(false)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={rotate}
            data-testid="remote-rotate-confirm"
          >
            Rotate
          </Button>
        </DialogActions>
      </Dialog>
      <SigningSecretDialog
        open={Boolean(secret)}
        secret={secret ?? ""}
        rotatedUntil={detail.signing?.previous_valid_until}
        onClose={() => {
          setSecret(null);
          load();
        }}
      />
      {scoreRun && (
        <ScoreRunDialog
          open
          templateId={templateId}
          name={detail.name}
          host={detail.host}
          onClose={() => setScoreRun(false)}
        />
      )}
    </Stack>
  );
}

RemoteScorerDetail.ready = true;

RemoteScorerDetail.propTypes = {
  templateId: PropTypes.string.isRequired,
  evalData: PropTypes.object,
};
