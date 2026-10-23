package main

import (
	"encoding/json"
	"os"
	"regexp"
	"strings"
	"testing"
)

// The README's Docker instructions name the image the release workflows
// publish and run it the way the image starts: its CMD reads a config file
// that the image does not contain, so the quickstart mounts one there.

// publishedImageLine is the image input of the release workflow's build
// (.github/workflows/build-image-multiarch.yml pushes docker.io/<image>).
var publishedImageLine = regexp.MustCompile(`(?m)^\s*image:\s*(futureagi/agentcc-gateway)\s*,?\s*$`)

// readmeImageRef matches an image reference with a tag, registry optional.
var readmeImageRef = regexp.MustCompile(`(?:[a-z0-9-]+(?:\.[a-z0-9-]+)+/)?[a-z0-9._-]+(?:/[a-z0-9._-]+)+:[A-Za-z0-9._-]+`)

// publishedGatewayImage returns the gateway image both release workflows
// publish, failing unless they agree.
func publishedGatewayImage(t *testing.T) string {
	t.Helper()
	var found []string
	for _, wf := range []string{"release-agentcc-gateway.yml", "release-images.yml"} {
		raw, err := os.ReadFile("../../../.github/workflows/" + wf)
		if err != nil {
			t.Fatal(err)
		}
		m := publishedImageLine.FindStringSubmatch(string(raw))
		if m == nil {
			t.Fatalf("%s: no `image: futureagi/agentcc-gateway` build input", wf)
		}
		found = append(found, m[1])
	}
	if found[0] != found[1] {
		t.Fatalf("the release workflows publish different gateway images: %v", found)
	}
	return found[0]
}

// gatewayImageRepo is the repository part of an image reference whose last
// path segment names the gateway ("" for any other image, the gateway's
// builder base included).
func gatewayImageRepo(ref string) string {
	repo, _, _ := strings.Cut(ref, ":")
	parts := strings.Split(repo, "/")
	switch parts[len(parts)-1] {
	case "agentcc-gateway", "agent-command-center":
		return repo
	}
	return ""
}

// dockerRunCommands returns each `docker run` command in the README with its
// continuation lines joined, and the line it starts on.
func dockerRunCommands(t *testing.T, path string) (lines []int, commands []string) {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	all := strings.Split(string(raw), "\n")
	for i := 0; i < len(all); i++ {
		if !strings.HasPrefix(strings.TrimSpace(all[i]), "docker run ") {
			continue
		}
		start, cmd := i+1, strings.TrimSpace(all[i])
		for strings.HasSuffix(cmd, `\`) && i+1 < len(all) {
			i++
			cmd = strings.TrimSuffix(cmd, `\`) + " " + strings.TrimSpace(all[i])
		}
		lines, commands = append(lines, start), append(commands, cmd)
	}
	return lines, commands
}

func TestReadmeNamesTheImageTheReleasePublishes(t *testing.T) {
	published := publishedGatewayImage(t)
	units := readmeUnits(t, "../../README.md")

	refs := 0
	dockerRow := false
	for _, u := range units {
		for _, ref := range readmeImageRef.FindAllString(u.text, -1) {
			repo := gatewayImageRepo(ref)
			if repo == "" {
				continue
			}
			refs++
			if repo != published && repo != "docker.io/"+published {
				t.Errorf("README.md:%d names the gateway image %s; the release publishes docker.io/%s", u.line, ref, published)
			}
		}
		if strings.HasPrefix(u.text, "| 🐳 Docker |") && strings.Contains(u.text, published+":") {
			dockerRow = true
		}
		if strings.Contains(u.text, "ghcr.io/future-agi/agent-command-center") {
			t.Errorf("README.md:%d names ghcr.io/future-agi/agent-command-center, which no release workflow publishes", u.line)
		}
	}
	if refs < 2 {
		t.Errorf("README.md names the gateway image %d times; want the quickstart and the Deployment surface's Docker row at least", refs)
	}
	if !dockerRow {
		t.Errorf("README.md: the Deployment surface's Docker row does not name %s:<tag>", published)
	}
}

func TestReadmeQuickstartMountsTheConfigTheImageReads(t *testing.T) {
	published := publishedGatewayImage(t)

	// The config path the image's CMD passes to --config.
	configPath := ""
	for _, s := range dockerfileSteps(t, "../../Dockerfile") {
		if s.keyword == "CMD" {
			if m := regexp.MustCompile(`"--config",\s*"([^"]+)"`).FindStringSubmatch(s.rest); m != nil {
				configPath = m[1]
			}
		}
	}
	if configPath == "" {
		t.Fatal(`Dockerfile: no CMD ["--config", "<path>"]`)
	}

	lines, commands := dockerRunCommands(t, "../../README.md")
	if len(commands) == 0 {
		t.Fatal("README.md has no docker run command")
	}
	mount := regexp.MustCompile(`(?:-v|--volume)[ =]"?[^"\s]+:` + regexp.QuoteMeta(configPath) + `(?::ro)?"?(?:\s|$)`)
	for i, cmd := range commands {
		if !strings.Contains(cmd, published+":") {
			continue
		}
		if !mount.MatchString(cmd) {
			t.Errorf("README.md:%d runs the image without mounting a config at %s, the path its CMD reads (the image has none, so the gateway exits):\n%s",
				lines[i], configPath, cmd)
		}
	}
	quick := commands[0]
	if !strings.Contains(quick, published+":") || !mount.MatchString(quick) {
		t.Errorf("README.md:%d: the quickstart's docker run must run %s with a config mounted at %s:\n%s", lines[0], published, configPath, quick)
	}
}

