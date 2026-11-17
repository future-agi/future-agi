package server

// 733 add.16(a): GET /-/batches/{id} and POST /-/batches/{id}/cancel move to
// the verified envelope principal on the listing's selection rule. Any
// envelope or grant header selects delegated mode, even beside FA's admin
// bearer; caller queries are refused (733 add.17(2)); another
// org's batch answers exactly as a batch that does not exist. The admin token
// alone keeps the unchanged admin + ?org_id= mode.
import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/batch"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// A well-formed id no batch has: the not-found control.
const missing733Batch = "batch-000000000000"
const notFound733Body = `{"error":"batch not found"}`
const forged733Secret = "test-only-forged-secret-not-the-admin-token"

func getPath733(id string) string    { return "/-/batches/" + id }
func cancelPath733(id string) string { return "/-/batches/" + id + "/cancel" }

var batchRoutes733 = []struct {
	method string
	path   func(string) string
}{{http.MethodGet, getPath733}, {http.MethodPost, cancelPath733}}

// batchCall733 sends method path+query with whichever of the envelope, the
// grant and the admin bearer are set.
func batchCall733(srv *Server, method, path, query, env, grant, bearer string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, path+query, nil)
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
	srv.httpServer.Handler.ServeHTTP(w, r)
	return w
}

// asOrg733 signs a fresh batch envelope for org's person, bound to method and
// the exact path (a signed route has no query), and sends it with query and
// bearer riding along.
func asOrg733(srv *Server, method, path, query, bearer, org, user string) *httptest.ResponseRecorder {
	env, grant, _ := signedAs733(method, path, "", "batch", org, user, delegated733Secret, time.Now().Unix())
	return batchCall733(srv, method, path, query, env, grant, bearer)
}

