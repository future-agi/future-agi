package providers

// The x-agentcc-dispatched header says "false" only when no provider request
// was started: a provider call is marked by the net/http client trace on the
// context it is given (models.WithDispatchTrace). That holds only while every
// provider and capability sender sends through net/http with that context.
// This census fails on a production sender that could send without it: a
// request built without a context, a package-level http.Get/Post/Head, a raw
// network dial, or a gRPC client.

import (
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// dispatchSenderExceptions are the reviewed uses, by file and pattern, with
// why each sends no provider request under a request's context.
var dispatchSenderExceptions = map[string]string{
	"hosted_tls.go|tls.DialWithDialer": "a startup probe of the hosted TLS endpoint, not a request's provider call",
}

var dispatchSenderPatterns = map[string]*regexp.Regexp{
	"http.NewRequest(":   regexp.MustCompile(`\bhttp\.NewRequest\(`),
	"http.Get/Post/Head": regexp.MustCompile(`\bhttp\.(Get|Post|PostForm|Head)\(`),
	"net.Dial":           regexp.MustCompile(`\bnet\.Dial(Timeout)?\(`),
	"tls.DialWithDialer": regexp.MustCompile(`\btls\.Dial(WithDialer)?\(`),
	"grpc":               regexp.MustCompile(`"google\.golang\.org/grpc`),
}

func TestEveryProviderSenderSendsUnderItsContext(t *testing.T) {
	var scanned int
	for _, root := range []string{".", filepath.Join("..", "capability")} {
		err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
			if err != nil {
				return err
			}
			if d.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
				return nil
			}
			if strings.Contains(filepath.ToSlash(path), "/testhttp/") || strings.Contains(filepath.ToSlash(path), "/videotest/") {
				return nil // test servers, not senders
			}
			raw, err := os.ReadFile(path)
			if err != nil {
				return err
			}
			scanned++
			for name, re := range dispatchSenderPatterns {
				if !re.Match(raw) {
					continue
				}
				if _, ok := dispatchSenderExceptions[filepath.Base(path)+"|"+name]; ok {
					continue
				}
				t.Errorf("%s uses %s: a provider sender must build its request with the context it is given and send it through net/http", path, name)
			}
			return nil
		})
		if err != nil {
			t.Fatal(err)
		}
	}
	if scanned < 40 {
		t.Fatalf("census scanned %d files: not at the providers package", scanned)
	}
}
