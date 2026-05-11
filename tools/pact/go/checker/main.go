// pact Go checker — static analysis of Go source files.
//
// Reads one or more .go files (or a directory), walks the AST, and
// emits a JSON array of violations to stdout. Each violation matches
// the pact FailureEvidence schema so the Python wrapper can convert it
// to a Violation object using the same deduplication logic as checker.py.
//
// Failure modes:
//   ignored_error         — x, _ := f() where _ discards an error return
//   bare_recover          — defer func() { recover() }() swallows all panics
//   unchecked_assertion   — v := x.(T) without the two-result ", ok" form
//   goroutine_no_sync     — go func(){...}() with no WaitGroup/channel/ctx
//
// Usage:
//   go run . --file path/to/file.go
//   go run . --dir  path/to/package/
//   go run . --file a.go --file b.go
//
// Build:
//   cd tools/pact/go/checker && go build -o pact-go .
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"strings"
)

// Violation mirrors pact's FailureEvidence schema.
type Violation struct {
	Mode    string `json:"mode"`
	File    string `json:"file"`
	Line    int    `json:"line"`
	Call    string `json:"call"`
	Message string `json:"message"`
}

// ----------------------------------------------------------------------------
// Failure mode: ignored_error
// Detects: x, _ := someFunc() where the blank identifier discards an error.
// Heuristic: the last return value of the called function ends in "error".
// Since we can't resolve types without full type-checking, we use naming
// conventions: functions named Err*, *Error, Open, Read, Write, Close,
// Connect, Send, Recv, Parse, Decode, Encode are assumed to return error.
// ----------------------------------------------------------------------------

var errorReturnFuncs = map[string]bool{
	"Open": true, "Create": true, "Read": true, "Write": true,
	"Close": true, "Connect": true, "Dial": true, "Listen": true,
	"Accept": true, "Send": true, "Recv": true, "Parse": true,
	"Decode": true, "Encode": true, "Marshal": true, "Unmarshal": true,
	"Scan": true, "Query": true, "Exec": true, "Begin": true,
	"Commit": true, "Rollback": true, "Prepare": true,
	"MkdirAll": true, "Mkdir": true, "Remove": true, "Rename": true,
	"Stat": true, "ReadFile": true, "WriteFile": true, "ReadDir": true,
}

func callName(expr ast.Expr) string {
	switch e := expr.(type) {
	case *ast.Ident:
		return e.Name
	case *ast.SelectorExpr:
		return e.Sel.Name
	case *ast.CallExpr:
		return callName(e.Fun)
	}
	return ""
}

func likelyReturnsError(call *ast.CallExpr) bool {
	name := callName(call.Fun)
	if name == "" {
		return false
	}
	if errorReturnFuncs[name] {
		return true
	}
	// Names ending in common error-returning suffixes
	for _, suffix := range []string{"Err", "Error", "WithError"} {
		if strings.HasSuffix(name, suffix) {
			return true
		}
	}
	return false
}

func checkIgnoredError(fset *token.FileSet, f *ast.File, file string) []Violation {
	var vs []Violation
	ast.Inspect(f, func(n ast.Node) bool {
		assign, ok := n.(*ast.AssignStmt)
		if !ok || assign.Tok != token.DEFINE {
			return true
		}
		// Look for: x, _ := call()  (exactly 2 lhs, last is blank)
		if len(assign.Lhs) < 2 || len(assign.Rhs) != 1 {
			return true
		}
		blank, isIdent := assign.Lhs[len(assign.Lhs)-1].(*ast.Ident)
		if !isIdent || blank.Name != "_" {
			return true
		}
		call, isCall := assign.Rhs[0].(*ast.CallExpr)
		if !isCall || !likelyReturnsError(call) {
			return true
		}
		pos := fset.Position(assign.Pos())
		name := callName(call.Fun)
		vs = append(vs, Violation{
			Mode:    "go_ignored_error",
			File:    file,
			Line:    pos.Line,
			Call:    name,
			Message: fmt.Sprintf("error return from %s() discarded with '_' — failures silently ignored", name),
		})
		return true
	})
	return vs
}

