package runtimesupport

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"sort"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/catalog/connectors"
)

type Identity struct{ AppID, ProviderID, Revision, RuntimeURL string }
type Route struct{ AppID, ProviderID string }

// The catalog's private embedded snapshot is the ONLY admission source.
// There is deliberately no injectable Observer/Evidence boolean, file/env
// allowlist or externally writable record list. New does no network I/O.
type Options struct {
	AdminToken, RuntimeURL, BuildRevision string
	Catalog                               *connectors.Catalog
	Routes                                []Route
	Now                                   func() time.Time
}

type installedRecord struct {
	kind, revision   string
	retired, enabled bool
	operations       []string
}

// admissionObserver observes exactly the build-admitted record, not a live
// grant or fleet. Enabled evidence comes only from that record's canonical
// connectionEnablement at the matching sha256. This is the r2 evidence model;
// it must not be presented as r1's live lifecycle/fleet observation.
type admissionObserver map[Route]installedRecord

type Handler struct {
	adminToken, runtimeURL, buildRevision, catalogDigest string
	observer                                             admissionObserver
	routes                                               map[Route]bool
	routeRevision                                        string
	now                                                  func() time.Time
}

func New(o Options) *Handler {
	records, digest := o.Catalog.RuntimeSnapshot()
	return newSnapshot(o, records, digest)
}

// Private post-admission seam, also exercised by synthetic unit fixtures.
// Production reaches this only through Catalog.RuntimeSnapshot above.
func newSnapshot(o Options, records []connectors.RuntimeRecord, digest string) *Handler {
	h := &Handler{adminToken: o.AdminToken, runtimeURL: o.RuntimeURL, buildRevision: o.BuildRevision, catalogDigest: digest,
		observer: admissionObserver{}, routes: map[Route]bool{}, now: o.Now}
	if h.now == nil {
		h.now = time.Now
	}
	var routeNames []string
	for _, route := range o.Routes {
		if !h.routes[route] {
			h.routes[route] = true
			routeNames = append(routeNames, route.AppID+"/"+route.ProviderID)
		}
	}
	sort.Strings(routeNames)
	routeBytes, _ := json.Marshal(routeNames)
	sum := sha256.Sum256(routeBytes)
	h.routeRevision = hex.EncodeToString(sum[:])
	for _, record := range records {
		key := Route{record.AppID, record.ProviderID}
		if _, dup := h.observer[key]; dup {
			h.observer[key] = installedRecord{kind: record.Kind}
			continue
		}
		entry := installedRecord{kind: record.Kind, retired: record.Retired}
		obj, err := strictObject(record.Raw)
		if err == nil && obj["app"] == record.AppID && obj["id"] == record.ProviderID && obj["kind"] == record.Kind {
			entry.revision, _ = DeclarationRevision(record.Raw)
			entry.operations, entry.enabled = admittedEnablement(obj)
		}
		h.observer[key] = entry
	}
	return h
}

func admittedEnablement(record map[string]any) ([]string, bool) {
	gateway, _ := record["x-gateway"].(map[string]any)
	e, _ := gateway["connectionEnablement"].(map[string]any)
	// Check literal presence/type. Operations alone, a string "true", an old
	// schema, or enabled:false cannot become authority evidence, even when the
	// build's catalog contains a record at the requested revision.
	if len(e) != 3 || e["schemaVersion"] != json.Number("1") || e["enabled"] != true {
		return nil, false
	}
	values, ok := e["operations"].([]any)
	if !ok {
		return nil, false
	}
	var operations []string
	for _, value := range values {
		op, ok := value.(string)
		if !ok {
			return nil, false
		}
		operations = append(operations, op)
	}
	if !validOperations(operations) {
		return nil, false
	}
	sort.Slice(operations, func(i, j int) bool { return operationOrder(operations[i]) < operationOrder(operations[j]) })
	return operations, true
}

// BuildRevision is a build-time SHA-256 of immutable build inputs, not the
// human-readable release version, short VCS hash or mutable environment.
func validBuildID(id string) bool {
	return strings.HasPrefix(id, "sha256:") && digestPattern.MatchString(strings.TrimPrefix(id, "sha256:")) && id != "sha256:"+strings.Repeat("0", 64)
}

func (h *Handler) answer(ctx context.Context, req Request) map[string]any {
	key := Route{req.AppID, req.ProviderID}
	entry, found := h.observer[key]
	if !validBase(h.runtimeURL) || req.RuntimeURL != h.runtimeURL || !found || entry.retired ||
		(entry.kind != "rest" && entry.kind != "vendor_mcp") || (entry.kind == "rest" && !h.routes[key]) {
		return unavailable(req, "runtime_support_not_served")
	}
	if entry.revision == "" || entry.revision != req.Revision {
		return unavailable(req, "runtime_support_unknown_revision")
	}
	refuse := func() map[string]any { return unavailable(req, "runtime_support_lifecycle_unavailable") }
	if ctx.Err() != nil || !entry.enabled || !validOperations(entry.operations) || !validBuildID(h.buildRevision) || !digestPattern.MatchString(h.catalogDigest) {
		return refuse()
	}
	now := h.now()
	observed := now.UnixMilli()
	expires := observed + 1000
	if observed < 1 || observed > 9007199254739991 {
		return refuse()
	}
	identity := Identity{req.AppID, req.ProviderID, req.Revision, req.RuntimeURL}
	snapshot, _ := json.Marshal([]any{"admitted-bundle-r2", ResponseSchema, h.buildRevision, h.catalogDigest, h.runtimeURL, h.routeRevision, identity, entry.operations})
	sum := sha256.Sum256(snapshot)
	body := responseIdentity(req, "available")
	body["runtime_revision"] = "rs2-sha256:" + hex.EncodeToString(sum[:])
	body["operations"] = append([]string(nil), entry.operations...)
	body["observed_at_ms"] = observed
	body["expires_at_ms"] = expires
	body["lifecycle_contract"] = "declared-oauth2-v1"
	body["authority_contract"] = "gateway-authority-version-contract-r1"
	body["reason_code"] = ""
	end := h.now()
	if ctx.Err() != nil || end.Round(0).Before(now.Round(0)) || end.UnixMilli() >= expires {
		return refuse()
	}
	return body
}

func operationOrder(op string) int {
	switch op {
	case "connect":
		return 0
	case "list_tools":
		return 1
	case "invoke":
		return 2
	}
	return -1
}

func validOperations(ops []string) bool {
	if len(ops) < 1 || len(ops) > 3 {
		return false
	}
	seen := map[string]bool{}
	for _, op := range ops {
		if operationOrder(op) < 0 || seen[op] {
			return false
		}
		seen[op] = true
	}
	return true
}
