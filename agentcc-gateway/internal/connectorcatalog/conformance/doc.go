// Package conformance is L3 of the connector conformance levels (scaling.md
// §5, CONNECTORS-PLAN CN8): a connector through the real gateway chain.
//
// For every provider of a catalog directory, Build plans one case: an
// agentcc-mcpmock upstream that serves the provider's tools (from its CN2 MCP
// cassette, fixtures/*.cassette.json, when it has one, else the tools it
// declares plus one it does not, which judges its default posture), the org
// config add-from-catalog would push for it (OrgConfigs: the server with
// catalog_ref, url_variables, its era pin and its credential's push-only
// secret, and the virtual server whose access rule refuses every tool the
// gateway must refuse), and the calls the harness makes. A second virtual
// server with no rule, admitting only a second key, is the audit view the
// harness lists every served tool and its annotations through; it is test
// scaffolding, never something add-from-catalog pushes. A K4 (stdio) provider
// runs as an operator YAML stdio server of the static org (RenderMCP), since
// an org config never carries a command (decision 34).
//
// scripts/scenarios/connectors/93_cn_conformance.sh (cmd/l3conformance)
// starts the mocks and the gateway, pushes the configs, makes the calls and
// collects the evidence (LoadEvidence); Check then judges every row from it.
//
// A row either runs or is skipped with the reason it waits on, which names
// the lane that turns it on (waiting.go). Nothing is skipped silently, and a
// row that runs fails on any evidence that breaks the rule it names.
//
// The gateway loads the run's catalogs as its connector catalog
// (connectors.catalog_dir, CatalogDir) and takes a catalog provider's
// credential whole (CN7, at the CW1 merge): every declared secret part
// pushed, its constants added from the catalog, anything less refused. So a
// case pushes every secret header part (the first as the secret, the rest in
// its parts) and the mock witnesses each, and each constant header, by
// sha256; or, when one part cannot be pushed or witnessed (a query parameter
// or URL variable, which agentcc-mcpmock cannot witness yet; a prefix other
// than Bearer; a non-secret header the customer sets), it pushes nothing and
// every part's row says why.
package conformance

// HarnessVersion is quality.conformance.harness for a run of this harness.
// 1.1.0: credentials are placed whole, with secret parts and constants
// (the CW1 integration).
const HarnessVersion = "1.1.0"
