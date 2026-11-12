package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"slices"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// These tests state one rule: no recorded "admitted" outcome authorises a
// credential lookup or a send. Every one starts from a real, admitted, USED
// connection (a retained client handle), changes something that must end its
// admission, and then expects zero token lookups, shared lookups, client
// factories, discoveries and upstream sends. They run the production wiring
// (real tenancy manager, real chain, real credential resolver); only the
// caller, the token service and the upstream transport are synthetic.

// k2TestRuntime is the runtime base newK2ConstructionFixture configures.
const k2TestRuntime = "http://127.0.0.1:9"

// k2Counters are the side effects a refused binding must never add.
type k2Counters struct{ tokens, shared, factories, discovery, dispatch int64 }

func k2Counts(f *k2ConstructionFixture) k2Counters {
	return k2Counters{f.fetches.Load(), f.sharedLookups.Load(), f.factories.Load(), f.discovery.Load(), f.dispatch.Load()}
}

func k2PerUserServer(url, ref string) map[string]any {
	return map[string]any{"url": url, "catalog_ref": ref, "credential": map[string]any{"mode": "per_user"}}
}

// k2PushServers replaces org-a's config with exactly these servers. The
// credential of a server is never changed by these tests: only its binding is.
func k2PushServers(t *testing.T, f *k2ConstructionFixture, version int, servers map[string]any) {
	t.Helper()
	raw, err := json.Marshal(map[string]any{"meta": map[string]any{"version": version}, "mcp": map[string]any{
		"enabled": true, "access": map[string]any{"default": "allow"}, "servers": servers}})
	if err != nil {
		t.Fatal(err)
	}
	var org tenant.OrgConfig
	if err := json.Unmarshal(raw, &org); err != nil {
		t.Fatal(err)
	}
	f.gateway.TenantStore.Set("org-a", &org)
}

func k2RetainedOverride() mcp.AuthOverride {
	return mcp.AuthOverride{Header: http.Header{"Authorization": {"Bearer synthetic-retained"}}}
}

// k2RetainHandle takes the client handle of the admitted server before any
// refusal: the retained handle a refused binding must not keep serving.
func k2RetainHandle(t *testing.T, f *k2BoundaryFixture) mcp.UpstreamClient {
	t.Helper()
	_, pool, err := f.srv.Tenancy().Resolve("org-a")
	if err != nil || pool == nil {
		t.Fatalf("admitted control has no pool: %v", err)
	}
	client, err := pool.Client("service", "user-a", k2RetainedOverride())
	if err != nil || client == nil {
		t.Fatalf("admitted control gave no client: %v", err)
	}
	return client
}

func k2WantComingSoon(t *testing.T, what string, err error, reason string) {
	t.Helper()
	var denial *mcp.Denial
	if !errors.As(err, &denial) {
		t.Errorf("%s: want typed Coming soon refusal %q, got %v", what, reason, err)
		return
	}
	meta, ok := denial.Metadata.(map[string]any)
	if denial.Code != mcp.CodePolicyUnavailable || !strings.Contains(denial.Message, "Coming soon") || !ok || meta["reason"] != reason || meta["state"] != "coming_soon" {
		t.Errorf("%s: wrong refusal, want reason %q: %+v", what, reason, denial)
	}
}

// k2RetainedRefused drives a handle obtained before the refusal through every
// operation it offers. Each is evaluated; none may reach the upstream.
func k2RetainedRefused(t *testing.T, retained mcp.UpstreamClient, reason string) {
	t.Helper()
	_, err := retained.CallTool(context.Background(), "echo", json.RawMessage(`{}`), mcp.CallOptions{})
	k2WantComingSoon(t, "retained tools/call", err, reason)
	_, err = retained.ListTools(context.Background())
	k2WantComingSoon(t, "retained tools/list", err, reason)
}

// k2ChainRefused runs the HTTP tools/call chain, which looks the credential up.
func k2ChainRefused(t *testing.T, f *k2BoundaryFixture, reason string) {
	t.Helper()
	answer := f.call(t)
	if !answer.IsError || answer.Denial.Code != mcp.CodePolicyUnavailable || !strings.Contains(answer.Denial.Message, "Coming soon") {
		t.Errorf("tools/call: want Coming soon, got isError=%v code=%q", answer.IsError, answer.Denial.Code)
		return
	}
	if meta, ok := answer.Denial.Metadata.(map[string]any); !ok || meta["reason"] != reason {
		t.Errorf("tools/call: wrong refusal metadata, want reason %q: %+v", reason, answer.Denial.Metadata)
	}
}