// ----------------------------------------------------------------------------
// Failure mode: bare_recover
// Detects: defer func() { recover() }() — swallows all panics silently.
// A recover() that doesn't log, wrap, or re-panic is a footgun.
// ----------------------------------------------------------------------------

func checkBareRecover(fset *token.FileSet, f *ast.File, file string) []Violation {
	var vs []Violation
	ast.Inspect(f, func(n ast.Node) bool {
		deferStmt, ok := n.(*ast.DeferStmt)
		if !ok {
			return true
		}
		call, ok := deferStmt.Call.Fun.(*ast.FuncLit)
		if !ok {
			return true
		}
		body := call.Body.List
		if len(body) != 1 {
			return true
		}
		exprStmt, ok := body[0].(*ast.ExprStmt)
		if !ok {
			return true
		}
		innerCall, ok := exprStmt.X.(*ast.CallExpr)
		if !ok {
			return true
		}
		ident, ok := innerCall.Fun.(*ast.Ident)
		if !ok || ident.Name != "recover" {
			return true
		}
		pos := fset.Position(deferStmt.Pos())
		vs = append(vs, Violation{
			Mode:    "go_bare_recover",
			File:    file,
			Line:    pos.Line,
			Call:    "recover()",
			Message: "bare recover() swallows all panics silently — log, wrap, or re-panic instead",
		})
		return true
	})
	return vs
}

// ----------------------------------------------------------------------------
// Failure mode: unchecked_assertion
// Detects: v := x.(T) — panics if x is not of type T at runtime.
// Safe form: v, ok := x.(T); if !ok { ... }
// ----------------------------------------------------------------------------

func checkUncheckedAssertion(fset *token.FileSet, f *ast.File, file string) []Violation {
	var vs []Violation
	ast.Inspect(f, func(n ast.Node) bool {
		assign, ok := n.(*ast.AssignStmt)
		if !ok {
			return true
		}
		// Single-value assignment only — two-value is the safe form
		if len(assign.Lhs) != 1 || len(assign.Rhs) != 1 {
			return true
		}
		typeAssert, ok := assign.Rhs[0].(*ast.TypeAssertExpr)
		if !ok {
			return true
		}
		// x.(type) in type switches is fine
		if typeAssert.Type == nil {
			return true
		}
		pos := fset.Position(assign.Pos())
		typeName := ""
		if ident, ok := typeAssert.Type.(*ast.Ident); ok {
			typeName = ident.Name
		}
		vs = append(vs, Violation{
			Mode:    "go_unchecked_assertion",
			File:    file,
			Line:    pos.Line,
			Call:    fmt.Sprintf(".(%s)", typeName),
			Message: fmt.Sprintf("type assertion .(%s) without ok check — panics at runtime if type doesn't match; use v, ok := x.(%s)", typeName, typeName),
		})
		return true
	})
	return vs
}

// ----------------------------------------------------------------------------
// Failure mode: goroutine_no_sync
// Detects: go func() { ... }() where the literal captures no WaitGroup,
// channel, or context — likely a fire-and-forget goroutine with no lifecycle.
// Heuristic: body doesn't reference wg, ch, ctx, cancel, done, errCh.
// ----------------------------------------------------------------------------

// "chan" is intentionally absent: it's a keyword, never an ast.Ident name.
var syncHints = []string{"wg", "Wait", "Add", "Done", "ch", "ctx", "cancel", "done", "errCh", "group"}

func bodySource(fset *token.FileSet, body *ast.BlockStmt) string {
	// Quick heuristic: stringify idents referenced in the body
	var names []string
	ast.Inspect(body, func(n ast.Node) bool {
		if id, ok := n.(*ast.Ident); ok {
			names = append(names, id.Name)
		}
		return true
	})
	return strings.Join(names, " ")
}

