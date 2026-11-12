import React from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";

import { useHubMe, useSendEmailVerification } from "../api/queries";

// Why the link was not sent, in the server's words with its fix. A request
// that got no answer (dropped, timed out or cancelled) has no HTTP status and
// no server code, and a gateway error with no reason reads only "Request
// failed (502)"; for both, the notice says what failed and what to do.
const NOT_SENT =
  "The link was not sent. The Hub registry did not answer; try again in a moment.";

function sendProblem(error) {
  const answered = Boolean(error?.status || error?.code);
  const message =
    answered && error?.message && !/^Request failed/.test(error.message)
      ? error.message
      : null;
  return [message, error?.fix].filter(Boolean).join(" ") || NOT_SENT;
}

// Shown where the server answered OMEGA_HUB_EMAIL_UNVERIFIED (a like, a
// report, a new publisher): what is needed, and one button that mails the
// confirmation link to the signed-in account's own address (SPEC §6.1).
export default function EmailVerifyNotice({ action = "do this" }) {
  const me = useHubMe();
  const send = useSendEmailVerification();
  const email = send.data?.sentTo || me.data?.user?.email;
  const sent = send.isSuccess && send.data?.sent;
  const already = send.isSuccess && send.data?.verified;

  return (
    <Alert
      severity={already ? "success" : "info"}
      data-testid="hub-email-verify-notice"
      action={
        already ? null : (
          <Button
            size="small"
            color="inherit"
            variant="outlined"
            onClick={() => send.mutate()}
            disabled={send.isPending}
            data-testid="hub-email-verify-send"
            sx={{ whiteSpace: "nowrap", flexShrink: 0 }}
          >
            {sent ? "Send again" : "Email me a link"}
          </Button>
        )
      }
      sx={{
        alignItems: "flex-start",
        "& .MuiAlert-message": { minWidth: 0, overflowWrap: "anywhere" },
      }}
    >
      <AlertTitle sx={{ typography: "s1", fontWeight: 600 }}>
        {already
          ? "Your email is confirmed"
          : "Confirm your email address first"}
      </AlertTitle>
      <Typography typography="s2" component="div">
        {already
          ? `Try again to ${action}.`
          : sent
            ? `We sent a link to ${email}. Open it while signed in to Future AGI; it expires in 24 hours.`
            : `To ${action} on the Hub, your account needs a confirmed email.${email ? ` We will send a link to ${email}.` : ""}`}
      </Typography>
      {send.isError && (
        <Typography
          typography="s2"
          color="error.main"
          component="div"
          sx={{ mt: 0.5 }}
          data-testid="hub-email-verify-error"
        >
          {sendProblem(send.error)}
        </Typography>
      )}
    </Alert>
  );
}

EmailVerifyNotice.propTypes = { action: PropTypes.string };
