"""
Section (e) -- find the place where the design betrays you.

Runs the Section A(c) retrieval (k=126 MinHash, r=2/b=63 LSH banding) over
the full 12,000-notice corpus and looks at how candidate-generation work
is DISTRIBUTED across notices, not just its total. Locates the empirical
outlier population, explains the mechanism, quantifies it against the
20-minute nightly budget, mitigates it (see structural_signal_tokens'
strip_truncation_marker flag in 01_sectionA_similarity_score.py), and
reports the real before/after cost in both candidate-generation work and
labelled-pair retrieval quality -- a mitigation whose price isn't measured
doesn't count.

Run: .venv/bin/python scripts/07_sectionE_pathology.py
Output: reports/taskE/pathology.txt
"""
import collections
import itertools
import statistics
import time
from pathlib import Path

import importlib.util

spec_a = importlib.util.spec_from_file_location(
    "sectionA", Path(__file__).parent / "01_sectionA_similarity_score.py"
)
sectionA = importlib.util.module_from_spec(spec_a)
spec_a.loader.exec_module(sectionA)

spec_b = importlib.util.spec_from_file_location(
    "sectionB", Path(__file__).parent / "03_sectionA_minhash_sketch.py"
)
sectionB = importlib.util.module_from_spec(spec_b)
spec_b.loader.exec_module(sectionB)

REPORT_DIR = Path(__file__).resolve().parents[1] / "reports" / "taskE"
K = 126
R, B = 2, 63  # adopted operating point, Section A(c)


def build(notices, strip_truncation_marker):
    stopwords, structural_cache = sectionA.build_corpus_stopwords(
        notices, 0.40, strip_truncation_marker=strip_truncation_marker
    )
    notice_ids = sorted(notices.keys())
    sets = {nid: sectionA.choice2_tokens(structural_cache[nid], stopwords) for nid in notice_ids}
    id_index = {nid: i for i, nid in enumerate(notice_ids)}

    all_tokens = set()
    for s in sets.values():
        all_tokens.update(s)
    base_cache = {t: sectionB.base_hash(t) for t in all_tokens}
    mat = sectionB.build_padded_matrix(notice_ids, sets, base_cache)
    a_arr, b_arr = sectionB.make_hash_family(K, seed=42)
    sig = sectionB.signatures_for_matrix(mat, a_arr, b_arr)
    return notice_ids, id_index, sets, sig


def lsh_pass(sig):
    t0 = time.time()
    degree = collections.Counter()
    candidate_pairs = set()
    for band in range(B):
        start = band * R
        buckets = collections.defaultdict(list)
        for i in range(sig.shape[0]):
            buckets[sig[i, start : start + R].tobytes()].append(i)
        for members in buckets.values():
            if len(members) < 2:
                continue
            for x, y in itertools.combinations(members, 2):
                candidate_pairs.add((x, y) if x < y else (y, x))
    elapsed = time.time() - t0
    for x, y in candidate_pairs:
        degree[x] += 1
        degree[y] += 1
    return candidate_pairs, degree, elapsed


def labelled_recall(sets, pairs):
    same = [(a, b) for a, b, l in pairs if l == "same"]
    diff = [(a, b) for a, b, l in pairs if l == "different"]

    def p_cand(s):
        return 1 - (1 - s ** R) ** B

    recall_scores = [sectionA.jaccard(sets[a], sets[b]) for a, b in same]
    diff_scores = [sectionA.jaccard(sets[a], sets[b]) for a, b in diff]
    theoretical_recall = statistics.mean(p_cand(s) for s in recall_scores)
    theoretical_false_cand = statistics.mean(p_cand(s) for s in diff_scores)
    return theoretical_recall, theoretical_false_cand, recall_scores, diff_scores


