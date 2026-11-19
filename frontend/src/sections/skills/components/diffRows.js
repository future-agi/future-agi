import { diffLines, diffWordsWithSpace } from 'diff';

const lines = (text) => {
  const values = text.split('\n');
  if (values.at(-1) === '') values.pop();
  return values;
};

// Generalizes PromptDiffView's aligned rows without its prompt-specific panels.
// Bound pathological diffs; a full removal/addition is still lossless when the
// edit-distance budget is exceeded. Each pair shares one CSS grid row height.
export function buildDiffRows(fromText = '', toText = '') {
  const changes = diffLines(fromText, toText, { maxEditLength: 2000, timeout: 150 }) || [
    { value: fromText, removed: true }, { value: toText, added: true },
  ];
  let leftNumber = 0;
  let rightNumber = 0;
  const rows = [];
  const side = (text, status, left, parts) => text === undefined ? null : {
    text, status, number: left ? ++leftNumber : ++rightNumber, parts,
  };
  for (let i = 0; i < changes.length; i += 1) {
    const change = changes[i];
    if (change.removed && changes[i + 1]?.added) {
      const before = lines(change.value);
      const after = lines(changes[++i].value);
      for (let j = 0; j < Math.max(before.length, after.length); j += 1) {
        const words = before[j] !== undefined && after[j] !== undefined
          ? diffWordsWithSpace(before[j], after[j], { maxEditLength: 500, timeout: 10 }) : null;
        rows.push({ left: side(before[j], 'removed', true, words?.filter((word) => !word.added)), right: side(after[j], 'added', false, words?.filter((word) => !word.removed)) });
      }
    } else {
      for (const text of lines(change.value)) {
        rows.push({ left: change.added ? null : side(text, change.removed ? 'removed' : 'unchanged', true), right: change.removed ? null : side(text, change.added ? 'added' : 'unchanged', false) });
      }
    }
  }
  return rows;
}
