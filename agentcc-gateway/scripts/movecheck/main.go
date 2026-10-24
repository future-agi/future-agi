// Command movecheck proves that a refactor of one Go package directory only
// moved code. It parses every .go file (tests included) in --base and --head,
// prints each top-level function, method, type, var and const declaration
// with go/printer, hashes it, and compares the two multisets of
// (kind, receiver, name, sha256). Any edited, added or dropped declaration is
// printed as a "-" (only in base) or "+" (only in head) line, and the exit
// status is 1. An empty output and exit status 0 mean a pure move: code may
// change files and whitespace, but not tokens.
//
// Doc comments are not hashed, and neither is the text of a comment inside a
// body; a comment line still holds its place in the line layout go/printer
// keeps, so moved code keeps its comments. Import declarations are ignored,
// since each new file carries its own.
//
// Usage:
//
//	go run ./scripts/movecheck --base <dir> --head <dir>
//
// The directories are read flat: subdirectories are separate packages and are
// not visited.
package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"flag"
	"fmt"
	"go/ast"
	"go/parser"
	"go/printer"
	"go/token"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// decl identifies one top-level declaration by what it is and what it
// prints as.
type decl struct {
	kind string // func, type, var or const
	recv string // the receiver type of a method, else ""
	name string
	hash string // sha256 of the printed declaration, without its doc comment
}

func (d decl) String() string {
	name := d.name
	if d.recv != "" {
		name = "(" + d.recv + ")." + d.name
	}
	return d.kind + " " + name + " " + d.hash
}

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

func run(args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("movecheck", flag.ContinueOnError)
	fs.SetOutput(stderr)
	base := fs.String("base", "", "package directory before the move")
	head := fs.String("head", "", "package directory after the move")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	if *base == "" || *head == "" {
		fmt.Fprintln(stderr, "movecheck: --base and --head are required")
		return 2
	}
	diff, err := compare(*base, *head)
	if err != nil {
		fmt.Fprintln(stderr, "movecheck:", err)
		return 2
	}
	for _, line := range diff {
		fmt.Fprintln(stdout, line)
	}
	if len(diff) > 0 {
		return 1
	}
	return 0
}

// compare returns one line per declaration whose count differs between the
// two directories, sorted: "- <decl>" when base has more, "+ <decl>" when
// head has more.
func compare(baseDir, headDir string) ([]string, error) {
	base, err := collect(baseDir)
	if err != nil {
		return nil, err
	}
	head, err := collect(headDir)
	if err != nil {
		return nil, err
	}
	var diff []string
	for d, n := range base {
		for i := head[d]; i < n; i++ {
			diff = append(diff, "- "+d.String())
		}
	}
	for d, n := range head {
		for i := base[d]; i < n; i++ {
			diff = append(diff, "+ "+d.String())
		}
	}
	sort.Strings(diff)
	return diff, nil
}

// collect parses every .go file directly in dir and counts its top-level
// declarations.
func collect(dir string) (map[decl]int, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	fset := token.NewFileSet()
	out := map[decl]int{}
	files := 0
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".go") {
			continue
		}
		f, err := parser.ParseFile(fset, filepath.Join(dir, e.Name()), nil, parser.SkipObjectResolution)
		if err != nil {
			return nil, err
		}
		files++
		for _, d := range f.Decls {
			ds, err := describe(fset, d)
			if err != nil {
				return nil, fmt.Errorf("%s: %w", e.Name(), err)
			}
			for _, x := range ds {
				out[x]++
			}
		}
	}
	if files == 0 {
		return nil, errors.New(dir + ": no .go files")
	}
	return out, nil
}

// describe returns the declarations one top-level decl holds: one for a
// function or method, one per spec for a type, var or const group, none for
// an import.
func describe(fset *token.FileSet, d ast.Decl) ([]decl, error) {
	switch d := d.(type) {
	case *ast.FuncDecl:
		fn := *d
		fn.Doc = nil
		h, err := hashNode(fset, &fn)
		if err != nil {
			return nil, err
		}
		recv := ""
		if d.Recv != nil && len(d.Recv.List) > 0 {
			var b bytes.Buffer
			if err := printer.Fprint(&b, fset, d.Recv.List[0].Type); err != nil {
				return nil, err
			}
			recv = b.String()
		}
		return []decl{{kind: "func", recv: recv, name: d.Name.Name, hash: h}}, nil
	case *ast.GenDecl:
		if d.Tok == token.IMPORT {
			return nil, nil
		}
		var out []decl
		for _, s := range d.Specs {
			name, spec := "", s
			switch s := s.(type) {
			case *ast.TypeSpec:
				ts := *s
				ts.Doc, ts.Comment = nil, nil
				name, spec = s.Name.Name, &ts
			case *ast.ValueSpec:
				vs := *s
				vs.Doc, vs.Comment = nil, nil
				names := make([]string, len(s.Names))
				for i, n := range s.Names {
					names[i] = n.Name
				}
				name, spec = strings.Join(names, ","), &vs
			}
			h, err := hashNode(fset, spec)
			if err != nil {
				return nil, err
			}
			out = append(out, decl{kind: d.Tok.String(), name: name, hash: h})
		}
		return out, nil
	}
	return nil, fmt.Errorf("unexpected declaration %T", d)
}

// hashNode prints n with a fixed printer configuration and hashes the text.
func hashNode(fset *token.FileSet, n ast.Node) (string, error) {
	var b bytes.Buffer
	cfg := printer.Config{Mode: printer.UseSpaces | printer.TabIndent, Tabwidth: 8}
	if err := cfg.Fprint(&b, fset, n); err != nil {
		return "", err
	}
	sum := sha256.Sum256(b.Bytes())
	return hex.EncodeToString(sum[:]), nil
}
