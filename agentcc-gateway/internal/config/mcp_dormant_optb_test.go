package config

import (
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"io/fs"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

// Dormant config-file admission is intentional in option A. Runtime inertness
// is paired with TestOptBHostedMCPNonActivationDormantFile in internal/server.
func TestOptBHostedMCPNonActivationDormantConfig(t *testing.T) {
	t.Setenv(EnvDeploymentProfile, "hosted")
	t.Setenv(EnvHostedOneKeyActivation, "false")
	for _, key := range []string{"AGENTCC_ADMIN_TOKEN", "AGENTCC_INTERNAL_API_KEY", "AGENTCC_WEBHOOK_SECRET"} {
		t.Setenv(key, strings.Repeat("test-admin-", 5))
	}
	t.Setenv("AGENTCC_CONTROL_PLANE_URL", "https://backend.invalid")
	t.Setenv("AGENTCC_SYNC_ON_STARTUP", "true")
	t.Setenv("AGENTCC_SYNC_INTERVAL", "60s")
	t.Setenv("AGENTCC_REDIS_ADDRESS", "redis:6379")
	path := filepath.Join(t.TempDir(), "gateway.json")
	body := `{"auth":{"enabled":true},"guardrails":{"enabled":true},"server":{"trusted_proxies":["192.0.2.10/32"],"max_request_timeout":"300s"},"redis":{"enabled":true,"timeout":"1s"},"mcp":{"enabled":false,"servers":{"fixture":{"url":"http://127.0.0.1:1","transport":"http"}}},"tools":{"enabled":false}}`
	if os.WriteFile(path, []byte(body), 0600) != nil {
		t.Fatal("dormant fixture write failed")
	}
	cfg, err := Load(path)
	if err != nil {
		t.Fatal("dormant hosted fixture refused")
	}
	if !cfg.IsHosted() || cfg.MCP.Enabled || len(cfg.MCP.ManagedServers) != 0 || len(cfg.MCP.Servers) != 1 {
		t.Fatal("dormant hosted configuration invariant failed")
	}
}

type optBNonactSources struct {
	set   *token.FileSet
	files map[string]*ast.File
}

// Only Go source is opened: never deployment YAML, credentials, or fixtures.
// Do not use a text regexp: comments and strings cannot satisfy this census.
func optBNonactSourceAST(t *testing.T) optBNonactSources {
	t.Helper()
	out := optBNonactSources{token.NewFileSet(), map[string]*ast.File{}}
	root := filepath.Join("..", "..")
	err := filepath.WalkDir(root, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() {
			if entry.Name() == ".git" || entry.Name() == "vendor" || entry.Name() == "node_modules" {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		rel, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		f, err := parser.ParseFile(out.set, path, nil, parser.SkipObjectResolution)
		if err != nil {
			return err
		}
		out.files[filepath.ToSlash(rel)] = f
		return nil
	})
	if err != nil {
		t.Fatal("production Go AST census failed")
	}
	for _, path := range []string{"internal/server/routes_mcp.go", "cmd/agentcc/wiring_mcp_tenancy.go", "cmd/agentcc/main.go", "internal/server/server.go"} {
		if out.files[path] == nil {
			t.Fatal("required source missing from AST census")
		}
	}
	return out
}

func optBNonactIdent(expr ast.Expr, name string) bool {
	id, ok := expr.(*ast.Ident)
	return ok && id.Name == name
}

func optBNonactSelect(expr ast.Expr, member string) (*ast.SelectorExpr, bool) {
	s, ok := expr.(*ast.SelectorExpr)
	return s, ok && s.Sel.Name == member
}

// An OR with this nil check also returns whenever MCP is nil; an AND does not.
func optBNonactNilCondition(expr ast.Expr) bool {
	if p, ok := expr.(*ast.ParenExpr); ok {
		return optBNonactNilCondition(p.X)
	}
	b, ok := expr.(*ast.BinaryExpr)
	if !ok {
		return false
	}
	if b.Op == token.LOR {
		return optBNonactNilCondition(b.X) || optBNonactNilCondition(b.Y)
	}
	if b.Op != token.EQL || !optBNonactIdent(b.Y, "nil") {
		return false
	}
	call, ok := b.X.(*ast.CallExpr)
	if !ok || len(call.Args) != 0 {
		return false
	}
	m, ok := optBNonactSelect(call.Fun, "MCP")
	if !ok {
		return false
	}
	s, ok := optBNonactSelect(m.X, "server")
	return ok && optBNonactIdent(s.X, "d")
}

func optBNonactNilReturn(stmt ast.Stmt) bool {
	guard, ok := stmt.(*ast.IfStmt)
	if !ok || guard.Init != nil || guard.Else != nil || !optBNonactNilCondition(guard.Cond) || len(guard.Body.List) != 1 {
		return false
	}
	_, ok = guard.Body.List[0].(*ast.ReturnStmt)
	return ok
}

func optBNonactImports(f *ast.File) map[string]string {
	out := map[string]string{}
	for _, spec := range f.Imports {
		path, _ := strconv.Unquote(spec.Path.Value)
		name := filepath.Base(path)
		if spec.Name != nil {
			name = spec.Name.Name
		}
		out[name] = path
	}
	return out
}

type optBNonactCensus struct {
	assignments, tenancyCalls, constructors int
	violations                              []string
}

func optBNonactAudit(src optBNonactSources) optBNonactCensus {
	var out optBNonactCensus
	for path, f := range src.files {
		imports := optBNonactImports(f)
		direct := map[*ast.SelectorExpr]bool{}
		ast.Inspect(f, func(n ast.Node) bool {
			if call, ok := n.(*ast.CallExpr); ok {
				if sel, ok := optBNonactSelect(call.Fun, "SetTenancy"); ok {
					direct[sel] = true
				}
			}
			return true
		})
		if imports["."] == "github.com/futureagi/agentcc-gateway/internal/mcp/tenancy" {
			out.violations = append(out.violations, "dot-import bypasses tenancy constructor census")
		}
		for _, decl := range f.Decls {
			name := ""
			if fn, ok := decl.(*ast.FuncDecl); ok {
				name = fn.Name.Name
			}
			bad := func(n ast.Node, why string) {
				out.violations = append(out.violations, fmt.Sprintf("%s:%d %s", path, src.set.Position(n.Pos()).Line, why))
			}
			var scan func(ast.Node, bool)
			scan = func(node ast.Node, guarded bool) {
				ast.Inspect(node, func(n ast.Node) bool {
					switch x := n.(type) {
					case *ast.BlockStmt:
						for _, stmt := range x.List {
							scan(stmt, guarded)
							if optBNonactNilReturn(stmt) {
								guarded = true
							}
						}
						return false
					case *ast.IfStmt:
						// A return inside one branch cannot guard a sibling branch.
						if x.Init != nil {
							scan(x.Init, guarded)
						}
						scan(x.Cond, guarded)
						scan(x.Body, guarded)
						if x.Else != nil {
							scan(x.Else, guarded)
						}
						return false
					case *ast.FuncLit:
						// A callback cannot inherit a caller's earlier nil check.
						scan(x.Body, false)
						return false
					case *ast.AssignStmt:
						if strings.HasPrefix(path, "internal/server/") {
							for _, lhs := range x.Lhs {
								if _, ok := optBNonactSelect(lhs, "mcp"); ok {
									out.assignments++
									if path != "internal/server/routes_mcp.go" || name != "registerMCPRoutes" {
										bad(x, "MCP assignment outside construction chokepoint")
									}
								}
							}
						}
					case *ast.KeyValueExpr:
						if strings.HasPrefix(path, "internal/server/") && optBNonactIdent(x.Key, "mcp") {
							bad(x, "MCP field initialized outside assignment census")
						}
					case *ast.UnaryExpr:
						if strings.HasPrefix(path, "internal/server/") && x.Op == token.AND {
							if _, ok := optBNonactSelect(x.X, "mcp"); ok {
								bad(x, "MCP field address escapes assignment census")
							}
						}
					case *ast.SelectorExpr:
						if x.Sel.Name == "SetTenancy" {
							if !direct[x] {
								bad(x, "SetTenancy method value escapes guard census")
							}
							out.tenancyCalls++ // Includes method-value escapes, not only direct calls.
							if path != "cmd/agentcc/wiring_mcp_tenancy.go" || name != "initMCPTenancy" || !guarded {
								bad(x, "SetTenancy not behind the MCP nil return")
							}
						}
					case *ast.CallExpr:
						if sel, ok := optBNonactSelect(x.Fun, "New"); ok {
							if id, ok := sel.X.(*ast.Ident); ok && imports[id.Name] == "github.com/futureagi/agentcc-gateway/internal/mcp/tenancy" {
								out.constructors++
								if path != "cmd/agentcc/wiring_mcp_tenancy.go" || name != "initMCPTenancy" || !guarded {
									bad(x, "tenancy manager constructed outside nil guard")
								}
							}
						}
					}
					return true
				})
			}
			scan(decl, false)
		}
	}
	return out
}

func TestOptBHostedMCPNonActivationStaticCensus(t *testing.T) {
	got := optBNonactAudit(optBNonactSourceAST(t))
	for _, violation := range got.violations {
		t.Error(violation)
	}
	if got.assignments != 1 || got.tenancyCalls != 1 || got.constructors != 1 {
		t.Fatal("MCP construction/tenancy site count changed")
	}
}

func TestOptBHostedMCPNonActivationStaticCensusSelfCheck(t *testing.T) {
	for _, tc := range []struct {
		name, path, source string
		reject             bool
	}{
		{"extra-assignment", "internal/server/extra.go", `package server; func extra(s *Server) { s.mcp = nil }`, true},
		{"branch-escape", "cmd/agentcc/wiring_mcp_tenancy.go", `package main; func initMCPTenancy(d *wiringDeps) { if ready { if d.server.MCP()==nil { return } } else { d.server.MCP().SetTenancy(nil) } }`, true},
		{"method-value-escape", "cmd/agentcc/wiring_mcp_tenancy.go", `package main; func initMCPTenancy(d *wiringDeps) { if d.server.MCP()==nil { return }; later := d.server.MCP().SetTenancy; later(nil) }`, true},
		{"composite-assignment", "internal/server/extra.go", `package server; var extra = Server{mcp:nil}`, true},
		{"unguarded-tenancy", "cmd/agentcc/wiring_mcp_tenancy.go", `package main; func initMCPTenancy(d *wiringDeps) { d.server.MCP().SetTenancy(nil) }`, true},
		{"and-is-not-a-guard", "cmd/agentcc/wiring_mcp_tenancy.go", `package main; func initMCPTenancy(d *wiringDeps) { if ready && d.server.MCP()==nil { return }; d.server.MCP().SetTenancy(nil) }`, true},
		{"late-guard", "cmd/agentcc/wiring_mcp_tenancy.go", `package main; func initMCPTenancy(d *wiringDeps) { d.server.MCP().SetTenancy(nil); if d.server.MCP()==nil { return } }`, true},
		{"callback-guard", "cmd/agentcc/wiring_mcp_tenancy.go", `package main; func initMCPTenancy(d *wiringDeps) { if d.server.MCP()==nil { return }; later(func(){ d.server.MCP().SetTenancy(nil) }) }`, true},
		{"guarded-tenancy", "cmd/agentcc/wiring_mcp_tenancy.go", `package main; func initMCPTenancy(d *wiringDeps) { later(func(){ if d.server==nil || d.server.MCP()==nil { return }; d.server.MCP().SetTenancy(nil) }) }`, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			set := token.NewFileSet()
			f, err := parser.ParseFile(set, tc.path, tc.source, parser.SkipObjectResolution)
			if err != nil {
				t.Fatal("synthetic AST fixture did not parse")
			}
			got := optBNonactAudit(optBNonactSources{set, map[string]*ast.File{tc.path: f}})
			if (len(got.violations) != 0) != tc.reject {
				t.Fatal("AST census failed its synthetic mutation self-check")
			}
		})
	}
}

// There is no signal reload path. Pin the only gateway signal subscription to
// shutdown signals, instead of sending SIGHUP to (and killing) the test runner.
func TestOptBHostedMCPNonActivationOSSignalAbsent(t *testing.T) {
	src := optBNonactSourceAST(t)
	count := 0
	for path, f := range src.files {
		if !optBNonactGatewaySource(path, f) {
			continue
		}
		imports := optBNonactImports(f)
		ast.Inspect(f, func(n ast.Node) bool {
			call, ok := n.(*ast.CallExpr)
			if !ok {
				return true
			}
			sel, ok := call.Fun.(*ast.SelectorExpr)
			if !ok {
				return true
			}
			id, ok := sel.X.(*ast.Ident)
			if !ok || imports[id.Name] != "os/signal" || (sel.Sel.Name != "Notify" && sel.Sel.Name != "NotifyContext") {
				return true
			}
			count++
			if path != "cmd/agentcc/main.go" || sel.Sel.Name != "Notify" || len(call.Args) != 3 {
				t.Error("signal subscription census changed")
				return true
			}
			for i, want := range []string{"SIGTERM", "SIGINT"} {
				sig, ok := optBNonactSelect(call.Args[i+1], want)
				if !ok {
					t.Error("non-shutdown signal subscribed")
					continue
				}
				pkg, ok := sig.X.(*ast.Ident)
				if !ok || imports[pkg.Name] != "syscall" {
					t.Error("shutdown signal source changed")
				}
			}
			return true
		})
	}
	if count != 1 {
		t.Fatal("gateway signal subscription count changed")
	}
}

func optBNonactGatewaySource(path string, f *ast.File) bool {
	return strings.HasPrefix(path, "cmd/agentcc/") || (strings.HasPrefix(path, "internal/") && f.Name.Name != "main")
}

// No file watcher reload exists. Pin both config.Load consumers and the sole
// internal file loader call; adding a watcher/poller through either fails here.
func TestOptBHostedMCPNonActivationFileWatchAbsent(t *testing.T) {
	src := optBNonactSourceAST(t)
	loads, fileLoads := map[string]int{}, 0
	for path, f := range src.files {
		if !optBNonactGatewaySource(path, f) {
			continue
		}
		imports := optBNonactImports(f)
		for alias, imp := range imports {
			if strings.Contains(imp, "fsnotify") || imp == "github.com/rjeczalik/notify" {
				t.Error("file watcher dependency added")
			}
			if alias == "." && imp == "github.com/futureagi/agentcc-gateway/internal/config" {
				t.Error("dot-import bypasses config loader census")
			}
		}
		for _, decl := range f.Decls {
			name := ""
			if fn, ok := decl.(*ast.FuncDecl); ok {
				name = fn.Name.Name
			}
			ast.Inspect(decl, func(n ast.Node) bool {
				if sel, ok := n.(*ast.SelectorExpr); ok && sel.Sel.Name == "Load" {
					if id, ok := sel.X.(*ast.Ident); ok && imports[id.Name] == "github.com/futureagi/agentcc-gateway/internal/config" {
						loads[path+"#"+name]++
					}
				}
				if call, ok := n.(*ast.CallExpr); ok && strings.HasPrefix(path, "internal/config/") && optBNonactIdent(call.Fun, "loadFromFile") {
					fileLoads++
					if path != "internal/config/config.go" || name != "Load" {
						t.Error("config file loader has a new caller")
					}
				}
				return true
			})
		}
	}
	if len(loads) != 2 || loads["cmd/agentcc/main.go#main"] != 1 || loads["internal/server/server.go#reloadHandler"] != 1 || fileLoads != 1 {
		t.Fatal("config load/reload consumer census changed")
	}
}
