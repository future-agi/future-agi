package server

import (
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
)

// The census (TestNoGatewayOwnedCancelReachesAProviderRecordSite) covers the
// cancellable contexts the request path's own packages create. A function in
// another package that creates one and hands it back, a context.Context
// together with its context.CancelFunc, would put a cancel the census never
// sees on the request path when one of those packages calls it. This test
// lists every such function in agentcc-gateway/internal outside the census
// packages that the census packages call; each must be shown not to reach a
// record site and listed in escapeCensus with why.

// escapeCensus is every function outside the census packages that returns a
// context with its cancel and that a census package calls, keyed
// "<package path under internal>.<function> called from <census file> <caller>",
// with why its cancel does not reach a record site. There is none today.
var escapeCensus = map[string]string{}

// cancelHelper is a function that returns a context.Context together with a
// context.CancelFunc (or CancelCauseFunc).
type cancelHelper struct {
	pkg    string // its package's path under internal, e.g. "a2a"
	name   string // its name; Type.Method for a method
	method bool
}

// returnsContextAndCancel says whether results hold a context.Context and a
// cancel function of the standard "context" package, by the file's names
// for it (contextNames).
func returnsContextAndCancel(results *ast.FieldList, names map[string]bool, dot bool) bool {
	if results == nil {
		return false
	}
	isContextType := func(e ast.Expr, want ...string) bool {
		match := func(n string) bool {
			for _, w := range want {
				if n == w {
					return true
				}
			}
			return false
		}
		switch x := e.(type) {
		case *ast.SelectorExpr:
			id, ok := x.X.(*ast.Ident)
			return ok && names[id.Name] && match(x.Sel.Name)
		case *ast.Ident:
			return dot && match(x.Name)
		}
		return false
	}
	var ctx, cancel bool
	for _, f := range results.List {
		ctx = ctx || isContextType(f.Type, "Context")
		cancel = cancel || isContextType(f.Type, "CancelFunc", "CancelCauseFunc")
	}
	return ctx && cancel
}

