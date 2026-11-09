// Django initializes SDK background threads even for `manage.py shell`.
// As temporal-probe.ts does, end only this one-shot fixture interpreter after
// the body (including transaction commit and synchronous pushes) finishes.
// Failures retain a nonzero status; never turn an exception into a success.
export function djangoFixtureCommand(body: string): string {
  return ['import os, sys, traceback', 'try:', ...body.trim().split('\n').map(line => `    ${line}`),
    'except BaseException:', '    traceback.print_exc()', '    sys.stdout.flush()', '    sys.stderr.flush()',
    '    os._exit(1)', 'sys.stdout.flush()', 'sys.stderr.flush()', 'os._exit(0)'].join('\n');
}
