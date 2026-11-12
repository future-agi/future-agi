package main

import (
	"context"
	"errors"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

func TestK2ConstructionAdmission(t *testing.T) {
	cases := []struct {
		name    string
		sources []string
		replace bool
		reason  string
	}{
		{name: "declared"},
		{"missing", nil, true, "k2_client_source_unavailable"},
		{"empty", []string{}, true, "k2_client_source_unavailable"},
		{"platform-only", []string{"platform"}, true, "k2_client_source_unavailable"},
		{"unknown", []string{"future"}, true, "k2_client_source_unavailable"},
		{"org-and-unknown", []string{"org", "future"}, true, "k2_client_source_unavailable"},
	}
	for _, runtimeSet := range []bool{false, true} {
		runtimeName := "url-empty"
		if runtimeSet {
			runtimeName = "url-set"
		}
		for _, mode := range []string{"shared", "per_user"} {
			for _, tc := range cases {
				for _, app := range k2BoundaryApps {
					t.Run(runtimeName+"/"+mode+"/"+tc.name+"/"+app, func(t *testing.T) {
						cat := k2ConstructionCatalog(t, func(_ string, record map[string]any) {
							if !tc.replace {
								return
							}
							oauth := record["auth"].(map[string]any)["oauth2"].(map[string]any)
							delete(oauth, "clientSources")
							if tc.sources != nil {
								oauth["clientSources"] = tc.sources
							}
						})
						p := cat.App(app).Provider("futureagi-rest")
						f := newK2ConstructionFixture(t, runtimeSet, app, app+"/futureagi-rest@"+p.Version, mode, "")
						reason := tc.reason
						if reason == "" && mode == "shared" {
							reason = "k2_posture_unavailable"
						}
						if !runtimeSet {
							reason = "k2_runtime_unavailable"
						}
						f.exercise(t)
						if reason == "" {
							if f.fetches.Load() == 0 || f.factories.Load() == 0 || f.discovery.Load() == 0 || f.dispatch.Load() != 1 {
								t.Fatalf("positive org control: tokens=%d factories=%d discovery=%d calls=%d", f.fetches.Load(), f.factories.Load(), f.discovery.Load(), f.dispatch.Load())
							}
							if f.sharedLookups.Load() != 0 {
								t.Fatal("per-user path used a shared credential")
							}
							return
						}
						assertK2ConstructionRefusal(t, f, reason)
					})
				}
			}
		}
	}
}

func assertK2ConstructionRefusal(t *testing.T, f *k2ConstructionFixture, reason string) {
	t.Helper()
	_, pool, err := f.srv.Tenancy().Resolve("org-a")
	if err != nil || pool == nil {
		t.Fatalf("refusal hid server instead of reporting Coming soon: %v", err)
	}
	// Even a supplied override cannot bypass a refusal recorded during construction.
	client, err := pool.Client("service", "user-a", mcp.AuthOverride{Header: http.Header{"Authorization": {"Bearer synthetic-override"}}})
	var denial *mcp.Denial
	if client != nil || !errors.As(err, &denial) {
		t.Errorf("client exists or refusal is untyped: client=%T error=%v", client, err)
	} else {
		meta, ok := denial.Metadata.(map[string]any)
		if denial.Code != mcp.CodePolicyUnavailable || !strings.Contains(denial.Message, "Coming soon") || !ok || meta["reason"] != reason || meta["state"] != "coming_soon" || meta["client_source"] != "org" {
			t.Errorf("wrong construction refusal: %+v", denial)
		}
	}
	for name, n := range map[string]int64{"token-store": f.fetches.Load(), "shared-credential-lookup": f.sharedLookups.Load(), "client-factory": f.factories.Load(), "discovery": f.discovery.Load(), "upstream-call": f.dispatch.Load()} {
		if n != 0 {
			t.Errorf("refused %s calls=%d, want zero", name, n)
		}
	}
}

func TestK2ConstructionOrdinarySharedAndPersonal(t *testing.T) {
	for _, runtimeSet := range []bool{false, true} {
		for _, mode := range []string{"shared", "per_user"} {
			t.Run(mode+map[bool]string{true: "/url-set", false: "/url-empty"}[runtimeSet], func(t *testing.T) {
				k2ConstructionCatalog(t, nil)
				f := newK2ConstructionFixture(t, runtimeSet, "ordinary", "ordinary/vendor-mcp@1.0.0", mode, "")
				f.exercise(t)
				if f.fetches.Load() == 0 || f.factories.Load() == 0 || f.discovery.Load() == 0 || f.dispatch.Load() != 1 {
					t.Fatalf("ordinary control: tokens=%d factories=%d discovery=%d calls=%d", f.fetches.Load(), f.factories.Load(), f.discovery.Load(), f.dispatch.Load())
				}
				if mode == "shared" && f.sharedLookups.Load() == 0 {
					t.Fatal("shared positive control did not exercise SharedCredentials")
				}
			})
		}
	}
}

func TestK2ConstructionNoRootFallbackAndEndpoint(t *testing.T) {
	// "@current" names the loaded record's version, so the two cases that need
	// a real record keep testing the root field and the endpoint (not a stale
	// reference) whichever catalog version the build embeds.
	for _, tc := range []struct{ name, ref, suffix, reason string }{
		{"root-only", "google-docs/futureagi-rest@current", "", "k2_client_source_unavailable"},
		{"stale", "google-docs/futureagi-rest@9.9.9", "", "k2_client_source_unavailable"},
		{"incomplete", "google-docs/futureagi-rest", "", "k2_client_source_unavailable"},
		{"wrong-endpoint", "google-docs/futureagi-rest@current", "/elsewhere", "k2_endpoint_unavailable"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			cat := k2ConstructionCatalog(t, func(_ string, record map[string]any) {
				if tc.name == "root-only" {
					delete(record["auth"].(map[string]any)["oauth2"].(map[string]any), "clientSources")
					record["clientSources"] = []string{"org"}
				}
			})
			ref := strings.Replace(tc.ref, "@current", "@"+cat.App("google-docs").Provider("futureagi-rest").Version, 1)
			f := newK2ConstructionFixture(t, true, "google-docs", ref, "per_user", tc.suffix)
			f.exercise(t)
			assertK2ConstructionRefusal(t, f, tc.reason)
		})
	}
}

// POST /-/mcp/credentials/purge with a revision answers 501 unless the wired
// resolver is a PushedGrantRevoker, and 200 once its revocation succeeds. The
// route handler is reachable only in builds tagged cataloguepreflighttest, so
// this asks the same resolver the route asks (Deps().CredentialResolver),
// built by the same production wiring, with no URL runtime configured.
func TestK2ConstructionURLMissingOrdinaryRevisionPurge(t *testing.T) {
	k2ConstructionCatalog(t, nil)
	f := newK2ConstructionFixture(t, false, "ordinary", "", "per_user", "")
	revoker, ok := f.srv.Deps().CredentialResolver.(mcp.PushedGrantRevoker)
	if !ok {
		t.Fatal("the URL-empty wiring dropped revision revocation: the purge route would answer 501")
	}
	if err := revoker.RevokePushedGrants(context.Background(), "org-a", "service", 3); err != nil {
		t.Fatalf("ordinary revision revocation failed: %v", err)
	}
}
