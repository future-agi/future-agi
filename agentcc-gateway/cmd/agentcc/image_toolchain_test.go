package main

import (
	"os"
	"regexp"
	"strings"
	"testing"
)

// The image's /app/agentcc-gateway is compiled by the Go toolchain that
// go.mod names, the one the release workflow builds the signed binaries with
// (.github/workflows/release-agentcc-gateway.yml: prepare reads it, binaries
// sets GOTOOLCHAIN to it) and the one the release's tests run on. The builder
// image's own Go is a different release (its GOTOOLCHAIN=local keeps it), so
// the Dockerfile names the toolchain on the build itself, and a go.mod change
// that moves the toolchain fails here until the Dockerfile moves with it.

var (
	goModToolchainLine = regexp.MustCompile(`^toolchain go([0-9.]*)$`)
	goModGoLine        = regexp.MustCompile(`^go ([0-9.]*)$`)
	twoPartVersion     = regexp.MustCompile(`^[0-9]+\.[0-9]+$`)
	threePartVersion   = regexp.MustCompile(`^[0-9]+\.[0-9]+\.[0-9]+$`)
	gatewayGoBuild     = regexp.MustCompile(`\bgo build\b.*\./cmd/agentcc/?(\s|$)`)
	inlineGoToolchain  = regexp.MustCompile(`(?:^|\s)GOTOOLCHAIN=(\S+)`)
	envGoToolchain     = regexp.MustCompile(`^GOTOOLCHAIN[= ](\S+)$`)
)

// goModToolchain returns go.mod's toolchain as the release workflow's prepare
// step reads it: the toolchain line, else the go line, a two-part version
// made three-part, prefixed "go".
func goModToolchain(t *testing.T, path string) string {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var toolchain, goVersion string
	for _, line := range strings.Split(string(raw), "\n") {
		if m := goModToolchainLine.FindStringSubmatch(line); m != nil {
			toolchain = m[1]
		}
		if m := goModGoLine.FindStringSubmatch(line); m != nil {
			goVersion = m[1]
		}
	}
	v := toolchain
	if v == "" {
		v = goVersion
	}
	if twoPartVersion.MatchString(v) {
		v += ".0"
	}
	if !threePartVersion.MatchString(v) {
		t.Fatalf("%s names no Go version on a toolchain or go line (got %q)", path, v)
	}
	return "go" + v
}

type dockerfileStep struct {
	line    int
	keyword string
	rest    string
}

// dockerfileSteps returns the Dockerfile's instructions with their
// continuation lines joined, leaving out comments (also those inside a
// continuation, which Docker drops).
func dockerfileSteps(t *testing.T, path string) []dockerfileStep {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var steps []dockerfileStep
	var cur strings.Builder
	start := 0
	for i, line := range strings.Split(string(raw), "\n") {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "#") || (cur.Len() == 0 && trimmed == "") {
			continue
		}
		if cur.Len() == 0 {
			start = i + 1
		}
		if body, more := strings.CutSuffix(trimmed, `\`); more {
			cur.WriteString(body + " ")
			continue
		}
		cur.WriteString(trimmed)
		keyword, rest, _ := strings.Cut(cur.String(), " ")
		steps = append(steps, dockerfileStep{start, strings.ToUpper(keyword), strings.TrimSpace(rest)})
		cur.Reset()
	}
	return steps
}

func TestImageBuildsTheGatewayWithGoModToolchain(t *testing.T) {
	want := goModToolchain(t, "../../go.mod")

	var builds []dockerfileStep
	var toolchains []string // the toolchain each build runs with, "" when unset
	stageEnv := ""
	for _, s := range dockerfileSteps(t, "../../Dockerfile") {
		switch s.keyword {
		case "FROM":
			stageEnv = ""
		case "ENV":
			if m := envGoToolchain.FindStringSubmatch(s.rest); m != nil {
				stageEnv = m[1]
			}
		case "RUN":
			if !gatewayGoBuild.MatchString(s.rest) {
				continue
			}
			got := stageEnv
			before, _, _ := strings.Cut(s.rest, "go build")
			if m := inlineGoToolchain.FindStringSubmatch(before); m != nil {
				got = m[1]
			}
			builds = append(builds, s)
			toolchains = append(toolchains, got)
		}
	}
	if len(builds) != 1 {
		t.Fatalf("Dockerfile: want exactly one RUN that builds ./cmd/agentcc, found %d", len(builds))
	}
	if got := toolchains[0]; got != want {
		if got == "" {
			got = "unset, so the builder image's own Go (GOTOOLCHAIN=local) compiles it"
		}
		t.Errorf("Dockerfile:%d builds the gateway with GOTOOLCHAIN %s; want GOTOOLCHAIN=%s, go.mod's toolchain, which the release binaries and the release's tests use:\n%s",
			builds[0].line, got, want, builds[0].rest)
	}
}
