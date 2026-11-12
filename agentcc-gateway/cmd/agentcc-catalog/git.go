package main

import (
	"fmt"
	"os/exec"
	"path/filepath"
	"strings"
)

// gitHead is the commit the catalog's records come from: HEAD of the
// repository dir is in, refused while apps/ or platforms/ have uncommitted
// changes, since HEAD would then not hold the records built.
func gitHead(dir string) (string, error) {
	status, err := git(dir, "status", "--porcelain", "--untracked-files=all", "--", "apps", "platforms")
	if err != nil {
		return "", fmt.Errorf("the records' source commit comes from git, which failed (%v): pass --source-commit", err)
	}
	if status != "" {
		return "", fmt.Errorf("apps/ or platforms/ have uncommitted changes, so HEAD does not hold the records: commit them, or pass --source-commit:\n%s", status)
	}
	head, err := git(dir, "rev-parse", "HEAD")
	if err != nil {
		return "", fmt.Errorf("git rev-parse HEAD failed (%v): pass --source-commit", err)
	}
	return head, nil
}

// gitTopLevel is the root of the repository dir is in.
func gitTopLevel(dir string) (string, error) {
	return git(dir, "rev-parse", "--show-toplevel")
}

func git(dir string, args ...string) (string, error) {
	cmd := exec.Command("git", append([]string{"-C", dir}, args...)...)
	out, err := cmd.Output()
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(out)), nil
}

// within reports whether path is root or under it, once both are absolute
// and free of symbolic links.
func within(path, root string) (bool, error) {
	p, err := realPath(path)
	if err != nil {
		return false, err
	}
	r, err := realPath(root)
	if err != nil {
		return false, err
	}
	rel, err := filepath.Rel(r, p)
	if err != nil {
		return false, nil
	}
	return rel == "." || (rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator))), nil
}

func realPath(p string) (string, error) {
	abs, err := filepath.Abs(p)
	if err != nil {
		return "", err
	}
	return filepath.EvalSymlinks(abs)
}
