// The SSO initiation endpoint returns the IdP redirect URL under result.url.
export function getSsoRedirectUrl(data) {
  return data?.result?.url || null;
}
