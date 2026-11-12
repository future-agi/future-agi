// Package restmcp is the K2 (rest provider) runtime: an MCP
// server over a vendor's REST API, built from a rest provider's manifest
// (its tools.items plus tools.generated.json, the generator's output). One Server
// serves /<app-id>/mcp for every rest provider in a catalog, over Streamable
// HTTP, in the 2026-07-28 (stateless) era and the 2025-11-25 era (with
// 2025-06-18 and 2025-03-26 negotiated for older clients). Legacy requests
// get no Mcp-Session-Id: the runtime keeps no session, so any replica can
// answer any request.
//
// The credential contract: the bearer on each MCP request is
// the end user's vendor access token (for an api_key provider, the parts its
// auth.credentialParts place, read where the manifest places them). The
// runtime never stores or logs it, and sends it only to the vendor origin
// named on the tool (the generator's x-origin, which Compile checks against
// the app's vendor.domains: a catalog with a tool outside them is not served
// at all). A request without it is answered 401 with a WWW-Authenticate
// challenge.
//
// Outbound calls go through the MCP SSRF guard (internal/mcp/egress): private,
// loopback and metadata addresses are refused at dial time, plaintext HTTP
// too; redirects are never followed, so a vendor cannot move the credential
// to another host. Results are shaped by the tool's response block (unwrap,
// fields) and capped at response.maxBytes; a vendor error becomes an isError
// result that names the vendor, its host and its HTTP status.
//
// The one exception to "only the vendor origin" is OriginReplay, an
// operator-only map from a vendor origin to a loopback base URL, which is
// how recorded exchanges (agentcc-cassette replay) stand in for a vendor in
// conformance runs and scenarios. A target that is not a loopback address is
// refused.
package restmcp
