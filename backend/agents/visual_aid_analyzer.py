"""Visual Aid Analyzer Agent - Picks paragraph-anchored concepts for short silent loops."""

import sys
from collections.abc import Iterable
from pathlib import Path

# Handle both package and direct imports
try:
    from ..ingestion.text_normalize import normalize_display_text, split_paragraphs
    from ..models.generation import VisualAidCandidate, VisualizationType
    from ..models.paper import Section
    from .base import BaseAgent
except ImportError:
    # Add parent to path for direct execution
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from agents.base import BaseAgent
    from ingestion.text_normalize import normalize_display_text, split_paragraphs
    from models.generation import VisualAidCandidate, VisualizationType
    from models.paper import Section


def display_paragraphs(section: Section) -> list[str]:
    """Paragraphs of the text the reader shows for a section.

    Must match the frontend's indexing: the summary when present, else the
    content, normalized, then split into paragraphs.
    """
    text = normalize_display_text(section.summary) if section.summary else normalize_display_text(section.content)
    return split_paragraphs(text)


def _concept_tokens(name: str) -> frozenset[str]:
    # Imported lazily: agents.pipeline imports the analyzers.
    from .pipeline import normalize_concept

    return frozenset(normalize_concept(name).split())


class VisualAidAnalyzer(BaseAgent):
    """
    Chooses concepts in a section that a short silent looping animation would clarify.

    Each aid is anchored to one displayed paragraph (by index) and carries 2-4
    storyboard beats that end in a state that loops back to the start.
    """

    def __init__(self, model: str | None = None):
        super().__init__("visual_aid_analyzer.md", model=model, system_prompt_file="system/json_analyst.md")

    def _format_equations(self, section: Section) -> str:
        """Format equations for the prompt."""
        if not section.equations:
            return "No equations in this section."

        equations_text = []
        for eq in section.equations:
            eq_str = f"- LaTeX: {eq.latex}"
            if eq.context:
                eq_str += f"\n  Context: {eq.context}"
            equations_text.append(eq_str)

        return "\n".join(equations_text)

    async def run(
        self,
        paper_title: str,
        paper_abstract: str,
        section: Section,
        *,
        max_aids: int = 3,
        skip_concepts: Iterable[str] = (),
    ) -> list[VisualAidCandidate]:
        """
        Identify visual aid candidates for a section.

        Args:
            paper_title: Title of the paper
            paper_abstract: Abstract for context
            section: The section to analyze
            max_aids: Maximum number of aids to return
            skip_concepts: Concept names already covered by narrated videos

        Returns:
            Candidates in LLM order, at most max_aids (empty if the section has no text).
        """
        paragraphs = display_paragraphs(section)
        if not paragraphs or max_aids <= 0:
            return []

        skip = [s for s in skip_concepts if s]
        prompt = self._format_prompt(
            paper_title=paper_title,
            paper_abstract=paper_abstract,
            section_id=section.id,
            section_title=section.title,
            paragraphs="\n\n".join(f"[{i}] {p}" for i, p in enumerate(paragraphs)),
            equations=self._format_equations(section),
            skip_concepts="\n".join(f"- {s}" for s in skip) if skip else "None.",
            max_aids=max_aids,
        )

        result = await self._call_llm_json(prompt)
        return self._parse_result(result, section.id, len(paragraphs), max_aids, skip)

    def _parse_result(
        self,
        result: dict,
        section_id: str,
        n_paragraphs: int,
        max_aids: int,
        skip_concepts: Iterable[str] = (),
    ) -> list[VisualAidCandidate]:
        """Parse the LLM response, dropping malformed and duplicate aids."""
        seen = [t for t in (_concept_tokens(s) for s in skip_concepts) if t]
        aids: list[VisualAidCandidate] = []

        raw_aids = result.get("aids", []) if isinstance(result, dict) else []
        for data in raw_aids if isinstance(raw_aids, list) else []:
            if len(aids) >= max_aids:
                break
            if not isinstance(data, dict):
                continue

            idx = data.get("paragraph_index")
            if isinstance(idx, bool) or not isinstance(idx, int) or not 0 <= idx < n_paragraphs:
                continue

            name = str(data.get("concept_name") or "Unknown Concept")
            tokens = _concept_tokens(name)
            # Same near-duplicate rule as pipeline._dedupe_candidates
            if tokens and any(len(tokens & o) / len(tokens | o) >= 0.6 for o in seen):
                continue

            try:
                viz_type = VisualizationType(data.get("visualization_type", "equation"))
            except ValueError:
                viz_type = VisualizationType.EQUATION

            raw_beats = data.get("beats")
            beats = [str(b).strip() for b in raw_beats if b is not None] if isinstance(raw_beats, list) else []

            aids.append(
                VisualAidCandidate(
                    section_id=section_id,  # Always use the actual section ID, not LLM-generated one
                    concept_name=name,
                    concept_description=str(data.get("concept_description") or ""),
                    visualization_type=viz_type,
                    paragraph_index=idx,
                    beats=[b for b in beats if b],
                    context=str(data.get("context") or ""),
                )
            )
            if tokens:
                seen.append(tokens)

        return aids
