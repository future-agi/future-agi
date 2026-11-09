// Package credentials is the gateway half of per-user upstream credentials
// (M25; MCP-PLAN M25, MC.1 step 13, MC.6, MC.13): who a call acts for, the
// user's upstream token fetched just in time from Django
// (mcp-upstream-token-v1) and kept sealed in Redis per org, user and server,
// and the typed auth_required answer, with a connect link, when there is no
// usable token.
//
// The guarantees, each proved by a named test:
//   - a call on a per_user server runs only as the user who made it: never as
//     a shared key's creator, and never with another user's or another org's
//     token;
//   - a token is stored only sealed, and a sealed token opens only for the
//     org, user and server it was sealed for;
//   - a token close to its expiry is fetched again before it is used;
//   - a revoked or lapsed credential answers auth_required with a link to
//     connect again, and is never replaced by a shared credential;
//   - a token never reaches a log, an error message or a response;
//   - a shared OAuth server (CN24) sends every request with its
//     organization's signed-in account's token, fetched just in time under a
//     key of the organization's own scope, which never opens as a person's
//     token, nor a person's as it.
package credentials