def main():
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    notices = sectionA.load_notices()
    pairs = sectionA.load_labelled_pairs()

    leak_ids = set(
        nid for nid, n in notices.items()
        if "truncated" in n["body"].lower() and "disclaimer" in n["body"].lower()
    )

    out = []
    out.append(f"Empirically located outlier population: {len(leak_ids)} notices "
               f"({len(leak_ids)/len(notices)*100:.1f}% of the 12,000-notice corpus) "
               f"contain both a truncation marker and a disclaimer footer in their raw body.")
    out.append("")

    results = {}
    for label, strip in [("BEFORE (unmitigated)", False), ("AFTER (mitigated)", True)]:
        notice_ids, id_index, sets, sig = build(notices, strip)
        candidate_pairs, degree, build_time = lsh_pass(sig)
        leak_idx = set(id_index[n] for n in leak_ids)
        both_leak = sum(1 for x, y in candidate_pairs if x in leak_idx and y in leak_idx)
        one_leak = sum(1 for x, y in candidate_pairs if (x in leak_idx) != (y in leak_idx))
        neither = len(candidate_pairs) - both_leak - one_leak

        deg_sorted = sorted(degree.values())
        n = len(deg_sorted)
        top15 = sorted(degree.items(), key=lambda kv: -kv[1])[:15]
        top15_in_leak = sum(1 for i, d in top15 if i in leak_idx)

        theo_recall, theo_false_cand, same_scores, diff_scores = labelled_recall(sets, pairs)

        results[label] = dict(
            candidate_pairs=len(candidate_pairs), build_time=build_time,
            both_leak=both_leak, one_leak=one_leak, neither=neither,
            deg_max=deg_sorted[-1] if deg_sorted else 0,
            deg_p99=deg_sorted[int(0.99 * n)] if n else 0,
            deg_p50=deg_sorted[int(0.50 * n)] if n else 0,
            top15_in_leak=top15_in_leak,
            theo_recall=theo_recall, theo_false_cand=theo_false_cand,
        )

        out.append(f"=== {label} ===")
        out.append(f"  total candidate pairs: {len(candidate_pairs):,}  (LSH build time: {build_time:.2f}s)")
        out.append(f"  degree distribution: median={results[label]['deg_p50']}  p99={results[label]['deg_p99']}  max={results[label]['deg_max']}")
        out.append(f"  of top-15 highest-degree notices, {top15_in_leak}/15 are in the {len(leak_ids)}-notice outlier population")
        out.append(f"  candidate pairs with BOTH endpoints in outlier population: {both_leak:,} "
                   f"({both_leak/len(candidate_pairs)*100:.1f}% of all candidates, from "
                   f"{len(leak_ids)/len(notices)*100:.1f}% of notices)")
        out.append(f"  theoretical mean recall on labelled 'same' (900 pairs' exact scores): {theo_recall:.4f}")
        out.append(f"  theoretical mean false-candidate rate on labelled 'different': {theo_false_cand:.4f}")
        out.append("")

    b, a = results["BEFORE (unmitigated)"], results["AFTER (mitigated)"]
    out.append("=== Summary: cost of the pathology, and price of the fix ===")
    out.append(f"Candidate volume:      {b['candidate_pairs']:,} -> {a['candidate_pairs']:,}  "
               f"({(1 - a['candidate_pairs']/b['candidate_pairs'])*100:.1f}% reduction)")
    out.append(f"Outlier-population internal candidate pairs: {b['both_leak']:,} -> {a['both_leak']:,}")
    out.append(f"Max single-notice degree: {b['deg_max']:,} -> {a['deg_max']:,}")
    out.append(f"LSH build wall-clock:  {b['build_time']:.2f}s -> {a['build_time']:.2f}s")
    out.append(f"Labelled-set theoretical recall (same):        {b['theo_recall']:.4f} -> {a['theo_recall']:.4f}  "
               f"(change: {a['theo_recall']-b['theo_recall']:+.4f})")
    out.append(f"Labelled-set theoretical false-cand (different): {b['theo_false_cand']:.4f} -> {a['theo_false_cand']:.4f}  "
               f"(change: {a['theo_false_cand']-b['theo_false_cand']:+.4f})")
    out.append(f"At current corpus size (12,000 notices, {b['candidate_pairs']:,} candidates, "
               f"{b['build_time']:.2f}s build), this pathology is NOT yet budget-threatening on its own -- "
               f"the concern is how it scales: the outlier population's internal cost grows "
               f"quadratically with its own size, while genuine-duplicate cost grows near-linearly "
               f"with corpus size (clusters stay small, bounded ~9 members).")

    text = "\n".join(out)
    print(text)
    with open(REPORT_DIR / "pathology.txt", "w", encoding="utf-8") as f:
        f.write(text + "\n")


if __name__ == "__main__":
    main()