// The quickstart runs a pinned release, not :latest, which moves with every
// release: the image tag a stable release publishes and the same tag of the
// repository, so the example config it mounts is that release's own. A stable
// release's tag is cut by release-please (release-please-config.json: the v,
// no component) and release-images.yml publishes on that tag push; the
// version itself is chosen when the release is cut, so the quickstart names
// the tag's form, vX.Y.Z, and links the published tags. :latest stays only as
// the alternative, in a comment.

// releaseTag is a stable release tag, or the quickstart's placeholder for one.
var releaseTag = regexp.MustCompile(`^v(?:[0-9]+|X)\.(?:[0-9]+|Y)\.(?:[0-9]+|Z)$`)

// shellVarRef is "$NAME" or "${NAME}".
var shellVarRef = regexp.MustCompile(`^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$`)

// quickstartBlock returns the first bash block after the Quick start heading.
func quickstartBlock(t *testing.T) []string {
	t.Helper()
	raw, err := os.ReadFile("../../README.md")
	if err != nil {
		t.Fatal(err)
	}
	var block []string
	heading, in := false, false
	for _, line := range strings.Split(string(raw), "\n") {
		switch {
		case !heading:
			heading = strings.HasPrefix(line, "## ") && strings.Contains(line, "Quick start")
		case !in:
			in = strings.TrimSpace(line) == "```bash"
		case strings.TrimSpace(line) == "```":
			return block
		default:
			block = append(block, line)
		}
	}
	t.Fatal("README.md: no ```bash block after the Quick start heading")
	return nil
}

