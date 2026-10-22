package server

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
)

// creditsAdmin calls an admin credits route on g, with the admin token
// unless token is "-".
func creditsAdmin(t *testing.T, g *managedTestGateway, method, path, body, token string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(method, path, bytes.NewBufferString(body))
	if token != "-" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// goldenPart is one top-level member of a contract golden example.
func goldenPart(t *testing.T, name, part string) json.RawMessage {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(capExamplesDir, name))
	if err != nil {
		t.Fatal(err)
	}
	var parts map[string]json.RawMessage
	if err := json.Unmarshal(raw, &parts); err != nil {
		t.Fatal(err)
	}
	return parts[part]
}

// validateCreditsV1 checks body against credits-v1's schema. The package's
// validator has no "maximum", which the schema puts only on
// admin.markup_percent: that bound is checked here and dropped before the
// rest is validated.
func validateCreditsV1(t *testing.T, body []byte) {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(capExamplesDir, "credits-v1.schema.json"))
	if err != nil {
		t.Fatal(err)
	}
	var schema map[string]any
	if err := json.Unmarshal(raw, &schema); err != nil {
		t.Fatal(err)
	}
	markup := schema["$defs"].(map[string]any)["Admin"].(map[string]any)["properties"].(map[string]any)["markup_percent"].(map[string]any)
	limit, ok := markup["maximum"].(float64)
	if !ok {
		t.Fatal("credits-v1 does not bound admin.markup_percent with maximum; update this check")
	}
	delete(markup, "maximum")
	var v struct {
		Admin struct {
			MarkupPercent float64 `json:"markup_percent"`
		} `json:"admin"`
	}
	if err := json.Unmarshal(body, &v); err != nil {
		t.Fatal(err)
	}
	if v.Admin.MarkupPercent > limit {
		t.Errorf("credits-v1: admin.markup_percent %v is over its maximum %v", v.Admin.MarkupPercent, limit)
	}
	var doc any
	if err := json.Unmarshal(body, &doc); err != nil {
		t.Fatal(err)
	}
	for _, problem := range checkSchema(schema, schema, doc, "$") {
		t.Errorf("credits-v1.schema.json: %s", problem)
	}
}

// unavailableGateway is the managed test gateway on a ledger with no store:
// a gateway that requires Redis and has none.
func unavailableGateway(t *testing.T) *managedTestGateway {
	g := newManagedTestGateway(t, managedOptIn())
	g.srv.managed.Ledger = creditsplugin.NewUnavailableOrgLedger()
	return g
}

func TestCreditsGrantedPush(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	push := func(body string) *httptest.ResponseRecorder {
		return creditsAdmin(t, g, "PUT", "/-/orgs/org-m/credits/granted", body, adminToken)
	}

	first := `{"granted_total_micros": 100000000, "version": 7, "settled_spend_floor_micros": 0}`
	w := push(first)
	if w.Code != http.StatusOK {
		t.Fatalf("push: %d %s", w.Code, w.Body)
	}
	validateAgainstSchema(t, []byte(`{"request":`+first+`,"response":`+w.Body.String()+`}`), "credits-granted-push-v1.schema.json")
	var res grantedPushView
	json.Unmarshal(w.Body.Bytes(), &res)
	if !res.Applied || res.GrantedTotalMicros != 100_000_000 || res.GrantedVersion != 7 || res.AvailableMicros != 100_000_000 {
		t.Fatalf("first push = %+v", res)
	}

	// Spend some, then re-push the same version: not applied, nothing moves.
	ctx := context.Background()
	g.ledger.Reserve(ctx, "org-m", "req-1", 50_000, time.Minute, billing.ReserveNew)
	g.ledger.Settle(ctx, "org-m", "req-1", billing.Settlement{BilledMicros: 41_262_000})
	g.ledger.Reserve(ctx, "org-m", "req-2", 40_000, time.Minute, billing.ReserveNew)
	json.Unmarshal(push(first).Body.Bytes(), &res)
	if res.Applied || res.SpentMicros != 41_262_000 || res.HeldMicros != 40_000 || res.AvailableMicros != 58_698_000 {
		t.Fatalf("re-pushed version 7 = %+v, want not applied", res)
	}

	// A newer version with a settled-spend floor above spent raises spent.
	w = push(`{"granted_total_micros": 120000000, "version": 8, "settled_spend_floor_micros": 50000000}`)
	json.Unmarshal(w.Body.Bytes(), &res)
	if !res.Applied || res.GrantedTotalMicros != 120_000_000 || res.SpentMicros != 50_000_000 || res.AvailableMicros != 69_960_000 {
		t.Fatalf("version 8 with a floor = %+v", res)
	}

	for _, bad := range []string{
		`{"granted_total_micros": 1, "version": 9}`,
		`{"granted_total_micros": -1, "version": 9, "settled_spend_floor_micros": 0}`,
		`{"granted_total_micros": 1, "version": 9, "settled_spend_floor_micros": 0, "balance_usd": 3}`,
		`{"granted_total_micros": 1.5, "version": 9, "settled_spend_floor_micros": 0}`,
		`not json`,
	} {
		if w := push(bad); w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), "invalid_body") {
			t.Errorf("push %s = %d %s, want 400 invalid_body", bad, w.Code, w.Body)
		}
	}
	if w := creditsAdmin(t, g, "PUT", "/-/orgs/org-m/credits/granted", first, "-"); w.Code != http.StatusUnauthorized {
		t.Errorf("push without the admin token = %d", w.Code)
	}
	if w := creditsAdmin(t, unavailableGateway(t), "PUT", "/-/orgs/org-m/credits/granted", first, adminToken); w.Code != http.StatusServiceUnavailable ||
		!strings.Contains(w.Body.String(), "managed_billing_unavailable") {
		t.Errorf("push with no store = %d %s, want 503", w.Code, w.Body)
	}
}

