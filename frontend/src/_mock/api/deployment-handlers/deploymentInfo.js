import { http, HttpResponse, passthrough } from "msw";
import { HOST_API } from "src/config-global";

// TH-7217 CLEANUP: optional — inert unless an env var is set, so it can stay.
// Dev-only override for GET /api/deployment-info/, so cloud / EE / OSS UI can be
// exercised against one local backend without recreating containers (which would
// also drop any in-container patches).
//
//   VITE_MSW_DEPLOYMENT_MODE = cloud | ee | oss     (unset = use the real backend)
//   VITE_MSW_EMAIL_DELIVERY  = none | provider
//
// With no override set this passes straight through, so the handler is inert
// unless you deliberately turn it on.

const MODE = import.meta.env.VITE_MSW_DEPLOYMENT_MODE;
const EMAIL_DELIVERY = import.meta.env.VITE_MSW_EMAIL_DELIVERY;

export const deploymentInfo = http.get(
  `${HOST_API}/api/deployment-info/`,
  () => {
    if (!MODE && !EMAIL_DELIVERY) return passthrough();

    return HttpResponse.json({
      status: true,
      result: {
        mode: MODE || "oss",
        // Omitted unless overridden, so the frontend's own "missing means none"
        // default stays exercised by default.
        ...(EMAIL_DELIVERY ? { email_delivery: EMAIL_DELIVERY } : {}),
      },
    });
  },
);
