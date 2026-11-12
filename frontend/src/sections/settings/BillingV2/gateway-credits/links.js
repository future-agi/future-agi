import { paths } from "src/routes/paths";

export const CREDITS_ANCHOR = "ai-gateway-credits";

// The checkout returns here; Stripe fills {CHECKOUT_SESSION_ID} in.
export function checkoutUrls(origin) {
  const billing = `${origin}${paths.dashboard.settings.billing}`;
  return {
    success_url: `${billing}?topup=success&session_id={CHECKOUT_SESSION_ID}#${CREDITS_ANCHOR}`,
    cancel_url: `${billing}?topup=cancel#${CREDITS_ANCHOR}`,
  };
}
