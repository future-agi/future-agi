package main

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"regexp"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/providers/contractspec"
)

// fetcher reaches upstream. Only the CLI uses the network one; tests pass a
// fake, so nothing in go test touches the network.
type fetcher interface {
	// LsRemote resolves ref ("HEAD", a branch or a tag) of a GitHub
	// repository to a commit SHA.
	LsRemote(ctx context.Context, repo, ref string) (string, error)
	// Get fetches url.
	Get(ctx context.Context, url string) ([]byte, error)
}

// netFetcher uses git ls-remote and HTTPS GET.
type netFetcher struct {
	client *http.Client
}

func newNetFetcher(timeout time.Duration) *netFetcher {
	return &netFetcher{client: &http.Client{Timeout: timeout}}
}

var shaRE = regexp.MustCompile(`^[0-9a-f]{40}$`)

func (f *netFetcher) LsRemote(ctx context.Context, repo, ref string) (string, error) {
	if ref == "" {
		ref = "HEAD"
	}
	if shaRE.MatchString(ref) {
		return ref, nil // already a commit
	}
	if !validRepo(repo) {
		return "", fmt.Errorf("bad repository %q", repo)
	}
	cmd := exec.CommandContext(ctx, "git", "ls-remote", "--", "https://github.com/"+repo+".git", ref)
	// Never prompt for credentials: a missing or renamed repository must
	// fail, not hang the job.
	cmd.Env = append(os.Environ(), "GIT_TERMINAL_PROMPT=0", "GIT_ASKPASS=true")
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err != nil {
		return "", fmt.Errorf("git ls-remote %s %s: %v: %s", repo, ref, err, strings.TrimSpace(stderr.String()))
	}
	return parseLsRemote(out, ref)
}

// parseLsRemote picks ref's commit from git ls-remote output ("<sha>\t<ref>"
// lines). For an annotated tag it prefers the peeled "^{}" line.
func parseLsRemote(out []byte, ref string) (string, error) {
	var sha, peeled string
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		fields := strings.Fields(line)
		if len(fields) != 2 || !shaRE.MatchString(fields[0]) {
			continue
		}
		name := fields[1]
		switch {
		case strings.HasSuffix(name, "^{}"):
			peeled = fields[0]
		case sha == "":
			sha = fields[0]
		}
	}
	if peeled != "" {
		return peeled, nil
	}
	if sha == "" {
		return "", fmt.Errorf("git ls-remote: ref %q not found", ref)
	}
	return sha, nil
}

var repoRE = regexp.MustCompile(`^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$`)

func validRepo(repo string) bool { return repoRE.MatchString(repo) && !strings.Contains(repo, "..") }

func (f *netFetcher) Get(ctx context.Context, url string) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "agentcc-gateway contract-specs (+https://github.com/future-agi/future-agi)")
	resp, err := f.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer func() { _ = resp.Body.Close() }()
	body, err := io.ReadAll(io.LimitReader(resp.Body, contractspec.MaxSpecBytes+1))
	if err != nil {
		return nil, fmt.Errorf("GET %s: %w", url, err)
	}
	if resp.StatusCode != http.StatusOK {
		snippet := body
		if len(snippet) > 200 {
			snippet = snippet[:200]
		}
		return nil, fmt.Errorf("GET %s: HTTP %d: %s", url, resp.StatusCode, bytes.TrimSpace(snippet))
	}
	if len(body) > contractspec.MaxSpecBytes {
		return nil, fmt.Errorf("GET %s: larger than %d bytes", url, contractspec.MaxSpecBytes)
	}
	return body, nil
}

// fetched is one provider's spec as fetched.
type fetched struct {
	url, commit string
	raw, spec   []byte
	at          time.Time
}

// fetchSource fetches src at ref (latest when empty) and decodes it.
func fetchSource(ctx context.Context, f fetcher, src contractspec.Source, ref string, now time.Time) (*fetched, error) {
	out := &fetched{at: now}
	if src.Repo != "" {
		commit, err := f.LsRemote(ctx, src.Repo, firstNonEmpty(ref, src.Branch, "HEAD"))
		if err != nil {
			return nil, err
		}
		out.commit = commit
	} else if ref != "" {
		return nil, fmt.Errorf("%s: -ref needs a git source; %s has no history (pin it by the committed sha256)", src.Provider, src.URL)
	}
	out.url = src.RawURL(out.commit)
	raw, err := f.Get(ctx, out.url)
	if err != nil {
		return nil, err
	}
	out.raw = raw
	if out.spec, err = contractspec.DecodeSource(raw, src.Decode); err != nil {
		return nil, fmt.Errorf("%s: decoding %s: %w", src.Provider, out.url, err)
	}
	return out, nil
}

// observeSignal reads an unfetchable provider's change signal at the
// repository's latest commit.
func observeSignal(ctx context.Context, f fetcher, sig *contractspec.Signal, now time.Time) (*contractspec.Observed, error) {
	commit, err := f.LsRemote(ctx, sig.Repo, "HEAD")
	if err != nil {
		return nil, err
	}
	raw, err := f.Get(ctx, fmt.Sprintf("https://raw.githubusercontent.com/%s/%s/%s", sig.Repo, commit, sig.Path))
	if err != nil {
		return nil, err
	}
	val := "sha256:" + contractspec.SHA256Hex(raw)
	if sig.Key != "" {
		if val, err = lookupKey(raw, sig.Key); err != nil {
			return nil, fmt.Errorf("%s %s: %w", sig.Repo, sig.Path, err)
		}
	}
	return &contractspec.Observed{Commit: commit, Value: val, CheckedAt: now.UTC().Format(time.RFC3339)}, nil
}

// lookupKey reads a dotted key from a YAML or JSON document.
func lookupKey(raw []byte, key string) (string, error) {
	doc, err := contractspec.ParseDocument(raw)
	if err != nil {
		return "", err
	}
	var node any = doc
	for _, part := range strings.Split(key, ".") {
		m, ok := node.(map[string]any)
		if !ok {
			return "", fmt.Errorf("key %q: %q is not an object", key, part)
		}
		if node, ok = m[part]; !ok {
			return "", fmt.Errorf("key %q not found", key)
		}
	}
	switch v := node.(type) {
	case string:
		return v, nil
	case nil:
		return "", errors.New("key " + key + " is null")
	default:
		return fmt.Sprint(v), nil
	}
}

func firstNonEmpty(vals ...string) string {
	for _, v := range vals {
		if v != "" {
			return v
		}
	}
	return ""
}
