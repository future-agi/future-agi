package credtemplate_test

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate"
	"github.com/futureagi/agentcc-gateway/internal/mcp/tenancy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// headerCase is one pushed header credential and the header the gateway
// sends for it today.
type headerCase struct {
	name string
	// credHeader is the credential reference's own header
	// (MCPServerCredential.header), which the gateway uses when the secret
	// names none.
	credHeader          string
	sec                 credtemplate.Secret // Value is set by the test
	wantName, wantValue string              // wantValue: "%s" is the secret
}

// headerCases are what today's pool path (tenancy's sharedAuth, then the
// HTTP transport's applyAuth) sends for each shape of pushed header secret,
// quirks included: a bearer secret always goes in Authorization, and an
// api_key whose header is Authorization, or that names no header at all, is
// sent as a bearer. TestHeaderPlacementUnchanged checks this table against
// the pool path itself, so it records today's behaviour, not a reading of
// the code.
var headerCases = []headerCase{
	{"bearer", "", credtemplate.Secret{Type: "bearer", Header: "Authorization"}, "Authorization", "Bearer %s"},
	{"bearer with another header still goes in Authorization", "", credtemplate.Secret{Type: "bearer", Header: "X-Token"}, "Authorization", "Bearer %s"},
	{"api_key in its header", "", credtemplate.Secret{Type: "api_key", Header: "X-Api-Key"}, "X-Api-Key", "%s"},
	{"api_key whose header is Authorization is a bearer", "", credtemplate.Secret{Type: "api_key", Header: "authorization"}, "Authorization", "Bearer %s"},
	{"api_key whose header is on the credential reference", "X-Vendor-Key", credtemplate.Secret{Type: "api_key"}, "X-Vendor-Key", "%s"},
	{"api_key with no header anywhere is a bearer", "", credtemplate.Secret{Type: "api_key"}, "Authorization", "Bearer %s"},
	{"a name equal to its header, no in", "", credtemplate.Secret{Type: "api_key", Header: "X-Api-Key", Name: "x-api-key"}, "X-Api-Key", "%s"},
	{"in header, header equal to name", "", credtemplate.Secret{Type: "api_key", Header: "x-api-key", In: "header", Name: "x-api-key"}, "X-Api-Key", "%s"},
	{"in header, the name equal to the credential reference's header", "X-Vendor-Key", credtemplate.Secret{Type: "api_key", In: "header", Name: "x-vendor-key"}, "X-Vendor-Key", "%s"},
	{"in header, a bearer in Authorization", "", credtemplate.Secret{Type: "bearer", Header: "Authorization", In: "header", Name: "Authorization"}, "Authorization", "Bearer %s"},
}

