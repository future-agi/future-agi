package auth

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// The resolver admits exactly one synthetic pair. Tests exercise the real
// middleware and authentication pipeline, without a database or network.
type collector401Resolver struct {
	api, secret string
	calls       int
	err         error
}

func (r *collector401Resolver) ValidateKey(_ context.Context, api, secret string) (*ResolveResult, error) {
	r.calls++
	if r.err != nil {
		return nil, r.err
	}
	if api != r.api || secret != r.secret {
		return nil, nil
	}
	return &ResolveResult{
		OrgID: "fixture-org", WorkspaceID: "fixture-workspace",
		UserID: "fixture-user", KeyType: "user", Principal: PrincipalPersonal,
		APIKeyDigest: APIKeyDigest(api), Projects: make(map[string]string),
	}, nil
}

func (*collector401Resolver) ResolveProjects(context.Context, string, string, []string) (map[string]string, error) {
	return nil, errors.New("unexpected project lookup")
}

func (*collector401Resolver) GetOrCreateProject(context.Context, string, string, string, string) (string, error) {
	return "", errors.New("unexpected project creation")
}

func collector401Pair() (string, string) {
	return strings.Join([]string{"fixture", "public"}, "-"), strings.Join([]string{"fixture", "private"}, "-")
}

func collector401Request(t *testing.T, apiValues, secretValues []string) *http.Request {
	t.Helper()
	var wire strings.Builder
	wire.WriteString("POST /tracer/v1/traces HTTP/1.1\r\nHost: example.invalid\r\n")
	for i, value := range apiValues {
		name := "X-Api-Key"
		if i%2 == 0 {
			name = "x-aPI-kEY"
		}
		fmt.Fprintf(&wire, "%s: %s\r\n", name, value)
	}
	for i, value := range secretValues {
		name := "X-Secret-Key"
		if i%2 == 0 {
			name = "x-sECRET-kEY"
		}
		fmt.Fprintf(&wire, "%s: %s\r\n", name, value)
	}
	wire.WriteString("\r\n")
	req, err := http.ReadRequest(bufio.NewReader(strings.NewReader(wire.String())))
	if err != nil {
		t.Fatalf("parse synthetic HTTP request: %v", err)
	}
	t.Cleanup(func() { _ = req.Body.Close() })
	return req
}

