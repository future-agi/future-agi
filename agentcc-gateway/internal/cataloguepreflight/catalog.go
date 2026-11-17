//go:build cataloguepreflighttest

package cataloguepreflight

import (
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing/fstest"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/bundle"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// BuildCatalog renders ONLY the existing port placeholder. The original
// dotted-host regex, host validation and fixture tool policies are untouched.
func BuildCatalog(scenarioRoot, destination, port, head string) (string, error) {
	if !regexp.MustCompile(`^[0-9a-f]{40}$`).MatchString(head) {
		return "", errors.New("full source head required")
	}
	fsys := fstest.MapFS{}
	for _, path := range []string{"apps/cn30a-docs/app.yaml", "apps/cn30a-docs/providers/key.yaml"} {
		wire, err := os.ReadFile(filepath.Join(scenarioRoot, "catalog", path+".in"))
		if err != nil {
			return "", err
		}
		text := strings.ReplaceAll(string(wire), "@@CN30A_TLS_PORT@@", port)
		if strings.Contains(text, "@@") {
			return "", errors.New("unresolved template placeholder")
		}
		fsys[path] = &fstest.MapFile{Data: []byte(text), Mode: 0600}
	}
	cat, err := manifest.LoadDir(fsys)
	if err != nil {
		return "", err
	}
	if err := manifest.CheckL1(cat); err != nil {
		return "", err
	}
	if err := manifest.CheckPublic(cat); err != nil {
		return "", err
	}
	b, err := bundle.Build(fsys, cat, nil, time.Date(2026, 10, 30, 0, 0, 0, 0, time.UTC), func() (string, error) { return head, nil })
	if err != nil {
		return "", err
	}
	wire, err := b.Encode()
	if err != nil {
		return "", err
	}
	for path, file := range fsys {
		full := filepath.Join(destination, path)
		if err := os.MkdirAll(filepath.Dir(full), 0700); err != nil {
			return "", err
		}
		if err := exclusiveWrite(full, file.Data); err != nil {
			return "", err
		}
	}
	path := filepath.Join(destination, "catalog.json")
	if err := exclusiveWrite(path, wire); err != nil {
		return "", err
	}
	return path, nil
}

func exclusiveWrite(path string, wire []byte) error {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	defer f.Close()
	if _, err := f.Write(wire); err != nil {
		return err
	}
	return f.Sync()
}
