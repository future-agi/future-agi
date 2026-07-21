from agentic_eval.core_evals.fi_evals import FunctionEvaluator
from agentic_eval.core_evals.fi_evals.eval_type import FunctionEvalTypeId


class ContainsAny(FunctionEvaluator):
    def __init__(
        self,
        keywords: list[str],
        case_sensitive: bool | None = False,
        display_name: str | None = None,
    ):
        """
        Initialize the ContainsAny function evaluator.

        Args:
            keywords (List[str]): List of keywords to check for in the text.
            case_sensitive (Optional[bool], optional): Whether the keyword matching should be case sensitive. Defaults to False.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.CONTAINS_ANY.value,
            function_arguments={"keywords": keywords, "case_sensitive": case_sensitive},
            display_name=display_name,
        )


class Regex(FunctionEvaluator):
    def __init__(
        self,
        pattern: str,
        display_name: str | None = None,
    ):
        """
        Initialize the Regex function evaluator.

        Args:
            pattern (str): The regular expression pattern to be matched in the text.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.REGEX.value,
            function_arguments={"pattern": pattern},
            display_name=display_name,
        )


class ContainsNone(FunctionEvaluator):
    def __init__(
        self,
        keywords: list[str],
        case_sensitive: bool = False,
        display_name: str | None = None,
    ):
        """
        Initialize the ContainsNone function evaluator.

        Args:
            keywords (str or List[str]): The keyword(s) to search for in the text.
            case_sensitive (bool, optional): If True, the comparison is case-sensitive. Defaults to False.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.CONTAINS_NONE.value,
            function_arguments={
                "keywords": keywords,
                "case_sensitive": case_sensitive,
            },
            display_name=display_name,
        )


class Contains(FunctionEvaluator):
    def __init__(
        self,
        keyword: str,
        case_sensitive: bool = False,
        display_name: str | None = None,
    ):
        """
        Initialize the Contains function evaluator.

        Args:
            keyword (str): The keyword to search for in the text.
            case_sensitive (bool, optional): If True, the comparison is case-sensitive. Defaults to False.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.CONTAINS.value,
            function_arguments={
                "keyword": keyword,
                "case_sensitive": case_sensitive,
            },
            display_name=display_name,
        )


class ContainsAll(FunctionEvaluator):
    def __init__(
        self,
        keywords: list[str],
        case_sensitive: bool = False,
        display_name: str | None = None,
    ):
        """
        Initialize the ContainsAll function evaluator.

        Args:
            keywords (List[str]): The list of keywords to search for in the text.
            case_sensitive (bool, optional): If True, the comparison is case-sensitive. Defaults to False.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.CONTAINS_ALL.value,
            function_arguments={
                "keywords": keywords,
                "case_sensitive": case_sensitive,
            },
            display_name=display_name,
        )


class BleuScore(FunctionEvaluator):
    def __init__(
        self,
        display_name: str | None = None,
    ):
        """
        Initialize the ContainsAll function evaluator.

        Args:
            keywords (List[str]): The list of keywords to search for in the text.
            case_sensitive (bool, optional): If True, the comparison is case-sensitive. Defaults to False.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.BLEU_SCORE.value,
            function_arguments={},
            display_name=display_name,
        )


class RougeScore(FunctionEvaluator):
    def __init__(
        self,
        display_name: str | None = None,
    ):
        """
        Initialize the RougeScore function evaluator.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.ROUGE_SCORE.value,
            function_arguments={},
            display_name=display_name,
        )


class FidScore(FunctionEvaluator):
    def __init__(
        self,
        display_name: str | None = None,
    ):
        """
        Initialize the FidScore (Frechet Inception Distance) function evaluator.

        FID measures the similarity between two distributions of images.
        Lower scores indicate more similar distributions.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.FID_SCORE.value,
            function_arguments={},
            display_name=display_name,
        )


