// The trace-hygiene fixture flow in Firefox with tracing on (trace-hygiene-flow.ts).
import { test } from '../lib/scope-actors';
import { liveTraceHygiene } from './trace-hygiene-flow';

test.use({ browserName: 'firefox', trace: 'on' });
liveTraceHygiene('firefox');
