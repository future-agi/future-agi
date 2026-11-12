// Package replay serves cassettes: MCP answers keyed by (tool, normalized
// arguments), HTTP responses keyed by (method, path, query, body sha256).
// It holds no HTTP client and dials nothing, so a replay makes no network
// call whatever it is asked; a request no recording answers fails loudly.
package replay

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"sync"
	"sync/atomic"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-cassette/cassette"
)

// answer is one recorded reply.
type answer struct {
	result, rpcErr json.RawMessage // MCP
	status         int             // MCP: the HTTP status, 0 for 200
	resp           *cassette.HTTPResponse
}

// sequence is every reply recorded for one key, in recorded order. Replay
// serves them in turn and then keeps serving the last (a client re-listing
// tools, or retrying a call, gets the final state).
type sequence struct {
	answers []answer
	next    int
	from    int // the cassette that recorded the key
}

// Player answers from recordings only.
type Player struct {
	kind      cassette.Kind
	exchanges int
	log       *log.Logger
	keys      *cassette.Keyer // one Keyer for stored and live keys: the built-in names plus every cassette's Rules

	mu       sync.Mutex
	recorded map[string]*sequence
	misses   []string
	sessions atomic.Int64
}

// New indexes cassettes, all of one kind. A key two cassettes both record,
// with different replies, is ambiguous and refused. Every key, stored or
// live, blinds the credential names of all the cassettes together.
func New(cs []*cassette.Cassette, logger *log.Logger) (*Player, error) {
	if len(cs) == 0 {
		return nil, errors.New("replay needs at least one cassette")
	}
	p := &Player{kind: cs[0].Kind, log: logger, keys: cassette.NewKeyer(cs...), recorded: map[string]*sequence{}}
	for i, c := range cs {
		if err := c.Validate(); err != nil {
			return nil, fmt.Errorf("cassette %d: %w", i, err)
		}
		if c.Kind != p.kind {
			return nil, fmt.Errorf("cassette %d is %s, the first is %s: one kind per replay", i, c.Kind, p.kind)
		}
		if err := p.index(i, c); err != nil {
			return nil, fmt.Errorf("cassette %d: %w", i, err)
		}
	}
	return p, nil
}

func (p *Player) index(from int, c *cassette.Cassette) error {
	if c.MCP != nil {
		for _, x := range c.MCP.Exchanges {
			key, err := p.keys.MCPKey(x.Method, x.Params)
			if err != nil {
				return err
			}
			if err := p.add(from, key, answer{result: x.Result, rpcErr: x.Error, status: x.HTTPStatus}); err != nil {
				return fmt.Errorf("%s: %w", p.keys.DescribeMCP(x.Method, x.Params), err)
			}
		}
	}
	if c.HTTP != nil {
		for i := range c.HTTP.Exchanges {
			x := &c.HTTP.Exchanges[i]
			body, err := x.Request.Body.Bytes()
			if err != nil {
				return err
			}
			ct := cassette.HeaderValue(x.Request.Headers, "Content-Type")
			key := p.keys.HTTPKey(x.Request.Method, x.Request.Path, x.Request.Query, ct, body)
			if err := p.add(from, key, answer{resp: &x.Response}); err != nil {
				return fmt.Errorf("%s: %w", p.keys.DescribeHTTP(x.Request.Method, x.Request.Path, x.Request.Query, ct, body), err)
			}
		}
	}
	return nil
}

func (p *Player) add(from int, key string, a answer) error {
	p.exchanges++
	s, ok := p.recorded[key]
	switch {
	case !ok:
		p.recorded[key] = &sequence{answers: []answer{a}, from: from}
	case s.from == from:
		s.answers = append(s.answers, a)
	case !sameAnswers(s.answers, a):
		return errors.New("recorded by two cassettes with different replies")
	}
	return nil
}

func sameAnswers(have []answer, a answer) bool {
	if len(have) != 1 {
		return false
	}
	x, err1 := json.Marshal(have[0].view())
	y, err2 := json.Marshal(a.view())
	return err1 == nil && err2 == nil && bytes.Equal(x, y)
}

func (a answer) view() any {
	return struct {
		R, E json.RawMessage
		S    int
		H    *cassette.HTTPResponse
	}{a.result, a.rpcErr, a.status, a.resp}
}

// next is the reply for key, or false when nothing was recorded for it.
func (p *Player) next(key string) (answer, bool) {
	p.mu.Lock()
	defer p.mu.Unlock()
	s, ok := p.recorded[key]
	if !ok {
		return answer{}, false
	}
	a := s.answers[s.next]
	if s.next < len(s.answers)-1 {
		s.next++
	}
	return a, true
}

// miss records and logs a request no recording answers.
func (p *Player) miss(what string) {
	p.mu.Lock()
	p.misses = append(p.misses, what)
	p.mu.Unlock()
	p.log.Printf("MISS: no recorded reply for %s", what)
}

// Misses lists the requests no recording answered, without their values.
func (p *Player) Misses() []string {
	p.mu.Lock()
	defer p.mu.Unlock()
	return append([]string(nil), p.misses...)
}
