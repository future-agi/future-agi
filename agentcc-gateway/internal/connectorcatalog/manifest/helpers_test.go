package manifest

import (
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"testing/fstest"
)

// fixture copies testdata/valid, a complete valid catalog, into a map the
// test can edit before loading it.
func fixture(t *testing.T) fstest.MapFS {
	t.Helper()
	const root = "testdata/valid"
	fsys := fstest.MapFS{}
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() || d.Name() == ".DS_Store" {
			return err
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		fsys[filepath.ToSlash(rel)] = &fstest.MapFile{Data: data}
		return nil
	})
	if err != nil {
		t.Fatalf("read %s: %v", root, err)
	}
	return fsys
}

// edit replaces the single occurrence of old in path with new.
func edit(t *testing.T, fsys fstest.MapFS, path, old, new string) {
	t.Helper()
	f, ok := fsys[path]
	if !ok {
		t.Fatalf("edit: no file %s", path)
	}
	s := string(f.Data)
	if n := strings.Count(s, old); n != 1 {
		t.Fatalf("edit %s: %q occurs %d times, want 1", path, old, n)
	}
	fsys[path] = &fstest.MapFile{Data: []byte(strings.Replace(s, old, new, 1))}
}

// put writes path.
func put(fsys fstest.MapFS, path, content string) {
	fsys[path] = &fstest.MapFile{Data: []byte(content)}
}

// cloneApp copies apps/<from>/** to apps/<to>/**, renaming the app id in
// app.yaml and the app field of each provider.
func cloneApp(t *testing.T, fsys fstest.MapFS, from, to string) {
	t.Helper()
	prefix := "apps/" + from + "/"
	found := false
	for path, f := range fsys {
		if !strings.HasPrefix(path, prefix) {
			continue
		}
		found = true
		s := string(f.Data)
		s = strings.Replace(s, "\nid: "+from+"\n", "\nid: "+to+"\n", 1)
		s = strings.Replace(s, "\napp: "+from+"\n", "\napp: "+to+"\n", 1)
		put(fsys, "apps/"+to+"/"+strings.TrimPrefix(path, prefix), s)
	}
	if !found {
		t.Fatalf("cloneApp: no app %s", from)
	}
}

// mustLoad loads fsys and fails the test on any error.
func mustLoad(t *testing.T, fsys fs.FS) *Catalog {
	t.Helper()
	cat, err := LoadDir(fsys)
	if err != nil {
		t.Fatalf("LoadDir: %v", err)
	}
	return cat
}

// wantLoadError loads fsys and requires an error holding every want.
func wantLoadError(t *testing.T, fsys fs.FS, want ...string) {
	t.Helper()
	cat, err := LoadDir(fsys)
	if err == nil {
		t.Fatalf("LoadDir succeeded, want an error with %q", want)
	}
	if cat != nil {
		t.Errorf("LoadDir returned a catalog with its error")
	}
	for _, w := range want {
		if !strings.Contains(err.Error(), w) {
			t.Errorf("error does not mention %q:\n%v", w, err)
		}
	}
}

// wantError requires err to hold every want.
func wantError(t *testing.T, err error, want ...string) {
	t.Helper()
	if err == nil {
		t.Fatalf("got no error, want one with %q", want)
	}
	for _, w := range want {
		if !strings.Contains(err.Error(), w) {
			t.Errorf("error does not mention %q:\n%v", w, err)
		}
	}
}
