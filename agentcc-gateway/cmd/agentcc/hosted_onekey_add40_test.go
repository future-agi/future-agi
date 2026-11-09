package main

import (
	"encoding/pem"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"go/types"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
)

// 708 add.40 on the composed INT-3C r4 head (HOSTED-ONEKEY-ACTIVATION-1 with
// MIXED-HOP-GUARD-1):
//
//	(a) AGENTCC_HOSTED_ONEKEY_ACTIVATION is read in exactly one place;
//	(b) nothing else makes a hosted node serve a managed provider: managed
//	    providers come only from a config file that config.Load validated
//	    (env, the admin API and org config pushes cannot add one; Helm
//	    overrides are refused by deploy/tests/test_hosted_deployment.py);
//	(c) with the flag on, any one missing condition stops the actual
//	    gateway binary at startup, before a listener opens.
//
// (a) and (b) read the gateway module's non-test source; (c) runs in the
// cloud process gate (G18_GATEWAY_BINARY, network none).

const hostedOneKeyFlag = "AGENTCC_HOSTED_ONEKEY_ACTIVATION"

type add40Source struct {
	rel  string // relative to agentcc-gateway/
	f    *ast.File
	fset *token.FileSet // shared by every source of one add40Sources call
}

// add40Sources parses every non-test Go file of the gateway module.
func add40Sources(t *testing.T) []add40Source {
	t.Helper()
	root := filepath.Join(onekeyRoot, "agentcc-gateway")
	fset := token.NewFileSet()
	var out []add40Source
	err := filepath.WalkDir(root, func(p string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() {
			if p != root && (d.Name() == "vendor" || d.Name() == "testdata" || strings.HasPrefix(d.Name(), ".")) {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(p, ".go") || strings.HasSuffix(p, "_test.go") {
			return nil
		}
		f, err := parser.ParseFile(fset, p, nil, parser.SkipObjectResolution)
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(root, p)
		out = append(out, add40Source{rel: filepath.ToSlash(rel), f: f, fset: fset})
		return nil
	})
	if err != nil {
		t.Fatalf("reading the gateway source: %v", err)
	}
	if len(out) < 200 {
		t.Fatalf("parsed %d gateway source files; the walk is not reading the module", len(out))
	}
	return out
}

// add40Walk visits every node of every source with the name of the
// top-level function it is in ("" at package level).
func add40Walk(sources []add40Source, visit func(rel, fn string, n ast.Node)) {
	for _, s := range sources {
		for _, decl := range s.f.Decls {
			fn := ""
			if fd, ok := decl.(*ast.FuncDecl); ok {
				fn = fd.Name.Name
			}
			ast.Inspect(decl, func(n ast.Node) bool {
				if n != nil {
					visit(s.rel, fn, n)
				}
				return true
			})
		}
	}
}

// add40Call names the function a call expression calls, as "pkg.Name" for
// a selector or "Name" for an identifier.
func add40Call(call *ast.CallExpr) string {
	switch fun := call.Fun.(type) {
	case *ast.SelectorExpr:
		if x, ok := fun.X.(*ast.Ident); ok {
			return x.Name + "." + fun.Sel.Name
		}
		return "." + fun.Sel.Name
	case *ast.Ident:
		return fun.Name
	}
	return ""
}

// (a) The flag's name appears in the source once, as the constant
// EnvHostedOneKeyActivation, and its only read is os.Getenv of that constant
// in config.loadDeploymentProfile, which is also the only writer of the
// unexported setting it decides.
func TestHostedOneKeyFlagReadInOnePlace(t *testing.T) {
	var names, reads, uses, writes, refs []string
	add40Walk(add40Sources(t), func(rel, fn string, n ast.Node) {
		at := rel + ":" + fn
		switch n := n.(type) {
		case *ast.BasicLit:
			if n.Kind == token.STRING {
				if value, err := strconv.Unquote(n.Value); err == nil && value == hostedOneKeyFlag {
					names = append(names, at)
				}
			}
		case *ast.CallExpr:
			switch add40Call(n) {
			case "os.Getenv", "os.LookupEnv", "syscall.Getenv":
				if len(n.Args) == 1 {
					arg := n.Args[0]
					if lit, ok := arg.(*ast.BasicLit); ok && strings.Contains(lit.Value, "ONEKEY") {
						reads = append(reads, at+" (literal)")
					}
					if id, ok := arg.(*ast.Ident); ok && id.Name == "EnvHostedOneKeyActivation" {
						reads = append(reads, at)
					}
					if sel, ok := arg.(*ast.SelectorExpr); ok && sel.Sel.Name == "EnvHostedOneKeyActivation" {
						reads = append(reads, at+" (selector)")
					}
				}
			}
		case *ast.Ident:
			if n.Name == "EnvHostedOneKeyActivation" {
				uses = append(uses, at)
			}
		case *ast.AssignStmt:
			for _, lhs := range n.Lhs {
				if sel, ok := lhs.(*ast.SelectorExpr); ok && sel.Sel.Name == "hostedOneKeyActivation" {
					writes = append(writes, at)
				}
			}
		case *ast.SelectorExpr:
			if n.Sel.Name == "hostedOneKeyActivation" {
				refs = append(refs, at)
			}
		}
	})
	const site = "internal/config/hosted.go:loadDeploymentProfile"
	if len(names) != 1 || names[0] != "internal/config/hosted.go:" {
		t.Errorf("the flag's name is spelled at %v; want once, the constant in internal/config/hosted.go", names)
	}
	if len(reads) != 1 || reads[0] != site {
		t.Errorf("the flag is read at %v; want exactly once, os.Getenv(EnvHostedOneKeyActivation) in %s", reads, site)
	}
	// The declaration and the one read.
	if len(uses) != 2 || uses[0] != "internal/config/hosted.go:" || uses[1] != site {
		t.Errorf("EnvHostedOneKeyActivation is used at %v; want its declaration and the read in %s", uses, site)
	}
	if len(writes) == 0 {
		t.Fatal("found no write of the activation setting; the scan is not reading config.loadDeploymentProfile")
	}
	for _, w := range writes {
		if w != site {
			t.Errorf("the activation setting is written at %s; only %s may decide it", w, site)
		}
	}
	for _, r := range refs {
		if !strings.HasPrefix(r, "internal/config/hosted.go:") {
			t.Errorf("the activation setting is read at %s, outside the hosted profile code", r)
		}
	}
}

// (b) A hosted node's managed providers are exactly the providers.*.managed
// entries of a config file config.Load validated (validateHosted refuses
// them without the flag): no code sets a ProviderConfig's Managed, the
// registry's managed set is written only by providers.NewRegistry from the
// loaded providers, NewRegistry runs once, at startup, and config.Load runs
// only at startup and on /-/reload, where a hosted node refuses any provider
// change (hostedReloadCompatible). The control plane's org configs and the
// key, batch, org and rotation admin APIs never reach any of these.
//
// Other structs have a Managed field too (the capabilities description of a
// provider, the catalogue access rows); writing one of those is not a
// provider's setting. So every assignment to a field named Managed is
// resolved with go/types: it is allowed only when it resolves to a different
// struct's field, and one that does not resolve counts as a write of
// config.ProviderConfig's Managed (the scan fails closed). A probe package,
// checked against the same packages, proves the scan catches a write of
// config.ProviderConfig's Managed, directly, through a map copy and promoted
// through an embedding, and lets another struct's Managed field through.
func TestHostedOneKeyManagedOnlyFromValidatedConfig(t *testing.T) {
	sources := add40Sources(t)
	checked := add40TypeCheck(t, sources)
	guarded := add40ProviderManagedField(t, checked)
	guardedUses := 0
	for _, s := range checked.info.Selections {
		if s.Obj() == guarded {
			guardedUses++
		}
	}
	if guardedUses == 0 {
		t.Fatal("the type check resolved no use of config.ProviderConfig's Managed (logUpstreamFees reads it); the scan cannot tell a write of it from another struct's")
	}
	var managedWrites, otherManaged, providerLiterals, managedSetWrites, loads, registries []string
	add40Walk(sources, func(rel, fn string, n ast.Node) {
		at := rel + ":" + fn
		switch n := n.(type) {
		case *ast.AssignStmt:
			for _, lhs := range n.Lhs {
				if sel, ok := lhs.(*ast.SelectorExpr); ok && sel.Sel.Name == "Managed" {
					if kind := add40ManagedWrite(checked.info, guarded, sel); kind == "other" {
						otherManaged = append(otherManaged, at)
					} else {
						managedWrites = append(managedWrites, at+" ("+kind+")")
					}
				}
				if idx, ok := lhs.(*ast.IndexExpr); ok {
					if sel, ok := idx.X.(*ast.SelectorExpr); ok && sel.Sel.Name == "managed" {
						managedSetWrites = append(managedSetWrites, at)
					}
				}
			}
		case *ast.CompositeLit:
			name := ""
			switch typ := n.Type.(type) {
			case *ast.Ident:
				name = typ.Name
			case *ast.SelectorExpr:
				name = typ.Sel.Name
			}
			if name != "ProviderConfig" {
				return
			}
			for _, elt := range n.Elts {
				if kv, ok := elt.(*ast.KeyValueExpr); ok {
					if key, ok := kv.Key.(*ast.Ident); ok && key.Name == "Managed" {
						providerLiterals = append(providerLiterals, at)
					}
				}
			}
		case *ast.CallExpr:
			switch add40Call(n) {
			case "config.Load":
				loads = append(loads, at)
			case "providers.NewRegistry":
				registries = append(registries, at)
			}
		}
	})
	t.Logf("%d resolved uses of config.ProviderConfig's Managed; Managed writes of other structs at %v", guardedUses, otherManaged)
	if len(managedWrites) != 0 {
		t.Errorf("code assigns config.ProviderConfig's Managed (or a Managed field the type check could not resolve) at %v; a provider's managed setting must come only from the validated config file", managedWrites)
	}
	probe := add40ProbeWrites(t, checked, guarded, add40ManagedProbe)
	if got := strings.Join(probe, ","); got != "guarded,guarded,guarded,other" {
		t.Errorf("the scan classifies the probe's direct, map-copy, promoted and other-struct Managed writes as %s; want guarded,guarded,guarded,other", got)
	}
	if len(providerLiterals) != 0 {
		t.Errorf("code builds a ProviderConfig with Managed set at %v", providerLiterals)
	}
	if len(managedSetWrites) != 1 || managedSetWrites[0] != "internal/providers/registry.go:NewRegistry" {
		t.Errorf("the registry's managed set is written at %v; want only providers.NewRegistry", managedSetWrites)
	}
	if strings.Join(registries, ",") != "cmd/agentcc/main.go:main" {
		t.Errorf("providers.NewRegistry is called at %v; want only the gateway's startup", registries)
	}
	if strings.Join(loads, ",") != "cmd/agentcc/main.go:main,internal/server/server.go:reloadHandler" {
		t.Errorf("config.Load is called at %v; want the gateway's startup and /-/reload only", loads)
	}
}

const add40Module = "github.com/futureagi/agentcc-gateway"

// add40ManagedProbe writes a Managed field four ways: config.ProviderConfig's
// directly, through a copy out of Config.Providers and promoted through an
// embedding (each a write of the guarded field), and another struct's.
const add40ManagedProbe = `package add40probe

import "github.com/futureagi/agentcc-gateway/internal/config"

type display struct{ Managed bool }

type embeds struct{ config.ProviderConfig }

func direct(p *config.ProviderConfig) { p.Managed = true }

func mapCopy(c *config.Config) {
	p := c.Providers["probe"]
	p.Managed = true
	c.Providers["probe"] = p
}

func promoted(e *embeds) { e.Managed = true }

func other(d *display) { d.Managed = true }
`

// add40Checked is the gateway module's non-test source type-checked from
// source: its own packages resolve to each other, and any other import is
// left unresolved (go/types records the error and keeps checking).
type add40Checked struct {
	fset *token.FileSet
	info *types.Info
	pkgs map[string]*types.Package
}

// Import serves the module's packages already checked; it is the
// types.Importer of every check.
func (c *add40Checked) Import(importPath string) (*types.Package, error) {
	if pkg, ok := c.pkgs[importPath]; ok {
		return pkg, nil
	}
	return nil, fmt.Errorf("%s is not checked from the gateway source", importPath)
}

func (c *add40Checked) check(importPath string, files []*ast.File) *types.Package {
	conf := types.Config{Importer: c, Error: func(error) {}, FakeImportC: true}
	pkg, _ := conf.Check(importPath, c.fset, files, c.info)
	return pkg
}

// add40TypeCheck checks every package of sources, each after the module
// packages it imports, into one types.Info.
func add40TypeCheck(t *testing.T, sources []add40Source) *add40Checked {
	t.Helper()
	c := &add40Checked{
		fset: sources[0].fset,
		info: &types.Info{Selections: map[*ast.SelectorExpr]*types.Selection{}},
		pkgs: map[string]*types.Package{},
	}
	files := map[string][]*ast.File{}
	for _, s := range sources {
		importPath := add40Module
		if dir := path.Dir(s.rel); dir != "." {
			importPath += "/" + dir
		}
		files[importPath] = append(files[importPath], s.f)
	}
	visited := map[string]bool{}
	var visit func(importPath string)
	visit = func(importPath string) {
		if visited[importPath] {
			return
		}
		visited[importPath] = true
		for _, f := range files[importPath] {
			for _, spec := range f.Imports {
				if dep, err := strconv.Unquote(spec.Path.Value); err == nil && len(files[dep]) > 0 {
					visit(dep)
				}
			}
		}
		if pkg := c.check(importPath, files[importPath]); pkg != nil {
			c.pkgs[importPath] = pkg
		}
	}
	paths := make([]string, 0, len(files))
	for importPath := range files {
		paths = append(paths, importPath)
	}
	sort.Strings(paths)
	for _, importPath := range paths {
		visit(importPath)
	}
	return c
}

// add40ProviderManagedField is config.ProviderConfig's Managed field object.
func add40ProviderManagedField(t *testing.T, c *add40Checked) *types.Var {
	t.Helper()
	pkg := c.pkgs[add40Module+"/internal/config"]
	if pkg == nil {
		t.Fatal("the config package was not type-checked")
	}
	obj := pkg.Scope().Lookup("ProviderConfig")
	if obj == nil {
		t.Fatal("the config package declares no ProviderConfig")
	}
	st, ok := obj.Type().Underlying().(*types.Struct)
	if !ok {
		t.Fatalf("config.ProviderConfig is a %s, not a struct", obj.Type().Underlying())
	}
	for i := 0; i < st.NumFields(); i++ {
		if field := st.Field(i); field.Name() == "Managed" {
			return field
		}
	}
	t.Fatal("config.ProviderConfig has no Managed field")
	return nil
}

// add40ManagedWrite classifies the assignment target sel (x.Managed):
// "guarded" when it is config.ProviderConfig's Managed (directly or promoted
// through an embedding), "other" when go/types resolved it to another
// struct's field, "unresolved" when it did not resolve.
func add40ManagedWrite(info *types.Info, guarded *types.Var, sel *ast.SelectorExpr) string {
	s := info.Selections[sel]
	if s == nil || s.Kind() != types.FieldVal {
		return "unresolved"
	}
	if s.Obj() == guarded {
		return "guarded"
	}
	return "other"
}

// add40ProbeWrites checks src as one more package against the checked module
// packages and classifies each assignment to a Managed field in it, in
// source order.
func add40ProbeWrites(t *testing.T, c *add40Checked, guarded *types.Var, src string) []string {
	t.Helper()
	f, err := parser.ParseFile(c.fset, "add40_probe.go", src, parser.SkipObjectResolution)
	if err != nil {
		t.Fatalf("parsing the probe: %v", err)
	}
	c.check(add40Module+"/internal/add40probe", []*ast.File{f})
	var kinds []string
	ast.Inspect(f, func(n ast.Node) bool {
		if assign, ok := n.(*ast.AssignStmt); ok {
			for _, lhs := range assign.Lhs {
				if sel, ok := lhs.(*ast.SelectorExpr); ok && sel.Sel.Name == "Managed" {
					kinds = append(kinds, add40ManagedWrite(c.info, guarded, sel))
				}
			}
		}
		return true
	})
	return kinds
}

// (c) With the flag on, a config missing any one activation condition stops
// the actual gateway at startup, before a listener opens, naming the
// condition and never the operator's values. Every case but the TLS ones is
// refused by config.Load, before any network use.
func TestHostedOneKeyProcessMissingConditionFailsClosed(t *testing.T) {
	binary := hostedProcessBinary(t)
	const qualified = "providers: {synthetic: {base_url: 'https://example.com', api_format: openai, managed: true, auto_discover: false}}\n"
	provider := func(url, extra string) string {
		return fmt.Sprintf("providers: {synthetic: {base_url: '%s', api_format: openai, managed: true, auto_discover: false%s}}\n", url, extra)
	}
	cases := []struct{ name, flag, config, want string }{
		{"malformed flag", "yes", qualified, "AGENTCC_HOSTED_ONEKEY_ACTIVATION must be true or false"},
		{"native format", "true", "providers: {synthetic: {base_url: 'https://example.com', api_format: anthropic, managed: true, auto_discover: false}}\n", "hosted one-key requires managed providers.*.api_format=openai"},
		{"provider kind", "true", provider("https://example.com", ", type: groq"), "hosted one-key requires an allowed managed provider kind"},
		{"plain http", "true", provider("http://example.com", ""), "hosted profile requires credential-free HTTPS provider URLs"},
		{"user info", "true", provider("https://operator@example.com", ""), "hosted profile requires credential-free HTTPS provider URLs"},
		{"skip tls", "true", provider("https://example.com", ", skip_tls: true"), "hosted profile refuses insecure or local provider overrides"},
		{"local", "true", provider("https://example.com", ", local: true"), "hosted profile refuses insecure or local provider overrides"},
		{"private address", "true", provider("https://10.0.0.1", ""), "hosted profile refuses private provider URLs"},
		{"carrier-grade NAT", "true", provider("https://100.64.0.1", ""), "hosted profile refuses private provider URLs"},
		{"NAT64 of a private address", "true", provider("https://[64:ff9b::a00:1]", ""), "hosted profile refuses private provider URLs"},
		{"development host", "true", provider("https://api.local", ""), "hosted profile refuses development provider URLs"},
		{"non-public name", "true", provider("https://metadata.google.internal", ""), "hosted profile refuses non-public provider hostnames"},
		{"numeric host", "true", provider("https://2130706433", ""), "hosted profile refuses numeric provider hostnames"},
		{"require credits", "true", qualified + "managed: {require_credits: false}\n", "managed.require_credits=false"},
		{"require redis", "true", qualified + "managed: {require_redis: false}\n", "managed.require_redis=false"},
		{"spend guard", "true", qualified + "managed: {spend_guard: {max_usd_per_minute: 0, max_usd_per_hour: 0, alert_usd_per_hour: 0, new_org_daily_cap_usd: 0}}\n", "managed.spend_guard"},
		{"alert only guard", "true", qualified + "managed: {spend_guard: {max_usd_per_minute: 0, max_usd_per_hour: 0, new_org_daily_cap_usd: 0}}\n", "managed.spend_guard"},
		{"alert only guard with auto pause", "true", qualified + "managed: {spend_guard: {max_usd_per_minute: 0, max_usd_per_hour: 0, new_org_daily_cap_usd: 0, auto_pause: true}}\n", "managed.spend_guard"},
		{"reserved address", "true", provider("https://198.18.0.1", ""), "hosted profile refuses private provider URLs"},
		{"NAT64 of a documentation address", "true", provider("https://[64:ff9b::c000:201]", ""), "hosted profile refuses private provider URLs"},
		{"grant only", "true", qualified + "managed: {grant_only_credits: true}\n", "managed.grant_only_credits=true"},
		{"mixed billing chains", "true", qualified + "managed: {mixed_billing_chains: true, spend_guard: {max_usd_per_minute: 5}}\n", "hosted one-key refuses managed.mixed_billing_chains=true"},
		{"model fallbacks", "true", qualified + "routing: {model_fallbacks: {catalogue/model: [token/model]}}\n", "hosted profile refuses model fallback chains, failover and retries"},
		{"failover", "true", qualified + "routing: {failover: {enabled: true}}\n", "hosted profile refuses model fallback chains, failover and retries"},
		{"retry", "true", qualified + "routing: {retry: {enabled: true}}\n", "hosted profile refuses model fallback chains, failover and retries"},
		{"new org only", "true", qualified + "managed: {spend_guard: {max_usd_per_minute: 0, max_usd_per_hour: 0, new_org_daily_cap_usd: 25}}\n", "enforcing velocity cap"},
		{"both velocity nan", "true", qualified + "managed: {spend_guard: {max_usd_per_minute: .nan, max_usd_per_hour: .nan, new_org_daily_cap_usd: 25}}\n", "NaN"},
		{"both velocity tiny", "true", qualified + "managed: {spend_guard: {max_usd_per_minute: 1e-7, max_usd_per_hour: 4e-7, new_org_daily_cap_usd: 25}}\n", "sub-micro"},
		{"all caps nan", "true", qualified + "managed: {spend_guard: {max_usd_per_minute: .nan, max_usd_per_hour: .nan, new_org_daily_cap_usd: .nan}}\n", "NaN"},
		{"omitted velocity", "true", qualified + "managed: {}\n", "enforcing velocity cap"},
	}
	for _, field := range []string{"max_usd_per_minute", "max_usd_per_hour", "new_org_daily_cap_usd"} {
		for _, value := range []struct{ name, yaml, class string }{
			{"nan", ".nan", "NaN"}, {"inf", ".inf", "infinite"},
			{"tiny", "1e-7", "sub-micro"}, {"tiny4", "4e-7", "sub-micro"},
		} {
			cases = append(cases, struct{ name, flag, config, want string }{
				field + " " + value.name, "true",
				qualified + fmt.Sprintf("managed: {spend_guard: {%s: %s}}\n", field, value.yaml),
				"managed.spend_guard." + field + " is " + value.class,
			})
		}
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			p, text := hostedProcessFixture(t, "http://127.0.0.1:9", freePort(t), processSecret(t))
			if !strings.Contains(tc.config, "\nmanaged:") {
				text += "managed: {spend_guard: {max_usd_per_minute: 5}}\n"
			}
			if err := os.WriteFile(p.path, []byte(text+tc.config), 0o600); err != nil {
				t.Fatal(err)
			}
			p.env = append(p.env, hostedOneKeyFlag+"="+tc.flag, "SSL_CERT_DIR="+t.TempDir(), "SSL_CERT_FILE="+filepath.Join(t.TempDir(), "absent.pem"))
			p.start(t, binary)
			p.wait(t, false)
			if !strings.Contains(p.out.String(), tc.want) {
				t.Fatalf("startup did not stop at %q", tc.want)
			}
			if processStatus(p.url+"/livez") != 0 {
				t.Fatal("a refused startup exposed a listener")
			}
		})
	}
}