class ClipScore(FunctionEvaluator):
    def __init__(
        self,
        display_name: str | None = None,
    ):
        """
        Initialize the ClipScore function evaluator.

        CLIP Score measures how well images match their text descriptions.
        Higher scores indicate better alignment between images and text (range: 0-100).

        Uses the existing CLIP-based image-text embedding service.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.CLIP_SCORE.value,
            function_arguments={},
            display_name=display_name,
        )


class RecallScore(FunctionEvaluator):
    def __init__(
        self,
        display_name: str | None = None,
    ):
        """
        Initialize the ContainsAll function evaluator.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.RECALL_SCORE.value,
            function_arguments={},
            display_name=display_name,
        )


class RecallAtK(FunctionEvaluator):
    def __init__(self, k: int | None = None, display_name: str | None = None):
        """Initialize the RecallAtK function evaluator."""
        super().__init__(
            function_name=FunctionEvalTypeId.RECALL_AT_K.value,
            function_arguments={"k": k},
            display_name=display_name,
        )


class PrecisionAtK(FunctionEvaluator):
    def __init__(self, k: int | None = None, display_name: str | None = None):
        """Initialize the PrecisionAtK function evaluator."""
        super().__init__(
            function_name=FunctionEvalTypeId.PRECISION_AT_K.value,
            function_arguments={"k": k},
            display_name=display_name,
        )


class NdcgAtK(FunctionEvaluator):
    def __init__(self, k: int | None = None, display_name: str | None = None):
        """Initialize the NdcgAtK function evaluator."""
        super().__init__(
            function_name=FunctionEvalTypeId.NDCG_AT_K.value,
            function_arguments={"k": k},
            display_name=display_name,
        )


class Mrr(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """Initialize the Mrr function evaluator."""
        super().__init__(
            function_name=FunctionEvalTypeId.MRR.value,
            function_arguments={},
            display_name=display_name,
        )


class HitRate(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """Initialize the HitRate function evaluator."""
        super().__init__(
            function_name=FunctionEvalTypeId.HIT_RATE.value,
            function_arguments={},
            display_name=display_name,
        )


class LevenshteinSimilarity(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """
        Initialize the LevenshteinSimilarity function evaluator.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.LEVENSHTEIN_SIMILARITY.value,
            function_arguments={},
            display_name=display_name,
        )


class NumericSimilarity(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """
        Initialize the NumericSimilarity function evaluator.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.NUMERIC_SIMILARITY.value,
            function_arguments={},
            display_name=display_name,
        )


class EmbeddingSimilarity(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """
        Initialize the EmbeddingSimilarity function evaluator.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.EMBEDDING_SIMILARITY.value,
            function_arguments={},
            display_name=display_name,
        )


class SemanticListContains(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """
        Initialize the SemanticListContains function evaluator.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.SEMANTIC_LIST_CONTAINS.value,
            function_arguments={},
            display_name=display_name,
        )


class ContainsJson(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """
        Initialize the ContainsJson function evaluator.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.CONTAINS_JSON.value,
            function_arguments={},
        )


class ContainsEmail(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """
        Initialize the ContainsEmail function evaluator.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.CONTAINS_EMAIL.value,
            function_arguments={},
            display_name=display_name,
        )


class IsJson(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """
        Initialize the IsJson function evaluator.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.IS_JSON.value,
            function_arguments={},
            display_name=display_name,
        )


class IsEmail(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """
        Initialize the IsEmail function evaluator.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.IS_EMAIL.value,
            function_arguments={},
            display_name=display_name,
        )


class NoInvalidLinks(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """
        Initialize the NoInvalidLinks function evaluator.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.NO_INVALID_LINKS.value,
            function_arguments={},
            display_name=display_name,
        )


class ContainsLink(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """
        Initialize the ContainsLink function evaluator.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.CONTAINS_LINK.value,
            function_arguments={},
            display_name=display_name,
        )


class ContainsValidLink(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """
        Initialize the ContainsValidLink function evaluator.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.CONTAINS_VALID_LINK.value,
            function_arguments={},
            display_name=display_name,
        )


class Equals(FunctionEvaluator):
    def __init__(
        self,
        case_sensitive: bool = False,
        display_name: str | None = None,
    ):
        """
        Initialize the Equals function evaluator.

        Args:
            case_sensitive (bool, optional): If True, the comparison is case-sensitive. Defaults to False.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.EQUALS.value,
            function_arguments={
                "case_sensitive": case_sensitive,
            },
            display_name=display_name,
        )


class StartsWith(FunctionEvaluator):
    def __init__(
        self,
        substring: str,
        case_sensitive: bool = False,
        display_name: str | None = None,
    ):
        """
        Initialize the StartsWith function evaluator.

        Args:
            substring (str): The substring to check for at the start of the text.
            case_sensitive (bool, optional): If True, the comparison is case-sensitive. Defaults to False.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.STARTS_WITH.value,
            function_arguments={
                "substring": substring,
                "case_sensitive": case_sensitive,
            },
            display_name=display_name,
        )


class EndsWith(FunctionEvaluator):
    def __init__(
        self,
        substring: str,
        case_sensitive: bool = False,
        display_name: str | None = None,
    ):
        """
        Initialize the EndsWith function evaluator.

        Args:
            substring (str): The substring to check for at the end of the text.
            case_sensitive (bool, optional): If True, the comparison is case-sensitive. Defaults to False.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.ENDS_WITH.value,
            function_arguments={
                "substring": substring,
                "case_sensitive": case_sensitive,
            },
            display_name=display_name,
        )


class LengthLessThan(FunctionEvaluator):
    def __init__(self, max_length: int, display_name: str | None = None):
        """
        Initialize the LengthLessThan function evaluator.

        Args:
            max_length (int): The maximum length that the text should have.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.LENGTH_LESS_THAN.value,
            function_arguments={
                "max_length": max_length,
            },
            display_name=display_name,
        )


class LengthGreaterThan(FunctionEvaluator):
    def __init__(self, min_length: int, display_name: str | None = None):
        """
        Initialize the LengthGreaterThan function evaluator.

        Args:
            min_length (int): The minimum length that the text should have.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.LENGTH_GREATER_THAN.value,
            function_arguments={
                "min_length": min_length,
            },
            display_name=display_name,
        )