// stableReleaseTagFilter returns release-images.yml's tag-push filter as a
// regular expression, after checking release-please cuts tags of that form.
func stableReleaseTagFilter(t *testing.T) *regexp.Regexp {
	t.Helper()
	raw, err := os.ReadFile("../../../release-please-config.json")
	if err != nil {
		t.Fatal(err)
	}
	var cfg struct {
		IncludeV         *bool `json:"include-v-in-tag"`
		IncludeComponent *bool `json:"include-component-in-tag"`
	}
	if err := json.Unmarshal(raw, &cfg); err != nil {
		t.Fatal(err)
	}
	if cfg.IncludeV == nil || !*cfg.IncludeV || cfg.IncludeComponent == nil || *cfg.IncludeComponent {
		t.Fatal("release-please-config.json: want include-v-in-tag true and include-component-in-tag false (tags vX.Y.Z)")
	}
	wf, err := os.ReadFile("../../../.github/workflows/release-images.yml")
	if err != nil {
		t.Fatal(err)
	}
	m := regexp.MustCompile(`(?m)^\s+tags:\s*\[\s*"([^"]+)"\s*\]`).FindSubmatch(wf)
	if m == nil {
		t.Fatal(`release-images.yml: no push tags filter ["..."]`)
	}
	// A GitHub filter pattern: '.' is literal, '+' repeats the class before it.
	return regexp.MustCompile("^" + strings.ReplaceAll(string(m[1]), ".", `\.`) + "$")
}

func TestReadmeQuickstartPinsTheReleaseTag(t *testing.T) {
	published := publishedGatewayImage(t)
	filter := stableReleaseTagFilter(t)
	block := quickstartBlock(t)
	_, commands := dockerRunCommands(t, "../../README.md")
	if len(commands) == 0 {
		t.Fatal("README.md has no docker run command")
	}
	image := ""
	for _, field := range strings.Fields(commands[0]) {
		if f := strings.Trim(field, `"'`); strings.HasPrefix(f, published+":") {
			image = f
		}
	}
	if image == "" {
		t.Fatalf("the quickstart's docker run does not run %s:<tag>:\n%s", published, commands[0])
	}
	tag := strings.TrimPrefix(image, published+":")
	variable := ""
	if m := shellVarRef.FindStringSubmatch(tag); m != nil {
		variable, tag = m[1], ""
		for _, line := range block {
			if value, ok := strings.CutPrefix(strings.TrimSpace(line), variable+"="); ok {
				if fields := strings.Fields(value); len(fields) > 0 {
					tag = strings.Trim(fields[0], `"'`)
				}
			}
		}
	}
	if !releaseTag.MatchString(tag) {
		t.Fatalf("the quickstart runs %s with tag %q; want a release tag, vX.Y.Z", published, tag)
	}
	if sample := strings.NewReplacer("X", "1", "Y", "2", "Z", "3").Replace(tag); !filter.MatchString(sample) {
		t.Errorf("the quickstart's tag %s (for example %s) is not what release-images.yml publishes on (%s)", tag, sample, filter)
	}

	cloneSameTag := false
	tagsLink := false
	for _, line := range block {
		code := strings.TrimSpace(line)
		if strings.HasPrefix(code, "git clone ") {
			cloneSameTag = strings.Contains(code, `--branch "$`+variable+`"`) && variable != "" ||
				strings.Contains(code, "--branch "+tag+" ")
		}
		if strings.Contains(line, "https://hub.docker.com/r/"+published+"/tags") {
			tagsLink = true
		}
		if strings.Contains(line, published+":latest") && !strings.HasPrefix(code, "#") {
			t.Errorf("the quickstart runs %s:latest outside a comment: %s", published, code)
		}
	}
	if !cloneSameTag {
		t.Error("the quickstart's git clone does not check out the same tag it runs (--branch)")
	}
	if !tagsLink {
		t.Errorf("the quickstart does not link the published tags, https://hub.docker.com/r/%s/tags", published)
	}
}

// internalRunID matches a run id of the form j-<date>t<time>-<hex>, which no
// public page resolves: the README names the machine, the toolchain and the
// commands that reproduce its figures.
var internalRunID = regexp.MustCompile(`\bj-\d{8}t\d{4}-[0-9a-f]{6}\b`)

func TestReadmeNamesNoInternalRunIDs(t *testing.T) {
	for _, u := range readmeUnits(t, "../../README.md") {
		for _, id := range internalRunID.FindAllString(u.text, -1) {
			t.Errorf("README.md:%d names the internal run id %s", u.line, id)
		}
	}
}
