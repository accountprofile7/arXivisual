// Dependency-free check that lib/paragraphs.ts matches the backend rule.
// Mirrors backend/tests/test_split_paragraphs.py. Run from frontend/:
//   node scripts/check-paragraphs.mjs        (Node >= 22.18 strips types natively)
import assert from "node:assert/strict";
import { splitParagraphs } from "../lib/paragraphs.ts";

const cases = [
  ["splits on blank lines and strips",
    "First para.\n\n  Second para.  \n\n\nThird.", ["First para.", "Second para.", "Third."]],
  ["single newlines stay in one paragraph",
    "- item one\n- item two\n\nAfter the list.", ["- item one\n- item two", "After the list."]],
  ["display math block with blank line is not split",
    "Intro.\n\n$$\na = b\n\nc = d\n$$\n\nOutro.", ["Intro.", "$$\na = b\n\nc = d\n$$", "Outro."]],
  ["code fence with blank line is not split",
    "Intro.\n\n```\nx = 1\n\ny = 2\n```\n\nOutro.", ["Intro.", "```\nx = 1\n\ny = 2\n```", "Outro."]],
  ["empty text", "", []],
  ["whitespace-only text", "  \n\n \n", []],
  ["whitespace-only lines count as blank", "A.\n   \nB.", ["A.", "B."]],
  ["windows line endings", "A.\r\n\r\nB.\r\nstill B.", ["A.", "B.\nstill B."]],
  ["display math opened and closed on text lines",
    "Eq: $$\na\n\nb\n$$ done.\n\nNext.", ["Eq: $$\na\n\nb\n$$ done.", "Next."]],
  ["inline pairs do not open a block",
    "$$a$$ and $$b$$\n\nNext.", ["$$a$$ and $$b$$", "Next."]],
  ["unclosed $$ falls back to blank-line split", "Open $$ x\n\nA\n\nB", ["Open $$ x", "A", "B"]],
  ["unclosed fence falls back to blank-line split", "```\ncode\n\nA", ["```\ncode", "A"]],
  ["$$ inside code is ignored", "```\n$$\n```\n\nNext.", ["```\n$$\n```", "Next."]],
];

for (const [name, input, expected] of cases) {
  assert.deepEqual(splitParagraphs(input), expected, name);
}
assert.deepEqual(splitParagraphs(null), []);
assert.deepEqual(splitParagraphs(undefined), []);
console.log(`check-paragraphs: ${cases.length + 2} checks passed`);
