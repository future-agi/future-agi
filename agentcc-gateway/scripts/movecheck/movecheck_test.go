package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// baseFiles is a small package: a type, a method, a function and a test,
// split over two files.
var baseFiles = map[string]string{
	"a.go": `package p

import "fmt"

// T is a type.
type T struct {
	N int // a count
}

// Hello says hello.
func (t *T) Hello() string {
	// an inner comment
	return fmt.Sprintf("hello %d", t.N)
}

const limit = 3
`,
	"b.go": `package p

func Add(a, b int) int { return a + b }

func Sub(a, b int) int { return a - b }
`,
	"a_test.go": `package p

import "testing"

func TestAdd(t *testing.T) {
	if Add(1, 2) != 3 {
		t.Fatal("add")
	}
}
`,
}

func writeDir(t *testing.T, files map[string]string) string {
	t.Helper()
	dir := t.TempDir()
	for name, src := range files {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(src), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return dir
}

// with returns a copy of baseFiles with the given files replaced; an empty
// source removes the file.
func with(changes map[string]string) map[string]string {
	out := map[string]string{}
	for k, v := range baseFiles {
		out[k] = v
	}
	for k, v := range changes {
		if v == "" {
			delete(out, k)
			continue
		}
		out[k] = v
	}
	return out
}

func check(t *testing.T, head map[string]string) ([]string, int) {
	t.Helper()
	base := writeDir(t, baseFiles)
	dir := writeDir(t, head)
	var stdout, stderr bytes.Buffer
	code := run([]string{"--base", base, "--head", dir}, &stdout, &stderr)
	if stderr.Len() > 0 {
		t.Fatalf("stderr: %s", stderr.String())
	}
	out := strings.TrimSpace(stdout.String())
	if out == "" {
		return nil, code
	}
	return strings.Split(out, "\n"), code
}

func TestMoveAcrossFilesIsEmpty(t *testing.T) {
	// Sub moves to its own file, Hello to b.go; whitespace and doc comments
	// change; imports follow the code.
	head := with(map[string]string{
		"a.go": `package p

// T is a type, documented differently.
type T struct {
	N   int // a count
}

const limit = 3
`,
		"b.go": `package p

import "fmt"

func Add(a, b int) int { return a + b }

// Hello now has another doc comment.
func (t *T) Hello() string {
	// an inner comment
	return fmt.Sprintf("hello %d",   t.N)
}
`,
		"sub.go": `package p

func Sub(a, b int) int { return a - b }
`,
	})
	diff, code := check(t, head)
	if len(diff) != 0 || code != 0 {
		t.Fatalf("a pure move reported %v (exit %d), want no diff and exit 0", diff, code)
	}
}

func TestEditedBodyCaught(t *testing.T) {
	head := with(map[string]string{"b.go": `package p

func Add(a, b int) int { return a + b + 0 }

func Sub(a, b int) int { return a - b }
`})
	diff, code := check(t, head)
	if code != 1 || len(diff) != 2 || !strings.HasPrefix(diff[0], "+ func Add ") || !strings.HasPrefix(diff[1], "- func Add ") {
		t.Fatalf("edited body: diff %v, exit %d; want a - and a + line for Add and exit 1", diff, code)
	}
}

func TestEditedSignatureAndMethodCaught(t *testing.T) {
	head := with(map[string]string{"a.go": strings.Replace(baseFiles["a.go"], "func (t *T) Hello() string", "func (t T) Hello() string", 1)})
	diff, code := check(t, head)
	if code != 1 || len(diff) != 2 || !strings.Contains(diff[0], "func (T).Hello") || !strings.Contains(diff[1], "func (*T).Hello") {
		t.Fatalf("changed receiver: diff %v, exit %d; want (T).Hello added and (*T).Hello dropped", diff, code)
	}
}

func TestDroppedFunctionCaught(t *testing.T) {
	head := with(map[string]string{"b.go": `package p

func Add(a, b int) int { return a + b }
`})
	diff, code := check(t, head)
	if code != 1 || len(diff) != 1 || !strings.HasPrefix(diff[0], "- func Sub ") {
		t.Fatalf("dropped function: diff %v, exit %d; want one - line for Sub and exit 1", diff, code)
	}
}

func TestAddedFunctionCaught(t *testing.T) {
	head := with(map[string]string{"c.go": `package p

func Mul(a, b int) int { return a * b }
`})
	diff, code := check(t, head)
	if code != 1 || len(diff) != 1 || !strings.HasPrefix(diff[0], "+ func Mul ") {
		t.Fatalf("added function: diff %v, exit %d; want one + line for Mul and exit 1", diff, code)
	}
}

func TestDuplicatedFunctionCaught(t *testing.T) {
	// The same body in a second, differently named file is still an added
	// declaration: the comparison counts, it does not just look things up.
	head := with(map[string]string{"c_test.go": `package p

func Sub(a, b int) int { return a - b }
`})
	diff, code := check(t, head)
	if code != 1 || len(diff) != 1 || !strings.HasPrefix(diff[0], "+ func Sub ") {
		t.Fatalf("duplicated function: diff %v, exit %d; want one + line for Sub", diff, code)
	}
}

func TestEditedTypeAndTestCaught(t *testing.T) {
	head := with(map[string]string{
		"a.go":      strings.Replace(baseFiles["a.go"], "N int // a count", "N int64 // a count", 1),
		"a_test.go": strings.Replace(baseFiles["a_test.go"], "Add(1, 2) != 3", "Add(2, 1) != 3", 1),
	})
	diff, code := check(t, head)
	joined := strings.Join(diff, "\n")
	if code != 1 || len(diff) != 4 || !strings.Contains(joined, "type T ") || !strings.Contains(joined, "func TestAdd ") {
		t.Fatalf("edited type and test: diff %v, exit %d; want T and TestAdd each dropped and added", diff, code)
	}
}

func TestUsageErrors(t *testing.T) {
	var stdout, stderr bytes.Buffer
	if code := run(nil, &stdout, &stderr); code != 2 {
		t.Fatalf("no flags: exit %d, want 2", code)
	}
	empty := t.TempDir()
	stderr.Reset()
	if code := run([]string{"--base", empty, "--head", empty}, &stdout, &stderr); code != 2 || !strings.Contains(stderr.String(), "no .go files") {
		t.Fatalf("empty dirs: exit %d, stderr %q; want 2 and a no-files error", code, stderr.String())
	}
}
