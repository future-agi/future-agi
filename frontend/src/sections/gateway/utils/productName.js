// The product's name, AI Gateway. The gateway record the API returns still
// carries the former product name (agentcc views/gateway.py), so a page
// that shows the gateway's name shows the product's name in its place. A
// name an operator gave the gateway is shown as given.
export const PRODUCT_NAME = "AI Gateway";

// The former name, as the API still answers it. This mapping is its one
// spelling in the console's sources (__tests__/one-product-name.test.js);
// no screen shows it.
const FORMER_NAME = "Agent Command Center";

export function gatewayDisplayName(name) {
  const text = typeof name === "string" ? name.trim() : "";
  const bare = text.toLowerCase().replace(/\s+gateway$/, "");
  if (!text || bare === FORMER_NAME.toLowerCase()) return PRODUCT_NAME;
  return text;
}
