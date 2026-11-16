import ts from 'typescript';

// Parse TypeScript rather than balancing '<' in a regex: generic arguments may
// contain nested types, function arrows, comments and newlines. Inspect the
// argument AST boundary, not the next ');' (which can occur inside a string).
export function ownerApiCalls(source) {
  const file = ts.createSourceFile('typesafe-live.spec.ts', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  if (file.parseDiagnostics.length) throw new Error('TypeSafe spec guard cannot parse source');
  const calls = [];
  function visit(node) {
    if (ts.isCallExpression(node)) {
      const access = node.expression;
      if (ts.isPropertyAccessExpression(access) || ts.isElementAccessExpression(access)) {
        const receiver = access.expression;
        if (ts.isPropertyAccessExpression(receiver) && receiver.name.text === 'api'
          && ts.isIdentifier(receiver.expression) && receiver.expression.text === 'owner') {
          calls.push({
            method: ts.isPropertyAccessExpression(access) ? access.name.text : access.argumentExpression.text,
            arguments: node.arguments.map(arg => arg.getText(file)).join(', '),
            start: node.getStart(file), end: node.end,
          });
        }
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(file);
  return calls;
}

export function assertNoTypeSafeKey(source) {
  for (const call of ownerApiCalls(source)) {
    if (/\bTYPESAFE_KEY\b/.test(call.arguments)) throw new Error('a Playwright request carries the TypeSafe key');
  }
}
