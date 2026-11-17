package server

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
)

// 733 add.17(2): every caller query is a signed-path mismatch, not an
// ignored override. Use the real router and handlers, without the outer
// middleware's per-request IDs and latency, to compare ALL refusal bytes.
func TestDelegated733BatchCallerQueryRefused(t *testing.T) {
	rig := newRunning733Rig(t)
	router := NewRouter()
	router.Handle(http.MethodGet, "/-/batches", rig.srv.listBatchesHandler)
	router.Handle(http.MethodGet, "/-/batches/{batch_id}", rig.srv.getBatchHandler)
	router.Handle(http.MethodPost, "/-/batches/{batch_id}/cancel", rig.srv.cancelBatchHandler)
	call := func(method, path, signedPath, query, bearer string, urlOnly bool) *httptest.ResponseRecorder {
		env, grant, _ := signedAs733(method, signedPath, "", "batch", delegated733Org, delegated733User, delegated733Secret, time.Now().Unix())
		r := httptest.NewRequest(method, path, nil)
		r.URL.RawQuery = query
		if query != "" {
			r.RequestURI += "?" + query
		}
		if urlOnly {
			// URL.RawQuery is authoritative even for an in-process request
			// that has no wire RequestURI. Do not recover it from RequestURI.
			r.RequestURI = ""
		}
		r.Header.Set(auth.EnvelopeHeader, env)
		r.Header.Set(auth.GrantHeader, grant)
		if bearer != "" {
			r.Header.Set("Authorization", "Bearer "+bearer)
		}
		w := httptest.NewRecorder()
		router.ServeHTTP(w, r)
		return w
	}
	queries := []string{
		"org_id=" + delegated733Org, "org_id=" + list733OrgB, "org_id=", "org_id",
		"batch_id=" + rig.a, "batch_id=" + rig.b, "batch_id=", "batch_id",
		"batch_id=" + rig.b + "&batch_id=" + rig.a,
		"org_id=&org_id=" + list733OrgB, "%62atch_id=" + rig.b, "%6Frg_id=",
		"unknown=1", "unknown", "=value", "=", "x=%26batch_id%3D" + rig.b,
		// Even malformed queries cannot disappear in URL.Query's decoder.
		"bad=%zz", "%zz=value", "x=1;y=2", "&",
	}
	routes := append([]struct {
		method string
		path   func(string) string
	}{{http.MethodGet, func(string) string { return "/-/batches" }}}, batchRoutes733...)
	for _, route := range routes {
		t.Run(route.method+route.path("batch"), func(t *testing.T) {
			// The mismatch uses a valid signed route with no query. It must
			// fail on path binding, not query parsing, and establishes the
			// exact status, body and headers the refusal contract requires.
			mismatchPath := getPath733(missing733Batch)
			if route.method == http.MethodPost {
				mismatchPath = cancelPath733(missing733Batch)
			}
			want := call(route.method, route.path(rig.a), mismatchPath, "", "", false)
			refused733(t, "signed-path mismatch control", want, rig.a, rig.b)
			var wantHeaders bytes.Buffer
			if err := want.Header().Write(&wantHeaders); err != nil {
				t.Fatal(err)
			}
			for _, id := range []string{rig.a, rig.b, missing733Batch} {
				for _, bearer := range []string{"", delegated733Secret} {
					for _, urlOnly := range []bool{false, true} {
						for _, query := range queries {
							path := route.path(id)
							got := call(route.method, path, path, query, bearer, urlOnly)
							var gotHeaders bytes.Buffer
							if err := got.Header().Write(&gotHeaders); err != nil {
								t.Fatal(err)
							}
							if got.Code != want.Code || !bytes.Equal(got.Body.Bytes(), want.Body.Bytes()) || !bytes.Equal(gotHeaders.Bytes(), wantHeaders.Bytes()) {
								t.Errorf("%s?%s admin=%t urlOnly=%t: got %d %q headers %q; want %d %q headers %q", path, query, bearer != "", urlOnly, got.Code, got.Body.String(), gotHeaders.String(), want.Code, want.Body.String(), wantHeaders.String())
							}
						}
					}
				}
			}
		})
	}
	for _, id := range []string{rig.a, rig.b} {
		if got := rig.status(t, id); got != "processing" {
			t.Errorf("refused query cancelled %s: %s", id, got)
		}
	}
	// No caller query: the router's synthetic batch_id must NOT be refused.
	for _, route := range routes {
		path := route.path(rig.a)
		if w := call(route.method, path, path, "", "", false); w.Code != http.StatusOK {
			t.Errorf("query-free %s %s: %d %s", route.method, path, w.Code, w.Body.String())
		}
	}
	if got := rig.status(t, rig.a); got != "cancelled" {
		t.Errorf("query-free owner cancel: %s", got)
	}
	if got := rig.status(t, rig.b); got != "processing" {
		t.Errorf("owner cancel touched foreign batch: %s", got)
	}
}