func TestHTTPMiddlewareRepeatedCredentials(t *testing.T) {
	api, secret := collector401Pair()
	other := strings.Join([]string{"fixture", "other"}, "-")
	cases := []struct {
		name              string
		api, secret       []string
		basic             bool
		wantStatus, calls int
	}{
		{"single_mixed_case_names", []string{api}, []string{secret}, false, http.StatusNoContent, 1},
		{"separate_identical", []string{api, api}, []string{secret, secret}, false, http.StatusNoContent, 1},
		{"coalesced_api", []string{api + ", " + api}, []string{secret}, false, http.StatusNoContent, 1},
		{"coalesced_secret", []string{api}, []string{secret + ", " + secret}, false, http.StatusNoContent, 1},
		{"coalesced_both", []string{api + "," + api}, []string{secret + ", " + secret}, false, http.StatusNoContent, 1},
		{"mixed_field_lines", []string{api, api + ", " + api}, []string{secret + ", " + secret, secret}, false, http.StatusNoContent, 1},
		{"coalesced_http_whitespace", []string{api + " \t,\t " + api}, []string{secret + "\t, " + secret}, false, http.StatusNoContent, 1},
		{"conflicting_api", []string{api, other}, []string{secret}, false, http.StatusUnauthorized, 0},
		{"conflicting_secret", []string{api}, []string{secret, other}, false, http.StatusUnauthorized, 0},
		{"conflicting_joined", []string{api}, []string{secret + ", " + other}, false, http.StatusUnauthorized, 0},
		{"case_sensitive_values", []string{api}, []string{secret, strings.ToUpper(secret)}, false, http.StatusUnauthorized, 0},
		{"empty_joined_member", []string{api}, []string{secret + ", ," + secret}, false, http.StatusUnauthorized, 0},
		{"empty_separate_member", []string{api, ""}, []string{secret}, false, http.StatusUnauthorized, 0},
		{"wrong_single_pair", []string{api}, []string{other}, false, http.StatusUnauthorized, 1},
		{"wrong_repeated_pair", []string{api}, []string{other + ", " + other}, false, http.StatusUnauthorized, 1},
		{"missing_secret", []string{api}, nil, false, http.StatusUnauthorized, 0},
		{"basic_only", nil, nil, true, http.StatusNoContent, 1},
		{"partial_native_basic", []string{api}, nil, true, http.StatusNoContent, 1},
		{"empty_native_basic", []string{""}, []string{""}, true, http.StatusNoContent, 1},
		{"native_beats_basic", []string{api}, []string{secret}, true, http.StatusNoContent, 1},
		{"ambiguous_native_no_basic_fallback", []string{api, other}, []string{secret}, true, http.StatusUnauthorized, 0},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			resolver := &collector401Resolver{api: api, secret: secret}
			a, err := NewWithResolver(Config{}, resolver, nil, slog.New(slog.NewTextHandler(io.Discard, nil)))
			if err != nil {
				t.Fatal(err)
			}
			req := collector401Request(t, tc.api, tc.secret)
			if tc.basic {
				req.SetBasicAuth(api, secret)
				if tc.name == "native_beats_basic" {
					req.SetBasicAuth(other, other)
				}
			}
			called := false
			handler := a.HTTPMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				called = true
				resolved := FromContext(r.Context())
				if resolved == nil || resolved.OrgID != "fixture-org" || resolved.WorkspaceID != "fixture-workspace" {
					t.Error("handler did not receive the authenticated scope")
				}
				if CacheKeyFromContext(r.Context()) != CacheKey(api, secret) {
					t.Error("cache identity must use the single verified pair")
				}
				creds, ok := r.Context().Value(credentialsContextKey{}).(requestCredentials)
				if !ok || creds.apiKey != api || creds.secretKey != secret {
					t.Error("reauthentication must retain the single verified pair")
				}
				w.WriteHeader(http.StatusNoContent)
			}))
			recorder := httptest.NewRecorder()
			handler.ServeHTTP(recorder, req)
			if recorder.Code != tc.wantStatus {
				t.Errorf("status = %d, want %d", recorder.Code, tc.wantStatus)
			}
			if called != (tc.wantStatus == http.StatusNoContent) {
				t.Error("downstream handler invocation disagrees with authentication outcome")
			}
			if resolver.calls != tc.calls {
				t.Errorf("key lookups = %d, want %d", resolver.calls, tc.calls)
			}
		})
	}
}

func TestHTTPMiddlewareRepeatedCredentialsPreservesFailures(t *testing.T) {
	api, secret := collector401Pair()
	for _, name := range []string{"backend_unavailable", "expected_target_mismatch"} {
		t.Run(name, func(t *testing.T) {
			resolver := &collector401Resolver{api: api, secret: secret}
			want := http.StatusForbidden
			if name == "backend_unavailable" {
				resolver.err = errors.New("synthetic backend unavailable")
				want = http.StatusServiceUnavailable
			}
			a, err := NewWithResolver(Config{}, resolver, nil, slog.New(slog.NewTextHandler(io.Discard, nil)))
			if err != nil {
				t.Fatal(err)
			}
			req := collector401Request(t, []string{api + ", " + api}, []string{secret + ", " + secret})
			if name == "expected_target_mismatch" {
				req.Header.Set(ExpectedOrganizationHeader, "fixture-other-org")
				req.Header.Set(ExpectedWorkspaceHeader, "fixture-workspace")
				req.Header.Set(ExpectedProjectHeader, "fixture-project")
			}
			recorder := httptest.NewRecorder()
			a.HTTPMiddleware(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
				t.Error("refused request reached downstream handler")
			})).ServeHTTP(recorder, req)
			if recorder.Code != want || resolver.calls != 1 {
				t.Errorf("status/lookups = %d/%d, want %d/1", recorder.Code, resolver.calls, want)
			}
		})
	}
}
