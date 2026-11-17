package server

import (
	"fmt"
	"go/ast"
	"go/constant"
	"go/parser"
	"go/token"
	"go/types"
	"reflect"
	"testing"
)

// Resolve registry constants from source, not a duplicated format-name table.
// Type-check only the constant declarations: discovering switch labels needs
// neither provider constructors nor imports from the rest of the package.
// Unknown identifiers and non-string values are errors, never skipped cases.
func readProviderFormats(filename string, source any) ([]string, error) {
	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, filename, source, 0)
	if err != nil {
		return nil, err
	}
	constants := &ast.File{Name: file.Name}
	for _, decl := range file.Decls {
		if gen, ok := decl.(*ast.GenDecl); ok && gen.Tok == token.CONST {
			constants.Decls = append(constants.Decls, gen)
		}
	}
	conf := types.Config{}
	pkg, err := conf.Check("providerformats", fset, []*ast.File{constants}, nil)
	if err != nil {
		return nil, fmt.Errorf("registry constants: %w", err)
	}
	var formats []string
	var caseErr error
	switches := 0
	for _, decl := range file.Decls {
		fn, ok := decl.(*ast.FuncDecl)
		if !ok || fn.Name.Name != "createProvider" || fn.Body == nil {
			continue
		}
		ast.Inspect(fn.Body, func(n ast.Node) bool {
			if caseErr != nil {
				return false
			}
			sw, ok := n.(*ast.SwitchStmt)
			if !ok {
				return true
			}
			if sel, ok := sw.Tag.(*ast.SelectorExpr); !ok || sel.Sel.Name != "APIFormat" {
				return true
			}
			switches++
			for _, stmt := range sw.Body.List {
				for _, expr := range stmt.(*ast.CaseClause).List {
					info := &types.Info{Types: map[ast.Expr]types.TypeAndValue{}}
					if err := types.CheckExpr(fset, pkg, token.NoPos, expr, info); err != nil {
						caseErr = fmt.Errorf("createProvider case at %s: %w", fset.Position(expr.Pos()), err)
						return false
					}
					value := info.Types[expr].Value
					if value == nil || value.Kind() != constant.String {
						caseErr = fmt.Errorf("createProvider case at %s is not a string constant", fset.Position(expr.Pos()))
						return false
					}
					formats = append(formats, constant.StringVal(value))
				}
			}
			return false
		})
	}
	if caseErr != nil {
		return nil, caseErr
	}
	if switches != 1 || len(formats) == 0 {
		return nil, fmt.Errorf("read %d api_format switches and %v from createProvider", switches, formats)
	}
	return formats, nil
}

func TestUsagePresenceProviderFormatConstants(t *testing.T) {
	source := `package providers
const (
    APIFormatSystemOne = "system" + "one"
    apiFormatTypeSafe string = "typesafe"
    alias = APIFormatSystemOne
)
func createProvider() {
    switch cfg.APIFormat {
    case "openai":
    case APIFormatSystemOne, apiFormatTypeSafe:
    case alias:
    default:
    }
}`
	got, err := readProviderFormats("registry.go", source)
	want := []string{"openai", "systemone", "typesafe", "systemone"}
	if err != nil || !reflect.DeepEqual(got, want) {
		t.Fatalf("resolved formats=%v err=%v want=%v", got, err, want)
	}
	for _, tc := range []struct{ name, source string }{
		{"unknown-identifier", `package providers; func createProvider() { switch cfg.APIFormat { case missing: } }`},
		{"non-string-constant", `package providers; const format = 7; func createProvider() { switch cfg.APIFormat { case format: } }`},
		{"variable-not-constant", `package providers; var format = "openai"; func createProvider() { switch cfg.APIFormat { case format: } }`},
		{"no-api-format-switch", `package providers; func createProvider() { switch cfg.Name { case "openai": } }`},
		{"empty-switch", `package providers; func createProvider() { switch cfg.APIFormat { default: } }`},
		{"multiple-switches", `package providers; func createProvider() { switch cfg.APIFormat { case "openai": }; switch cfg.APIFormat { case "azure": } }`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if formats, err := readProviderFormats("registry.go", tc.source); err == nil {
				t.Fatalf("must fail closed, got formats=%v", formats)
			}
		})
	}
}
