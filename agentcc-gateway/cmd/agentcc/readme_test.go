// Copyright 2026 Future AGI, Inc.
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// Check paths, not timings or the wording of the guide. A missing runner
// or command package should be caught without starting a gateway.
func TestBenchmarkReadmePaths(t *testing.T) {
	root := filepath.Join("..", "..")
	data, err := os.ReadFile(filepath.Join(root, "README.md"))
	if err != nil {
		t.Fatal(err)
	}
	link := regexp.MustCompile(`\]\((\./[^)# ]+)`)
	command := regexp.MustCompile(`^go run (\./cmd/[\w-]+)`)
	inBenchmarks, checked := false, 0
	for _, line := range strings.Split(string(data), "\n") {
		if strings.HasPrefix(line, "## ") {
			inBenchmarks = strings.Contains(strings.ToLower(line), "benchmark")
		}
		if !inBenchmarks {
			continue
		}
		for _, match := range append(link.FindAllStringSubmatch(line, -1), command.FindAllStringSubmatch(line, -1)...) {
			checked++
			if _, err := os.Stat(filepath.Join(root, match[1])); err != nil {
				t.Errorf("benchmark guide references missing path %q: %v", match[1], err)
			}
		}
	}
	if checked == 0 {
		t.Fatal("benchmark guide has no local tool or source paths")
	}
}
