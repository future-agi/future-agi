package main

import (
	"go/ast"
	"go/parser"
	"go/token"
	"testing"
)

// Wire the real fail-closed meter on every bootstrap path, including Helm's
// external-TLS-Redis path where RedisAddr is empty. No service is started.
func TestBootstrapAlwaysConstructsBudgetAdmission(t *testing.T) {
	file, err := parser.ParseFile(token.NewFileSet(), "main.go", nil, 0)
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, decl := range file.Decls {
		fn, ok := decl.(*ast.FuncDecl)
		if !ok || fn.Name.Name != "main" {
			continue
		}
		for _, stmt := range fn.Body.List {
			assignment, ok := stmt.(*ast.AssignStmt)
			if !ok {
				continue
			}
			for _, rhs := range assignment.Rhs {
				call, ok := rhs.(*ast.CallExpr)
				if !ok {
					continue
				}
				selector, ok := call.Fun.(*ast.SelectorExpr)
				if !ok || selector.Sel.Name != "NewMetering" {
					continue
				}
				pkg, ok := selector.X.(*ast.Ident)
				found = found || (ok && pkg.Name == "auth")
			}
		}
		ast.Inspect(fn, func(node ast.Node) bool {
			if selector, ok := node.(*ast.SelectorExpr); ok && selector.Sel.Name == "NoopMetering" {
				t.Error("bootstrap cannot infer a nonbillable mode from missing Redis")
			}
			return true
		})
	}
	if !found {
		t.Fatal("budget admission constructor must run outside the optional Redis block")
	}
}
