import math
import re
from abc import ABC, abstractmethod


class Comparator(ABC):
    @abstractmethod
    def compare(self, string1, string2):
        pass


class CosineSimilarity(Comparator):
    def compare(self, string1, string2):
        # Tokenize and create a combined set of unique words
        combined_set = self._create_combined_set(string1, string2)
        # Vectorize the strings
        vector1 = self._vectorize(string1, combined_set)
        vector2 = self._vectorize(string2, combined_set)
        dot_product = sum(p * q for p, q in zip(vector1, vector2, strict=False))
        magnitude_vec1 = math.sqrt(sum([val**2 for val in vector1]))
        magnitude_vec2 = math.sqrt(sum([val**2 for val in vector2]))
        if magnitude_vec1 * magnitude_vec2 == 0:
            # Avoid division by zero
            return 0
        return dot_product / (magnitude_vec1 * magnitude_vec2)

    def _tokenize(self, string):
        """
        Tokenize the input string into a list of words.

        Args:
            string (str): The string to tokenize.

        Returns:
            list: A list of lowercased words from the string.
        """
        return re.findall(r"\b\w+\b", string.lower())

    def _create_combined_set(self, string1, string2):
        return set(self._tokenize(string1)).union(set(self._tokenize(string2)))

    def _vectorize(self, string, combined_set):
        tokenized = self._tokenize(string)
        vector = [tokenized.count(word) for word in combined_set]
        return vector


class NormalisedLevenshteinSimilarity(Comparator):
    def compare(self, string1, string2):
        return 1 - self._normalised_levenshtein_distance(string1, string2)

    def _normalised_levenshtein_distance(self, str1, str2):
        if len(str1) == 0 and len(str2) == 0:
            return 0
        if len(str1) == 0 or len(str2) == 0:
            return 1.0
        m, n = len(str1), len(str2)
        # Create a matrix to store the distances
        dp = [[0] * (n + 1) for _ in range(m + 1)]
        # Initialize the first row and first column
        for i in range(m + 1):
            dp[i][0] = i
        for j in range(n + 1):
            dp[0][j] = j
        # Calculate the distance
        for i in range(1, m + 1):
            for j in range(1, n + 1):
                if str1[i - 1] == str2[j - 1]:
                    dp[i][j] = dp[i - 1][j - 1]
                else:
                    dp[i][j] = 1 + min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1])
        if len(str1) >= len(str2):
            return dp[m][n] / len(str1)
        else:
            return dp[m][n] / len(str2)


class JaroWincklerSimilarity(Comparator):
    def compare(self, string1, string2):
        return self._jaro_winckler_similarity(string1, string2)

    def _jaro_winckler_similarity(self, str1, str2):
        len1 = len(str1)
        len2 = len(str2)
        if len1 == 0 or len2 == 0:
            return 0.0
        max_dist = (max(len(str1), len(str2)) // 2) - 1
        match = 0
        hash_str1 = [0] * len(str1)
        hash_str2 = [0] * len(str2)
        for i in range(len1):
            for j in range(max(0, i - max_dist), min(len2, i + max_dist + 1)):
                if str1[i] == str2[j] and hash_str2[j] == 0:
                    hash_str1[i] = 1
                    hash_str2[j] = 1
                    match += 1
                    break
        if match == 0:
            return 0.0
        t = 0
        point = 0
        for i in range(len1):
            if hash_str1[i]:
                while hash_str2[point] == 0:
                    point += 1
                if str1[i] != str2[point]:
                    point += 1
                    t += 1
        t //= 2
        return (match / len1 + match / len2 + (match - t) / match) / 3.0


class JaccardSimilarity(Comparator):
    def compare(self, string1, string2):
        return self._jaccard_similarity(string1, string2)

    def _jaccard_similarity(self, str1, str2):
        str1_tokens = set(str1.split())
        str2_tokens = set(str2.split())
        if not str1_tokens and not str2_tokens:
            return 1.0
        if not str1_tokens or not str2_tokens:
            return 0.0
        return len(str1_tokens.intersection(str2_tokens)) / len(
            str1_tokens.union(str2_tokens)
        )


class SorensenDiceSimilarity(Comparator):
    def compare(self, string1, string2):
        return self._sorensen_dice_similarity(string1, string2)

    def _sorensen_dice_similarity(self, str1, str2):
        str1_tokens = set(str1.split())
        str2_tokens = set(str2.split())
        if not str1_tokens and not str2_tokens:
            return 1.0
        if not str1_tokens or not str2_tokens:
            return 0.0
        return (
            2
            * len(str1_tokens.intersection(str2_tokens))
            / (len(str1_tokens) + len(str2_tokens))
        )


class PhoneticSimilarity(Comparator):
    """
    Compares string similarity based on how words sound phonetically.
    Useful for evaluation paths prone to transcription or speech abnormalities.
    """

    SOUNDEX_MAP = {
        "B": "1",
        "F": "1",
        "P": "1",
        "V": "1",
        "C": "2",
        "G": "2",
        "J": "2",
        "K": "2",
        "Q": "2",
        "S": "2",
        "X": "2",
        "Z": "2",
        "D": "3",
        "T": "3",
        "L": "4",
        "M": "5",
        "N": "5",
        "R": "6",
    }

    def _get_soundex_code(self, word: str) -> str:
        word = word.upper()
        if not word or not word[0].isalpha():
            return "0000"

        first_letter = word[0]
        first_code = self.SOUNDEX_MAP.get(first_letter, "")

        encoded_tail = []
        for char in word[1:]:
            code = self.SOUNDEX_MAP.get(char, "")
            if not code:
                continue
            # Skip if same as previous code OR matches first letter's code on first entry
            if encoded_tail and encoded_tail[-1] == code:
                continue
            if not encoded_tail and code == first_code:
                continue
            encoded_tail.append(code)

        soundex = (first_letter + "".join(encoded_tail) + "000")[:4]
        return soundex

    def _tokenize(self, string: str) -> list[str]:
        return re.findall(r"\b\w+\b", string.lower())

    def _to_soundex_tokens(self, string: str) -> list[str]:
        return [self._get_soundex_code(token) for token in self._tokenize(string)]

    def compare(self, string1: str, string2: str) -> float:
        tokens1 = set(self._to_soundex_tokens(string1))
        tokens2 = set(self._to_soundex_tokens(string2))

        if not tokens1 and not tokens2:
            return 1.0
        if not tokens1 or not tokens2:
            return 0.0

        matches = len(tokens1.intersection(tokens2))
        return matches / len(tokens1.union(tokens2))
