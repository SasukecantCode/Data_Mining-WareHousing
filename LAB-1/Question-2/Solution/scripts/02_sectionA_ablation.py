"""
Section A(a) -- ablation / sanity check.

The bigram + denoising design in 01_sectionA_similarity_score.py was first
worked out on a hand-built toy example, before this script ever touched the
real corpus. That is a real risk: a choice that looked right on an
invented pair can be an artifact of how the toy example was constructed,
not a property of the actual data. This script re-checks it honestly by
decomposing the two decisions (denoise or not x unigram or bigram) into
four independent variants and scoring all four against the real 900
labelled pairs, plus a document-frequency threshold sweep, plus a
character-shingle alternative -- so the choice adopted in the README rests
on which variant actually wins on this corpus, not on which one was tried
first.
"""
import statistics
from collections import Counter

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "sectionA", Path(__file__).parent / "01_sectionA_similarity_score.py"
)
sectionA = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sectionA)


def ngram_set(tokens, n):
    if len(tokens) < n:
        return set(tokens)
    return set(zip(*[tokens[i:] for i in range(n)]))


def char_shingle_set(text, k=5):
    t = "".join(ch for ch in text.lower() if ch.isalnum() or ch == " ")
    t = " ".join(t.split())
    return set(t[i : i + k] for i in range(max(0, len(t) - k + 1)))


def separation(same, diff):
    thr, acc = sectionA.separation_stats(same, diff)
    return thr, acc, statistics.mean(same), statistics.mean(diff)


def main():
    notices = sectionA.load_notices()
    pairs = sectionA.load_labelled_pairs()
    same_ids = [(a, b) for a, b, l in pairs if l == "same"]
    diff_ids = [(a, b) for a, b, l in pairs if l == "different"]

    raw_tokens = {nid: list(sectionA.TOKEN_RE.findall((n["title"] + " " + n["body"]).lower()))
                  for nid, n in notices.items()}
    stopwords_040, structural_cache = sectionA.build_corpus_stopwords(notices, 0.40)

    print("=== 1. Isolate denoising vs. granularity (4-way ablation) ===")
    variants = {
        "raw+unigram (Choice 1 as shipped)": lambda nid: set(raw_tokens[nid]),
        "raw+bigram (granularity only)": lambda nid: ngram_set(raw_tokens[nid], 2),
        "denoised+unigram (denoising only)": lambda nid: set(
            t for t in structural_cache[nid] if t not in stopwords_040
        ),
        "denoised+bigram (Choice 2 as shipped)": lambda nid: sectionA.choice2_tokens(
            structural_cache[nid], stopwords_040
        ),
    }
    for name, fn in variants.items():
        cache = {nid: fn(nid) for nid in notices}
        same = [sectionA.jaccard(cache[a], cache[b]) for a, b in same_ids]
        diff = [sectionA.jaccard(cache[a], cache[b]) for a, b in diff_ids]
        thr, acc, ms, md = separation(same, diff)
        print(f"{name:38s} acc={acc:.3f}  thr={thr:.3f}  mean_same={ms:.3f}  mean_diff={md:.3f}")

    print()
    print("=== 2. Is the 40% document-frequency stopword cutoff load-bearing? ===")
    for df_thr in (0.15, 0.20, 0.30, 0.40, 0.50, 0.60, 0.80, 1.01):
        stopwords, cache_struct = sectionA.build_corpus_stopwords(notices, df_thr)
        cache = {nid: sectionA.choice2_tokens(cache_struct[nid], stopwords) for nid in notices}
        same = [sectionA.jaccard(cache[a], cache[b]) for a, b in same_ids]
        diff = [sectionA.jaccard(cache[a], cache[b]) for a, b in diff_ids]
        thr, acc, ms, md = separation(same, diff)
        print(f"df_threshold={df_thr:<5} n_stopwords={len(stopwords):4d}  acc={acc:.3f}  mean_same={ms:.3f}  mean_diff={md:.3f}")

    print()
    print("=== 3. Alternative: character 5-shingles, same structural denoising, no stopwording ===")
    for k in (3, 5, 8):
        cache = {}
        for nid, notice in notices.items():
            text = " ".join(structural_cache[nid])  # already structurally denoised token stream
            cache[nid] = char_shingle_set(text, k)
        same = [sectionA.jaccard(cache[a], cache[b]) for a, b in same_ids]
        diff = [sectionA.jaccard(cache[a], cache[b]) for a, b in diff_ids]
        thr, acc, ms, md = separation(same, diff)
        sizes = statistics.mean(len(cache[nid]) for nid in list(notices)[:200])
        print(f"char {k}-shingle  acc={acc:.3f}  mean_same={ms:.3f}  mean_diff={md:.3f}  mean_set_size~{sizes:.0f}")

    print()
    print("=== 4. Trigram instead of bigram, same denoising ===")
    for n in (1, 2, 3, 4):
        cache = {nid: ngram_set([t for t in structural_cache[nid] if t not in stopwords_040], n) for nid in notices}
        same = [sectionA.jaccard(cache[a], cache[b]) for a, b in same_ids]
        diff = [sectionA.jaccard(cache[a], cache[b]) for a, b in diff_ids]
        thr, acc, ms, md = separation(same, diff)
        print(f"word {n}-gram, denoised  acc={acc:.3f}  mean_same={ms:.3f}  mean_diff={md:.3f}")


if __name__ == "__main__":
    main()