// k2FreshHandleRefused asks for a new handle and for the credential-lookup
// gate itself, with an override that would otherwise build a client.
func k2FreshHandleRefused(t *testing.T, f *k2BoundaryFixture, reason string) {
	t.Helper()
	_, pool, err := f.srv.Tenancy().Resolve("org-a")
	if err != nil || pool == nil {
		t.Errorf("a refusal must keep the pool and report Coming soon: %v", err)
		return
	}
	client, err := pool.Client("service", "user-a", k2RetainedOverride())
	if client != nil {
		t.Error("a refused binding still handed out a client")
	}
	k2WantComingSoon(t, "pool.Client", err, reason)
	gate, ok := pool.(mcp.ClientAdmissionPool)
	if !ok {
		t.Error("the pool lost the credential-lookup gate")
		return
	}
	if denial := gate.ClientAdmission("service"); denial == nil {
		t.Error("the credential-lookup gate admitted a refused binding")
	} else {
		k2WantComingSoon(t, "credential-lookup gate", denial, reason)
	}
}

func k2NoSideEffects(t *testing.T, f *k2ConstructionFixture, before k2Counters) {
	t.Helper()
	if after := k2Counts(f); after != before {
		t.Errorf("a refused binding added credential lookups, constructions, discoveries or sends: before=%+v after=%+v", before, after)
	}
}

// The catalog owner's records (gwc d2fd7fd6): all twelve futureagi-rest
// providers are enabled, gmail included, and declare org; calendar also
// declares platform. Each one is a positive org control through the full
// wiring, and the fixture is checked against those declarations so it cannot
// drift from them.
func TestK2PositiveOrgControlsAllTwelve(t *testing.T) {
	if len(k2BoundaryApps) != 12 {
		t.Fatalf("census: %d served K2 records, want 12", len(k2BoundaryApps))
	}
	for _, app := range k2BoundaryApps {
		t.Run(app, func(t *testing.T) {
			cat := k2ConstructionCatalog(t, nil)
			p := cat.App(app).Provider("futureagi-rest")
			sources := []string{"org"}
			if app == "google-calendar" {
				sources = []string{"org", "platform"}
			}
			if p.Auth.OAuth2 == nil || !slices.Equal(p.Auth.OAuth2.ClientSources, sources) {
				t.Fatalf("fixture clientSources drifted from the catalog owner's record: want %v", sources)
			}
			if p.XGateway == nil || p.XGateway.ConnectionEnablement == nil || !p.XGateway.ConnectionEnablement.Enabled ||
				p.XGateway.ConnectionEnablement.SchemaVersion != 1 || len(p.XGateway.ConnectionEnablement.Operations) != 3 {
				t.Fatalf("fixture connectionEnablement drifted from the catalog owner's record: %+v", p.XGateway)
			}
			f := newK2BoundaryFixture(t, true, app+"/futureagi-rest@"+p.Version, app)
			assertK2BoundarySuccess(t, f, f.call(t))
		})
	}
}

// A refused replacement must not leave the formerly admitted client serving.
// The pushed config keeps the credential identical and changes only the
// binding; the invalid sibling makes the pool refuse the whole config and fall
// back to last-known-good, which must never resurrect the refused binding.
func TestK2RetainedClientAfterRefusedReplacement(t *testing.T) {
	for _, wiring := range []struct {
		name string
		set  bool
	}{{"url-empty", false}, {"url-set", true}} {
		for _, change := range []string{"ref-only", "url-only", "ref-and-url", "rebind-admitted", "removed"} {
			for _, sibling := range []string{"valid", "invalid"} {
				if !wiring.set && (change == "url-only" || change == "removed") {
					continue // an ordinary server stays ordinary: nothing is refused
				}
				if change == "removed" && sibling == "valid" {
					continue // the server simply leaves the pool
				}
				t.Run(wiring.name+"/"+change+"/"+sibling+"-sibling", func(t *testing.T) {
					cat := k2ConstructionCatalog(t, func(app string, record map[string]any) {
						if app == "google-sheets" {
							record["x-gateway"].(map[string]any)["connectionEnablement"].(map[string]any)["enabled"] = false
						}
					})
					version := func(app string) string { return cat.App(app).Provider("futureagi-rest").Version }
					startApp, startRef := "google-docs", "google-docs/futureagi-rest@"+version("google-docs")
					if !wiring.set {
						// Nothing K2 can be admitted without a runtime: the retained
						// client is an ordinary server's, then re-pointed at K2.
						startApp, startRef = "ordinary", "ordinary/vendor-mcp@1.0.0"
					}
					f := newK2BoundaryFixture(t, wiring.set, startRef, startApp)
					assertK2BoundarySuccess(t, f, f.call(t))
					retained := k2RetainHandle(t, f)
					before := k2Counts(f.real)

					url, ref, reason, admitted := k2TestRuntime+"/"+startApp+"/mcp", startRef, "k2_runtime_unavailable", false
					switch change {
					case "ref-only":
						ref = "google-docs/futureagi-rest@" + version("google-docs")
						if wiring.set {
							ref, reason = "google-docs/futureagi-rest@9.9.9", "k2_client_source_unavailable"
						}
					case "url-only":
						url, reason = k2TestRuntime+"/elsewhere", "k2_endpoint_unavailable"
					case "ref-and-url":
						ref, url = "google-sheets/futureagi-rest@"+version("google-sheets"), k2TestRuntime+"/google-sheets/mcp"
						if wiring.set {
							reason = "k2_connection_disabled"
						}
					case "rebind-admitted":
						ref, url = "google-drive/futureagi-rest@"+version("google-drive"), k2TestRuntime+"/google-drive/mcp"
						if wiring.set {
							reason, admitted = "k2_binding_changed", sibling == "valid"
						}
					case "removed":
						reason = "k2_binding_changed"
					}
					servers := map[string]any{"service": k2PerUserServer(url, ref)}
					if change == "removed" {
						servers = map[string]any{}
					}
					if sibling == "invalid" {
						servers["sibling"] = k2PerUserServer("not-a-url", "")
					} else {
						servers["sibling"] = k2PerUserServer(k2TestRuntime+"/sibling/mcp", "ordinary/vendor-mcp@1.0.0")
					}
					k2PushServers(t, f.real, 2, servers)

					// Before anything resolves the pool again: the store write itself
					// must have ended the old binding's admission.
					k2RetainedRefused(t, retained, reason)
					if admitted {
						// A valid, admitted rebinding is the positive control: the new
						// binding serves, and only the new binding.
						answer := f.call(t)
						if answer.IsError || answer.Denial.Code != "" || f.real.dispatch.Load() != before.dispatch+1 {
							t.Errorf("valid rebinding did not serve: isError=%v code=%q dispatches=%d", answer.IsError, answer.Denial.Code, f.real.dispatch.Load())
						}
						k2RetainedRefused(t, retained, reason)
						return
					}
					k2ChainRefused(t, f, reason)
					k2FreshHandleRefused(t, f, reason)
					k2RetainedRefused(t, retained, reason)
					k2NoSideEffects(t, f.real, before)
				})
			}
		}
	}
}

