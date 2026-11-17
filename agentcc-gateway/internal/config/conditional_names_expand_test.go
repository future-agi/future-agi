package config

import (
	"strings"
	"testing"
)

// Every operator and logical key stays as written when bare, whatever the
// environment holds, including the names that share a prefix ($gt and $gte,
// $in and $nin, $ne and $not). The braced form and any longer name are
// environment variables and expand.
func TestEveryConditionNameStaysLiteralAndLongerNamesExpand(t *testing.T) {
	names := append(append([]string{}, ConditionOperators...), ConditionLogicalKeys...)
	if len(names) != 13 {
		t.Fatalf("got %d condition names, want 13: %q", len(names), names)
	}
	for _, name := range names {
		bare := strings.TrimPrefix(name, "$")
		t.Run(bare, func(t *testing.T) {
			t.Setenv(bare, "env-"+bare)
			t.Setenv(bare+"_x", "env-"+bare+"_x")
			t.Setenv(bare+"x", "env-"+bare+"x")
			in := name + `: ["` + name + `", "${` + bare + `}", "$` + bare + `_x", "$` + bare + `x", "` + name + `"]` + "\n" + name
			want := name + `: ["` + name + `", "env-` + bare + `", "env-` + bare + `_x", "env-` + bare + `x", "` + name + `"]` + "\n" + name
			if got := expandEnv(in); got != want {
				t.Errorf("expandEnv(%q)\n got %q\nwant %q", in, got, want)
			}
		})
	}
}

// Names written one straight after another all stay as written.
func TestConditionNamesWrittenTogetherStayLiteral(t *testing.T) {
	for _, name := range []string{"eq", "and", "or", "not", "in"} {
		t.Setenv(name, "env-"+name)
	}
	t.Setenv("CONDROUTE_TEST_TAIL", "tail")
	for in, want := range map[string]string{
		`"$eq$eq"`:                  `"$eq$eq"`,
		`"$and$or$not$in"`:          `"$and$or$not$in"`,
		`"$eq$CONDROUTE_TEST_TAIL"`: `"$eqtail"`,
		`"${eq}$eq"`:                `"env-eq$eq"`,
	} {
		if got := expandEnv(in); got != want {
			t.Errorf("expandEnv(%s) = %s, want %s", in, got, want)
		}
	}
}
