package mcp

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"go/ast"
	"go/build"
	"go/format"
	"go/importer"
	"go/parser"
	"go/token"
	"go/types"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"testing"
)

const r7Boundary = "internal/mcp/request_credential.go"

const r7Module = "github.com/futureagi/agentcc-gateway"

type r7DispatchSite struct {
	Count  int    `json:"count"`
	Reason string `json:"reason"`
}

// Scan every production Go file in the gateway MODULE, including root MCP
// hooks, cmd wiring and build-tagged files. Exceptions are exact function/site
// counts, never subtree exemptions. Each has an ownership reason in the census;
// credential-service token fetches are not MCP connector sends. This is an
// architecture regression, not a type/taint proof against adversarial Go code.
func r7ModuleSources(t *testing.T) map[string]string {
	t.Helper()
	root := filepath.Join("..", "..")
	mod, err := os.ReadFile(filepath.Join(root, "go.mod"))
	if err != nil || !strings.Contains(string(mod), "module github.com/futureagi/agentcc-gateway") {
		t.Fatal("not at the gateway module root")
	}
	sources := map[string]string{}
	err = filepath.WalkDir(root, func(path string, d fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if d.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		sources[filepath.ToSlash(rel)] = string(raw)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(sources) < 500 {
		t.Fatalf("incomplete whole-module production source census: %d", len(sources))
	}
	return sources
}

func r7AllowedDispatch(t *testing.T) map[string]r7DispatchSite {
	t.Helper()
	raw, err := os.ReadFile("testdata/k2_dispatch_census.json")
	if err != nil {
		t.Fatal(err)
	}
	var sites map[string]r7DispatchSite
	if err := json.Unmarshal(raw, &sites); err != nil {
		t.Fatal(err)
	}
	for key, site := range sites {
		if site.Count <= 0 || site.Reason == "" {
			t.Fatalf("unjustified dispatch exception %s", key)
		}
	}
	return sites
}

func r7Expr(n ast.Node) string {
	var b bytes.Buffer
	_ = format.Node(&b, token.NewFileSet(), n)
	return b.String()
}

func r7Authorization(n ast.Node) bool {
	lit, ok := n.(*ast.BasicLit)
	if !ok || lit.Kind != token.STRING {
		return false
	}
	value, err := strconv.Unquote(lit.Value)
	return err == nil && strings.EqualFold(value, "Authorization")
}

// Count every selector named Do, RoundTrip, NewClient, NewHTTPTransport or
// SetBasicAuth, whatever its receiver, with one exception: a Do call that
// go/types resolves positively to sync.Once's own method is a run-once
// callback, not a send (r7OnceDoCalls). Resolving types across build-tag
// combinations has blind spots, so the exception fails closed: a call in a
// file excluded by build constraints, or in a package that does not load or
// type-check, is still counted. Redis wrapper Do calls and every other
// ambiguous name stay counted; their exact sites are documented instead.
func r7DispatchKind(n ast.Node, httpNames map[string]bool) string {
	if index, ok := n.(*ast.IndexExpr); ok && r7Authorization(index.Index) {
		return "AuthorizationIndex"
	}
	call, ok := n.(*ast.CallExpr)
	if !ok {
		return ""
	}
	if id, ok := call.Fun.(*ast.Ident); ok && (id.Name == "NewClient" || id.Name == "NewHTTPTransport") {
		return id.Name
	}
	sel, ok := call.Fun.(*ast.SelectorExpr)
	if !ok {
		return ""
	}
	switch sel.Sel.Name {
	case "Do", "RoundTrip", "NewClient", "NewHTTPTransport", "SetBasicAuth":
		return sel.Sel.Name
	case "Set", "Add":
		if len(call.Args) > 0 && r7Authorization(call.Args[0]) {
			return "Authorization" + sel.Sel.Name
		}
	case "Get", "Head", "Post", "PostForm":
		if id, ok := sel.X.(*ast.Ident); ok && httpNames[id.Name] {
			return "http." + sel.Sel.Name
		}
	}
	return ""
}

// r7Types is shared by every census run in this test binary: each import is
// loaded once, and a package's resolved calls are kept by the digest of its
// files, so a source mutation rechecks only the package it edits.
var r7Types struct {
	sync.Mutex
	root     string
	fset     *token.FileSet
	source   types.ImporterFrom
	imports  map[string]r7Import
	packages map[[sha256.Size]byte]map[string]map[int]bool
}

type r7Import struct {
	pkg *types.Package
	err error
}

// r7Importer loads module packages from their build-matched files on disk and
// everything else through the source importer, once per import path.
type r7Importer struct{}

func (r7Importer) Import(importPath string) (*types.Package, error) {
	return r7Importer{}.ImportFrom(importPath, "", 0)
}

func (r7Importer) ImportFrom(importPath, dir string, mode types.ImportMode) (*types.Package, error) {
	if hit, ok := r7Types.imports[importPath]; ok {
		return hit.pkg, hit.err
	}
	r7Types.imports[importPath] = r7Import{err: fmt.Errorf("import cycle through %s", importPath)}
	var hit r7Import
	if rel, ok := r7ModuleDir(importPath); ok {
		hit.pkg, hit.err = r7ImportModule(importPath, rel)
	} else {
		hit.pkg, hit.err = r7Types.source.ImportFrom(importPath, dir, mode)
	}
	r7Types.imports[importPath] = hit
	return hit.pkg, hit.err
}

// r7ModuleDir maps an import path inside the gateway module to its slash
// directory relative to the module root.
func r7ModuleDir(importPath string) (string, bool) {
	if importPath == r7Module {
		return ".", true
	}
	return strings.CutPrefix(importPath, r7Module+"/")
}

// r7ImportModule loads a module package for import as the source importer
// does (its build-matched files on disk, without function bodies), without a
// go list run for every import edge.
func r7ImportModule(importPath, dir string) (*types.Package, error) {
	bp, err := build.Default.ImportDir(filepath.Join(r7Types.root, filepath.FromSlash(dir)), 0)
	if err != nil {
		return nil, err
	}
	files := make([]*ast.File, 0, len(bp.GoFiles))
	for _, name := range bp.GoFiles {
		f, err := parser.ParseFile(r7Types.fset, filepath.Join(bp.Dir, name), nil, 0)
		if err != nil {
			return nil, err
		}
		files = append(files, f)
	}
	var hard error
	conf := types.Config{
		Importer:         r7Importer{},
		IgnoreFuncBodies: true,
		// Without bodies, an import used only inside them is a soft error.
		Error: func(err error) {
			if soft, ok := err.(types.Error); hard == nil && (!ok || !soft.Soft) {
				hard = err
			}
		},
	}
	pkg, _ := conf.Check(importPath, r7Types.fset, files, nil)
	if hard != nil {
		return nil, hard
	}
	return pkg, nil
}

// r7SyncOnceMethod reports whether a resolved selection is the Do method
// declared on sync.Once, reached through a sync.Once, a *sync.Once or a
// struct that embeds either.
func r7SyncOnceMethod(selection *types.Selection) bool {
	if selection == nil || selection.Kind() != types.MethodVal {
		return false
	}
	method, ok := selection.Obj().(*types.Func)
	if !ok || method.Pkg() == nil || method.Pkg().Path() != "sync" || method.Name() != "Do" {
		return false
	}
	signature, ok := method.Type().(*types.Signature)
	if !ok || signature.Recv() == nil {
		return false
	}
	recv := signature.Recv().Type()
	if pointer, ok := recv.(*types.Pointer); ok {
		recv = pointer.Elem()
	}
	named, ok := recv.(*types.Named)
	return ok && named.Obj().Pkg() == method.Pkg() && named.Obj().Name() == "Once"
}

// r7OnceDoCalls type-checks each production package in sources and returns,
// per file, the offsets of the Do selectors that go/types resolves positively
// to sync.Once's method. Names never decide. Fail closed: a file excluded by
// build constraints, a directory whose files are not one package, and every
// file of a package with any load or type-check error give no offsets, so
// their calls are counted.
func r7OnceDoCalls(sources map[string]string) map[string]map[int]bool {
	r7Types.Lock()
	defer r7Types.Unlock()
	if r7Types.fset == nil {
		// The pure-Go net and os/user export the same types; with cgo on, the
		// source importer would need a C toolchain to load them.
		build.Default.CgoEnabled = false
		r7Types.root, _ = filepath.Abs(filepath.Join("..", ".."))
		r7Types.fset = token.NewFileSet()
		r7Types.source = importer.ForCompiler(r7Types.fset, "source", nil).(types.ImporterFrom)
		r7Types.imports = map[string]r7Import{}
		r7Types.packages = map[[sha256.Size]byte]map[string]map[int]bool{}
	}
	ctxt := build.Default
	ctxt.OpenFile = func(name string) (io.ReadCloser, error) {
		raw, ok := sources[filepath.ToSlash(name)]
		if !ok {
			return nil, fs.ErrNotExist
		}
		return io.NopCloser(strings.NewReader(raw)), nil
	}
	packages := map[string][]string{}
	for file := range sources {
		dir, name := ".", file
		if i := strings.LastIndex(file, "/"); i >= 0 {
			dir, name = file[:i], file[i+1:]
		}
		if match, err := ctxt.MatchFile(dir, name); match && err == nil {
			packages[dir] = append(packages[dir], file)
		}
	}
	once := map[string]map[int]bool{}
	for dir, files := range packages {
		sort.Strings(files)
		digest := sha256.New()
		for _, file := range files {
			fmt.Fprintf(digest, "%s\x00%d\x00%s", file, len(sources[file]), sources[file])
		}
		var key [sha256.Size]byte
		copy(key[:], digest.Sum(nil))
		found, ok := r7Types.packages[key]
		if !ok {
			found = r7CheckOnce(dir, files, sources)
			r7Types.packages[key] = found
		}
		for file, offsets := range found {
			once[file] = offsets
		}
	}
	return once
}

// r7CheckOnce type-checks one directory's build-matched files, with function
// bodies, as one package. It returns nil when anything fails to parse, load or
// type-check.
func r7CheckOnce(dir string, files []string, sources map[string]string) map[string]map[int]bool {
	parsed := make([]*ast.File, 0, len(files))
	for _, file := range files {
		f, err := parser.ParseFile(r7Types.fset, filepath.Join(r7Types.root, filepath.FromSlash(file)), sources[file], 0)
		if err != nil || (len(parsed) > 0 && f.Name.Name != parsed[0].Name.Name) {
			return nil
		}
		parsed = append(parsed, f)
	}
	importPath := r7Module
	if dir != "." {
		importPath += "/" + dir
	}
	info := &types.Info{Selections: map[*ast.SelectorExpr]*types.Selection{}}
	conf := types.Config{Importer: r7Importer{}}
	if _, err := conf.Check(importPath, r7Types.fset, parsed, info); err != nil {
		return nil
	}
	found := map[string]map[int]bool{}
	for i, f := range parsed {
		ast.Inspect(f, func(n ast.Node) bool {
			sel, ok := n.(*ast.SelectorExpr)
			if ok && sel.Sel.Name == "Do" && r7SyncOnceMethod(info.Selections[sel]) {
				if found[files[i]] == nil {
					found[files[i]] = map[int]bool{}
				}
				found[files[i]][r7Types.fset.Position(sel.Sel.Pos()).Offset] = true
			}
			return true
		})
	}
	return found
}

// r7OnceCall reports whether a Do call is one r7OnceDoCalls resolved to
// sync.Once. Every other dispatch kind and every unresolved Do is counted.
func r7OnceCall(n ast.Node, kind string, fset *token.FileSet, offsets map[int]bool) bool {
	call, ok := n.(*ast.CallExpr)
	if !ok || kind != "Do" {
		return false
	}
	sel, ok := call.Fun.(*ast.SelectorExpr)
	return ok && offsets[fset.Position(sel.Sel.Pos()).Offset]
}

func r7CustodyProblems(sources map[string]string, allowed map[string]r7DispatchSite) []string {
	var problems []string
	bad := func(format string, args ...any) { problems = append(problems, fmt.Sprintf(format, args...)) }
	seen := map[string]int{}
	installer, placed, guard, transfer := 0, 0, 0, 0
	constructors := map[string]int{}
	definitions := map[string]bool{}
	once := r7OnceDoCalls(sources)
	for path, raw := range sources {
		fset := token.NewFileSet()
		f, err := parser.ParseFile(fset, path, raw, 0)
		if err != nil {
			bad("parse %s: %v", path, err)
			continue
		}
		httpNames := map[string]bool{}
		for _, imp := range f.Imports {
			importPath, _ := strconv.Unquote(imp.Path.Value)
			if importPath == "net/http" {
				name := "http"
				if imp.Name != nil {
					name = imp.Name.Name
				}
				httpNames[name] = true
				if name == "." {
					bad("dot net/http import bypasses explicit dispatch census: %s", path)
				}
			}
		}
		ast.Inspect(f, func(n ast.Node) bool {
			if id, ok := n.(*ast.Ident); ok && (id.Name == "requestCredential" || id.Name == "bearerHeaders") && path != r7Boundary && path != "internal/mcp/transport.go" {
				bad("credential payload named outside boundary: %s", path)
			}
			if sel, ok := n.(*ast.SelectorExpr); ok && (sel.Sel.Name == "requestCredential" || sel.Sel.Name == "bearerHeaders") && path != r7Boundary {
				bad("credential payload accessed outside boundary: %s", path)
			}
			if path == r7Boundary {
				if typ, ok := n.(*ast.TypeSpec); ok && typ.Name.Name == "requestCredential" {
					_, definitions["requestCredential"] = typ.Type.(*ast.StructType)
				}
			}
			return true
		})
		for _, decl := range f.Decls {
			owner, siteOwner := "<package>", "<package>"
			if fn, ok := decl.(*ast.FuncDecl); ok {
				owner = fn.Name.Name
				siteOwner = owner
				if fn.Recv != nil {
					siteOwner = r7Expr(fn.Recv.List[0].Type) + "." + owner
				}
				if path == r7Boundary && fn.Body != nil {
					if fn.Recv == nil {
						definitions[owner] = true
					} else if r7Expr(fn.Recv.List[0].Type) == "credentialTransport" && owner == "RoundTrip" {
						definitions["credentialTransport.RoundTrip"] = true
					}
				}
			}
			ast.Inspect(decl, func(n ast.Node) bool {
				if kind := r7DispatchKind(n, httpNames); kind != "" && !r7OnceCall(n, kind, fset, once[path]) {
					seen[path+"|"+siteOwner+"|"+kind]++
				}
				call, ok := n.(*ast.CallExpr)
				if !ok {
					return true
				}
				fun := r7Expr(call.Fun)
				switch fun {
				case "installRequestCredential":
					installer++
					if path == "internal/mcp/transport.go" && owner == "upstreamHTTPClient" && len(call.Args) == 2 && r7Expr(call.Args[0]) == "c" && r7Expr(call.Args[1]) == "auth" {
						placed++
					}
				case "mcp.GuardClientAuth":
					if path == "internal/mcp/tenancy/build_lifetime.go" && owner == "guardedAuth" && len(call.Args) == 2 && r7Expr(call.Args[0]) == "cfg" {
						guard++
					}
				case "guardedAuth":
					if path == "internal/mcp/tenancy/manager.go" && owner == "build" && len(call.Args) == 3 && r7Expr(call.Args[0]) == "cfg.Auth" && r7Expr(call.Args[1]) == "auth" && r7Expr(call.Args[2]) == "authorize" {
						transfer++
					}
				case "e.pool.m.build":
					if len(call.Args) != 5 || r7Expr(call.Args[4]) != "e.authorize" {
						bad("construction omitted entry-bound admission: %s", path)
					} else {
						constructors[path+"|"+owner]++
					}
				}
				return true
			})
		}
	}
	for key, count := range seen {
		if count != allowed[key].Count {
			bad("dispatch census %s: got %d want %d", key, count, allowed[key].Count)
		}
	}
	for key, expected := range allowed {
		if seen[key] == 0 {
			bad("dispatch census missing %s (want %d)", key, expected.Count)
		}
	}
	if installer != 1 || placed != 1 {
		bad("installer CALL: got %d total, %d in shared HTTP/SSE builder; want one of each", installer, placed)
	}
	if guard != 1 || transfer != 1 {
		bad("custody CALLS: guard=%d transfer=%d; want one of each", guard, transfer)
	}
	for _, key := range []string{"internal/mcp/tenancy/entry.go|connect", "internal/mcp/tenancy/user_client.go|userClientFor"} {
		if constructors[key] != 1 {
			bad("entry-bound construction CALL missing: %s", key)
		}
	}
	for _, name := range []string{"requestCredential", "GuardClientAuth", "PinnedClientAuth", "credentialTransport.RoundTrip"} {
		if !definitions[name] {
			bad("custody definition missing: %s", name)
		}
	}
	sort.Strings(problems)
	return problems
}

func TestK2R6CredentialCustody(t *testing.T) {
	for _, problem := range r7CustodyProblems(r7ModuleSources(t), r7AllowedDispatch(t)) {
		t.Error(problem)
	}
}
