package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// A made-up overlay entry for the fixture's gmail/relay provider; its price
// and source are unique strings no output may contain.
const testOverlay = `schema: connector-price-overlay/v1
key: gmail/relay
estimate:
  - {unit: tool_call, usdPerUnit: 0.000419, source: https://relay.example/pricing-7c1e, checkedAt: 2026-10-19}
billingMode: byok
`

func TestOverlayNeverInBundle(t *testing.T) {
	// An overlay document anywhere in the catalog stops build and both
	// checks, and the message names the file but never quotes the price.
	for _, rel := range []string{
		"apps/gmail/providers/futureagi-rest/fixtures/price.yaml",
		"pricing/gmail.yaml",
		"schema/price.yml",
		"dist/emit/price.json",
		"overlay.yaml",
	} {
		c := newCopy(t)
		c.build(t)
		doc := testOverlay
		if strings.HasSuffix(rel, ".json") {
			doc = `{"schema": "connector-price-overlay/v1", "key": "gmail/relay", "estimate": [{"unit": "tool_call", "usdPerUnit": 0.000419,` +
				` "source": "https://relay.example/pricing-7c1e", "checkedAt": "2026-10-19"}], "billingMode": "byok"}`
		}
		if err := writeFile(filepath.Join(c.catalog, rel), []byte(doc)); err != nil {
			t.Fatal(err)
		}
		for _, cmd := range [][]string{{"build", "--date", "2026-10-20", "--source-commit", testCommit}, {"check"}, {"check", "--sources-only"}} {
			out := c.run(t, 1, cmd...)
			if !strings.Contains(out, rel) || !strings.Contains(out, "price overlay data") {
				t.Errorf("%s with an overlay at %s: %s", cmd[0], rel, out)
			}
			if strings.Contains(out, "0.000419") {
				t.Errorf("%s quotes the price: %s", cmd[0], out)
			}
		}
	}

	// An operator's overlay directory is validated against the catalog,
	// and no output changes or carries any of it.
	c := newCopy(t)
	c.build(t)
	before := c.snapshot(t)
	ops := t.TempDir()
	if err := os.WriteFile(filepath.Join(ops, "gmail-relay.yaml"), []byte(testOverlay), 0o644); err != nil {
		t.Fatal(err)
	}
	c.run(t, 0, "check", "--overlay", ops)
	c.run(t, 0, "check", "--sources-only", "--overlay", ops)
	c.build(t)
	for p, data := range c.snapshot(t) {
		if before[p] != data {
			t.Errorf("%s changed", p)
		}
		for _, leak := range []string{"0.000419", "pricing-7c1e", "usdPerUnit", "billingMode", "priceCardRef", "connector-price-overlay"} {
			if strings.Contains(data, leak) {
				t.Errorf("%s holds %q", p, leak)
			}
		}
	}

	// A key the catalog lacks, and a malformed overlay, are refused.
	if err := os.WriteFile(filepath.Join(ops, "composio.yaml"), []byte(strings.Replace(testOverlay, "gmail/relay", "gmail/composio", 1)), 0o644); err != nil {
		t.Fatal(err)
	}
	if out := c.run(t, 1, "check", "--overlay", ops); !strings.Contains(out, "gmail/composio names no provider") {
		t.Errorf("an unknown key: %s", out)
	}
	os.Remove(filepath.Join(ops, "composio.yaml"))
	if err := os.WriteFile(filepath.Join(ops, "bad.yaml"), []byte(testOverlay+"discount: 1\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if out := c.run(t, 1, "check", "--overlay", ops); !strings.Contains(out, `member "discount"`) {
		t.Errorf("an unknown member: %s", out)
	}

	// An overlay directory inside the repository is refused outright.
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git is not installed")
	}
	if out, err := exec.Command("git", "-C", c.root, "init", "-q").CombinedOutput(); err != nil {
		t.Fatalf("git init: %v\n%s", err, out)
	}
	inside := filepath.Join(c.root, "ops")
	if err := writeFile(filepath.Join(inside, "gmail-relay.yaml"), []byte(testOverlay)); err != nil {
		t.Fatal(err)
	}
	if out := c.run(t, 1, "check", "--overlay", inside); !strings.Contains(out, "overlay data never enters the repository") {
		t.Errorf("an overlay inside the repository: %s", out)
	}
}
