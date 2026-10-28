// Package contractcoverage makes "every provider API the gateway parses is
// under contract" a tested claim. It has no code of its own; its tests read
// the fixtures, the capability interfaces and the committed allowlists, and
// write two generated documents next to the adapters:
//
//   - ../CONTRACT_COVERAGE.md (TestCoverageGrid): the matrix of provider key
//     (each native adapter, each OpenAI-compatible preset as "<preset>@",
//     Vertex AI as "vertex@", each speech-relay vendor) by endpoint (each
//     capability interface in ../provider.go the constructed adapter
//     implements), with the contract cases in each cell. Every implemented
//     cell needs at least one case, or an entry in exemptions.json with a
//     reason; a stale exemption fails.
//   - ../KNOWN_GAPS.md (TestKnownGaps): every "known gap <ID>: <reason>" in
//     the fixtures' meta.json and case.json, the speech sessions, the
//     spec_allowlist*.json files and the provider tests' gap tables, one
//     line per ID with where it appears. IDs must be well formed, and an ID
//     whose occurrences word the loss differently must have a reviewed
//     one-line summary in gap_summaries.json (so two losses cannot share an
//     ID unnoticed).
//
// Both documents are goldens: go test ./internal/providers/contractcoverage/
// -update rewrites them, and the tests fail while they are stale. Nothing
// here calls a provider or the network.
//
// That every covered model in the model-watch registry has its fixture
// directory (and every fixture directory a registry entry) is checked by
// contractwatch's TestRegistryMatchesFixtures, not here.
package contractcoverage
