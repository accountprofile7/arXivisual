/**
 * Display paragraphs of a section's text: blank-line separated, never split
 * inside a ``` code fence or a $$ display-math block.
 *
 * MUST stay in sync with backend/ingestion/text_normalize.py `split_paragraphs`
 * (cases pinned by backend/tests/test_split_paragraphs.py and mirrored in
 * scripts/check-paragraphs.mjs). A visual aid's paragraph_index is an index
 * into this list for the text the reader shows.
 */
export function splitParagraphs(text: string | null | undefined): string[] {
  if (!text) return [];
  const tracked = splitBlocks(text, true);
  // A stray $$ or an unclosed fence would merge every later paragraph into
  // one; fall back to plain blank-line splitting so anchors stay usable.
  return tracked.unclosed ? splitBlocks(text, false).paragraphs : tracked.paragraphs;
}

function splitBlocks(
  text: string,
  trackBlocks: boolean
): { paragraphs: string[]; unclosed: boolean } {
  const paragraphs: string[] = [];
  let current: string[] = [];
  let inMath = false;
  let inCode = false;
  for (const line of text.replace(/\r\n/g, "\n").split("\n")) {
    const stripped = line.trim();
    if (!stripped && !inMath && !inCode) {
      if (current.length > 0) {
        paragraphs.push(current.join("\n").trim());
        current = [];
      }
      continue;
    }
    current.push(line);
    if (!trackBlocks) continue;
    if (stripped.startsWith("```")) {
      inCode = !inCode;
    } else if (!inCode) {
      // An odd number of $$ on a line opens or closes a display block.
      if ((stripped.split("$$").length - 1) % 2 === 1) {
        inMath = !inMath;
      }
    }
  }
  if (current.length > 0) paragraphs.push(current.join("\n").trim());
  return { paragraphs: paragraphs.filter(Boolean), unclosed: inMath || inCode };
}
