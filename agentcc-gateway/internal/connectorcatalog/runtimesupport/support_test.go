package runtimesupport

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/catalog/connectors"
)

const syntheticToken = "unit-only-0123456789abcdef0123456789abcdef"
const syntheticBase = "https://runtime.invalid/base"
const syntheticBuild = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
const syntheticRecord = `{"app":"sample-app","id":"native-rest","version":"1.2.3","kind":"rest","lifecycle":{"status":"active"},"x-gateway":{"connectionEnablement":{"schemaVersion":1,"enabled":true,"operations":["connect","list_tools","invoke"]}},"note":"café <>& /"}`

// This models the private post-admission snapshot boundary, NOT generator
// approval of a real provider. Production New cannot accept these raw records.
func fixture(t *testing.T) (Options, Request, []connectors.RuntimeRecord) {
	t.Helper()
	rev, err := DeclarationRevision([]byte(syntheticRecord))
	if err != nil {
		t.Fatal(err)
	}
	now := time.UnixMilli(1800000000000)
	o := Options{AdminToken: syntheticToken, RuntimeURL: syntheticBase, BuildRevision: syntheticBuild,
		Routes: []Route{{"sample-app", "native-rest"}}, Now: func() time.Time { return now }}
	req := Request{Schema: RequestSchema, AppID: "sample-app", ProviderID: "native-rest", Revision: rev, RuntimeURL: syntheticBase, RequestID: strings.Repeat("a", 32)}
	return o, req, []connectors.RuntimeRecord{{AppID: req.AppID, ProviderID: req.ProviderID, Kind: "rest", Raw: []byte(syntheticRecord)}}
}

func snapshot(o Options, records []connectors.RuntimeRecord) *Handler {
	return newSnapshot(o, records, strings.Repeat("b", 64))
}

func post(t *testing.T, h http.Handler, req Request, token string) (*httptest.ResponseRecorder, map[string]any) {
	t.Helper()
	data, err := json.Marshal(req)
	if err != nil {
		t.Fatal(err)
	}
	r := httptest.NewRequest(http.MethodPost, Path, strings.NewReader(string(data)))
	r.Header.Set("Content-Type", "application/json")
	if token != "" {
		r.Header.Set("Authorization", "Bearer "+token)
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	var body map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if w.Header().Get("Cache-Control") != "no-store" {
		t.Fatal("cacheable response")
	}
	return w, body
}

func TestSupportExactAdmittedRevision(t *testing.T) {
	o, req, records := fixture(t)
	h := snapshot(o, records)
	w, body := post(t, h, req, syntheticToken)
	if w.Code != 200 || body["state"] != "available" || len(body) != 14 {
		t.Fatalf("%d: %s", w.Code, w.Body)
	}
	for key, want := range map[string]any{"schema": ResponseSchema, "app_id": req.AppID, "provider_id": req.ProviderID, "revision": req.Revision, "runtime_url": req.RuntimeURL, "request_id": req.RequestID, "reason_code": "", "lifecycle_contract": "declared-oauth2-v1", "authority_contract": "gateway-authority-version-contract-r1"} {
		if body[key] != want {
			t.Fatalf("%s = %v", key, body[key])
		}
	}
	if !reflect.DeepEqual(body["operations"], []any{"connect", "list_tools", "invoke"}) {
		t.Fatal(body)
	}
	if body["observed_at_ms"] != float64(o.Now().UnixMilli()) || body["expires_at_ms"] != float64(o.Now().Add(time.Second).UnixMilli()) {
		t.Fatal("wrong lease")
	}
	req.RequestID = strings.Repeat("b", 32)
	_, again := post(t, h, req, syntheticToken)
	if again["request_id"] != req.RequestID || again["runtime_revision"] != body["runtime_revision"] {
		t.Fatal("reused request or unstable snapshot")
	}
	o.BuildRevision = "sha256:" + strings.Repeat("c", 64)
	w, next := post(t, snapshot(o, records), req, syntheticToken)
	if w.Code != 200 || next["state"] != "available" || len(next) != 14 {
		t.Fatalf("changed build refused: %d: %s", w.Code, w.Body)
	}
	if next["runtime_revision"] == body["runtime_revision"] {
		t.Fatal("build not bound")
	}
	buildRevision := next["runtime_revision"]
	// Hold the build and every other input fixed; change only the catalog digest.
	w, next = post(t, newSnapshot(o, records, strings.Repeat("d", 64)), req, syntheticToken)
	if w.Code != 200 || next["state"] != "available" || len(next) != 14 {
		t.Fatalf("changed catalog refused: %d: %s", w.Code, w.Body)
	}
	if next["runtime_revision"] == buildRevision {
		t.Fatal("catalog not bound")
	}
}

func TestSupportAdmissionRefusals(t *testing.T) {
	cases := []struct {
		name, reason string
		change       func(*Options, *Request, []connectors.RuntimeRecord)
	}{
		{"sha_mismatch", "runtime_support_unknown_revision", func(o *Options, r *Request, rs []connectors.RuntimeRecord) {
			r.Revision = "1.2.3+sha256:" + strings.Repeat("0", 64)
		}},
		{"same_sha_other_version", "runtime_support_unknown_revision", func(o *Options, r *Request, rs []connectors.RuntimeRecord) {
			r.Revision = strings.Replace(r.Revision, "1.2.3", "1.2.4", 1)
		}},
		{"unknown_app", "runtime_support_not_served", func(o *Options, r *Request, rs []connectors.RuntimeRecord) { r.AppID = "other-app" }},
		{"unknown_provider", "runtime_support_not_served", func(o *Options, r *Request, rs []connectors.RuntimeRecord) { r.ProviderID = "other-provider" }},
		{"wrong_audience", "runtime_support_not_served", func(o *Options, r *Request, rs []connectors.RuntimeRecord) { r.RuntimeURL = "https://other.invalid" }},
		{"hidden_route", "runtime_support_not_served", func(o *Options, r *Request, rs []connectors.RuntimeRecord) { o.Routes = nil }},
		{"sunset", "runtime_support_not_served", func(o *Options, r *Request, rs []connectors.RuntimeRecord) { rs[0].Retired = true }},
		{"no_record_bytes", "runtime_support_unknown_revision", func(o *Options, r *Request, rs []connectors.RuntimeRecord) { rs[0].Raw = nil }},
		{"wrong_raw_identity", "runtime_support_unknown_revision", func(o *Options, r *Request, rs []connectors.RuntimeRecord) {
			rs[0].Raw = []byte(strings.Replace(syntheticRecord, "sample-app", "other-app", 1))
		}},
		{"unbound_build", "runtime_support_lifecycle_unavailable", func(o *Options, r *Request, rs []connectors.RuntimeRecord) { o.BuildRevision = "" }},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			o, r, rs := fixture(t)
			tc.change(&o, &r, rs)
			w, b := post(t, snapshot(o, rs), r, syntheticToken)
			if w.Code != 200 || b["state"] != "unavailable" || b["reason_code"] != tc.reason || len(b) != 8 {
				t.Fatalf("%d: %s", w.Code, w.Body)
			}
		})
	}
}