func TestLegacyCreditRoutes410(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	g.grant(t, "org-m", 5_000_000)
	for _, tc := range []struct{ method, body string }{
		{"POST", `{"amount_usd": 10}`},
		{"PUT", `{"balance_usd": 3}`},
	} {
		w := creditsAdmin(t, g, tc.method, "/-/orgs/org-m/credits", tc.body, adminToken)
		if w.Code != http.StatusGone || !strings.Contains(w.Body.String(), `"code":"endpoint_retired"`) ||
			!strings.Contains(w.Body.String(), "/credits/granted") {
			t.Errorf("%s /credits = %d %s, want 410 endpoint_retired naming the granted push", tc.method, w.Code, w.Body)
		}
		if w := creditsAdmin(t, g, tc.method, "/-/orgs/org-m/credits", tc.body, "-"); w.Code != http.StatusUnauthorized {
			t.Errorf("%s /credits without the admin token = %d, want 401", tc.method, w.Code)
		}
	}
	if bal := g.ledger.BalanceMicros("org-m"); bal != 5_000_000 {
		t.Fatalf("a retired route changed the balance: %d", bal)
	}
	if w := creditsAdmin(t, g, "GET", "/-/orgs/org-m/credits", "", adminToken); w.Code != http.StatusOK {
		t.Fatalf("GET /credits = %d, still served", w.Code)
	}
}

func TestCreditsGetKeepsBalanceUSD(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	ctx := context.Background()
	g.ledger.Grant(ctx, "org-m", billing.GrantPush{GrantedTotalMicros: 100_000_000, Version: 7})
	for _, id := range []string{"req-1", "req-2", "req-3"} {
		g.ledger.Reserve(ctx, "org-m", id, 50_000, time.Minute, billing.ReserveNew)
	}
	g.ledger.Settle(ctx, "org-m", "req-1", billing.Settlement{BilledMicros: 41_252_000})
	g.ledger.Settle(ctx, "org-m", "req-2", billing.Settlement{BilledMicros: 10_000})
	g.ledger.Void(ctx, "org-m", "req-3", billing.Settlement{})
	g.ledger.Reserve(ctx, "org-m", "req-4", 40_000, time.Minute, billing.ReserveNew)

	w := creditsAdmin(t, g, "GET", "/-/orgs/org-m/credits", "", adminToken)
	if w.Code != http.StatusOK {
		t.Fatalf("GET: %d %s", w.Code, w.Body)
	}
	validateCreditsV1(t, []byte(`{"admin":`+w.Body.String()+`,"customer":`+string(goldenPart(t, "credits-v1.json", "customer"))+`}`))
	var got, want map[string]any
	json.Unmarshal(w.Body.Bytes(), &got)
	json.Unmarshal(goldenPart(t, "credits-v1.json", "admin"), &want)
	want["ledger"] = "memory"
	want["org_id"] = "org-m" // the golden's own org id is an example
	for k, v := range want {
		if got[k] != v {
			t.Errorf("%s = %v, want %v (golden credits-v1 admin, on a memory ledger)", k, got[k], v)
		}
	}

	if w := creditsAdmin(t, g, "GET", "/-/orgs/org-new/credits", "", adminToken); !strings.Contains(w.Body.String(), `"balance_usd":0`) {
		t.Errorf("an org never pushed: %s, want zeros", w.Body)
	}
	if w := creditsAdmin(t, unavailableGateway(t), "GET", "/-/orgs/org-m/credits", "", adminToken); w.Code != http.StatusServiceUnavailable ||
		!strings.Contains(w.Body.String(), "redis_unavailable") {
		t.Fatalf("GET with no store = %d %s, want 503 managed_billing_unavailable", w.Code, w.Body)
	}
}

// The admin credits body names the org it belongs to, and it validates
// against credits-v1, whose $defs/Admin requires org_id.
func TestCreditsAdminCarriesOrgID(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	g.ledger.Grant(context.Background(), "org-m", billing.GrantPush{GrantedTotalMicros: 5_000_000, Version: 1})
	for _, org := range []string{"org-m", "org-new"} {
		w := creditsAdmin(t, g, "GET", "/-/orgs/"+org+"/credits", "", adminToken)
		if w.Code != http.StatusOK {
			t.Fatalf("GET %s: %d %s", org, w.Code, w.Body)
		}
		var got map[string]any
		if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil {
			t.Fatal(err)
		}
		if got["org_id"] != org {
			t.Fatalf("GET %s: org_id = %v", org, got["org_id"])
		}
		validateCreditsV1(t, []byte(`{"admin":`+w.Body.String()+`,"customer":`+string(goldenPart(t, "credits-v1.json", "customer"))+`}`))
	}
	var noOrg map[string]any
	json.Unmarshal(goldenPart(t, "credits-v1.json", "admin"), &noOrg)
	delete(noOrg, "org_id")
	b, _ := json.Marshal(map[string]any{"admin": noOrg, "customer": json.RawMessage(goldenPart(t, "credits-v1.json", "customer"))})
	raw, err := os.ReadFile(filepath.Join(capExamplesDir, "credits-v1.schema.json"))
	if err != nil {
		t.Fatal(err)
	}
	var schema, doc any
	json.Unmarshal(raw, &schema)
	json.Unmarshal(b, &doc)
	if problems := checkSchema(schema.(map[string]any), schema.(map[string]any), doc, "$"); len(problems) == 0 {
		t.Fatal("an admin body without org_id validated; credits-v1 must require it")
	}
}