// cancelHelpers are the functions, methods and package-level function
// values outside the census packages, in agentcc-gateway/internal, that
// return a context with its cancel, by import path.
func cancelHelpers(t *testing.T, module string) map[string][]cancelHelper {
	t.Helper()
	census := map[string]bool{}
	for _, dir := range censusDirs {
		abs, err := filepath.Abs(dir)
		if err != nil {
			t.Fatal(err)
		}
		census[abs] = true
	}
	internal, err := filepath.Abs("..")
	if err != nil {
		t.Fatal(err)
	}
	helpers := map[string][]cancelHelper{}
	err = filepath.WalkDir(internal, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() {
			if d.Name() == "testdata" || census[path] {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		src, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		file, err := parser.ParseFile(token.NewFileSet(), path, src, 0)
		if err != nil {
			return err
		}
		names, dot := contextNames(file)
		if len(names) == 0 && !dot {
			return nil
		}
		rel, err := filepath.Rel(internal, filepath.Dir(path))
		if err != nil {
			return err
		}
		pkg := filepath.ToSlash(rel)
		importPath := module + "/internal/" + pkg
		for _, decl := range file.Decls {
			switch decl := decl.(type) {
			case *ast.FuncDecl:
				if returnsContextAndCancel(decl.Type.Results, names, dot) {
					helpers[importPath] = append(helpers[importPath], cancelHelper{pkg: pkg, name: funcName(decl), method: decl.Recv != nil})
				}
			case *ast.GenDecl:
				for _, spec := range decl.Specs {
					vs, ok := spec.(*ast.ValueSpec)
					if !ok {
						continue
					}
					for i, id := range vs.Names {
						var ft *ast.FuncType
						if f, ok := vs.Type.(*ast.FuncType); ok {
							ft = f
						} else if i < len(vs.Values) {
							if lit, ok := vs.Values[i].(*ast.FuncLit); ok {
								ft = lit.Type
							}
						}
						if ft != nil && returnsContextAndCancel(ft.Results, names, dot) {
							helpers[importPath] = append(helpers[importPath], cancelHelper{pkg: pkg, name: id.Name})
						}
					}
				}
			}
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return helpers
}

// gatewayModule is agentcc-gateway's module path, from its go.mod.
func gatewayModule(t *testing.T) string {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("..", "..", "go.mod"))
	if err != nil {
		t.Fatal(err)
	}
	for _, line := range strings.Split(string(b), "\n") {
		if rest, ok := strings.CutPrefix(strings.TrimSpace(line), "module "); ok {
			return strings.TrimSpace(rest)
		}
	}
	t.Fatal("go.mod names no module")
	return ""
}

// helperCalls are the census packages' calls of helpers, keyed as
// escapeCensus is. A function is matched by its package's import (under
// whatever name the file gives it) and name; a method, whose receiver's type
// this parse does not know, by its name alone, so a method of the same name
// on another type is listed too.
func helperCalls(t *testing.T, helpers map[string][]cancelHelper) map[string]bool {
	t.Helper()
	methods := map[string]cancelHelper{}
	for _, hs := range helpers {
		for _, h := range hs {
			if h.method {
				methods[h.name[strings.LastIndex(h.name, ".")+1:]] = h
			}
		}
	}
	calls := map[string]bool{}
	for _, dir := range censusDirs {
		files, err := filepath.Glob(filepath.Join(dir, "*.go"))
		if err != nil {
			t.Fatal(err)
		}
		pkg := filepath.Base(dir)
		if dir == "." {
			pkg = "server"
		}
		for _, path := range files {
			if strings.HasSuffix(path, "_test.go") {
				continue
			}
			file, err := parser.ParseFile(token.NewFileSet(), path, nil, 0)
			if err != nil {
				t.Fatal(err)
			}
			imports := map[string]string{} // local name -> import path
			for _, imp := range file.Imports {
				ip := strings.Trim(imp.Path.Value, `"`)
				local := ip[strings.LastIndex(ip, "/")+1:]
				if imp.Name != nil {
					local = imp.Name.Name
				}
				imports[local] = ip
			}
			name := pkg + "/" + filepath.Base(path)
			for _, decl := range file.Decls {
				scope := "<file scope>"
				if fn, ok := decl.(*ast.FuncDecl); ok {
					scope = funcName(fn)
				}
				ast.Inspect(decl, func(n ast.Node) bool {
					call, ok := n.(*ast.CallExpr)
					if !ok {
						return true
					}
					sel, ok := call.Fun.(*ast.SelectorExpr)
					if !ok {
						return true
					}
					if id, ok := sel.X.(*ast.Ident); ok {
						if ip, ok := imports[id.Name]; ok {
							for _, h := range helpers[ip] {
								if !h.method && h.name == sel.Sel.Name {
									calls[fmt.Sprintf("%s.%s called from %s %s", h.pkg, h.name, name, scope)] = true
								}
							}
							return true
						}
					}
					if h, ok := methods[sel.Sel.Name]; ok {
						calls[fmt.Sprintf("%s.%s called from %s %s", h.pkg, h.name, name, scope)] = true
					}
					return true
				})
			}
		}
	}
	return calls
}

func TestNoCancellableContextHelperOutsideTheCensus(t *testing.T) {
	helpers := cancelHelpers(t, gatewayModule(t))
	var all []string
	for _, hs := range helpers {
		for _, h := range hs {
			all = append(all, h.pkg+"."+h.name)
		}
	}
	sort.Strings(all)
	t.Logf("functions outside the census that return a context with its cancel: %v", all)

	calls := helperCalls(t, helpers)
	var added, gone []string
	for key := range calls {
		if _, ok := escapeCensus[key]; !ok {
			added = append(added, key)
		}
	}
	for key := range escapeCensus {
		if !calls[key] {
			gone = append(gone, key)
		}
	}
	sort.Strings(added)
	sort.Strings(gone)
	if len(added) > 0 {
		t.Errorf("the request path calls functions outside the census that hand back a context with its cancel: show each cancel never "+
			"becomes the context, or an ancestor of the context, a provider record site reads, then list the call in escapeCensus "+
			"with why:\n  %s", strings.Join(added, "\n  "))
	}
	if len(gone) > 0 {
		t.Errorf("escapeCensus entries the code no longer has; remove them:\n  %s", strings.Join(gone, "\n  "))
	}
}
