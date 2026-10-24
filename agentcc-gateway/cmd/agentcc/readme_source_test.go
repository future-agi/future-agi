package main

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// The README sits in agentcc-gateway/, so its links resolve from there: the
// repository root is ../, not ../../. Its "Run from source" block runs the
// gateway's real entry point (cmd/agentcc; there is no cmd/server) on the
// config file it copies, and it and the Go badge name go.mod's Go versions.

const readmeFile = "../../README.md"

var (
	readmeMarkdownLink = regexp.MustCompile(`\]\(([^)\s]+)\)`)
	readmeHTMLLink     = regexp.MustCompile(`\b(?:href|src|srcset)="([^"]+)"`)
	urlScheme          = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9+.-]*:`)
	readmeGoBadge      = regexp.MustCompile(`img\.shields\.io/badge/built%20with-Go%20([0-9.]+)-`)
	packageMainLine    = regexp.MustCompile(`(?m)^package main$`)
)

// goModVersions returns go.mod's go line ("1.25.0") and toolchain line
// ("go1.26.10").
func goModVersions(t *testing.T) (goVersion, toolchain string) {
	t.Helper()
	raw, err := os.ReadFile("../../go.mod")
	if err != nil {
		t.Fatal(err)
	}
	for _, line := range strings.Split(string(raw), "\n") {
		if m := goModGoLine.FindStringSubmatch(line); m != nil {
			goVersion = m[1]
		}
		if m := goModToolchainLine.FindStringSubmatch(line); m != nil {
			toolchain = "go" + m[1]
		}
	}
	if goVersion == "" || toolchain == "" {
		t.Fatalf("go.mod: want a go line and a toolchain line (the release builds with the toolchain line), got go %q, toolchain %q", goVersion, toolchain)
	}
	return goVersion, toolchain
}

func readReadme(t *testing.T) []string {
	t.Helper()
	raw, err := os.ReadFile(readmeFile)
	if err != nil {
		t.Fatal(err)
	}
	return strings.Split(string(raw), "\n")
}

func TestReadmeRelativeLinksResolve(t *testing.T) {
	checked := 0
	for i, line := range readReadme(t) {
		var targets []string
		for _, m := range readmeMarkdownLink.FindAllStringSubmatch(line, -1) {
			targets = append(targets, m[1])
		}
		for _, m := range readmeHTMLLink.FindAllStringSubmatch(line, -1) {
			targets = append(targets, m[1])
		}
		for _, target := range targets {
			if urlScheme.MatchString(target) || strings.HasPrefix(target, "#") || strings.HasPrefix(target, "//") {
				continue
			}
			path, _, _ := strings.Cut(target, "#")
			path, _, _ = strings.Cut(path, "?")
			if path == "" {
				continue
			}
			checked++
			if _, err := os.Stat(filepath.Join(filepath.Dir(readmeFile), filepath.FromSlash(path))); err != nil {
				t.Errorf("README.md:%d links %q, which does not exist (the README's links resolve from agentcc-gateway/)", i+1, target)
			}
		}
	}
	if checked == 0 {
		t.Fatal("README.md: no relative link found, so the link patterns do not match it")
	}
}

func TestReadmeRunFromSourceRunsTheEntryPointWithGoModsGo(t *testing.T) {
	goVersion, toolchain := goModVersions(t)
	lines := readReadme(t)

	start := -1
	for i, line := range lines {
		if strings.Contains(line, "<summary>") && strings.Contains(line, "Run from source") {
			start = i
			break
		}
	}
	if start < 0 {
		t.Fatal(`README.md: no "Run from source" <details> block`)
	}
	if want := "Go " + goVersion; !strings.Contains(lines[start], want) {
		t.Errorf("README.md:%d: the Run from source summary should name go.mod's go line (%q): %s", start+1, want, lines[start])
	}

	var body []string
	for _, line := range lines[start+1:] {
		if strings.Contains(line, "</details>") {
			break
		}
		body = append(body, line)
	}
	block := strings.Join(body, "\n")
	if !strings.Contains(block, toolchain) {
		t.Errorf("README.md Run from source: names no %s, go.mod's toolchain, which the release builds and tests with", toolchain)
	}

	copied := map[string]bool{}
	runs := 0
	for k, line := range body {
		fields := strings.Fields(line)
		if len(fields) == 3 && fields[0] == "cp" {
			copied[fields[2]] = true
		}
		if len(fields) < 3 || fields[0] != "go" || (fields[1] != "run" && fields[1] != "build") {
			continue
		}
		runs++
		at := start + 2 + k
		pkg := fields[2]
		dir := filepath.Join("../..", filepath.FromSlash(pkg))
		mains, _ := filepath.Glob(filepath.Join(dir, "*.go"))
		found := false
		for _, file := range mains {
			if strings.HasSuffix(file, "_test.go") {
				continue
			}
			if raw, err := os.ReadFile(file); err == nil && packageMainLine.Match(raw) {
				found = true
				break
			}
		}
		if !found {
			t.Errorf("README.md:%d: %q builds %s, which holds no package main in agentcc-gateway/", at, line, pkg)
		}
		if fields[1] != "run" {
			continue
		}
		// Without --config the gateway loads no file at all (config.Load("")),
		// so the copied config would be ignored.
		config := ""
		for j := 3; j+1 < len(fields); j++ {
			if fields[j] == "--config" || fields[j] == "-config" {
				config = fields[j+1]
			}
		}
		if config == "" || !copied[config] {
			t.Errorf("README.md:%d: %q should pass --config the file the block copies (%v)", at, line, copied)
		}
	}
	if runs == 0 {
		t.Error("README.md Run from source: no `go run` or `go build` line")
	}
}

func TestReadmeGoBadgeNamesGoModsToolchain(t *testing.T) {
	_, toolchain := goModVersions(t)
	badges := 0
	for i, line := range readReadme(t) {
		for _, m := range readmeGoBadge.FindAllStringSubmatch(line, -1) {
			badges++
			if "go"+m[1] != toolchain {
				t.Errorf("README.md:%d: the Go badge says Go %s; the release builds with %s (go.mod's toolchain)", i+1, m[1], toolchain)
			}
		}
	}
	if badges != 1 {
		t.Errorf("README.md: want one built-with-Go badge, found %d", badges)
	}
}
