package chwriter

import (
	"io"
	"net/http"
	"sync"
)

// A RoundTripper may return before its goroutine has finished reading and
// closing the request body. Track that ownership separately from response EOF:
// a completed insert lets the server return its encoding-memory reservation.
// Redirect/replay bodies made through GetBody have the same lifetime rule.
func trackRequestBodies(req *http.Request) func() {
	var owners sync.WaitGroup
	wrap := func(body io.ReadCloser) io.ReadCloser {
		if body == nil || body == http.NoBody {
			return body
		}
		owners.Add(1)
		return &trackedRequestBody{ReadCloser: body, owners: &owners}
	}
	req.Body = wrap(req.Body)
	if getBody := req.GetBody; getBody != nil {
		req.GetBody = func() (io.ReadCloser, error) {
			body, err := getBody()
			if err != nil {
				return nil, err
			}
			return wrap(body), nil
		}
	}
	return owners.Wait
}

type trackedRequestBody struct {
	io.ReadCloser
	owners *sync.WaitGroup
	once   sync.Once
	err    error
}

func (b *trackedRequestBody) Close() error {
	b.once.Do(func() { b.err = b.ReadCloser.Close(); b.owners.Done() })
	return b.err
}
