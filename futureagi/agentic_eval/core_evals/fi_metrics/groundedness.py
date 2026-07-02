from abc import ABC
from typing import TypedDict


class GroundednessEvidence(TypedDict):
    sentence: str
    supporting_evidence: list[str]

class GroundednessScore(ABC):
    """
    Computes the groundedness score.
    """

    @staticmethod
    def compute(sentences_with_evidence: list[GroundednessEvidence]):
        """
        Computes the metric.
        """
        unsupported_sentences: list[str] = [] # List of unsupported sentences
        supported_sentences: list[tuple[str, list[str]]] = [] # List of (sentence, evidences) pairs
        for sentence_with_evidence in sentences_with_evidence:
            sentence_str = sentence_with_evidence.get('sentence')
            supported_evidence_for_sentence = sentence_with_evidence.get('supporting_evidence', [])
            if sentence_str is None:
                continue
            if len(supported_evidence_for_sentence) != 0:
                supported_sentences.append((sentence_str, supported_evidence_for_sentence))
            else:
                unsupported_sentences.append(sentence_str)
        num_supported_sentences = len(supported_sentences)
        # Score over the sentences that were actually classified. Skipped
        # (sentence is None) entries were dropped above, so counting the raw
        # input length would wrongly lower the score, and an empty input would
        # divide by zero. A response with no evaluable sentences is treated as
        # fully grounded (nothing is unsupported).
        num_classified_sentences = num_supported_sentences + len(unsupported_sentences)
        if num_classified_sentences == 0:
            score = 1.0
        else:
            score = num_supported_sentences / num_classified_sentences
        precision = 4
        score = round(score, precision)
        return score, unsupported_sentences, supported_sentences