// The catalog record is read at every use. Nothing is pushed here: the record
// the gate reads changes under a live, retained, admitted connection, and the
// next lookup and the next send must both refuse. All twelve records first
// prove the positive org control (gmail included, enabled at gwc d2fd7fd6).
func TestK2CatalogRecordFlipRefusedOnNextUse(t *testing.T) {
	flips := []struct {
		name, reason string
		edit         func(*manifest.Provider)
	}{
		{"enablement-off", "k2_connection_disabled", func(p *manifest.Provider) { p.XGateway.ConnectionEnablement.Enabled = false }},
		{"operations-narrowed", "k2_connection_disabled", func(p *manifest.Provider) { p.XGateway.ConnectionEnablement.Operations = []string{"connect"} }},
		{"enablement-removed", "k2_connection_disabled", func(p *manifest.Provider) { p.XGateway.ConnectionEnablement = nil }},
		{"source-platform-only", "k2_client_source_unavailable", func(p *manifest.Provider) { p.Auth.OAuth2.ClientSources = []string{"platform"} }},
	}
	if len(k2BoundaryApps) != 12 {
		t.Fatalf("census: %d served K2 records, want 12", len(k2BoundaryApps))
	}
	for _, flip := range flips {
		for _, app := range k2BoundaryApps {
			t.Run(flip.name+"/"+app, func(t *testing.T) {
				cat := k2ConstructionCatalog(t, nil)
				p := cat.App(app).Provider("futureagi-rest")
				f := newK2BoundaryFixture(t, true, app+"/futureagi-rest@"+p.Version, app)
				assertK2BoundarySuccess(t, f, f.call(t))
				retained := k2RetainHandle(t, f)
				before := k2Counts(f.real)

				flip.edit(p)

				k2RetainedRefused(t, retained, flip.reason)
				k2ChainRefused(t, f, flip.reason)
				k2FreshHandleRefused(t, f, flip.reason)
				k2NoSideEffects(t, f.real, before)
			})
		}
	}
}

// A control that keeps the fix honest about scope: an ordinary server keeps
// its last-known-good behaviour exactly as before.
func TestK2OrdinaryServerKeepsLastKnownGood(t *testing.T) {
	for _, wiring := range []struct {
		name string
		set  bool
	}{{"url-empty", false}, {"url-set", true}} {
		t.Run(wiring.name, func(t *testing.T) {
			k2ConstructionCatalog(t, nil)
			f := newK2BoundaryFixture(t, wiring.set, "ordinary/vendor-mcp@1.0.0", "ordinary")
			assertK2BoundarySuccess(t, f, f.call(t))
			retained := k2RetainHandle(t, f)
			k2PushServers(t, f.real, 2, map[string]any{
				"service": k2PerUserServer(k2TestRuntime+"/ordinary-next/mcp", "ordinary/vendor-mcp@1.0.0"),
				"sibling": k2PerUserServer("not-a-url", ""),
			})
			before := k2Counts(f.real)
			if _, err := retained.CallTool(context.Background(), "echo", json.RawMessage(`{}`), mcp.CallOptions{}); err != nil {
				t.Errorf("an ordinary server lost its last-known-good client: %v", err)
			}
			if got := f.real.dispatch.Load(); got != before.dispatch+1 {
				t.Errorf("ordinary retained client dispatches=%d, want %d", got, before.dispatch+1)
			}
		})
	}
}
