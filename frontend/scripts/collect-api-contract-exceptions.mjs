import { parse } from "@babel/parser";
import traverseModule from "@babel/traverse";

const traverse = traverseModule.default ?? traverseModule;

function staticStringValue(node) {
  if (node?.type === "StringLiteral") return node.value;
  if (node?.type === "TemplateLiteral" && node.expressions.length === 0) {
    return node.quasis[0]?.value?.cooked || node.quasis[0]?.value?.raw || "";
  }
  return "";
}

// Inspect actual helper calls; a string, comment or manifest entry is not use.
// Registry mode retains the existing endpoints-only boundary in axios.js.
export function collectContractExceptionCalls(
  source,
  { registryOnly = false } = {},
) {
  const ast = parse(source, {
    sourceType: "module",
    plugins: ["jsx", "typescript"],
  });
  const calls = [];
  traverse(ast, {
    CallExpression(nodePath) {
      if (nodePath.node.callee?.type !== "Identifier") return;
      if (nodePath.node.callee.name !== "uncontractedApiPath") return;
      if (
        registryOnly &&
        !nodePath.findParent(
          (parent) =>
            parent.isVariableDeclarator() &&
            parent.node.id?.type === "Identifier" &&
            parent.node.id.name === "endpoints",
        )
      )
        return;

      const [templateArg, secondArg, thirdArg] = nodePath.node.arguments;
      calls.push({
        value: staticStringValue(templateArg),
        inlineReason: Boolean(staticStringValue(thirdArg || secondArg)),
        line:
          templateArg?.loc?.start?.line || nodePath.node.loc?.start?.line || 1,
      });
    },
  });
  return calls;
}