func TestSupportEnablementMustBeExplicitAndExact(t *testing.T) {
	for _, declaration := range []string{`null`, `{}`, `{"schemaVersion":1,"enabled":false,"operations":[]}`, `{"schemaVersion":1,"enabled":false,"operations":["connect"]}`, `{"schemaVersion":1,"operations":["connect"]}`, `{"schemaVersion":1,"enabled":"true","operations":["connect"]}`, `{"schemaVersion":2,"enabled":true,"operations":["connect"]}`, `{"schemaVersion":1,"enabled":true,"operations":[]}`, `{"schemaVersion":1,"enabled":true,"operations":["connect","connect"]}`, `{"schemaVersion":1,"enabled":true,"operations":["other"]}`, `{"schemaVersion":1,"enabled":true,"operations":["connect"],"authority":true}`} {
		o, req, rs := fixture(t)
		var obj map[string]json.RawMessage
		_ = json.Unmarshal(rs[0].Raw, &obj)
		obj["x-gateway"] = json.RawMessage(`{"connectionEnablement":` + declaration + `}`)
		rs[0].Raw, _ = json.Marshal(obj)
		req.Revision, _ = DeclarationRevision(rs[0].Raw)
		w, b := post(t, snapshot(o, rs), req, syntheticToken)
		if b["reason_code"] != "runtime_support_lifecycle_unavailable" || len(b) != 8 {
			t.Fatalf("%s: %s", declaration, w.Body)
		}
	}
	o, req, rs := fixture(t)
	rs[0].Raw = []byte(`{"app":"sample-app","id":"native-rest","version":"1.2.3","kind":"rest"}`)
	req.Revision, _ = DeclarationRevision(rs[0].Raw)
	_, b := post(t, snapshot(o, rs), req, syntheticToken)
	if b["reason_code"] != "runtime_support_lifecycle_unavailable" {
		t.Fatal("absent schema became support")
	}
}

func TestSupportImmutableBuildRequired(t *testing.T) {
	for _, build := range []string{"", "dev", "unknown", "unbound", "latest", "v1.2.3", "613951c5", strings.Repeat("a", 40), "sha256:" + strings.Repeat("0", 64), "sha256:" + strings.Repeat("A", 64), syntheticBuild + "-dirty"} {
		o, r, rs := fixture(t)
		o.BuildRevision = build
		_, b := post(t, snapshot(o, rs), r, syntheticToken)
		if b["state"] != "unavailable" {
			t.Fatalf("accepted build %q", build)
		}
	}
}

