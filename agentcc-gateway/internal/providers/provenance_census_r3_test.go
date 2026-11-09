package providers

import (
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"sort"
	"strings"
	"testing"
)

// The provenance state is package-private. Census every non-test source in the
// package, including new files: only the org-cache constructor may grant it;
// the registry's three factory calls are platform construction, never grants.
func r3ProvenanceCensus(sources map[string]string) []string {
	var failures []string
	calls := map[string][]string{}
	for path, source := range sources {
		fset := token.NewFileSet()
		file, err := parser.ParseFile(fset, path, source, 0)
		if err != nil {
			failures = append(failures, err.Error())
			continue
		}
		for _, decl := range file.Decls {
			fn, ok := decl.(*ast.FuncDecl)
			if !ok {
				continue
			}
			name := fn.Name.Name
			ast.Inspect(fn.Body, func(node ast.Node) bool {
				if id, ok := node.(*ast.Ident); ok && id.Name == "orgCredentialProvenance" && name != "markOrgInstance" && name != "unmarkOrgInstance" && name != "IsOrgInstance" {
					failures = append(failures, fmt.Sprintf("%s:%s: unauthorized provenance access", path, name))
				}
				if call, ok := node.(*ast.CallExpr); ok {
					if id, ok := call.Fun.(*ast.Ident); ok {
						switch id.Name {
						case "createProvider", "orgInstanceID", "markOrgInstance", "unmarkOrgInstance":
							calls[id.Name] = append(calls[id.Name], name)
						}
					}
				}
				return true
			})
			if name == "GetOrCreateWithTenantConfig" || name == "Evict" || name == "EvictAll" {
				// Every mutator holds the same c.mu from Lock through deferred
				// Unlock. Validate the AST order, not comments or line numbers.
				locked, deferred := false, false
				for _, statement := range fn.Body.List {
					if expr, ok := statement.(*ast.ExprStmt); ok && r3CacheLockCall(expr.X, "Lock") {
						locked = true
					}
					if d, ok := statement.(*ast.DeferStmt); ok && r3CacheLockCall(d.Call, "Unlock") {
						deferred = locked
					}
					ast.Inspect(statement, func(node ast.Node) bool {
						call, ok := node.(*ast.CallExpr)
						if !ok {
							return true
						}
						id, ok := call.Fun.(*ast.Ident)
						if ok && (id.Name == "markOrgInstance" || id.Name == "unmarkOrgInstance") && (!locked || !deferred) {
							failures = append(failures, name+": provenance write outside cache lock")
						}
						return true
					})
				}
				if name == "GetOrCreateWithTenantConfig" {
					mark, publish := token.NoPos, token.NoPos
					ast.Inspect(fn.Body, func(node ast.Node) bool {
						if call, ok := node.(*ast.CallExpr); ok {
							if id, ok := call.Fun.(*ast.Ident); ok && id.Name == "markOrgInstance" {
								mark = call.Pos()
							}
						}
						if assignment, ok := node.(*ast.AssignStmt); ok {
							for _, lhs := range assignment.Lhs {
								if index, ok := lhs.(*ast.IndexExpr); ok {
									if sel, ok := index.X.(*ast.SelectorExpr); ok && sel.Sel.Name == "providers" {
										publish = assignment.Pos()
									}
								}
							}
						}
						return true
					})
					if mark == token.NoPos || publish == token.NoPos || mark >= publish {
						failures = append(failures, "provenance must precede map publication")
					}
				}
			}
		}
	}
	for callee, expected := range map[string][]string{
		"createProvider":    {"GetOrCreateWithTenantConfig", "NewRegistry", "RotateProviderKey", "RotationCandidate"},
		"orgInstanceID":     {"GetOrCreateWithTenantConfig"},
		"markOrgInstance":   {"GetOrCreateWithTenantConfig"},
		"unmarkOrgInstance": {"Evict", "EvictAll"},
	} {
		sort.Strings(calls[callee])
		sort.Strings(expected)
		if strings.Join(calls[callee], ",") != strings.Join(expected, ",") {
			failures = append(failures, fmt.Sprintf("%s callers = %v, want %v", callee, calls[callee], expected))
		}
	}
	return failures
}

func r3CacheLockCall(expr ast.Expr, method string) bool {
	call, ok := expr.(*ast.CallExpr)
	if !ok {
		return false
	}
	sel, ok := call.Fun.(*ast.SelectorExpr)
	if !ok || sel.Sel.Name != method {
		return false
	}
	mu, ok := sel.X.(*ast.SelectorExpr)
	if !ok || mu.Sel.Name != "mu" {
		return false
	}
	recv, ok := mu.X.(*ast.Ident)
	return ok && recv.Name == "c"
}

func r3ProvenanceSources(t *testing.T) map[string]string {
	t.Helper()
	entries, err := os.ReadDir(".")
	if err != nil {
		t.Fatal(err)
	}
	sources := map[string]string{}
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".go") || strings.HasSuffix(entry.Name(), "_test.go") {
			continue
		}
		data, err := os.ReadFile(entry.Name())
		if err != nil {
			t.Fatal(err)
		}
		sources[entry.Name()] = string(data)
	}
	return sources
}

func TestOptBR3OrgConstructorCensus(t *testing.T) {
	if failures := r3ProvenanceCensus(r3ProvenanceSources(t)); len(failures) > 0 {
		t.Fatal(strings.Join(failures, "\n"))
	}
}

func TestOptBR3OrgConstructorCensusMutations(t *testing.T) {
	for _, mutation := range []struct{ name, old, replacement string }{
		{"registry_grant", "", "func rogue(p Provider) { markOrgInstance(p, \"acme\") }"},
		{"direct_record_write", "", "func rogue(p Provider) { orgCredentialProvenance.owner[p] = \"acme\" }"},
		{"alternate_org_factory", "", "func rogue() { createProvider(orgInstanceID(\"edge\", \"acme\"), cfg) }"},
		{"mark_after_publish", "if !markOrgInstance(p, orgID) {", "c.providers[key] = p\nif !markOrgInstance(p, orgID) {"},
		{"missing_eviction_revoke", "unmarkOrgInstance(p)", "identityKeyed(p)"},
		{"missing_cache_lock", "c.mu.Lock()", "c.mu.RLock()"},
	} {
		t.Run(mutation.name, func(t *testing.T) {
			sources := r3ProvenanceSources(t)
			if mutation.old == "" {
				sources["rogue.go"] = "package providers\n" + mutation.replacement
			} else {
				if !strings.Contains(sources["override.go"], mutation.old) {
					t.Fatal("mutation anchor missing")
				}
				sources["override.go"] = strings.ReplaceAll(sources["override.go"], mutation.old, mutation.replacement)
				if mutation.name == "mark_after_publish" {
					// Remove the original later publication, leaving only the injected early one.
					anchor := "\n\tc.providers[key] = p\n\tslog.Info"
					if !strings.Contains(sources["override.go"], anchor) {
						t.Fatal("publication anchor missing")
					}
					sources["override.go"] = strings.Replace(sources["override.go"], anchor, "\n\tslog.Info", 1)
				}
			}
			if failures := r3ProvenanceCensus(sources); len(failures) == 0 {
				t.Fatal("census accepted forbidden provenance mutation")
			}
		})
	}
}
