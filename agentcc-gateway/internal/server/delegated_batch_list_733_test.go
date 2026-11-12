package server

// 733 add.13(3): FA's delegated batch recovery lists the calling org's
// batches (org_id and key_id=dlg_<jti>) with the same fe1 envelope and batch
// grant that submit uses. The admin token lists every org's batches. Both
// modes answer the same metadata-only rows.
import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const list733OrgB = "44444444-4444-4444-8444-444444444444"
const list733UserB = "55555555-5555-4555-8555-555555555555"
const list733BatchBody = `{"requests":[` + delegated733Body + `],"max_concurrency":1}`

// signedAs733 is signed733 for any org and person, and any request method.
func signedAs733(method, route, body, purpose, org, user, secret string, at int64) (string, string, []string) {
	id := nonce733()
	sub := "chat:33333333-3333-4333-8333-333333333333"
	if purpose == "batch" {
		sub = "batch:33333333-3333-4333-8333-333333333333"
	}
	grant := wire733([]string{"fe1", "grant", "k1", "fa", "fa-eval-grant", "-", "-", fmt.Sprint(at), fmt.Sprint(at + 600), id, purpose, org, "-", user, "person", sub, "eval", "-", "-"}, "test-only-fa-secret-unavailable-to-gateway")
	fields := []string{"fe1", "request", "k1", "fa", "gw-delegated", method, route, fmt.Sprint(at), fmt.Sprint(at + 60), nonce733(), "-", org, "-", user, "grant:" + id, "-", purpose, digest733(body), digest733(grant)}
	return wire733(fields, secret), grant, fields
}

type list733Rig struct {
	srv *Server
	// batch id -> dlg_<jti> of the grant that submitted it, per org.
	a, b map[string]string
}

func newList733Rig(t *testing.T) *list733Rig {
	t.Helper()
	srv := newDelegated733Server(t)
	// An epoch far enough back that an expired envelope is refused for its
	// expiry, not for predating the restart rule.
	srv.delegated = auth.NewEnvelopeVerifier(delegated733Secret, time.Now().Add(-10*time.Minute))
	srv.TenantStore.Set(list733OrgB, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"openai": {Enabled: true, APIKey: "test-only-org-b-provider", Models: []string{"gpt-4o-mini"}},
	}})
	srv.keyStore.SyncDelegations(&auth.DelegationSnapshot{Complete: true, Members: []auth.DelegatedMember{
		{Org: delegated733Org, User: delegated733User, Workspace: "-"},
		{Org: list733OrgB, User: list733UserB, Workspace: "-"},
	}}, time.Now())
	rig := &list733Rig{srv: srv, a: map[string]string{}, b: map[string]string{}}
	for i := 0; i < 2; i++ {
		id, key := rig.submit(t, delegated733Org, delegated733User)
		rig.a[id] = key
	}
	id, key := rig.submit(t, list733OrgB, list733UserB)
	rig.b[id] = key
	for id := range rig.all() {
		b, ok := srv.batchProcessor.Get(id)
		if !ok {
			t.Fatal("submitted batch missing")
		}
		for deadline := time.Now().Add(5 * time.Second); b.GetCompletedAt() == nil; time.Sleep(time.Millisecond) {
			if time.Now().After(deadline) {
				t.Fatal("batch did not finish")
			}
		}
	}
	return rig
}

func (rig *list733Rig) all() map[string]string {
	out := map[string]string{}
	for id, key := range rig.a {
		out[id] = key
	}
	for id, key := range rig.b {
		out[id] = key
	}
	return out
}

