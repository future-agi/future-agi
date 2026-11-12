package main

import (
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// Eight additions plus four pilots, all enabled at gwc d2fd7fd6: this is a
// fixture census, not an admission list. Every refusal must be at the credential boundary, not a
// test false-positive caused by missing identity, tools, policy or transport.
var k2BoundaryApps = []string{
	"google-docs", "google-drive", "google-meet", "google-sheets", "google-slides",
	"microsoft-excel", "microsoft-onedrive", "microsoft-sharepoint",
	"gmail", "google-calendar", "microsoft-outlook", "microsoft-teams",
}

func TestK2ServedSetFullWiringBoundary(t *testing.T) {
	for _, runtime := range []struct {
		name string
		set  bool
	}{{"url-empty", false}, {"url-set", true}} {
		for _, sources := range []struct {
			name   string
			values []string
			org    bool
		}{
			{"missing", nil, false},
			{"platform-only", []string{"platform"}, false},
			{"unknown", []string{"future"}, false},
			{"org-and-unknown", []string{"org", "future"}, false},
			{"org", []string{"org"}, true},
		} {
			for _, app := range k2BoundaryApps {
				t.Run(runtime.name+"/"+sources.name+"/"+app, func(t *testing.T) {
					cat := k2BoundaryCatalog(t, sources.values)
					p := cat.App(app).Provider("futureagi-rest")
					if p == nil {
						t.Fatalf("missing fixture provider %s", app)
					}
					f := newK2BoundaryFixture(t, runtime.set, app+"/futureagi-rest@"+p.Version, app)
					answer := f.call(t)
					if runtime.set && sources.org {
						assertK2BoundarySuccess(t, f, answer)
						return
					}
					reason := "k2_client_source_unavailable"
					if !runtime.set {
						reason = "k2_runtime_unavailable"
					}
					assertK2BoundaryRefusal(t, f, answer, reason)
				})
			}
		}
	}
}

func TestK2ServedSetFullWiringUnknownReferences(t *testing.T) {
	for _, runtimeSet := range []bool{false, true} {
		for _, ref := range []string{
			"catalog-next-app/futureagi-rest@1.0.0",
			"gmail/futureagi-rest@9.9.9", "gmail/futureagi-rest",
		} {
			name := "url-empty/" + ref
			if runtimeSet {
				name = "url-set/" + ref
			}
			t.Run(name, func(t *testing.T) {
				k2BoundaryCatalog(t, []string{"org"})
				f := newK2BoundaryFixture(t, runtimeSet, ref, "gmail")
				reason := "k2_runtime_unavailable"
				if runtimeSet {
					reason = "k2_client_source_unavailable"
				}
				assertK2BoundaryRefusal(t, f, f.call(t), reason)
			})
		}
	}
}

func TestK2ServedSetFullWiringOrdinaryServers(t *testing.T) {
	for _, runtimeSet := range []bool{false, true} {
		for _, ref := range []string{"", "gmail/other-provider@1.0.0", "ordinary/vendor-mcp@1.0.0"} {
			name := "url-empty/" + ref
			if runtimeSet {
				name = "url-set/" + ref
			}
			t.Run(name, func(t *testing.T) {
				k2BoundaryCatalog(t, nil)
				f := newK2BoundaryFixture(t, runtimeSet, ref, "ordinary")
				assertK2BoundarySuccess(t, f, f.call(t))
				purger, ok := f.srv.Deps().CredentialResolver.(mcp.CredentialPurger)
				if !ok {
					t.Fatal("wiring dropped credential purge support")
				}
				if _, err := purger.PurgeCredentials(t.Context(), "org-a", "user-a", "service"); err != nil {
					t.Fatalf("ordinary credential purge failed: %v", err)
				}
			})
		}
	}
}

func assertK2BoundaryRefusal(t *testing.T, f *k2BoundaryFixture, answer k2BoundaryAnswer, reason string) {
	t.Helper()
	// Evaluate both counters even if the typed denial assertion fails on RED.
	if got := f.real.fetches.Load(); got != 0 {
		t.Errorf("refusal fetched credentials %d times, want zero", got)
	}
	if got := f.real.factories.Load(); got != 0 {
		t.Errorf("refusal acquired upstream clients %d times, want zero", got)
	}
	if got := f.real.dispatch.Load(); got != 0 {
		t.Errorf("refusal dispatched upstream %d times, want zero", got)
	}
	if !answer.IsError || answer.Denial.Code != mcp.CodePolicyUnavailable || !strings.Contains(answer.Denial.Message, "Coming soon") {
		t.Fatalf("wanted Coming soon, got isError=%v code=%q", answer.IsError, answer.Denial.Code)
	}
	meta, ok := answer.Denial.Metadata.(map[string]any)
	if !ok || meta["state"] != "coming_soon" || meta["client_source"] != "org" || meta["reason"] != reason {
		t.Fatalf("wrong refusal metadata: %+v", answer.Denial.Metadata)
	}
}

func assertK2BoundarySuccess(t *testing.T, f *k2BoundaryFixture, answer k2BoundaryAnswer) {
	t.Helper()
	if answer.IsError || answer.Denial.Code != "" || f.real.fetches.Load() == 0 || f.real.factories.Load() == 0 || f.real.dispatch.Load() != 1 {
		t.Fatalf("positive control failed: isError=%v code=%q fetches=%d clients=%d dispatches=%d",
			answer.IsError, answer.Denial.Code, f.real.fetches.Load(), f.real.factories.Load(), f.real.dispatch.Load())
	}
}
