// Package contractspec checks the provider adapters against the providers'
// official machine-readable API specs, offline.
//
// It has three parts:
//
//   - Flatteners for OpenAPI 3.0/3.1/3.2 (openapi.go), Google discovery
//     documents (discovery.go) and Smithy 2.0 JSON models (smithy.go). Each
//     turns the roots the gateway parses (a response, a stream's events, an
//     error body) into a Subset: every JSON path, in contracttest's
//     UnknownFields syntax ("$.content[].type", "message_delta:$.delta"),
//     with its kinds, enum values, nullability, and the stream's events.
//   - The subset definitions (providers.go), the committed subsets and
//     their provenance (subsets/, embedded), and Diff (diff.go), which
//     cmd/contract-specs uses to re-pin them (-update) or to fail on drift
//     (-check, daily in .github/workflows/agentcc-provider-spec-drift.yml).
//   - Check (coverage.go), which a provider package's TestSpecCoverage
//     calls: every committed path must be mapped by the adapter's Go types,
//     sit in a declared blob, be forwarded through an Extra map, or be in the
//     package's testdata/spec_allowlist.json with a reason; every enum value
//     and stream event the spec lists must be handled by the adapter's own
//     code or allowlisted; and no allowlist entry may be stale.
//
// Specs are untrusted input: they are parsed, never executed, only local
// "#/" refs are followed, and walks and sizes are bounded. Only the
// flattened subsets are vendored, never a full spec. See README.md.
package contractspec