func checkGoroutineNoSync(fset *token.FileSet, f *ast.File, file string) []Violation {
	var vs []Violation
	ast.Inspect(f, func(n ast.Node) bool {
		goStmt, ok := n.(*ast.GoStmt)
		if !ok {
			return true
		}
		lit, ok := goStmt.Call.Fun.(*ast.FuncLit)
		if !ok {
			return true
		}
		body := bodySource(fset, lit.Body)
		for _, hint := range syncHints {
			if strings.Contains(body, hint) {
				return true // looks like it has sync
			}
		}
		pos := fset.Position(goStmt.Pos())
		vs = append(vs, Violation{
			Mode:    "go_goroutine_no_sync",
			File:    file,
			Line:    pos.Line,
			Call:    "go func()",
			Message: "goroutine launched with no apparent sync mechanism (WaitGroup, channel, or context) — possible leak or undetected failure",
		})
		return true
	})
	return vs
}

// ----------------------------------------------------------------------------
// File analysis
// ----------------------------------------------------------------------------

func analyzeFile(path string) ([]Violation, error) {
	fset := token.NewFileSet()
	f, err := parser.ParseFile(fset, path, nil, 0)
	if err != nil {
		return nil, err
	}
	var all []Violation
	all = append(all, checkIgnoredError(fset, f, path)...)
	all = append(all, checkBareRecover(fset, f, path)...)
	all = append(all, checkUncheckedAssertion(fset, f, path)...)
	all = append(all, checkGoroutineNoSync(fset, f, path)...)
	return all, nil
}

func analyzeDir(dir string) ([]Violation, error) {
	var all []Violation
	err := filepath.Walk(dir, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return nil
		}
		if info.IsDir() {
			name := info.Name()
			if name == "vendor" || name == "testdata" || strings.HasPrefix(name, ".") {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		vs, err := analyzeFile(path)
		if err == nil {
			all = append(all, vs...)
		}
		return nil
	})
	return all, err
}

// ----------------------------------------------------------------------------
// Entry point
// ----------------------------------------------------------------------------

func main() {
	var files []string
	var dirs []string

	flag.Func("file", "Go source file to analyze (repeatable)", func(s string) error {
		files = append(files, s)
		return nil
	})
	flag.Func("dir", "Directory to analyze recursively (repeatable)", func(s string) error {
		dirs = append(dirs, s)
		return nil
	})
	flag.Parse()

	// Positional args treated as files or dirs
	for _, arg := range flag.Args() {
		info, err := os.Stat(arg)
		if err != nil {
			fmt.Fprintf(os.Stderr, "pact-go: cannot stat %s: %v\n", arg, err)
			continue
		}
		if info.IsDir() {
			dirs = append(dirs, arg)
		} else {
			files = append(files, arg)
		}
	}

	if len(files) == 0 && len(dirs) == 0 {
		fmt.Fprintln(os.Stderr, "usage: pact-go [--file F] [--dir D] [path...]")
		os.Exit(2)
	}

	var all []Violation
	for _, f := range files {
		vs, err := analyzeFile(f)
		if err != nil {
			fmt.Fprintf(os.Stderr, "pact-go: error parsing %s: %v\n", f, err)
			continue
		}
		all = append(all, vs...)
	}
	for _, d := range dirs {
		vs, err := analyzeDir(d)
		if err != nil {
			fmt.Fprintf(os.Stderr, "pact-go: error scanning %s: %v\n", d, err)
			continue
		}
		all = append(all, vs...)
	}

	if all == nil {
		all = []Violation{} // emit [] not null
	}
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	if err := enc.Encode(all); err != nil {
		fmt.Fprintf(os.Stderr, "pact-go: JSON encode error: %v\n", err)
		os.Exit(1)
	}
}