// TestHeaderPlacementUnchanged: a header credential, written in today's
// form or with in: header, is placed exactly as the gateway places
// it now. For each case the table is first checked against today's pool
// path (an org config pushed into a tenancy pool whose real client calls
// the MCP mock); then the template, given the same secret, sends the same
// header with the same value on every request of a real MCP session, adds
// no other header carrying the secret, and leaves the URL as configured.
func TestHeaderPlacementUnchanged(t *testing.T) {
	const secret = "hdr-secret-4d1e9b"
	for _, tc := range headerCases {
		t.Run(tc.name, func(t *testing.T) {
			want := fmt.Sprintf(tc.wantValue, secret)
			sec := tc.sec
			sec.Value = secret
			check := func(path string, reqs []seen) {
				t.Helper()
				if len(reqs) == 0 {
					t.Fatalf("%s: the upstream received nothing", path)
				}
				for _, r := range reqs {
					if got := r.header.Values(tc.wantName); len(got) != 1 || got[0] != want {
						t.Fatalf("%s: %s %s carried %d %s values, want one equal to the table's", path, r.method, r.path, len(got), tc.wantName)
					}
					if hs := headerCarrying(r.header, secret); len(hs) != 1 || http.CanonicalHeaderKey(hs[0]) != http.CanonicalHeaderKey(tc.wantName) {
						t.Fatalf("%s: the secret is carried by headers %v, want only %s", path, hs, tc.wantName)
					}
					if r.path != "/mcp" || r.rawQuery != "" {
						t.Fatalf("%s: the URL changed to path %q with a query of %d bytes", path, r.path, len(r.rawQuery))
					}
				}
			}

			// Today's pool path: the table is what it sends.
			srv, rec := serve(t, mcpMock(t, "2025-06-18").Handler())
			todayPool(t, srv.URL+"/mcp", tc.credHeader, sec)
			check("today's pool path", rec.all())

			// The template, given the same secret and the credential
			// reference's header.
			withRef := sec
			withRef.ReferenceHeader = tc.credHeader
			srv2, rec2 := serve(t, mcpMock(t, "2025-06-18").Handler())
			tpl := mustTemplate(t, srv2.URL+"/mcp", withRef)
			echo(t, connect(t, "s", srv2.URL+"/mcp", "http", templated{guard(t), tpl}))
			check("the template", rec2.all())
			if got := tpl.HeaderNames(); len(got) != 1 || http.CanonicalHeaderKey(got[0]) != http.CanonicalHeaderKey(tc.wantName) {
				t.Fatalf("HeaderNames() = %v, want [%s] (the header dropped on a cross-origin redirect)", got, tc.wantName)
			}
		})
	}

	t.Run("in header with no header field is placed under its name", func(t *testing.T) {
		// Off the catalog form (which sends header equal to name), so today's gateway
		// would send it as a bearer; in and name decide where it goes.
		srv, rec := serve(t, mcpMock(t, "2025-06-18").Handler())
		tpl := mustTemplate(t, srv.URL+"/mcp", credtemplate.Secret{Type: "api_key", In: "header", Name: "X-Api-Key", Value: secret})
		echo(t, connect(t, "s", srv.URL+"/mcp", "http", templated{guard(t), tpl}))
		for _, r := range rec.all() {
			if r.header.Get("X-Api-Key") != secret || r.header.Get("Authorization") != "" {
				t.Fatalf("%s %s: want the key in X-Api-Key only", r.method, r.path)
			}
		}
	})
}

// todayPool pushes one shared server with the pushed secret sec into an org
// config and makes one tools/call through the tenancy pool's real client:
// the gateway's path for a pushed credential today.
func todayPool(t *testing.T, serverURL, credHeader string, sec credtemplate.Secret) {
	t.Helper()
	secret := map[string]string{"type": sec.Type, "value": sec.Value}
	for k, v := range map[string]string{"header": sec.Header, "in": sec.In, "name": sec.Name} {
		if v != "" {
			secret[k] = v
		}
	}
	cred := map[string]any{"mode": "shared", "credential_id": "cred_cn7", "state": "active", "secret": secret}
	if credHeader != "" {
		cred["header"] = credHeader
	}
	body, err := json.Marshal(map[string]any{"meta": map[string]any{"version": 1}, "mcp": map[string]any{
		"enabled": true, "servers": map[string]any{"s": map[string]any{"url": serverURL, "credential": cred}}}})
	if err != nil {
		t.Fatal(err)
	}
	var cfg tenant.OrgConfig
	if err := json.Unmarshal(body, &cfg); err != nil {
		t.Fatalf("org config: %v", err)
	}
	store := tenant.NewStore()
	store.Set("org-cn7", &cfg)
	m := tenancy.New(tenancy.Options{
		Store: store,
		Deps: func() *mcp.Deps {
			return &mcp.Deps{ClientFactory: mcp.DefaultClientFactory, Builtins: mcp.NewBuiltinRegistry(), ManagedServers: noManaged{}}
		},
		InitialWait: 10 * time.Second,
	})
	defer func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = m.Close(ctx)
	}()
	_, pool, err := m.Resolve("org-cn7")
	if err != nil {
		t.Fatalf("resolve: %v", err)
	}
	uc, err := pool.Client("s", "", mcp.AuthOverride{})
	if err != nil {
		t.Fatalf("the pool has no client: %v", err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if _, err := uc.CallTool(ctx, "echo", json.RawMessage(`{"text":"cn7-hello"}`), mcp.CallOptions{}); err != nil {
		t.Fatalf("tools/call through the pool: %v", err)
	}
}

type noManaged struct{}

func (noManaged) ForOrg(string) []mcp.ClientConfig { return nil }
