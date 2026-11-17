package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const refusalValidRoute = `routing:
  conditional_routes:
    - name: "plain"
      condition:
        field: "model"
        op: "$eq"
        value: "gpt-4o"
      action:
        provider: "openai"
`

// A condition node holds either field and op or exactly one of $and, $or,
// $not. A node that mixes them, or holds two combinators, is refused at
// load, naming the route's position and the node's path. (An op that is not
// one of the condition names, such as "$like", is an environment variable
// like any other bare $NAME, so beside $or it expands away.)
func TestAConditionNodeOfTwoShapesIsRefusedAtLoad(t *testing.T) {
	for _, tc := range []struct{ name, node, path string }{
		{"op beside $or", `
        op: "$eq"
        $or:
          - field: "model"
            op: "$eq"
            value: "gpt-4o"`, "condition"},
		{"field beside $and", `
        field: "model"
        $and:
          - field: "model"
            op: "$eq"
            value: "gpt-4o"`, "condition"},
		{"$and beside $or", `
        $and:
          - field: "model"
            op: "$eq"
            value: "gpt-4o"
        $or:
          - field: "stream"
            op: "$eq"
            value: true`, "condition"},
		{"nested op beside $not", `
        $or:
          - field: "model"
            op: "$eq"
            value: "gpt-4o"
          - op: "$eq"
            $not:
              field: "stream"
              op: "$eq"
              value: true`, "condition.$or[1]"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			body := refusalValidRoute + `    - name: "mixed"
      condition:` + tc.node + `
      action:
        provider: "openai"
`
			_, err := Load(writeConfigFile(t, body))
			if err == nil {
				t.Fatal("Load accepted a condition node of two shapes")
			}
			for _, want := range []string{`routing.conditional_routes[1] "mixed"`, ": " + tc.path + " holds "} {
				if !strings.Contains(err.Error(), want) {
					t.Errorf("error %q should contain %q", err, want)
				}
			}
		})
	}
}

// The refusal of an op names the route's position, the condition's path and
// the op as the loader read it, after environment expansion: a bare "$EQ" is
// an environment variable, so an unset one reads as "".
func TestAnOpRefusalNamesThePositionThePathAndTheExpandedOp(t *testing.T) {
	t.Setenv("EQ", "")
	body := refusalValidRoute + `    - name: "tier-route"
      condition:
        $or:
          - field: "metadata.tier"
            op: "$eq"
            value: "enterprise"
          - field: "metadata.tier"
            op: "$EQ"
            value: "premium"
      action:
        provider: "openai"
`
	_, err := Load(writeConfigFile(t, body))
	if err == nil {
		t.Fatal(`Load accepted op "$EQ"`)
	}
	for _, want := range []string{`routing.conditional_routes[1] "tier-route"`, `condition.$or[1] op ""`, "after environment expansion", "$eq"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("error %q should contain %q", err, want)
		}
	}
}

// The refusal of an op never prints a value environment expansion put there:
// an op that expanded to something other than a condition name is named by
// its length only.
func TestAnOpRefusalDoesNotPrintAnExpandedValue(t *testing.T) {
	for _, tc := range []struct{ name, value, length string }{
		{"a plain value", "expanded-op-value", "17 characters"},
		{"a value that itself begins with $", "$2b$12$expanded", "15 characters"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("CONDROUTE_OP_VALUE", tc.value)
			body := refusalValidRoute + `    - name: "from-env"
      condition:
        field: "model"
        op: "$CONDROUTE_OP_VALUE"
        value: "gpt-4o"
      action:
        provider: "openai"
`
			_, err := Load(writeConfigFile(t, body))
			if err == nil {
				t.Fatal("Load accepted an op that expanded to no condition name")
			}
			if strings.Contains(err.Error(), tc.value) {
				t.Errorf("error %q prints the expanded value", err)
			}
			for _, want := range []string{`routing.conditional_routes[1] "from-env"`, "condition op", tc.length, "after environment expansion"} {
				if !strings.Contains(err.Error(), want) {
					t.Errorf("error %q should contain %q", err, want)
				}
			}
		})
	}
}

// A config file holds no NUL byte; one that does is refused before any
// expansion, so no byte sequence of the file can stand in for the loader's
// own marks.
func TestAConfigFileWithANULByteIsRefused(t *testing.T) {
	t.Setenv("CONDROUTE_TEST_HOME", "/home/u")
	path := filepath.Join(t.TempDir(), "config.yaml")
	body := "server:\n  host: \"\x00agentcc-condition-name\x00CONDROUTE_TEST_HOME\"\n"
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	_, err := Load(path)
	if err == nil {
		t.Fatal("Load accepted a config file holding NUL bytes")
	}
	if !strings.Contains(err.Error(), "NUL") {
		t.Errorf("error %q should say the file holds a NUL byte", err)
	}
}

// A config file saved as UTF-16 is refused with what to do about it.
func TestAUTF16ConfigFileIsRefusedNamingTheEncoding(t *testing.T) {
	for _, bom := range [][]byte{{0xFF, 0xFE}, {0xFE, 0xFF}} {
		path := filepath.Join(t.TempDir(), "config.yaml")
		body := append(append([]byte{}, bom...), []byte("s\x00e\x00r\x00v\x00e\x00r\x00:\x00")...)
		if err := os.WriteFile(path, body, 0o600); err != nil {
			t.Fatal(err)
		}
		_, err := Load(path)
		if err == nil || !strings.Contains(err.Error(), "UTF-16") || !strings.Contains(err.Error(), "UTF-8") {
			t.Errorf("BOM % x: error %v, want one naming UTF-16 and asking for UTF-8", bom, err)
		}
	}
}

// A config file saved as UTF-32 is refused naming UTF-32, not UTF-16: the
// UTF-32LE mark begins with the UTF-16LE one.
func TestAUTF32ConfigFileIsRefusedNamingTheEncoding(t *testing.T) {
	for _, bom := range [][]byte{{0xFF, 0xFE, 0x00, 0x00}, {0x00, 0x00, 0xFE, 0xFF}} {
		path := filepath.Join(t.TempDir(), "config.yaml")
		body := append(append([]byte{}, bom...), []byte("s\x00\x00\x00:\x00\x00\x00")...)
		if err := os.WriteFile(path, body, 0o600); err != nil {
			t.Fatal(err)
		}
		_, err := Load(path)
		if err == nil || !strings.Contains(err.Error(), "UTF-32") || strings.Contains(err.Error(), "UTF-16") || !strings.Contains(err.Error(), "UTF-8") {
			t.Errorf("BOM % x: error %v, want one naming UTF-32 and asking for UTF-8", bom, err)
		}
	}
}
