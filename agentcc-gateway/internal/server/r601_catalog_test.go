package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestR601RetiredListingNotAdvertised(t *testing.T) {
	g, _, _ := r601Gateway(t)
	for _, path := range []string{"/v1/models", "/-/orgs/org-m/catalog/access"} {
		t.Run(path, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, path, nil)
			credential := managedOrgKey
			if strings.HasPrefix(path, "/-/") {
				credential = adminToken
			}
			req.Header.Set("Authorization", "Bearer "+credential)
			w := httptest.NewRecorder()
			g.srv.httpServer.Handler.ServeHTTP(w, req)
			if w.Code != 200 {
				t.Fatalf("status %d body %s", w.Code, w.Body)
			}
			var doc map[string]json.RawMessage
			if json.Unmarshal(w.Body.Bytes(), &doc) != nil {
				t.Fatal("invalid JSON")
			}
			if strings.Contains(w.Body.String(), `"google/gemini-2.5-flash-image"`) {
				t.Fatal("calendar-retired listing advertised")
			}
		})
	}
}
