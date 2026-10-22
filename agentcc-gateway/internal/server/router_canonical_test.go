package server

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// The router refuses any path that is not already canonical, before matching
// anything.
func TestRouterRejectsNonCanonicalPathsBeforeMatching(t *testing.T) {
	rt := NewRouter()
	rt.Handle("GET", "/", dummyHandler("root"))
	rt.Handle("GET", "/healthz", dummyHandler("healthz"))
	rt.Handle("GET", "/v1/videos/{video_id}", dummyHandler("video"))
	rt.Handle("GET", "/v1/files/{file_id}/content", dummyHandler("file_content"))
	rt.Handle("POST", "/v1/chat/completions", dummyHandler("chat"))

	rejected := []struct{ method, path string }{
		{"GET", "//v1/videos/x"},
		{"GET", "///v1/videos/x"},
		{"GET", "/v1//videos/x"},
		{"GET", "/v1/videos/x/"},
		{"GET", "/v1/videos/x//"},
		{"GET", "/./v1/videos/x"},
		{"GET", "/v1/./videos/x"},
		{"GET", "/v1/videos/x/."},
		{"GET", "/zz/../v1/videos/x"},
		{"GET", "/v1/zz/../videos/x"},
		{"GET", "/v1/files//content"},
		{"GET", "//v1/files/x/content"},
		{"GET", "/healthz/"},
		{"GET", "//healthz"},
		{"POST", "/v1/chat/completions/"},
		{"POST", "//v1/chat/completions"},
	}
	for _, tt := range rejected {
		req := httptest.NewRequest(tt.method, tt.path, nil)
		w := httptest.NewRecorder()
		rt.ServeHTTP(w, req)
		if w.Code != http.StatusNotFound || !strings.Contains(w.Body.String(), "non_canonical_path") {
			t.Errorf("%s %s: got %d %q, want 404 non_canonical_path", tt.method, tt.path, w.Code, w.Body.String())
		}
	}

	served := []struct{ method, path, body string }{
		{"GET", "/", "root"},
		{"GET", "/healthz", "healthz"},
		{"GET", "/v1/videos/x", "video"},
		{"GET", "/v1/files/x/content", "file_content"},
		{"POST", "/v1/chat/completions", "chat"},
	}
	for _, tt := range served {
		req := httptest.NewRequest(tt.method, tt.path, nil)
		w := httptest.NewRecorder()
		rt.ServeHTTP(w, req)
		if w.Code != http.StatusOK || w.Body.String() != tt.body {
			t.Errorf("%s %s: got %d %q, want 200 %q", tt.method, tt.path, w.Code, w.Body.String(), tt.body)
		}
	}
}

func TestRouterRoutesListsEveryRegistration(t *testing.T) {
	rt := NewRouter()
	rt.Handle("GET", "/healthz", dummyHandler("h"))
	rt.Handle("GET", "/v1/videos/{video_id}", dummyHandler("v"))
	rt.Handle("DELETE", "/v1/videos/{video_id}", dummyHandler("d"))

	got := rt.Routes()
	want := []string{"GET /healthz", "GET /v1/videos/{video_id}", "DELETE /v1/videos/{video_id}"}
	if len(got) != len(want) {
		t.Fatalf("Routes() returned %d routes, want %d", len(got), len(want))
	}
	for i, r := range got {
		if r.Method+" "+r.Pattern != want[i] {
			t.Errorf("Routes()[%d] = %s %s, want %s", i, r.Method, r.Pattern, want[i])
		}
		if r.Handler == nil {
			t.Errorf("Routes()[%d] has no handler", i)
		}
	}
	got[0].Pattern = "/mutated"
	if rt.Routes()[0].Pattern != "/healthz" {
		t.Error("Routes() returned the router's own slice; callers can mutate the table")
	}
}

// A pattern is stored in canonical form, so a non-canonical pattern from
// config (mcp.endpoint: "/mcp/") registers a live route at its canonical path.
func TestRouterStoresPatternsInCanonicalForm(t *testing.T) {
	rt := NewRouter()
	rt.Handle("GET", "/mcp/", dummyHandler("mcp"))
	rt.Handle("GET", "//v1/items/{item_id}/", dummyHandler("item"))

	for _, tt := range []struct{ path, body string }{
		{"/mcp", "mcp"},
		{"/v1/items/x", "item"},
	} {
		w := httptest.NewRecorder()
		rt.ServeHTTP(w, httptest.NewRequest("GET", tt.path, nil))
		if w.Code != http.StatusOK || w.Body.String() != tt.body {
			t.Errorf("GET %s: got %d %q, want 200 %q", tt.path, w.Code, w.Body.String(), tt.body)
		}
	}
	for _, p := range []string{"/mcp/", "//v1/items/x/"} {
		w := httptest.NewRecorder()
		rt.ServeHTTP(w, httptest.NewRequest("GET", p, nil))
		if w.Code != http.StatusNotFound || !strings.Contains(w.Body.String(), "non_canonical_path") {
			t.Errorf("GET %s: got %d %q, want 404 non_canonical_path", p, w.Code, w.Body.String())
		}
	}
	if got := rt.Routes()[0].Pattern; got != "/mcp" {
		t.Errorf("Routes()[0].Pattern = %q, want the canonical %q", got, "/mcp")
	}
}