func headerNames733(h http.Header) []string {
	names := []string{}
	for name := range h {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

// sameAnswer733 fails unless got is want byte for byte: the status, the body,
// the header names and the headers a caller could tell apart.
func sameAnswer733(t *testing.T, name string, want, got *httptest.ResponseRecorder) {
	t.Helper()
	if got.Code != want.Code || !bytes.Equal(got.Body.Bytes(), want.Body.Bytes()) {
		t.Errorf("%s: %d %q, want %d %q", name, got.Code, got.Body.String(), want.Code, want.Body.String())
	}
	if fmt.Sprint(headerNames733(got.Header())) != fmt.Sprint(headerNames733(want.Header())) {
		t.Errorf("%s: headers %v, want %v", name, headerNames733(got.Header()), headerNames733(want.Header()))
	}
	for _, h := range []string{"Content-Type", "Cache-Control"} {
		if got.Header().Get(h) != want.Header().Get(h) {
			t.Errorf("%s: %s %q, want %q", name, h, got.Header().Get(h), want.Header().Get(h))
		}
	}
}

func notFound733(t *testing.T, name string, w *httptest.ResponseRecorder) bool {
	t.Helper()
	if w.Code != http.StatusNotFound || strings.TrimSpace(w.Body.String()) != notFound733Body {
		t.Errorf("%s: %d %s, want 404 %s", name, w.Code, w.Body.String(), notFound733Body)
		return false
	}
	return true
}

// refused733 is a 401 that names none of the given batches.
func refused733(t *testing.T, name string, w *httptest.ResponseRecorder, ids ...string) {
	t.Helper()
	if w.Code != http.StatusUnauthorized {
		t.Errorf("%s: %d %s, want 401", name, w.Code, w.Body.String())
		return
	}
	for _, id := range ids {
		if strings.Contains(w.Body.String(), id) {
			t.Errorf("%s: refusal names batch %s", name, id)
		}
	}
}

// running733Rig holds one batch per org that stays processing until cleanup.
type running733Rig struct {
	srv  *Server
	a, b string
}

const running733Body = `{"requests":[` + delegated733Body + `,` + delegated733Body + `],"max_concurrency":1}`

func newRunning733Rig(t *testing.T) *running733Rig {
	t.Helper()
	srv := newDelegated733Server(t)
	srv.delegated = auth.NewEnvelopeVerifier(delegated733Secret, time.Now().Add(-10*time.Minute))
	srv.keyStore.SyncDelegations(&auth.DelegationSnapshot{Complete: true, Members: []auth.DelegatedMember{
		{Org: delegated733Org, User: delegated733User, Workspace: "-"},
		{Org: list733OrgB, User: list733UserB, Workspace: "-"},
	}}, time.Now())
	release := make(chan struct{})
	var once sync.Once
	p := batch.NewProcessor(func(ctx context.Context, _ *models.ChatCompletionRequest) (*models.ChatCompletionResponse, map[string]string, error) {
		select {
		case <-release:
		case <-ctx.Done():
		}
		return &models.ChatCompletionResponse{}, nil, nil
	}, batch.WithKeyStore(srv.keyStore, true))
	srv.batchProcessor = p
	t.Cleanup(p.Stop)
	t.Cleanup(func() { once.Do(func() { close(release) }) })
	rig := &running733Rig{srv: srv}
	rig.a = rig.submit(t, delegated733Org, delegated733User)
	rig.b = rig.submit(t, list733OrgB, list733UserB)
	return rig
}

func (rig *running733Rig) submit(t *testing.T, org, user string) string {
	t.Helper()
	env, grant, _ := signedAs733("POST", "/-/batches", running733Body, "batch", org, user, delegated733Secret, time.Now().Unix())
	w := request733(rig.srv, "/-/batches", running733Body, env, grant)
	if w.Code != http.StatusAccepted {
		t.Fatalf("submit for %s: %d %s", org, w.Code, w.Body.String())
	}
	var out struct {
		ID string `json:"batch_id"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &out); err != nil || out.ID == "" {
		t.Fatalf("submit for %s: %s %v", org, w.Body.String(), err)
	}
	return out.ID
}

func (rig *running733Rig) status(t *testing.T, id string) string {
	t.Helper()
	b, ok := rig.srv.batchProcessor.Get(id)
	if !ok {
		t.Fatalf("batch %s missing", id)
	}
	return b.GetStatus()
}

// The owner's delegated GET is get's existing contract, results and summary
// included, for any person of the verified org; ?org_id= is refused, and
// FA's admin bearer beside the envelope never selects admin mode.
func TestDelegated733BatchGetOwnOrg(t *testing.T) {
	rig := newList733Rig(t)
	for _, p := range []struct {
		org, user, other string
		mine             map[string]string
	}{
		{delegated733Org, delegated733User, list733OrgB, rig.a},
		{list733OrgB, list733UserB, delegated733Org, rig.b},
	} {
		for id, key := range p.mine {
			admin := batchCall733(rig.srv, http.MethodGet, getPath733(id), "?org_id="+p.org, "", "", delegated733Secret)
			if admin.Code != http.StatusOK {
				t.Fatalf("admin readback of %s: %d %s", id, admin.Code, admin.Body.String())
			}
			for _, tc := range []struct{ name, query, bearer string }{
				{"envelope alone", "", ""},
				{"beside the admin bearer", "", delegated733Secret},
				{"naming another org in ?org_id=", "?org_id=" + p.other, ""},
				{"naming another org in ?org_id= beside the admin bearer", "?org_id=" + p.other, delegated733Secret},
				{"naming its own org in ?org_id= beside the admin bearer", "?org_id=" + p.org, delegated733Secret},
			} {
				name := fmt.Sprintf("org %s reads %s, %s", p.org, id, tc.name)
				w := asOrg733(rig.srv, http.MethodGet, getPath733(id), tc.query, tc.bearer, p.org, p.user)
				if tc.query != "" {
					refused733(t, name, w, id)
					continue
				}
				if w.Code != http.StatusOK {
					t.Errorf("%s: %d %s", name, w.Code, w.Body.String())
					continue
				}
				if !bytes.Equal(w.Body.Bytes(), admin.Body.Bytes()) {
					t.Errorf("%s: %s, want get's contract %s", name, w.Body.String(), admin.Body.String())
				}
				if w.Header().Get("Cache-Control") != "no-store" {
					t.Errorf("%s: Cache-Control %q", name, w.Header().Get("Cache-Control"))
				}
				var out map[string]any
				if err := json.Unmarshal(w.Body.Bytes(), &out); err != nil {
					t.Fatal(err)
				}
				if out["batch_id"] != id || out["org_id"] != p.org || out["key_id"] != key || out["status"] != "completed" {
					t.Errorf("%s: identity %v", name, out)
				}
				if results, _ := out["results"].([]any); len(results) != 1 {
					t.Errorf("%s: results %v", name, out["results"])
				}
				if _, ok := out["summary"].(map[string]any); !ok {
					t.Errorf("%s: no summary %v", name, out)
				}
				for _, leak := range []string{"hello", "fe1.", "test-only"} {
					if strings.Contains(w.Body.String(), leak) {
						t.Errorf("%s: leaks %q: %s", name, leak, w.Body.String())
					}
				}
			}
		}
	}
	if len(rig.srv.keyStore.List()) != 0 {
		t.Fatal("a delegated get minted a key")
	}
}

// Another org's batch id is not found, byte for byte as an id no batch has,
// for get and cancel, also when FA's admin bearer and ?org_id= of the batch's
// own org ride along (queries are refused with 401 for both ids).
func TestDelegated733BatchOtherOrgIsNotFound(t *testing.T) {
	rig := newList733Rig(t)
	if _, ok := rig.srv.batchProcessor.Get(missing733Batch); ok {
		t.Fatal("the missing-id control exists")
	}
	for _, p := range []struct {
		org, user, other string
		theirs           map[string]string
	}{
		{delegated733Org, delegated733User, list733OrgB, rig.b},
		{list733OrgB, list733UserB, delegated733Org, rig.a},
	} {
		for _, route := range batchRoutes733 {
			for _, tc := range []struct{ name, query, bearer string }{
				{"envelope alone", "", ""},
				{"beside the admin bearer", "", delegated733Secret},
				{"naming the batch's org in ?org_id= beside the admin bearer", "?org_id=" + p.other, delegated733Secret},
			} {
				missing := asOrg733(rig.srv, route.method, route.path(missing733Batch), tc.query, tc.bearer, p.org, p.user)
				if tc.query != "" {
					refused733(t, tc.name, missing)
				} else if !notFound733(t, fmt.Sprintf("org %s %s a missing id, %s", p.org, route.method, tc.name), missing) {
					continue
				}
				for id := range p.theirs {
					got := asOrg733(rig.srv, route.method, route.path(id), tc.query, tc.bearer, p.org, p.user)
					sameAnswer733(t, fmt.Sprintf("org %s %s %s of org %s, %s", p.org, route.method, id, p.other, tc.name), missing, got)
				}
			}
		}
	}
}

// A cancel for another org's running batch is not found and the batch keeps
// running; its own org then cancels it.
func TestDelegated733BatchCancelOtherOrgKeepsRunning(t *testing.T) {
	rig := newRunning733Rig(t)
	for _, tc := range []struct{ name, query, bearer string }{
		{"envelope alone", "", ""},
		{"naming org B in ?org_id= beside the admin bearer", "?org_id=" + list733OrgB, delegated733Secret},
	} {
		for _, route := range batchRoutes733 {
			missing := asOrg733(rig.srv, route.method, route.path(missing733Batch), tc.query, tc.bearer, delegated733Org, delegated733User)
			if tc.query != "" {
				refused733(t, tc.name, missing)
			} else if !notFound733(t, "org A "+route.method+" a missing id, "+tc.name, missing) {
				continue
			}
			got := asOrg733(rig.srv, route.method, route.path(rig.b), tc.query, tc.bearer, delegated733Org, delegated733User)
			sameAnswer733(t, "org A "+route.method+" org B's running batch, "+tc.name, missing, got)
		}
		if s := rig.status(t, rig.b); s != "processing" {
			t.Fatalf("org A's cancel (%s) reached org B's batch: %s", tc.name, s)
		}
	}
	w := asOrg733(rig.srv, http.MethodPost, cancelPath733(rig.b), "", "", list733OrgB, list733UserB)
	var out map[string]any
	if w.Code != http.StatusOK || json.Unmarshal(w.Body.Bytes(), &out) != nil {
		t.Fatalf("org B cancels its batch: %d %s", w.Code, w.Body.String())
	}
	if len(out) != 3 || out["batch_id"] != rig.b || out["org_id"] != list733OrgB || out["status"] != "cancelled" {
		t.Fatalf("org B's cancel answer: %v", out)
	}
	if w.Header().Get("Cache-Control") != "no-store" {
		t.Errorf("cancel Cache-Control %q", w.Header().Get("Cache-Control"))
	}
	if s := rig.status(t, rig.b); s != "cancelled" {
		t.Fatalf("org B's batch after its own cancel: %s", s)
	}
	if s := rig.status(t, rig.a); s != "processing" {
		t.Fatalf("org B's cancel reached org A's batch: %s", s)
	}
	w = asOrg733(rig.srv, http.MethodGet, getPath733(rig.b), "", "", list733OrgB, list733UserB)
	out = nil
	if w.Code != http.StatusOK || json.Unmarshal(w.Body.Bytes(), &out) != nil || out["status"] != "cancelled" || out["org_id"] != list733OrgB {
		t.Fatalf("org B reads its cancelled batch: %d %s", w.Code, w.Body.String())
	}
}

// A forged envelope beside FA's admin bearer never falls back to admin
// mode: without any query to mask the auth gate, it is refused and the batch
// keeps running. Either delegated header alone selects delegated mode.
func TestDelegated733BatchGetCancelRefuseForgedEnvelopeBesideAdminBearer(t *testing.T) {
	rig := newRunning733Rig(t)
	now := time.Now().Unix()
	for _, route := range batchRoutes733 {
		path := route.path(rig.b)
		query := ""
		env, grant, _ := signedAs733(route.method, path, "", "batch", delegated733Org, delegated733User, forged733Secret, now)
		refused733(t, route.method+" forged org A envelope", batchCall733(rig.srv, route.method, path, query, env, grant, delegated733Secret), rig.a, rig.b)
		env, grant, _ = signedAs733(route.method, path, "", "batch", list733OrgB, list733UserB, forged733Secret, now)
		refused733(t, route.method+" forged org B envelope", batchCall733(rig.srv, route.method, path, query, env, grant, delegated733Secret), rig.a, rig.b)
		env, grant, _ = signedAs733(route.method, path, "", "batch", list733OrgB, list733UserB, delegated733Secret, now)
		refused733(t, route.method+" grant without envelope", batchCall733(rig.srv, route.method, path, query, "", grant, delegated733Secret), rig.a, rig.b)
		refused733(t, route.method+" envelope without grant", batchCall733(rig.srv, route.method, path, query, env, "", delegated733Secret), rig.a, rig.b)
	}
	if s := rig.status(t, rig.b); s != "processing" {
		t.Fatalf("a refused cancel reached org B's batch: %s", s)
	}
}

// Each envelope is single-use: a replay is refused even beside FA's admin
// bearer, without a query refusal masking the replay gate.
func TestDelegated733BatchGetCancelReplayRefused(t *testing.T) {
	rig := newList733Rig(t)
	for _, route := range batchRoutes733 {
		for id := range rig.a {
			path := route.path(id)
			env, grant, _ := signedAs733(route.method, path, "", "batch", delegated733Org, delegated733User, delegated733Secret, time.Now().Unix())
			if w := batchCall733(rig.srv, route.method, path, "", env, grant, ""); w.Code != http.StatusOK {
				t.Fatalf("first use of %s %s: %d %s", route.method, path, w.Code, w.Body.String())
			}
			w := batchCall733(rig.srv, route.method, path, "", env, grant, delegated733Secret)
			refused733(t, "replayed "+route.method+" "+path, w, id)
		}
	}
}

// The envelope binds the method, the exact batch route, the batch scope, the
// body and the org; a bad one is refused even beside FA's admin bearer,
// without a query refusal masking the gate. The query never picks the batch.
func TestDelegated733BatchGetCancelBindRouteAndScope(t *testing.T) {
	rig := newList733Rig(t)
	ids := list733Want(rig.a)
	one, two := ids[0], ids[1]
	now := time.Now().Unix()
	sign := func(method, route, body, purpose string) (string, string, []string) {
		return signedAs733(method, route, body, purpose, delegated733Org, delegated733User, delegated733Secret, now)
	}
	send := func(method, path, env, grant string) *httptest.ResponseRecorder {
		return batchCall733(rig.srv, method, path, "", env, grant, delegated733Secret)
	}
	env, grant, _ := sign("GET", getPath733(one), "", "batch")
	refused733(t, "GET envelope for one batch sent to another", send("GET", getPath733(two), env, grant), one, two)
	env, grant, _ = sign("GET", getPath733(one), "", "batch")
	refused733(t, "GET envelope sent to the cancel route", send("POST", cancelPath733(one), env, grant), one, two)
	env, grant, _ = sign("POST", cancelPath733(one), "", "batch")
	refused733(t, "cancel envelope for one batch sent to another", send("POST", cancelPath733(two), env, grant), one, two)
	env, grant, _ = sign("POST", cancelPath733(one), "", "batch")
	refused733(t, "cancel envelope sent as a GET", send("GET", getPath733(one), env, grant), one, two)
	env, grant, _ = sign("GET", "/-/batches", "", "batch")
	refused733(t, "listing envelope sent to one batch", send("GET", getPath733(one), env, grant), one, two)
	env, grant, _ = sign("POST", getPath733(one), "", "batch")
	refused733(t, "POST-signed single batch", send("GET", getPath733(one), env, grant), one, two)
	env, grant, _ = sign("GET", getPath733(one), "", "playground")
	refused733(t, "playground grant on get", send("GET", getPath733(one), env, grant), one, two)
	env, grant, _ = sign("POST", cancelPath733(one), "", "playground")
	refused733(t, "playground grant on cancel", send("POST", cancelPath733(one), env, grant), one, two)
	env, grant, _ = sign("GET", getPath733(one), `{"x":1}`, "batch")
	refused733(t, "signed body not sent", send("GET", getPath733(one), env, grant), one, two)
	env, grant, _ = signedAs733("GET", getPath733(one), "", "batch", delegated733Org, delegated733User, delegated733Secret, now-100)
	refused733(t, "expired", send("GET", getPath733(one), env, grant), one, two)
	env, grant, fields := sign("GET", getPath733(one), "", "batch")
	fields[11] = list733OrgB
	refused733(t, "org swapped under the grant", send("GET", getPath733(one), wire733(fields, delegated733Secret), grant), one, two)
	// The router writes the path's batch id into the query; a caller's
	// ?batch_id= must be refused even though the router overwrites it.
	env, grant, _ = sign("GET", getPath733(one), "", "batch")
	w := batchCall733(rig.srv, "GET", getPath733(one), "?batch_id="+two, env, grant, "")
	refused733(t, "?batch_id= beside a signed path", w, one, two)
}

// Same gate as submit: a person no longer in the org, or a revoked grant,
// gets nothing, even beside FA's admin bearer, without a query masking it.
func TestDelegated733BatchGetCancelFollowDelegationSnapshot(t *testing.T) {
	rig := newList733Rig(t)
	both := []auth.DelegatedMember{
		{Org: delegated733Org, User: delegated733User, Workspace: "-"},
		{Org: list733OrgB, User: list733UserB, Workspace: "-"},
	}
	ids := list733Want(rig.all())
	for _, route := range batchRoutes733 {
		for id := range rig.b {
			path := route.path(id)
			env, grant, _ := signedAs733(route.method, path, "", "batch", list733OrgB, list733UserB, delegated733Secret, time.Now().Unix())
			rig.srv.keyStore.SyncDelegations(&auth.DelegationSnapshot{Complete: true, Members: both[:1]}, time.Now())
			refused733(t, "removed member "+route.method, batchCall733(rig.srv, route.method, path, "", env, grant, delegated733Secret), ids...)
			rig.srv.keyStore.SyncDelegations(&auth.DelegationSnapshot{Complete: true, Members: both}, time.Now())
		}
		for id := range rig.a {
			path := route.path(id)
			env, grant, fields := signedAs733(route.method, path, "", "batch", delegated733Org, delegated733User, delegated733Secret, time.Now().Unix())
			rig.srv.keyStore.SyncDelegations(&auth.DelegationSnapshot{Complete: true, Members: both, RevokedGrants: []string{strings.TrimPrefix(fields[14], "grant:")}}, time.Now())
			refused733(t, "revoked grant "+route.method, batchCall733(rig.srv, route.method, path, "", env, grant, delegated733Secret), ids...)
			rig.srv.keyStore.SyncDelegations(&auth.DelegationSnapshot{Complete: true, Members: both}, time.Now())
		}
	}
	for id := range rig.a {
		if w := asOrg733(rig.srv, http.MethodGet, getPath733(id), "", "", delegated733Org, delegated733User); w.Code != http.StatusOK {
			t.Fatalf("a current member reads its batch: %d %s", w.Code, w.Body.String())
		}
	}
}

// The admin token alone keeps the unchanged mode: ?org_id= scopes it, and the
// retired batch-key header is no delegated header.
func TestDelegated733BatchGetCancelAdminModeUnchanged(t *testing.T) {
	rig := newList733Rig(t)
	admin := func(method, path, query, bearer string) *httptest.ResponseRecorder {
		return batchCall733(rig.srv, method, path, query, "", "", bearer)
	}
	for id := range rig.a {
		w := admin(http.MethodGet, getPath733(id), "?org_id="+delegated733Org, delegated733Secret)
		var out map[string]any
		if w.Code != http.StatusOK || json.Unmarshal(w.Body.Bytes(), &out) != nil || out["batch_id"] != id || out["org_id"] != delegated733Org {
			t.Fatalf("admin get: %d %s", w.Code, w.Body.String())
		}
		if results, _ := out["results"].([]any); len(results) != 1 {
			t.Errorf("admin get results: %v", out["results"])
		}
		for _, query := range []string{"", "?org_id=" + list733OrgB} {
			notFound733(t, "admin get "+query, admin(http.MethodGet, getPath733(id), query, delegated733Secret))
			notFound733(t, "admin cancel "+query, admin(http.MethodPost, cancelPath733(id), query, delegated733Secret))
		}
		w = admin(http.MethodPost, cancelPath733(id), "?org_id="+delegated733Org, delegated733Secret)
		out = nil
		if w.Code != http.StatusOK || json.Unmarshal(w.Body.Bytes(), &out) != nil || out["batch_id"] != id || out["status"] != "completed" {
			t.Fatalf("admin cancel of a finished batch: %d %s", w.Code, w.Body.String())
		}
		for _, bearer := range []string{"", "test-only-not-the-admin-token"} {
			for _, route := range batchRoutes733 {
				w = admin(route.method, route.path(id), "?org_id="+delegated733Org, bearer)
				if w.Code != http.StatusUnauthorized || strings.TrimSpace(w.Body.String()) != `{"error":"unauthorized"}` {
					t.Errorf("%s with bearer %q: %d %s", route.method, bearer, w.Code, w.Body.String())
				}
			}
		}
		r := httptest.NewRequest(http.MethodGet, getPath733(id)+"?org_id="+delegated733Org, nil)
		r.Header.Set("Authorization", "Bearer "+delegated733Secret)
		r.Header.Set(batch.KeyHeader, "test-only-retired-batch-key")
		w = httptest.NewRecorder()
		rig.srv.httpServer.Handler.ServeHTTP(w, r)
		if w.Code != http.StatusOK {
			t.Errorf("admin get beside the retired batch-key header: %d %s", w.Code, w.Body.String())
		}
	}
}
