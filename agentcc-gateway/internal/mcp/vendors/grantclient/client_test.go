package grantclient

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func testRequest() Request {
	return Request{OrgID: "org-a", ServerID: "partner", Vendor: "pipedream", EndUser: EndUser{Source: "member", ID: "member-a"}}
}
func TestGrantClientSyntheticServer(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != Path || r.Method != "POST" || r.Header.Get("X-Agentcc-MCP-Internal") != "fake-internal-secret-not-real" {
			t.Error("invalid internal request")
		}
		var req Request
		if json.NewDecoder(r.Body).Decode(&req) != nil || req.OrgID != "org-a" || req.EndUser.ID != "member-a" {
			t.Error("identity not bound")
		}
		json.NewEncoder(w).Encode(Grant{Vendor: "pipedream", AccessToken: "fake-bearer-not-real", ExpiresAt: time.Now().Add(time.Hour).UTC().Format(time.RFC3339), ProjectID: "proj_fixture", Environment: "development", AppSlug: "gmail", ExternalUserID: "fau_fixture", CredentialVersion: 1})
	}))
	defer s.Close()
	c, err := New(Options{BaseURL: s.URL, Secret: "fake-internal-secret-not-real"})
	if err != nil {
		t.Fatal(err)
	}
	grant, err := c.Fetch(context.Background(), testRequest())
	if err != nil || grant.ProjectID != "proj_fixture" {
		t.Fatal("expected complete grant")
	}
}
func TestGrantClientNoRedirectNoSecretInError(t *testing.T) {
	hits := 0
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { hits++ }))
	defer target.Close()
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, target.URL+"/fake-secret", http.StatusTemporaryRedirect)
	}))
	defer s.Close()
	c, _ := New(Options{BaseURL: s.URL, Secret: "fake-internal-secret-not-real"})
	_, err := c.Fetch(context.Background(), testRequest())
	if !errors.Is(err, ErrUnavailable) || hits != 0 || strings.Contains(err.Error(), "fake-secret") {
		t.Fatal("redirect or secret exposure")
	}
}
func TestGrantClientAnswerCap(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { io.WriteString(w, strings.Repeat("x", 65537)) }))
	defer s.Close()
	c, _ := New(Options{BaseURL: s.URL, Secret: "fake-internal-secret-not-real"})
	if _, err := c.Fetch(context.Background(), testRequest()); !errors.Is(err, ErrUnavailable) {
		t.Fatal("oversize answer accepted")
	}
}
func TestGrantClientRefusesMalformedAndCrossVendorAnswers(t *testing.T) {
	for _, body := range []string{`{"vendor":"composio","api_key":"fake-project-not-real","endpoint":"https://backend.composio.dev/mcp/fixture","credential_version":1}`, `{"error":"a secret must not become a refusal code"}`, `{"vendor":"pipedream","access_token":"fake-bearer-not-real"}`} {
		s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { io.WriteString(w, body) }))
		c, _ := New(Options{BaseURL: s.URL, Secret: "fake-internal-secret-not-real"})
		_, err := c.Fetch(context.Background(), testRequest())
		s.Close()
		if !errors.Is(err, ErrUnavailable) {
			t.Fatal("invalid grant accepted")
		}
	}
	if _, err := New(Options{BaseURL: "https://django.example", Secret: "same-credential", AdminTokens: []string{"same-credential"}}); err == nil {
		t.Fatal("admin credential accepted")
	}
}