func (rig *list733Rig) submit(t *testing.T, org, user string) (string, string) {
	t.Helper()
	env, grant, fields := signedAs733("POST", "/-/batches", list733BatchBody, "batch", org, user, delegated733Secret, time.Now().Unix())
	w := request733(rig.srv, "/-/batches", list733BatchBody, env, grant)
	if w.Code != http.StatusAccepted {
		t.Fatalf("submit for %s: %d %s", org, w.Code, w.Body.String())
	}
	var out struct {
		ID    string `json:"batch_id"`
		Org   string `json:"org_id"`
		KeyID string `json:"key_id"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &out); err != nil {
		t.Fatal(err)
	}
	want := "dlg_" + strings.TrimPrefix(fields[14], "grant:")
	if out.ID == "" || out.Org != org || out.KeyID != want {
		t.Fatalf("submit identity: %+v want org %s key %s", out, org, want)
	}
	return out.ID, want
}

// list sends GET /-/batches with the given envelope, grant and bearer.
func (rig *list733Rig) list(env, grant, query, bearer string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(http.MethodGet, "/-/batches"+query, nil)
	if env != "" {
		r.Header.Set(auth.EnvelopeHeader, env)
	}
	if grant != "" {
		r.Header.Set(auth.GrantHeader, grant)
	}
	if bearer != "" {
		r.Header.Set("Authorization", "Bearer "+bearer)
	}
	w := httptest.NewRecorder()
	rig.srv.httpServer.Handler.ServeHTTP(w, r)
	return w
}

// orgList signs a fresh GET listing envelope for org's person.
func (rig *list733Rig) orgList(org, user, query, bearer string) *httptest.ResponseRecorder {
	env, grant, _ := signedAs733("GET", "/-/batches", "", "batch", org, user, delegated733Secret, time.Now().Unix())
	return rig.list(env, grant, query, bearer)
}

func list733Rows(t *testing.T, w *httptest.ResponseRecorder) []map[string]any {
	t.Helper()
	if w.Code != http.StatusOK {
		t.Fatalf("listing: %d %s", w.Code, w.Body.String())
	}
	var out struct {
		Batches []map[string]any `json:"batches"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &out); err != nil {
		t.Fatal(err)
	}
	return out.Batches
}

func list733IDs(rows []map[string]any) []string {
	ids := []string{}
	for _, row := range rows {
		id, _ := row["batch_id"].(string)
		ids = append(ids, id)
	}
	sort.Strings(ids)
	return ids
}