func TestSupportSnapshotCopiesAndRebuildWithdrawal(t *testing.T) {
	o, r, rs := fixture(t)
	h := snapshot(o, rs)
	rs[0].Raw[0] = 'x'
	o.Routes[0].ProviderID = "other"
	_, b := post(t, h, r, syntheticToken)
	if b["state"] != "available" {
		t.Fatal("caller mutation changed snapshot")
	}
	o, r, rs = fixture(t)
	rs[0].Raw = []byte(strings.Replace(syntheticRecord, `"enabled":true`, `"enabled":false`, 1))
	_, b = post(t, snapshot(o, rs), r, syntheticToken)
	if b["reason_code"] != "runtime_support_unknown_revision" {
		t.Fatal("old digest survived withdrawal")
	}
	r.Revision, _ = DeclarationRevision(rs[0].Raw)
	_, b = post(t, snapshot(o, rs), r, syntheticToken)
	if b["reason_code"] != "runtime_support_lifecycle_unavailable" {
		t.Fatal("disabled revision supported")
	}
}

func TestSupportVendorUsesAdmissionNotRESTRoutes(t *testing.T) {
	o, r, rs := fixture(t)
	o.Routes = nil
	rs[0].Kind = "vendor_mcp"
	rs[0].Raw = []byte(strings.Replace(syntheticRecord, `"kind":"rest"`, `"kind":"vendor_mcp"`, 1))
	r.Revision, _ = DeclarationRevision(rs[0].Raw)
	_, b := post(t, snapshot(o, rs), r, syntheticToken)
	if b["state"] != "available" {
		t.Fatal("exact admitted vendor refused")
	}
	_, b = post(t, New(o), r, syntheticToken)
	if b["state"] != "unavailable" {
		t.Fatal("missing embedded admission blessed vendor")
	}
}

func TestSupportAuthenticationBeforeParsing(t *testing.T) {
	for _, mode := range []string{"missing", "wrong", "empty_config", "duplicate"} {
		t.Run(mode, func(t *testing.T) {
			o, _, rs := fixture(t)
			token := syntheticToken
			if mode == "empty_config" {
				o.AdminToken = ""
			}
			if mode == "wrong" {
				token = "wrong"
			}
			r := httptest.NewRequest("POST", Path, strings.NewReader("not json"))
			if mode != "missing" {
				r.Header.Set("Authorization", "Bearer "+token)
			}
			if mode == "duplicate" {
				r.Header.Add("Authorization", "Bearer "+token)
			}
			w := httptest.NewRecorder()
			snapshot(o, rs).ServeHTTP(w, r)
			if w.Code != 401 || w.Body.String() != "{\"error\":\"unauthorized\"}\n" {
				t.Fatalf("%d: %s", w.Code, w.Body)
			}
		})
	}
}

func TestSupportMalformedRequests(t *testing.T) {
	o, r, rs := fixture(t)
	good, _ := json.Marshal(r)
	cases := []string{"", "null", "[]", "{}", string(good) + "{}", strings.Replace(string(good), `"app_id":`, `"app_id":"sample-app","app_id":`, 1), strings.Replace(string(good), `"app_id":"sample-app"`, `"app_id":null`, 1), string([]byte{0xff}), strings.Repeat(" ", 16385) + string(good)}
	for _, field := range []string{`"operations":["connect"],`, `"authority_version":1,`, `"grant":"synthetic",`, `"extra":{"nested":1},`} {
		cases = append(cases, strings.Replace(string(good), `"schema":`, field+`"schema":`, 1))
	}
	for _, base := range []string{"http://runtime.invalid/base", "https://user@runtime.invalid/base", "https://runtime.invalid/base?x=1", "https://runtime.invalid:0/base", "https://runtime.invalid/base#"} {
		cases = append(cases, strings.Replace(string(good), syntheticBase, base, 1))
	}
	for i, data := range cases {
		req := httptest.NewRequest("POST", Path, strings.NewReader(data))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer "+syntheticToken)
		w := httptest.NewRecorder()
		snapshot(o, rs).ServeHTTP(w, req)
		if w.Code != 400 {
			t.Errorf("case %d status %d", i, w.Code)
		}
	}
}

func TestSupportMethodAndContentType(t *testing.T) {
	o, _, rs := fixture(t)
	for _, tc := range []struct {
		method, media string
		status        int
	}{{"GET", "application/json", 405}, {"POST", "text/plain", 400}} {
		r := httptest.NewRequest(tc.method, Path, strings.NewReader("{}"))
		r.Header.Set("Authorization", "Bearer "+syntheticToken)
		r.Header.Set("Content-Type", tc.media)
		w := httptest.NewRecorder()
		snapshot(o, rs).ServeHTTP(w, r)
		if w.Code != tc.status {
			t.Fatal(w.Code)
		}
	}
}

func TestSupportDuplicateAndUnboundCatalogRefuse(t *testing.T) {
	o, r, rs := fixture(t)
	rs = append(rs, rs[0])
	_, b := post(t, snapshot(o, rs), r, syntheticToken)
	if b["reason_code"] != "runtime_support_unknown_revision" {
		t.Fatal("duplicate accepted")
	}
	o, r, rs = fixture(t)
	_, b = post(t, newSnapshot(o, rs, ""), r, syntheticToken)
	if b["reason_code"] != "runtime_support_lifecycle_unavailable" {
		t.Fatal("unbound catalog accepted")
	}
}
