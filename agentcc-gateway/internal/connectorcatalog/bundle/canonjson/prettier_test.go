package canonjson

import (
	"fmt"
	"math/rand"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// TestPrettyIsPrettierStable writes random documents with Pretty and asks
// the real Prettier whether it would change any of them. It runs only when
// CANONJSON_PRETTIER names a Prettier 3 binary, such as the repository's
// frontend/node_modules/.bin/prettier, the one the commit hook runs:
//
//	CANONJSON_PRETTIER=$PWD/../frontend/node_modules/.bin/prettier go test ./internal/connectorcatalog/bundle/canonjson/ -run TestPrettyIsPrettierStable
func TestPrettyIsPrettierStable(t *testing.T) {
	bin := os.Getenv("CANONJSON_PRETTIER")
	if bin == "" {
		t.Skip("CANONJSON_PRETTIER is not set")
	}
	dir := t.TempDir()
	rng := rand.New(rand.NewSource(20261020))
	const docs = 400
	for i := 0; i < docs; i++ {
		data, err := Pretty(randomObject(rng, 0))
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, fmt.Sprintf("doc%03d.json", i)), data, 0o644); err != nil {
			t.Fatal(err)
		}
	}
	cmd := exec.Command(bin, "--list-different", ".")
	cmd.Dir = dir
	out, err := cmd.CombinedOutput()
	if err != nil || strings.TrimSpace(string(out)) != "" {
		changed := strings.Fields(string(out))
		sample := ""
		if len(changed) > 0 {
			before, _ := os.ReadFile(filepath.Join(dir, changed[0]))
			fix := exec.Command(bin, filepath.Join(dir, changed[0]))
			after, _ := fix.Output()
			sample = fmt.Sprintf("\n%s as written:\n%s\nas Prettier prints it:\n%s", changed[0], before, after)
		}
		t.Fatalf("Prettier would change %d of %d documents (%v): %v%s", len(changed), docs, err, changed, sample)
	}
}

func randomObject(rng *rand.Rand, depth int) Object {
	n := rng.Intn(6)
	if depth == 0 {
		n += 2
	}
	obj := Object{}
	for i := 0; i < n; i++ {
		obj = append(obj, Member{fmt.Sprintf("%s%d", randomText(rng, 1+rng.Intn(12)), i), randomValue(rng, depth+1)})
	}
	return obj
}

func randomValue(rng *rand.Rand, depth int) any {
	if depth > 4 {
		return randomAtom(rng)
	}
	switch rng.Intn(10) {
	case 0, 1:
		return randomObject(rng, depth)
	case 2, 3, 4:
		return randomArray(rng, depth)
	case 5:
		nums := make([]any, rng.Intn(40))
		for i := range nums {
			nums[i] = randomNumber(rng)
		}
		return nums
	}
	return randomAtom(rng)
}

func randomArray(rng *rand.Rand, depth int) []any {
	n := rng.Intn(5)
	arr := make([]any, n)
	kind := rng.Intn(3)
	for i := range arr {
		switch kind {
		case 0:
			arr[i] = randomAtom(rng)
		case 1:
			inner := make([]any, rng.Intn(4))
			for j := range inner {
				inner[j] = randomAtom(rng)
			}
			arr[i] = inner
		default:
			arr[i] = randomValue(rng, depth+1)
		}
	}
	return arr
}

func randomAtom(rng *rand.Rand) any {
	switch rng.Intn(8) {
	case 0:
		return nil
	case 1:
		return rng.Intn(2) == 0
	case 2, 3:
		return randomNumber(rng)
	}
	return randomText(rng, rng.Intn(70))
}

func randomNumber(rng *rand.Rand) any {
	switch rng.Intn(4) {
	case 0:
		return int64(rng.Intn(100000) - 50000)
	case 1:
		return rng.Float64() * 1000
	case 2:
		return float64(rng.Intn(1000)) * 1e21
	}
	return rng.Float64() * 1e-8
}

var alphabet = []rune("abcdefghijklmnopqrstuvwxyzABC0123456789 _-./:\"\\\n\téü€😀 \u0001\u007f<>&")

func randomText(rng *rand.Rand, n int) string {
	var b strings.Builder
	for i := 0; i < n; i++ {
		b.WriteRune(alphabet[rng.Intn(len(alphabet))])
	}
	return b.String()
}