func list733Want(m map[string]string) []string {
	ids := []string{}
	for id := range m {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	return ids
}

// The org listing is exactly the org's rows, each naming its own delegated
// key and the credential-free summary FA needs, and nothing else.
func assertOrgListing733(t *testing.T, rig *list733Rig, w *httptest.ResponseRecorder, org string, want map[string]string) {
	t.Helper()
	rows := list733Rows(t, w)
	if got := list733IDs(rows); fmt.Sprint(got) != fmt.Sprint(list733Want(want)) {
		t.Fatalf("org %s listing ids %v, want exactly %v", org, got, list733Want(want))
	}
	allowed := map[string]bool{"error_code": true}
	for _, field := range list733DelegatedKeys {
		allowed[field] = true
	}
	for _, row := range rows {
		for field := range row {
			if !allowed[field] {
				t.Errorf("listing row carries %q: %v", field, row)
			}
		}
		id := row["batch_id"].(string)
		if row["org_id"] != org || row["key_id"] != want[id] {
			t.Errorf("row %s identity: org=%v key=%v want org=%s key=%s", id, row["org_id"], row["key_id"], org, want[id])
		}
		if row["status"] != "completed" || row["total"] != float64(1) {
			t.Errorf("row %s state: %v", id, row)
		}
		if _, ok := row["created_at"].(string); !ok {
			t.Errorf("row %s has no created_at: %v", id, row)
		}
		if _, ok := row["completed_at"].(string); !ok {
			t.Errorf("row %s has no completed_at: %v", id, row)
		}
		counts, ok := row["counts"].(map[string]any)
		if !ok || counts["completed"] != float64(1) || counts["failed"] != float64(0) || counts["cancelled"] != float64(0) {
			t.Errorf("row %s counts: %v", id, row["counts"])
		}
	}
	raw := w.Body.String()
	for _, leak := range []string{"hello", "results", "messages", "fe1.", "test-only"} {
		if strings.Contains(raw, leak) {
			t.Errorf("listing leaks %q: %s", leak, raw)
		}
	}
	for id := range rig.all() {
		if _, mine := want[id]; !mine && strings.Contains(raw, id) {
			t.Errorf("org %s listing names another org's batch %s", org, id)
		}
	}
}

func TestDelegated733BatchListTwoOrgs(t *testing.T) {
	rig := newList733Rig(t)
	assertOrgListing733(t, rig, rig.orgList(delegated733Org, delegated733User, "", ""), delegated733Org, rig.a)
	assertOrgListing733(t, rig, rig.orgList(list733OrgB, list733UserB, "", ""), list733OrgB, rig.b)
	// FA's delegated transport also sends its admin bearer: the envelope
	// still scopes the listing to its org, never the ops inventory.
	assertOrgListing733(t, rig, rig.orgList(delegated733Org, delegated733User, "", delegated733Secret), delegated733Org, rig.a)
	assertOrgListing733(t, rig, rig.orgList(list733OrgB, list733UserB, "", delegated733Secret), list733OrgB, rig.b)
	if len(rig.srv.keyStore.List()) != 0 {
		t.Fatal("listing or submit minted a key")
	}
}

// The admin token alone still lists every org's batches, each with the same
// summary row as the delegated listing: identity, counts and timestamps.
func TestDelegated733BatchListAdminInventoryUnchanged(t *testing.T) {
	rig := newList733Rig(t)
	rows := list733Rows(t, rig.list("", "", "", delegated733Secret))
	if got := list733IDs(rows); fmt.Sprint(got) != fmt.Sprint(list733Want(rig.all())) {
		t.Fatalf("admin inventory %v, want both orgs %v", got, list733Want(rig.all()))
	}
	for _, row := range rows {
		id := row["batch_id"].(string)
		org := delegated733Org
		if _, ok := rig.b[id]; ok {
			org = list733OrgB
		}
		if row["org_id"] != org || row["key_id"] != rig.all()[id] || row["status"] != "completed" || row["total"] != float64(1) {
			t.Errorf("admin row %s: %v", id, row)
		}
		counts, ok := row["counts"].(map[string]any)
		if !ok || counts["completed"] != float64(1) || counts["failed"] != float64(0) || counts["cancelled"] != float64(0) {
			t.Errorf("admin row %s counts: %v", id, row["counts"])
		}
		for _, field := range []string{"results", "max_concurrency"} {
			if _, ok := row[field]; ok {
				t.Errorf("admin inventory carries %s: %v", field, row)
			}
		}
	}
	if w := rig.list("", "", "", ""); w.Code != http.StatusUnauthorized {
		t.Fatalf("no credential: %d %s", w.Code, w.Body.String())
	}
	if w := rig.list("", "", "", "test-only-not-the-admin-token"); w.Code != http.StatusUnauthorized {
		t.Fatalf("wrong admin token: %d %s", w.Code, w.Body.String())
	}
}

func TestDelegated733BatchListRefusesBadEnvelopes(t *testing.T) {
	rig := newList733Rig(t)
	refused := func(name string, w *httptest.ResponseRecorder) {
		t.Helper()
		if w.Code != http.StatusUnauthorized {
			t.Errorf("%s: %d %s", name, w.Code, w.Body.String())
			return
		}
		for id := range rig.all() {
			if strings.Contains(w.Body.String(), id) {
				t.Errorf("%s: refusal names batch %s", name, id)
			}
		}
	}
	now := time.Now().Unix()
	env, grant, _ := signedAs733("GET", "/-/batches", "", "batch", delegated733Org, delegated733User, "test-only-forged-secret-not-the-admin-token", now)
	refused("forged", rig.list(env, grant, "", ""))
	refused("forged with admin bearer", rig.list(env, grant, "", delegated733Secret))
	env, grant, _ = signedAs733("GET", "/-/batches", "", "batch", delegated733Org, delegated733User, delegated733Secret, now-100)
	refused("expired", rig.list(env, grant, "", ""))
	env, grant, _ = signedAs733("GET", "/-/batches", "", "batch", delegated733Org, delegated733User, delegated733Secret, now)
	if w := rig.list(env, grant, "", ""); w.Code != http.StatusOK {
		t.Fatalf("first use: %d %s", w.Code, w.Body.String())
	}
	refused("replay", rig.list(env, grant, "", ""))
	// A query cannot widen or move the org; a signed envelope has no query.
	refused("org override", rig.orgList(delegated733Org, delegated733User, "?org_id="+list733OrgB, ""))
	env, grant, _ = signedAs733("POST", "/-/batches", "", "batch", delegated733Org, delegated733User, delegated733Secret, now)
	refused("POST envelope sent as GET", rig.list(env, grant, "", ""))
	env, grant, _ = signedAs733("GET", "/-/batches", "", "playground", delegated733Org, delegated733User, delegated733Secret, now)
	refused("playground grant", rig.list(env, grant, "", ""))
	env, grant, _ = signedAs733("GET", "/-/batches", "", "batch", delegated733Org, delegated733User, delegated733Secret, now)
	refused("envelope without grant", rig.list(env, "", "", delegated733Secret))
	refused("grant without envelope", rig.list("", grant, "", delegated733Secret))
	// Org A's person cannot read org B by claiming org B with org A's grant.
	env, grant, fields := signedAs733("GET", "/-/batches", "", "batch", delegated733Org, delegated733User, delegated733Secret, now)
	fields[11] = list733OrgB
	refused("org swapped under grant", rig.list(wire733(fields, delegated733Secret), grant, "", ""))
}

// Same gate as submit: a person no longer in the org, or a revoked grant,
// lists nothing.
func TestDelegated733BatchListFollowsDelegationSnapshot(t *testing.T) {
	rig := newList733Rig(t)
	env, grant, _ := signedAs733("GET", "/-/batches", "", "batch", list733OrgB, list733UserB, delegated733Secret, time.Now().Unix())
	rig.srv.keyStore.SyncDelegations(&auth.DelegationSnapshot{Complete: true, Members: []auth.DelegatedMember{
		{Org: delegated733Org, User: delegated733User, Workspace: "-"},
	}}, time.Now())
	if w := rig.list(env, grant, "", ""); w.Code != http.StatusUnauthorized || strings.Contains(w.Body.String(), "batch-") {
		t.Fatalf("removed member listed: %d %s", w.Code, w.Body.String())
	}
	assertOrgListing733(t, rig, rig.orgList(delegated733Org, delegated733User, "", ""), delegated733Org, rig.a)
	env, grant, fields := signedAs733("GET", "/-/batches", "", "batch", delegated733Org, delegated733User, delegated733Secret, time.Now().Unix())
	rig.srv.keyStore.SyncDelegations(&auth.DelegationSnapshot{Complete: true, Members: []auth.DelegatedMember{
		{Org: delegated733Org, User: delegated733User, Workspace: "-"},
	}, RevokedGrants: []string{strings.TrimPrefix(fields[14], "grant:")}}, time.Now())
	if w := rig.list(env, grant, "", ""); w.Code != http.StatusUnauthorized || strings.Contains(w.Body.String(), "batch-") {
		t.Fatalf("revoked grant listed: %d %s", w.Code, w.Body.String())
	}
}

// 733 add.13(3): in BOTH modes the listing returns metadata
// only. "Admin mode included: batch_id, status, org_id, key_id, counts and
// timestamps. Never results, request bodies or prompt text." Each mode's row
// keys are pinned exactly, so any field added to a row fails here until it is
// reviewed and listed. Both modes pin the same summary row: batch_id, status,
// org_id, key_id, total, counts{completed,failed,cancelled}, created_at,
// completed_at. Neither carries max_concurrency or results; get keeps its
// own contract.
//
// error_code is allowed in either mode only on a batch stopped for a reason,
// never on a completed one.
var list733DelegatedKeys = []string{"batch_id", "status", "org_id", "key_id", "total", "counts", "created_at", "completed_at"}
var list733AdminKeys = []string{"batch_id", "status", "org_id", "key_id", "total", "counts", "created_at", "completed_at"}
var list733CountKeys = []string{"completed", "failed", "cancelled"}

// Results, request and prompt content, provider output and credentials or
// header names; the listing body (lower-cased) carries none of them.
var list733Forbidden = []string{
	"results", "result", "response", "request_id", "requests", "messages", "content", "prompt", "hello", "gpt-4o-mini",
	"choices", "assistant", "chat-test", "usage", "tokens", "cost", "headers", "authorization", "bearer",
	"x-fa-envelope", "x-fa-grant", "x-agentcc", "fe1.", "test-only", "grant:",
}

func list733KeySet(keys []string) string {
	out := append([]string(nil), keys...)
	sort.Strings(out)
	return strings.Join(out, ",")
}

func assertMetadataOnly733(t *testing.T, mode string, w *httptest.ResponseRecorder, keys []string, wantRows int) {
	t.Helper()
	rows := list733Rows(t, w)
	if len(rows) != wantRows {
		t.Fatalf("%s listing has %d rows, want %d: %s", mode, len(rows), wantRows, w.Body.String())
	}
	for _, row := range rows {
		want := append([]string(nil), keys...)
		if code, ok := row["error_code"]; ok {
			if s, _ := code.(string); s == "" || row["status"] == "completed" {
				t.Errorf("%s row error_code %v on status %v", mode, code, row["status"])
			}
			want = append(want, "error_code")
		}
		got := []string{}
		for field := range row {
			got = append(got, field)
		}
		if list733KeySet(got) != list733KeySet(want) {
			t.Errorf("%s row keys [%s], want exactly [%s]: %v", mode, list733KeySet(got), list733KeySet(want), row)
		}
		for field, value := range row {
			if field == "counts" {
				counts, ok := value.(map[string]any)
				if !ok {
					t.Errorf("%s row counts is %T: %v", mode, value, value)
					continue
				}
				got := []string{}
				for name, n := range counts {
					got = append(got, name)
					if _, ok := n.(float64); !ok {
						t.Errorf("%s row counts.%s is %T: %v", mode, name, n, n)
					}
				}
				if list733KeySet(got) != list733KeySet(list733CountKeys) {
					t.Errorf("%s row counts keys [%s], want exactly [%s]", mode, list733KeySet(got), list733KeySet(list733CountKeys))
				}
				continue
			}
			switch value.(type) {
			case string, float64, bool:
			default:
				t.Errorf("%s row field %q is not a scalar (%T): %v", mode, field, value, value)
			}
		}
	}
	raw := strings.ToLower(w.Body.String())
	for _, leak := range list733Forbidden {
		if strings.Contains(raw, leak) {
			t.Errorf("%s listing carries %q: %s", mode, leak, w.Body.String())
		}
	}
}

func TestDelegated733BatchListMetadataOnlyBothModes(t *testing.T) {
	rig := newList733Rig(t)
	// Not vacuous: every listed batch holds a provider result in memory, so a
	// row that let results through would carry them.
	for id := range rig.all() {
		b, ok := rig.srv.batchProcessor.Get(id)
		if !ok {
			t.Fatal("submitted batch missing")
		}
		held := b.Snapshot().Results
		if len(held) != 1 || held[0] == nil || held[0].Response == nil {
			t.Fatalf("batch %s holds no result to withhold: %+v", id, held)
		}
		raw, err := json.Marshal(held)
		if err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(string(raw), "choices") {
			t.Fatalf("batch %s result carries no provider output: %s", id, raw)
		}
	}
	assertMetadataOnly733(t, "delegated org A", rig.orgList(delegated733Org, delegated733User, "", ""), list733DelegatedKeys, len(rig.a))
	assertMetadataOnly733(t, "delegated org B", rig.orgList(list733OrgB, list733UserB, "", ""), list733DelegatedKeys, len(rig.b))
	assertMetadataOnly733(t, "delegated org A beside the admin bearer", rig.orgList(delegated733Org, delegated733User, "", delegated733Secret), list733DelegatedKeys, len(rig.a))
	assertMetadataOnly733(t, "admin", rig.list("", "", "", delegated733Secret), list733AdminKeys, len(rig.all()))
}