// (c) The trusted-CA, wrong-hostname case: the certificate chains to the
// trusted synthetic CA but does not name the host, so the TLS preflight
// refuses it. The gate maps G18_MISMATCH_HOST to loopback.
func TestHostedOneKeyProcessHostnameMismatchFailsClosed(t *testing.T) {
	binary := hostedProcessBinary(t)
	host := os.Getenv("G18_MISMATCH_HOST")
	if host == "" {
		t.Skip("cloud process gate maps G18_MISMATCH_HOST to loopback")
	}
	var requests atomic.Int32
	upstream := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		w.WriteHeader(http.StatusOK)
	}))
	upstream.Config.ErrorLog = log.New(io.Discard, "", 0)
	upstream.StartTLS()
	defer upstream.Close()
	// httptest's certificate also names example.com and *.example.com, so the
	// host must be checked to be one it does not name.
	if upstream.Certificate().VerifyHostname(host) == nil {
		t.Fatalf("G18_MISMATCH_HOST %q is a name on the synthetic certificate %v; it must be one the certificate does not name", host, upstream.Certificate().DNSNames)
	}
	_, port, err := net.SplitHostPort(upstream.Listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	trust := filepath.Join(t.TempDir(), "synthetic-ca.pem")
	if err := os.WriteFile(trust, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: upstream.Certificate().Raw}), 0o600); err != nil {
		t.Fatal(err)
	}
	p, text := hostedProcessFixture(t, "http://127.0.0.1:9", freePort(t), processSecret(t))
	text += "managed: {spend_guard: {max_usd_per_minute: 5}}\n"
	text += fmt.Sprintf("providers: {synthetic: {type: openrouter, base_url: 'https://%s:%s', managed: true, auto_discover: false}}\n", host, port)
	if err := os.WriteFile(p.path, []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
	p.env = append(p.env, hostedOneKeyFlag+"=true", "SSL_CERT_DIR="+t.TempDir(), "SSL_CERT_FILE="+trust)
	p.start(t, binary)
	p.wait(t, false)
	if !strings.Contains(p.out.String(), "hosted one-key provider TLS verification failed") {
		t.Fatal("a certificate that does not name the host passed the TLS preflight")
	}
	if processStatus(p.url+"/livez") != 0 {
		t.Fatal("a refused startup exposed a listener")
	}
	if requests.Load() != 0 {
		t.Fatal("the TLS preflight sent an HTTP request")
	}
}