class ApiCall(FunctionEvaluator):
    def __init__(
        self,
        url: str,
        payload: dict | None = None,
        headers: dict | None = None,
        display_name: str | None = None,
    ):
        """
        Initialize the ApiCall function evaluator.

        Args:
            url (str): The URL to make the API call to.
            payload (dict): The payload to be sent in the API call. response, query, context, expected_response will be added to the payload.
            headers (dict, optional): The headers to be included in the API call. Defaults to None.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.API_CALL.value,
            function_arguments={
                "url": url,
                "payload": payload,
                "headers": headers,
            },
            display_name=display_name,
        )


class LengthBetween(FunctionEvaluator):
    def __init__(
        self, min_length: int, max_length: int, display_name: str | None = None
    ):
        """
        Initialize the LengthBetween function evaluator.

        Args:
            min_length (int): The minimum length that the text should have.
            max_length (int): The maximum length that the text should have.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.LENGTH_BETWEEN.value,
            function_arguments={
                "min_length": min_length,
                "max_length": max_length,
            },
            display_name=display_name,
        )


class OneLine(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """
        Initialize the OneLine function evaluator.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.ONE_LINE.value,
            function_arguments={},
            display_name=display_name,
        )


class CustomCodeEval(FunctionEvaluator):
    def __init__(self, code: str, display_name: str | None = None):
        """
        Initialize the Custom code evaluator.

        Args:
            code (str): The custom code to be executed.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.CUSTOM_CODE_EVAL.value,
            function_arguments={
                "code": code,
            },
            display_name=display_name,
        )


