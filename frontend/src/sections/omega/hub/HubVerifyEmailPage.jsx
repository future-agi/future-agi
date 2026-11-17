import React, { useEffect, useRef } from "react";
import Button from "@mui/material/Button";
import { useSearchParams } from "react-router-dom";

import { RouterLink } from "src/routes/components";

import { useConfirmEmail, useSendEmailVerification } from "./api/queries";
import HubFrame from "./components/HubFrame";
import { HubState } from "./components/HubStates";
import { hubPaths } from "./constants";

// The page the confirmation email links to (/dashboard/hub/verify-email?token=…).
// It confirms once on arrival, for the signed-in account, and says what to do
// when the link is for another account, has expired, or the email changed.
export default function HubVerifyEmailPage() {
  const [search] = useSearchParams();
  const token = search.get("token") || "";
  const confirm = useConfirmEmail();
  const resend = useSendEmailVerification();
  const started = useRef(false);

  useEffect(() => {
    if (token && !started.current) {
      started.current = true;
      confirm.mutate(token);
    }
  }, [token, confirm]);

  const back = (
    <Button
      component={RouterLink}
      href={hubPaths.browse}
      variant="contained"
      size="small"
      data-testid="hub-verify-back"
    >
      Back to the Hub
    </Button>
  );

  let state;
  if (!token) {
    state = (
      <HubState
        icon="inbox"
        tone="error"
        title="This link has no token"
        description="Open the link from the email exactly as it arrived, or send yourself a new one from a Like or Report button."
        action={back}
        testId="hub-verify-missing"
      />
    );
  } else if (confirm.isSuccess) {
    state = (
      <HubState
        icon="check"
        title="Your email is confirmed"
        description="You can now like, report and create publishers on the Hub."
        action={back}
        testId="hub-verify-done"
      />
    );
  } else if (confirm.isError) {
    const sent = resend.isSuccess && resend.data?.sent;
    state = (
      <HubState
        icon="inbox"
        tone="error"
        title={
          confirm.error?.code === "OMEGA_HUB_EMAIL_LINK_EXPIRED"
            ? "This link has expired"
            : "This link did not work"
        }
        description={
          sent
            ? `A new link is on its way to ${resend.data.sentTo}.`
            : confirm.error?.message ||
              "Send yourself a new link and open the newest one."
        }
        action={
          <Button
            variant="outlined"
            size="small"
            onClick={() => resend.mutate()}
            disabled={resend.isPending || sent}
            data-testid="hub-verify-resend"
          >
            {sent ? "Link sent" : "Send a new link"}
          </Button>
        }
        testId="hub-verify-failed"
      />
    );
  } else {
    state = (
      <HubState
        icon="inbox"
        title="Confirming your email…"
        description="One moment."
        testId="hub-verify-pending"
      />
    );
  }

  return (
    <HubFrame
      title="Confirm your email"
      documentTitle="Confirm your email"
      back={{ href: hubPaths.browse, label: "Hub" }}
      testId="hub-verify-email"
    >
      {state}
    </HubFrame>
  );
}
