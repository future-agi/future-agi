// The trace-hygiene fixture flow in Chromium with tracing on (trace-hygiene-flow.ts).
import { test } from '../lib/scope-actors';
import { liveTraceHygiene } from './trace-hygiene-flow';

test.use({ browserName: 'chromium', trace: 'on' });
liveTraceHygiene('chromium');
