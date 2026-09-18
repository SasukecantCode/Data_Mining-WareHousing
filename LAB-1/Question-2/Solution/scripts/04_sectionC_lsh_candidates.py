"""
Section A(c) -- make retrieval sublinear, and price the risk.

Builds LSH banding on top of the k=126 MinHash signatures from Section
A(b), so that instead of scoring a notice against the whole corpus, it is
only scored against a short candidate list assembled by cheap hash-bucket
lookups. Characterises, and plots, the probability that a pair with true
similarity s survives to the candidate stage:

    P_candidate(s) = 1 - (1 - s^r)^b        (b bands of r rows, b*r = k)

then measures real recall/candidate-volume/wall-clock cost of several
(b, r) choices on the actual 12,000-notice corpus and the 900 labelled
pairs, and reports which operating point was adopted and why.
"""
import itertools
import math
import statistics
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

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

REPORT_DIR = Path(__file__).resolve().parents[1] / "reports" / "taskC"
K = 126  # the sketch size fixed in Section A(b)


def p_candidate(s, b, r):
    return 1 - (1 - s ** r) ** b


def band_key(sig_row, start, r):
    return sig_row[start : start + r].tobytes()


def build_candidates(sig, b, r, notice_ids):
    """Full-corpus LSH: returns (unique candidate pair count, wall-clock seconds)."""
    t0 = time.time()
    candidate_pairs = set()
    for band in range(b):
        start = band * r
        buckets = defaultdict(list)
        for i in range(sig.shape[0]):
            key = band_key(sig[i], start, r)
            buckets[key].append(i)
        for members in buckets.values():
            if len(members) < 2:
                continue
            for x, y in itertools.combinations(members, 2):
                candidate_pairs.add((x, y) if x < y else (y, x))
    elapsed = time.time() - t0
    return len(candidate_pairs), elapsed


def pair_is_candidate(sig_a, sig_b, b, r):
    for band in range(b):
        start = band * r
        if np.array_equal(sig_a[start : start + r], sig_b[start : start + r]):
            return True
    return False


