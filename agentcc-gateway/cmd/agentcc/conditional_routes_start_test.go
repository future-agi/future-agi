package main

import (
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

const startValidRoute = `    - name: "plain"
      priority: 10
      condition:
        field: "model"
        op: "$eq"
        value: "gpt-4o"
      action:
        provider: "openai"
`

func loadStartConfig(t *testing.T, routes string) *config.Config {
	t.Helper()
	path := filepath.Join(t.TempDir(), "config.yaml")
	if err := os.WriteFile(path, []byte("routing:\n  conditional_routes:\n"+startValidRoute+routes), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg, err := config.Load(path)
	if err != nil {
		t.Fatalf("config.Load: %v", err)
	}
	return cfg
}

// The start compiles the config file's conditional routes with the router's
// own parser. A route list the router refuses stops the start, naming the
// route's position and the condition's path, where the gateway used to start
// without conditional routes.
func TestStartRefusesARouteListTheRouterRefuses(t *testing.T) {
	for _, tc := range []struct{ name, route, want string }{
		{"a leaf with no field", `    - name: "no-field"
      condition:
        $or:
          - field: "model"
            op: "$eq"
            value: "gpt-4o"
          - op: "$eq"
            value: "enterprise"
      action:
        provider: "openai"
`, `routing.conditional_routes[1] "no-field": $or[1]: `},
		{"a $regex that does not compile", `    - name: "bad-regex"
      condition:
        field: "model"
        op: "$regex"
        value: "^gpt-("
      action:
        provider: "openai"
`, `routing.conditional_routes[1] "bad-regex": `},
		{"an empty action.provider", `    - name: "no-provider"
      condition:
        field: "model"
        op: "$eq"
        value: "gpt-4o"
      action:
        provider: ""
`, `routing.conditional_routes[1] "no-provider": action.provider`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			err := checkConditionalRoutes(loadStartConfig(t, tc.route))
			if err == nil {
				t.Fatal("the start accepted a route list the router refuses")
			}
			if !strings.Contains(err.Error(), tc.want) {
				t.Errorf("error %q should contain %q", err, tc.want)
			}
		})
	}
	t.Run("a list the router compiles starts", func(t *testing.T) {
		if err := checkConditionalRoutes(loadStartConfig(t, "")); err != nil {
			t.Fatalf("checkConditionalRoutes: %v", err)
		}
	})
}

// main runs the check right after config.Load and before -check-config
// returns, so an operator's -check-config refuses the same files the start
// does.
func TestMainChecksConditionalRoutesBeforeCheckConfigReturns(t *testing.T) {
	file, err := parser.ParseFile(token.NewFileSet(), "main.go", nil, 0)
	if err != nil {
		t.Fatal(err)
	}
	var mainFn *ast.FuncDecl
	for _, d := range file.Decls {
		if fn, ok := d.(*ast.FuncDecl); ok && fn.Name.Name == "main" && fn.Recv == nil {
			mainFn = fn
		}
	}
	if mainFn == nil {
		t.Fatal("main.go has no func main")
	}
	var load, check, checkConfig, exit token.Pos
	assigned := false
	ast.Inspect(mainFn.Body, func(n ast.Node) bool {
		switch n := n.(type) {
		case *ast.AssignStmt:
			if len(n.Lhs) == 1 && len(n.Rhs) == 1 {
				lhs, lok := n.Lhs[0].(*ast.Ident)
				call, cok := n.Rhs[0].(*ast.CallExpr)
				// "err =", never "err :=": a shadowing err inside an if
				// would leave Load's own exit check reading a different err.
				if lok && cok && lhs.Name == "err" && n.Tok == token.ASSIGN {
					if id, ok := call.Fun.(*ast.Ident); ok && id.Name == "checkConditionalRoutes" {
						assigned = true
					}
				}
			}
		case *ast.CallExpr:
			if sel, ok := n.Fun.(*ast.SelectorExpr); ok && sel.Sel.Name == "Load" && load == 0 {
				if pkg, ok := sel.X.(*ast.Ident); ok && pkg.Name == "config" {
					load = n.Pos()
				}
			}
			if id, ok := n.Fun.(*ast.Ident); ok && id.Name == "checkConditionalRoutes" && check == 0 {
				check = n.Pos()
			}
		case *ast.IfStmt:
			if cond, ok := n.Cond.(*ast.BinaryExpr); ok && cond.Op == token.NEQ && exit == 0 && check != 0 {
				if id, ok := cond.X.(*ast.Ident); ok && id.Name == "err" && callsOSExit(n.Body) {
					exit = n.Pos()
				}
			}
			if star, ok := n.Cond.(*ast.StarExpr); ok && checkConfig == 0 {
				if id, ok := star.X.(*ast.Ident); ok && id.Name == "checkConfig" {
					checkConfig = n.Pos()
				}
			}
		}
		return true
	})
	if load == 0 || check == 0 || checkConfig == 0 {
		t.Fatalf("main: config.Load at %v, checkConditionalRoutes at %v, if *checkConfig at %v; want all three", load, check, checkConfig)
	}
	if !(load < check && check < checkConfig) {
		t.Errorf("main must call checkConditionalRoutes after config.Load and before -check-config returns")
	}
	if !assigned || exit == 0 || !(check < exit && exit < checkConfig) {
		t.Errorf("main must assign checkConditionalRoutes' error to err and exit on it before -check-config returns (assigned %v, exit at %v)", assigned, exit)
	}
}

// callsOSExit reports whether body calls os.Exit.
func callsOSExit(body *ast.BlockStmt) bool {
	found := false
	ast.Inspect(body, func(n ast.Node) bool {
		if call, ok := n.(*ast.CallExpr); ok {
			if sel, ok := call.Fun.(*ast.SelectorExpr); ok && sel.Sel.Name == "Exit" {
				if pkg, ok := sel.X.(*ast.Ident); ok && pkg.Name == "os" {
					found = true
				}
			}
		}
		return !found
	})
	return found
}
