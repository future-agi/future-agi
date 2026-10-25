package capmock

import (
	"fmt"
	"math"
	"net/http"
	"strconv"
	"strings"
	"time"
)

// BehaviorHeader carries a call's directives: comma-separated name[:arg]
// tokens. A call without it takes the directives armed for its fake
// (POST /__arm), because a gateway forwards no test header upstream.
const BehaviorHeader = "x-capmock-behavior"

// behavior is a call's parsed directives.
//
//	status:<code>      answer <code> with a vendor-shaped error (429 adds
//	                   Retry-After: 1); with fail_after, the code failing calls get
//	delay_ms:<n>       wait n ms before answering
//	fail_after:<n>     the fake's first n calls (since /__reset or its /__arm)
//	                   succeed; later ones fail with status (default 500)
//	report_cost:<usd>  the response reports this cost where the vendor does
//	                   (refused for a fake whose vendor reports none)
//	job_delay:<ms>     an async job takes ms to finish (default 0)
//	job_fail_at:<s>    an async job ends in its failure status: failed, or
//	                   the vendor's own failure word (assemblyai: error)
//	url_expiry:<s>     file URLs this call returns expire after s seconds
//	drop_response      close the connection without answering
type behavior struct {
	status     int
	delay      time.Duration
	failAfter  int // -1: unset
	reportCost *float64
	jobDelay   time.Duration
	jobFailAt  string
	urlExpiry  time.Duration
	drop       bool
}

// directiveError refuses a call whose directives cannot be applied; the
// mock answers it 400 invalid_capmock_directive.
type directiveError struct{ msg string }

func (e *directiveError) Error() string { return e.msg }

func badDirective(tok, why string) error {
	return &directiveError{fmt.Sprintf("%s directive %q: %s", BehaviorHeader, tok, why)}
}

// parseBehavior parses a directive list for fake f.
func parseBehavior(v string, f *fake) (behavior, error) {
	b := behavior{failAfter: -1}
	for _, tok := range strings.Split(v, ",") {
		tok = strings.TrimSpace(tok)
		if tok == "" {
			continue
		}
		name, arg, hasArg := strings.Cut(tok, ":")
		needArg := func() (int, error) {
			n, err := strconv.Atoi(arg)
			if !hasArg || err != nil || n < 0 {
				return 0, badDirective(tok, "needs a non-negative integer")
			}
			return n, nil
		}
		var err error
		var n int
		switch name {
		case "status":
			if n, err = needArg(); err == nil && (n < 400 || n > 599) {
				err = badDirective(tok, "the code must be 400-599")
			}
			b.status = n
		case "delay_ms":
			n, err = needArg()
			b.delay = time.Duration(n) * time.Millisecond
		case "fail_after":
			n, err = needArg()
			b.failAfter = n
		case "job_delay":
			n, err = needArg()
			b.jobDelay = time.Duration(n) * time.Millisecond
		case "url_expiry":
			n, err = needArg()
			b.urlExpiry = time.Duration(n) * time.Second
		case "report_cost":
			usd, perr := strconv.ParseFloat(arg, 64)
			switch {
			case !hasArg || perr != nil || usd < 0 || math.IsNaN(usd) || math.IsInf(usd, 0):
				err = badDirective(tok, "needs a USD amount >= 0")
			case f.costPath == "":
				err = badDirective(tok, f.name+"'s vendor reports no cost in its response")
			}
			b.reportCost = &usd
		case "job_fail_at":
			switch {
			case f.failWord == "":
				err = badDirective(tok, f.name+" has no async job")
			case arg != "failed" && arg != f.failWord:
				err = badDirective(tok, "the failure status is failed or "+f.failWord)
			}
			b.jobFailAt = arg
		case "drop_response":
			if hasArg {
				err = badDirective(tok, "takes no argument")
			}
			b.drop = true
		default:
			err = badDirective(tok, "unknown directive")
		}
		if err != nil {
			return behavior{}, err
		}
	}
	return b, nil
}

// failing reports whether the fake's n-th call fails, and with which code.
func (b behavior) failing(n int) (int, bool) {
	if b.failAfter >= 0 {
		if n <= b.failAfter {
			return 0, false
		}
		if b.status == 0 {
			return http.StatusInternalServerError, true
		}
		return b.status, true
	}
	return b.status, b.status != 0
}