def main():
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    notices = sectionA.load_notices()
    pairs = sectionA.load_labelled_pairs()
    stopwords, structural_cache = sectionA.build_corpus_stopwords(notices, 0.40)

    notice_ids = sorted(notices.keys())
    id_index = {nid: i for i, nid in enumerate(notice_ids)}
    sets = {nid: sectionA.choice2_tokens(structural_cache[nid], stopwords) for nid in notice_ids}

    all_tokens = set()
    for s in sets.values():
        all_tokens.update(s)
    base_cache = {t: sectionB.base_hash(t) for t in all_tokens}

    print("Building padded shingle-hash matrix for all 12,000 notices ...")
    mat = sectionB.build_padded_matrix(notice_ids, sets, base_cache)
    a_arr, b_arr = sectionB.make_hash_family(K, seed=42)
    print("Computing MinHash signatures (k=126) for all 12,000 notices ...")
    sig = sectionB.signatures_for_matrix(mat, a_arr, b_arr)
    print("Done:", sig.shape)

    exact = {(a, b): sectionA.jaccard(sets[a], sets[b]) for a, b, _ in pairs}
    same_pairs = [(a, b) for a, b, l in pairs if l == "same"]
    diff_pairs = [(a, b) for a, b, l in pairs if l == "different"]

    # candidate divisor pairs of K=126: (r, b)
    configs = [(2, 63), (3, 42), (6, 21), (7, 18), (9, 14), (14, 9), (18, 7)]

    total_possible_pairs = len(notice_ids) * (len(notice_ids) - 1) // 2

    out = []
    out.append(f"Corpus: {len(notice_ids)} notices, {total_possible_pairs:,} total possible pairs.")
    out.append(f"MinHash sketch size k={K} (Section A(b)).")
    out.append("")
    out.append(f"{'r':>3} {'b':>4} {'s* (50% pt)':>12} {'recall@same':>12} {'false-cand@diff':>16} {'candidate pairs':>16} {'%corpus':>9} {'build sec':>10}")

    rows = []
    for r, b in configs:
        s_star = (1.0 / b) ** (1.0 / r)  # where P_candidate(s)=0.5 (approx, ignoring the (1-x)^b expansion)
        recall = sum(
            pair_is_candidate(sig[id_index[a]], sig[id_index[bb]], b, r) for a, bb in same_pairs
        ) / len(same_pairs)
        false_cand = sum(
            pair_is_candidate(sig[id_index[a]], sig[id_index[bb]], b, r) for a, bb in diff_pairs
        ) / len(diff_pairs)
        n_cand, elapsed = build_candidates(sig, b, r, notice_ids)
        pct = n_cand / total_possible_pairs * 100
        rows.append(dict(r=r, b=b, s_star=s_star, recall=recall, false_cand=false_cand,
                          n_cand=n_cand, pct=pct, elapsed=elapsed))
        out.append(f"{r:>3} {b:>4} {s_star:>12.3f} {recall:>12.3f} {false_cand:>16.3f} {n_cand:>16,} {pct:>8.4f}% {elapsed:>9.2f}s")

    text = "\n".join(out)
    print("\n" + text)
    with open(REPORT_DIR / "lsh_sweep.txt", "w", encoding="utf-8") as f:
        f.write(text + "\n")

    # ---------------- plot ----------------
    same_scores = [exact[p] for p in same_pairs]
    diff_scores = [exact[p] for p in diff_pairs]
    same_scores.sort()
    diff_scores.sort()

    chosen = next(row for row in rows if (row["r"], row["b"]) == (2, 63))

    s_axis = np.linspace(0, 1, 400)
    fig, ax = plt.subplots(figsize=(9, 6))
    for row in rows:
        r, b = row["r"], row["b"]
        y = p_candidate(s_axis, b, r)
        style = dict(linewidth=2.4, color="#c0392b") if (r, b) == (chosen["r"], chosen["b"]) else dict(linewidth=1.1, alpha=0.55)
        label = f"r={r}, b={b}" + ("  <-- adopted" if (r, b) == (chosen["r"], chosen["b"]) else "")
        ax.plot(s_axis, y, label=label, **style)

    # overlay real score distributions as rug/histograms on the s-axis
    ax.scatter(diff_scores, [0.02] * len(diff_scores), marker="|", color="#2c3e50", alpha=0.5, s=60,
               label="labelled 'different' pairs (true score)")
    ax.scatter(same_scores, [0.98] * len(same_scores), marker="|", color="#27ae60", alpha=0.5, s=60,
               label="labelled 'same' pairs (true score)")

    ax.axvline(0.215, color="gray", linestyle=":", linewidth=1)
    ax.text(0.218, 0.5, "merge threshold tau=0.215\n(Section A(a)/(b))", fontsize=8, color="gray")

    op_s = 0.215
    op_y = p_candidate(op_s, chosen["b"], chosen["r"])
    ax.scatter([op_s], [op_y], color="#c0392b", zorder=5, s=70)
    ax.annotate(
        f"operating point\nr={chosen['r']}, b={chosen['b']}\nP(candidate | s=0.215) = {op_y:.3f}\nmeasured recall on labelled 'same' = {chosen['recall']:.3f}",
        xy=(op_s, op_y), xytext=(0.35, 0.55),
        arrowprops=dict(arrowstyle="->", color="#c0392b"), fontsize=8.5, color="#c0392b",
    )

    ax.set_xlabel("true similarity s (Choice-2 Jaccard, Section A(a))")
    ax.set_ylabel("P(pair survives to candidate stage)")
    ax.set_title("LSH candidate-survival probability vs. true similarity\n(SetuBid tender dedup, k=126 MinHash, various (b,r) bandings)")
    ax.legend(fontsize=7.5, loc="center right")
    ax.set_xlim(0, 1)
    ax.set_ylim(-0.03, 1.03)
    fig.tight_layout()
    fig.savefig(REPORT_DIR / "lsh_scurve.png", dpi=150)
    print(f"\nSaved plot to {REPORT_DIR / 'lsh_scurve.png'}")


if __name__ == "__main__":
    main()
