from enum import Enum


class FutureAgiEvalTypeId(Enum):
    RANKING_EVAL = "RankingEvaluator"
    DETERMINISTIC_EVAL = "DeterministicEvaluator"
    FORMAL_CONSTRAINT_EVAL = "FormalConstraintEvaluator"


class LlmEvalTypeId(Enum):
    CUSTOM_PROMPT_EVAL = "CustomPromptEvaluator"
    GROUNDEDNESS = "Groundedness"


class FunctionEvalTypeId(Enum):
    REGEX = "Regex"
    CONTAINS_ANY = "ContainsAny"
    CONTAINS_ALL = "ContainsAll"
    CONTAINS = "Contains"
    CONTAINS_NONE = "ContainsNone"
    CONTAINS_JSON = "ContainsJson"
    CONTAINS_EMAIL = "ContainsEmail"
    IS_JSON = "IsJson"
    IS_EMAIL = "IsEmail"
    NO_INVALID_LINKS = "NoInvalidLinks"
    CONTAINS_LINK = "ContainsLink"
    CONTAINS_VALID_LINK = "ContainsValidLink"
    EQUALS = "Equals"
    STARTS_WITH = "StartsWith"
    ENDS_WITH = "EndsWith"
    LENGTH_LESS_THAN = "LengthLessThan"
    LENGTH_GREATER_THAN = "LengthGreaterThan"
    LENGTH_BETWEEN = "LengthBetween"
    ONE_LINE = "OneLine"
    JSON_SCHEMA = "JsonSchema"
    JSON_VALIDATION = "JsonValidation"
    CUSTOM_CODE_EVAL = "CustomCodeEval"
    API_CALL = "ApiCall"
    ROUGE_SCORE = "RougeScore"
    BLEU_SCORE = "BleuScore"
    FID_SCORE = "FidScore"
    CLIP_SCORE = "ClipScore"
    RECALL_SCORE = "RecallScore"
    RECALL_AT_K = "RecallAtK"
    PRECISION_AT_K = "PrecisionAtK"
    NDCG_AT_K = "NdcgAtK"
    MRR = "Mrr"
    HIT_RATE = "HitRate"
    LEVENSHTEIN_SIMILARITY = "LevenshteinSimilarity"
    NUMERIC_SIMILARITY = "NumericSimilarity"
    EMBEDDING_SIMILARITY = "EmbeddingSimilarity"
    SEMANTIC_LIST_CONTAINS = "SemanticListContains"
    METEOR_SCORE = "MeteorScore"
    GLEU_SCORE = "GleuScore"
    CHRF_SCORE = "ChrfScore"
    F1_SCORE = "F1Score"
    JACCARD_SIMILARITY = "JaccardSimilarity"
    JARO_WINKLER_SIMILARITY = "JaroWinklerSimilarity"
    HAMMING_SIMILARITY = "HammingSimilarity"
    FUZZY_MATCH = "FuzzyMatch"
    IS_XML = "IsXml"
    IS_SQL = "IsSql"
    IS_URL = "IsUrl"
    WORD_COUNT_IN_RANGE = "WordCountInRange"
    READABILITY_SCORE = "ReadabilityScore"
    SENTENCE_COUNT = "SentenceCount"
    TOOL_CALL_ACCURACY = "ToolCallAccuracy"
    SSIM = "Ssim"
    PSNR = "Psnr"
    IMAGE_PROPERTIES = "ImageProperties"
    WORD_ERROR_RATE = "WordErrorRate"
    CHARACTER_ERROR_RATE = "CharacterErrorRate"
    SYNTAX_VALIDATION = "SyntaxValidation"
    CODE_COMPLEXITY = "CodeComplexity"
    CODE_BLEU = "CodeBleu"
    ACCURACY = "Accuracy"
    PRECISION_SCORE = "PrecisionScore"
    COHEN_KAPPA = "CohenKappa"
    MATTHEWS_CORRELATION = "MatthewsCorrelation"
    JSON_DIFF = "JsonDiff"
    IS_HTML = "IsHtml"
    TRANSLATION_EDIT_RATE = "TranslationEditRate"
    TRAJECTORY_MATCH = "TrajectoryMatch"
    STEP_COUNT = "StepCount"
    REGEX_PII_DETECTION = "RegexPiiDetection"
    PEARSON_CORRELATION = "PearsonCorrelation"
    SPEARMAN_CORRELATION = "SpearmanCorrelation"
    R2_SCORE = "R2Score"
    RMSE = "Rmse"
    BALANCED_ACCURACY = "BalancedAccuracy"
    F_BETA_SCORE = "FBetaScore"
    LOG_LOSS = "LogLoss"
    MEAN_AVERAGE_PRECISION = "MeanAveragePrecision"
    SQUAD_SCORE = "SquadScore"
    MATCH_ERROR_RATE = "MatchErrorRate"
    WORD_INFO_LOST = "WordInfoLost"
    WORD_INFO_PRESERVED = "WordInfoPreserved"
    NON_LLM_CONTEXT_PRECISION = "NonLlmContextPrecision"
    NON_LLM_CONTEXT_RECALL = "NonLlmContextRecall"
    DISTINCT_N = "DistinctN"
    TYPE_TOKEN_RATIO = "TypeTokenRatio"
    REPETITION_RATE = "RepetitionRate"
    IS_REFUSAL = "IsRefusal"
    LATENCY_CHECK = "LatencyCheck"
    FLEISS_KAPPA = "FleissKappa"


class GroundedEvalTypeId(Enum):
    ANSWER_SIMILARITY = "AnswerSimilarity"


def is_llm_eval(evaluator_type: str) -> bool:
    return any(evaluator_type == member.value for member in LlmEvalTypeId)


def is_function_eval(evaluator_type: str) -> bool:
    return any(evaluator_type == member.value for member in FunctionEvalTypeId)


def is_grounded_eval(evaluator_type: str) -> bool:
    return any(evaluator_type == member.value for member in GroundedEvalTypeId)


def is_future_agi_eval(evaluator_type: str) -> bool:
    return any(evaluator_type == member.value for member in FutureAgiEvalTypeId)