class JsonSchema(FunctionEvaluator):
    def __init__(self, schema: str, display_name: str | None = None):
        """
        Initialize the JsonSchema function evaluator.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.JSON_SCHEMA.value,
            function_arguments={"schema": schema},
            display_name=display_name,
        )


class JsonValidation(FunctionEvaluator):
    def __init__(self, validations=None, display_name: str | None = None):
        """
        Initialize the JsonValidation function evaluator.
        """
        super().__init__(
            function_name=FunctionEvalTypeId.JSON_VALIDATION.value,
            function_arguments={"validations": validations},
            display_name=display_name,
        )


class MeteorScore(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """Initialize the MeteorScore evaluator. Computes METEOR metric with stemming."""
        super().__init__(
            function_name=FunctionEvalTypeId.METEOR_SCORE.value,
            function_arguments={},
            display_name=display_name,
        )


class GleuScore(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """Initialize the GleuScore evaluator. Computes Google BLEU (sentence-level)."""
        super().__init__(
            function_name=FunctionEvalTypeId.GLEU_SCORE.value,
            function_arguments={},
            display_name=display_name,
        )


class ChrfScore(FunctionEvaluator):
    def __init__(self, n: int = 6, beta: float = 2.0, display_name: str | None = None):
        """Initialize the ChrfScore evaluator. Computes character n-gram F-score."""
        super().__init__(
            function_name=FunctionEvalTypeId.CHRF_SCORE.value,
            function_arguments={"n": n, "beta": beta},
            display_name=display_name,
        )


class F1Score(FunctionEvaluator):
    def __init__(self, case_insensitive: bool = True, display_name: str | None = None):
        """Initialize the F1Score evaluator. Computes token-level F1."""
        super().__init__(
            function_name=FunctionEvalTypeId.F1_SCORE.value,
            function_arguments={"case_insensitive": case_insensitive},
            display_name=display_name,
        )


class JaccardSimilarity(FunctionEvaluator):
    def __init__(self, case_insensitive: bool = True, display_name: str | None = None):
        """Initialize the JaccardSimilarity evaluator. Token set overlap similarity."""
        super().__init__(
            function_name=FunctionEvalTypeId.JACCARD_SIMILARITY.value,
            function_arguments={"case_insensitive": case_insensitive},
            display_name=display_name,
        )


class JaroWinklerSimilarity(FunctionEvaluator):
    def __init__(
        self,
        case_insensitive: bool = True,
        prefix_weight: float = 0.1,
        display_name: str | None = None,
    ):
        """Initialize the JaroWinklerSimilarity evaluator. Effective for short strings."""
        super().__init__(
            function_name=FunctionEvalTypeId.JARO_WINKLER_SIMILARITY.value,
            function_arguments={
                "case_insensitive": case_insensitive,
                "prefix_weight": prefix_weight,
            },
            display_name=display_name,
        )


class HammingSimilarity(FunctionEvaluator):
    def __init__(self, case_insensitive: bool = True, display_name: str | None = None):
        """Initialize the HammingSimilarity evaluator. Character-level positional comparison."""
        super().__init__(
            function_name=FunctionEvalTypeId.HAMMING_SIMILARITY.value,
            function_arguments={"case_insensitive": case_insensitive},
            display_name=display_name,
        )


class FuzzyMatch(FunctionEvaluator):
    def __init__(self, case_insensitive: bool = True, display_name: str | None = None):
        """Initialize the FuzzyMatch evaluator. Approximate string matching via SequenceMatcher."""
        super().__init__(
            function_name=FunctionEvalTypeId.FUZZY_MATCH.value,
            function_arguments={"case_insensitive": case_insensitive},
            display_name=display_name,
        )


class IsXml(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """Initialize the IsXml evaluator. Validates XML format."""
        super().__init__(
            function_name=FunctionEvalTypeId.IS_XML.value,
            function_arguments={},
            display_name=display_name,
        )


class IsSql(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """Initialize the IsSql evaluator. Validates SQL syntax."""
        super().__init__(
            function_name=FunctionEvalTypeId.IS_SQL.value,
            function_arguments={},
            display_name=display_name,
        )


class IsUrl(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """Initialize the IsUrl evaluator. Validates URL format."""
        super().__init__(
            function_name=FunctionEvalTypeId.IS_URL.value,
            function_arguments={},
            display_name=display_name,
        )


class WordCountInRange(FunctionEvaluator):
    def __init__(
        self,
        min_words: int | None = None,
        max_words: int | None = None,
        display_name: str | None = None,
    ):
        """Initialize the WordCountInRange evaluator. Checks word count within range."""
        super().__init__(
            function_name=FunctionEvalTypeId.WORD_COUNT_IN_RANGE.value,
            function_arguments={"min_words": min_words, "max_words": max_words},
            display_name=display_name,
        )


class ReadabilityScore(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """Initialize the ReadabilityScore evaluator. Flesch-Kincaid readability metrics."""
        super().__init__(
            function_name=FunctionEvalTypeId.READABILITY_SCORE.value,
            function_arguments={},
            display_name=display_name,
        )


class SentenceCount(FunctionEvaluator):
    def __init__(
        self,
        min_sentences: int | None = None,
        max_sentences: int | None = None,
        display_name: str | None = None,
    ):
        """Initialize the SentenceCount evaluator. Counts and validates sentence count."""
        super().__init__(
            function_name=FunctionEvalTypeId.SENTENCE_COUNT.value,
            function_arguments={
                "min_sentences": min_sentences,
                "max_sentences": max_sentences,
            },
            display_name=display_name,
        )


class ToolCallAccuracy(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """Initialize the ToolCallAccuracy evaluator. Validates agent tool/function calls."""
        super().__init__(
            function_name=FunctionEvalTypeId.TOOL_CALL_ACCURACY.value,
            function_arguments={},
            display_name=display_name,
        )


class Ssim(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """Initialize the SSIM evaluator. Structural Similarity Index for images."""
        super().__init__(
            function_name=FunctionEvalTypeId.SSIM.value,
            function_arguments={},
            display_name=display_name,
        )


class Psnr(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """Initialize the PSNR evaluator. Peak Signal-to-Noise Ratio for images."""
        super().__init__(
            function_name=FunctionEvalTypeId.PSNR.value,
            function_arguments={},
            display_name=display_name,
        )


class ImageProperties(FunctionEvaluator):
    def __init__(
        self,
        expected_width: int | None = None,
        expected_height: int | None = None,
        min_width: int | None = None,
        min_height: int | None = None,
        max_file_size_kb: int | None = None,
        expected_format: str | None = None,
        display_name: str | None = None,
    ):
        """Initialize the ImageProperties evaluator. Validates image dimensions, format, size."""
        super().__init__(
            function_name=FunctionEvalTypeId.IMAGE_PROPERTIES.value,
            function_arguments={
                "expected_width": expected_width,
                "expected_height": expected_height,
                "min_width": min_width,
                "min_height": min_height,
                "max_file_size_kb": max_file_size_kb,
                "expected_format": expected_format,
            },
            display_name=display_name,
        )


class WordErrorRate(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """Initialize the WER evaluator. Word Error Rate for ASR/STT."""
        super().__init__(
            function_name=FunctionEvalTypeId.WORD_ERROR_RATE.value,
            function_arguments={},
            display_name=display_name,
        )


class CharacterErrorRate(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """Initialize the CER evaluator. Character Error Rate for ASR/OCR."""
        super().__init__(
            function_name=FunctionEvalTypeId.CHARACTER_ERROR_RATE.value,
            function_arguments={},
            display_name=display_name,
        )


class SyntaxValidation(FunctionEvaluator):
    def __init__(self, language: str = "python", display_name: str | None = None):
        """Initialize the SyntaxValidation evaluator. Validates code syntax."""
        super().__init__(
            function_name=FunctionEvalTypeId.SYNTAX_VALIDATION.value,
            function_arguments={"language": language},
            display_name=display_name,
        )


class CodeComplexity(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """Initialize the CodeComplexity evaluator. Cyclomatic complexity for Python code."""
        super().__init__(
            function_name=FunctionEvalTypeId.CODE_COMPLEXITY.value,
            function_arguments={},
            display_name=display_name,
        )


class CodeBleu(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """Initialize the CodeBLEU evaluator. Code-specific BLEU variant."""
        super().__init__(
            function_name=FunctionEvalTypeId.CODE_BLEU.value,
            function_arguments={},
            display_name=display_name,
        )


class Accuracy(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """Initialize the Accuracy evaluator. Classification accuracy."""
        super().__init__(
            function_name=FunctionEvalTypeId.ACCURACY.value,
            function_arguments={},
            display_name=display_name,
        )


class PrecisionScore(FunctionEvaluator):
    def __init__(
        self, positive_label: str | None = None, display_name: str | None = None
    ):
        """Initialize the PrecisionScore evaluator. Classification precision."""
        super().__init__(
            function_name=FunctionEvalTypeId.PRECISION_SCORE.value,
            function_arguments={"positive_label": positive_label},
            display_name=display_name,
        )


class CohenKappa(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """Initialize the CohenKappa evaluator. Inter-rater agreement coefficient."""
        super().__init__(
            function_name=FunctionEvalTypeId.COHEN_KAPPA.value,
            function_arguments={},
            display_name=display_name,
        )


class MatthewsCorrelation(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """Initialize the MatthewsCorrelation evaluator. MCC for classification."""
        super().__init__(
            function_name=FunctionEvalTypeId.MATTHEWS_CORRELATION.value,
            function_arguments={},
            display_name=display_name,
        )


class JsonDiff(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """Initialize the JsonDiff evaluator. Deep structural JSON comparison."""
        super().__init__(
            function_name=FunctionEvalTypeId.JSON_DIFF.value,
            function_arguments={},
            display_name=display_name,
        )


class IsHtml(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """Initialize the IsHtml evaluator. Validates HTML format."""
        super().__init__(
            function_name=FunctionEvalTypeId.IS_HTML.value,
            function_arguments={},
            display_name=display_name,
        )


class TranslationEditRate(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        """Initialize the TER evaluator. Translation Edit Rate."""
        super().__init__(
            function_name=FunctionEvalTypeId.TRANSLATION_EDIT_RATE.value,
            function_arguments={},
            display_name=display_name,
        )


class TrajectoryMatch(FunctionEvaluator):
    def __init__(self, mode: str = "strict", display_name: str | None = None):
        """Initialize the TrajectoryMatch evaluator. Agent action sequence validation."""
        super().__init__(
            function_name=FunctionEvalTypeId.TRAJECTORY_MATCH.value,
            function_arguments={"mode": mode},
            display_name=display_name,
        )


class StepCount(FunctionEvaluator):
    def __init__(
        self,
        min_steps: int | None = None,
        max_steps: int | None = None,
        expected_steps: int | None = None,
        display_name: str | None = None,
    ):
        """Initialize the StepCount evaluator. Agent step count validation."""
        super().__init__(
            function_name=FunctionEvalTypeId.STEP_COUNT.value,
            function_arguments={
                "min_steps": min_steps,
                "max_steps": max_steps,
                "expected_steps": expected_steps,
            },
            display_name=display_name,
        )


class RegexPiiDetection(FunctionEvaluator):
    def __init__(
        self, detect_types: list[str] | None = None, display_name: str | None = None
    ):
        """Initialize the RegexPiiDetection evaluator. Regex-based PII detection."""
        super().__init__(
            function_name=FunctionEvalTypeId.REGEX_PII_DETECTION.value,
            function_arguments={"detect_types": detect_types},
            display_name=display_name,
        )


class PromptInjectionDetection(FunctionEvaluator):
    def __init__(
        self, detect_types: list[str] | None = None, display_name: str | None = None
    ):
        """Initialize the PromptInjectionDetection evaluator. Regex-based prompt-injection / jailbreak detection."""
        super().__init__(
            function_name=FunctionEvalTypeId.PROMPT_INJECTION_DETECTION.value,
            function_arguments={"detect_types": detect_types},
            display_name=display_name,
        )


class PearsonCorrelation(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        super().__init__(
            function_name=FunctionEvalTypeId.PEARSON_CORRELATION.value,
            function_arguments={},
            display_name=display_name,
        )


class SpearmanCorrelation(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        super().__init__(
            function_name=FunctionEvalTypeId.SPEARMAN_CORRELATION.value,
            function_arguments={},
            display_name=display_name,
        )


class R2Score(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        super().__init__(
            function_name=FunctionEvalTypeId.R2_SCORE.value,
            function_arguments={},
            display_name=display_name,
        )


class Rmse(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        super().__init__(
            function_name=FunctionEvalTypeId.RMSE.value,
            function_arguments={},
            display_name=display_name,
        )


class BalancedAccuracy(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        super().__init__(
            function_name=FunctionEvalTypeId.BALANCED_ACCURACY.value,
            function_arguments={},
            display_name=display_name,
        )


class FBetaScore(FunctionEvaluator):
    def __init__(
        self,
        beta: float = 1.0,
        positive_label: str | None = None,
        display_name: str | None = None,
    ):
        super().__init__(
            function_name=FunctionEvalTypeId.F_BETA_SCORE.value,
            function_arguments={"beta": beta, "positive_label": positive_label},
            display_name=display_name,
        )


class LogLoss(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        super().__init__(
            function_name=FunctionEvalTypeId.LOG_LOSS.value,
            function_arguments={},
            display_name=display_name,
        )


class MeanAveragePrecision(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        super().__init__(
            function_name=FunctionEvalTypeId.MEAN_AVERAGE_PRECISION.value,
            function_arguments={},
            display_name=display_name,
        )


class SquadScore(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        super().__init__(
            function_name=FunctionEvalTypeId.SQUAD_SCORE.value,
            function_arguments={},
            display_name=display_name,
        )


class MatchErrorRate(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        super().__init__(
            function_name=FunctionEvalTypeId.MATCH_ERROR_RATE.value,
            function_arguments={},
            display_name=display_name,
        )


class WordInfoLost(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        super().__init__(
            function_name=FunctionEvalTypeId.WORD_INFO_LOST.value,
            function_arguments={},
            display_name=display_name,
        )


class WordInfoPreserved(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        super().__init__(
            function_name=FunctionEvalTypeId.WORD_INFO_PRESERVED.value,
            function_arguments={},
            display_name=display_name,
        )


class NonLlmContextPrecision(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        super().__init__(
            function_name=FunctionEvalTypeId.NON_LLM_CONTEXT_PRECISION.value,
            function_arguments={},
            display_name=display_name,
        )


class NonLlmContextRecall(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        super().__init__(
            function_name=FunctionEvalTypeId.NON_LLM_CONTEXT_RECALL.value,
            function_arguments={},
            display_name=display_name,
        )


class DistinctN(FunctionEvaluator):
    def __init__(self, n: int = 1, display_name: str | None = None):
        super().__init__(
            function_name=FunctionEvalTypeId.DISTINCT_N.value,
            function_arguments={"n": n},
            display_name=display_name,
        )


class TypeTokenRatio(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        super().__init__(
            function_name=FunctionEvalTypeId.TYPE_TOKEN_RATIO.value,
            function_arguments={},
            display_name=display_name,
        )


class RepetitionRate(FunctionEvaluator):
    def __init__(self, n: int = 3, display_name: str | None = None):
        super().__init__(
            function_name=FunctionEvalTypeId.REPETITION_RATE.value,
            function_arguments={"n": n},
            display_name=display_name,
        )


class IsRefusal(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        super().__init__(
            function_name=FunctionEvalTypeId.IS_REFUSAL.value,
            function_arguments={},
            display_name=display_name,
        )


class LatencyCheck(FunctionEvaluator):
    def __init__(
        self, max_latency_ms: float | None = None, display_name: str | None = None
    ):
        super().__init__(
            function_name=FunctionEvalTypeId.LATENCY_CHECK.value,
            function_arguments={"max_latency_ms": max_latency_ms},
            display_name=display_name,
        )


class FleissKappa(FunctionEvaluator):
    def __init__(self, display_name: str | None = None):
        super().__init__(
            function_name=FunctionEvalTypeId.FLEISS_KAPPA.value,
            function_arguments={},
            display_name=display_name,
        )
