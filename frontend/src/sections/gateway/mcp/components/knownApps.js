// The app an MCP server belongs to, from its URL's domain: the major apps'
// own MCP endpoints. Only the whole registered domain counts (mcp.stripe.com
// is Stripe, evilstripe.com is not), never the server's id, which is the
// org's own name for it. Logos are bundled assets only; apps without one
// use local initials, never a request to the vendor's favicon endpoint.

const LOCAL = { github: "/assets/icons/app/ic_github_grey.svg" };

// A recognized domain supplies a name, not permission to fetch a logo.
const APPS = [
  {
    name: "GitHub",
    domains: ["githubcopilot.com", "github.com"],
    logo: LOCAL.github,
  },
  { name: "GitLab", domains: ["gitlab.com"] },
  { name: "Stripe", domains: ["stripe.com"] },
  { name: "Notion", domains: ["notion.com", "notion.so"] },
  { name: "Linear", domains: ["linear.app"] },
  { name: "Atlassian", domains: ["atlassian.com", "atlassian.net"] },
  { name: "Sentry", domains: ["sentry.dev", "sentry.io"] },
  { name: "Asana", domains: ["asana.com"] },
  { name: "PayPal", domains: ["paypal.com"] },
  { name: "Square", domains: ["squareup.com"] },
  { name: "Cloudflare", domains: ["cloudflare.com"] },
  { name: "Intercom", domains: ["intercom.com", "intercom.io"] },
  { name: "HubSpot", domains: ["hubspot.com"] },
  { name: "Zapier", domains: ["zapier.com"] },
  { name: "Vercel", domains: ["vercel.com"] },
  { name: "Supabase", domains: ["supabase.com"] },
  { name: "Figma", domains: ["figma.com"] },
  { name: "Hugging Face", domains: ["huggingface.co"] },
  { name: "Neon", domains: ["neon.tech"] },
  { name: "Webflow", domains: ["webflow.com"] },
  { name: "Canva", domains: ["canva.com"] },
  { name: "Slack", domains: ["slack.com"] },
  { name: "Shopify", domains: ["shopify.com"] },
  { name: "Box", domains: ["box.com"] },
  { name: "Context7", domains: ["context7.com"] },
  { name: "Exa", domains: ["exa.ai"] },
  { name: "Plaid", domains: ["plaid.com"] },
  { name: "Twilio", domains: ["twilio.com"] },
];

const hostOf = (url) => {
  try {
    return new URL(url).hostname.toLowerCase();
  } catch {
    return "";
  }
};

const under = (host, domain) => host === domain || host.endsWith(`.${domain}`);

/** { name, logo } for a server URL on a known app's domain, else null. */
export function appForUrl(url) {
  const host = hostOf(url);
  if (!host) return null;
  const app = APPS.find((a) => a.domains.some((d) => under(host, d)));
  if (!app) return null;
  return { name: app.name, logo: app.logo || null };
}
