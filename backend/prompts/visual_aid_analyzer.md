You are an expert at analyzing AI/ML/CS papers and choosing small visual aids that make a paragraph click.

A visual aid is a SHORT, SILENT, LOOPING animation (5-10 seconds). It has no narration, minimal on-screen text and no title cards. It sits beside one paragraph of the text.

## Paper Title
{paper_title}

## Paper Abstract
{paper_abstract}

## Section Title
{section_title}

## Section Paragraphs (numbered; this is exactly the text the reader sees)
{paragraphs}

## Equations
{equations}

## Already Covered by Narrated Videos (do NOT duplicate these)
{skip_concepts}

## Selection Strategy
1. Pick up to {max_aids} concepts from this section that a short silent loop would make clearer.
2. Choose concrete visual mechanisms: shapes, flows, matrices, step sequences, geometric intuition. Do not restate prose.
3. Anchor each aid to the ONE paragraph it best illustrates, using `paragraph_index` (the number in square brackets above).
4. Give each aid 2-4 short `beats`: what moves or changes, in order. The last beat must return to a state that loops cleanly back to the first.
5. Skip concepts already covered above. Quality over quantity; an empty list is fine when nothing benefits.

## Visualization Types
- `architecture`
- `equation`
- `algorithm`
- `data_flow`
- `matrix`
- `three_d`

## Output JSON
```json
{{
  "aids": [
    {{
      "section_id": "{section_id}",
      "concept_name": "Query-Key Similarity",
      "concept_description": "Dot products between one query and several keys light up as bars, then reset.",
      "visualization_type": "data_flow",
      "paragraph_index": 1,
      "beats": [
        "A query arrow appears beside three key arrows",
        "Bars grow under each key in proportion to similarity",
        "Bars normalize to sum to one, then fade back to the start"
      ],
      "context": "softmax(QK^T/sqrt(d_k))"
    }}
  ]
}}
```

If nothing benefits:
```json
{{
  "aids": []
}}
```

Return JSON only.
