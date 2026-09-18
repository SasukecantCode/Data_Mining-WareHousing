"""
Section A(b) -- trade exactness for space, deliberately.

Instead of storing each notice's full denoised-bigram shingle set (Section
A(a): mean 81 shingles/notice, range 19-205), hold a fixed-size MinHash
signature of k integers per notice and ESTIMATE Jaccard from it:

    est(X, Y) = (# positions where sig(X)[i] == sig(Y)[i]) / k

The whole point of this section is to fix k from a stated accuracy
requirement, argued BEFORE implementing it, then check on the real 900
labelled pairs whether the estimator behaved the way the argument
predicted -- including where it didn't. k is not chosen because 128 or 256
is a conventional MinHash size; it is derived below from the actual score
distribution measured in Section A(a).

--------------------------------------------------------------------------
THE ARGUMENT (done first, against reports/taskA/pair_scores.csv from
01_sectionA_similarity_score.py -- the exact-score classifier's own
operating threshold tau=0.215 and its distance-to-threshold distribution
over the 900 labelled pairs):

  p5 margin-to-threshold  ~= 0.091   (only the closest 5% of labelled pairs
                                       sit within this distance of tau)

We do not try to protect the closest 5% -- that band already contains most
of the exact-score classifier's own ~0.7% error rate, and protecting it
fully would need k in the thousands (shown in the sweep below). Instead we
require: a pair whose TRUE score sits at least the p5-margin away from tau
must survive sketch noise with at least ~97.7% probability (a classical
one-sided 2-sigma bound). That fixes the target standard error:

    SE_target = margin_p5 / 2

and MinHash's classical worst-case bound (valid for any true similarity s,
since s(1-s) <= 1/4 for all s in [0,1]):

    Var[estimate] <= 1/(4k)   =>   k >= 1 / (4 * SE_target^2) = 1 / margin_p5^2

is solved for k. Whatever integer falls out is what gets implemented --
not rounded to 128/256/512.
--------------------------------------------------------------------------
"""
import csv
import hashlib
import math
import random
import statistics
from pathlib import Path

import numpy as np
import importlib.util

spec = importlib.util.spec_from_file_location(
    "sectionA", Path(__file__).parent / "01_sectionA_similarity_score.py"
)
sectionA = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sectionA)

REPORT_DIR = Path(__file__).resolve().parents[1] / "reports" / "taskA"
PRIME = np.uint64(2_147_483_647)  # 2^31 - 1, a Mersenne prime; small enough that
# a*h fits in uint64 without overflow -- an implementation constant for the hash
# family, unrelated to the accuracy-driven choice of k below.
PAD_SENTINEL = np.uint64(PRIME)  # padding value that can never win a min()


def base_hash(token_tuple):
    """Deterministic hash of a shingle mod PRIME, independent of
    PYTHONHASHSEED (Python's built-in hash() is salted per-process and
    unsafe to persist)."""
    s = " ".join(token_tuple) if isinstance(token_tuple, tuple) else str(token_tuple)
    v = int.from_bytes(hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest(), "big")
    return v % int(PRIME)


def make_hash_family(k, seed):
    rng = random.Random(seed)
    a = np.array([rng.randrange(1, int(PRIME)) for _ in range(k)], dtype=np.uint64)
    b = np.array([rng.randrange(0, int(PRIME)) for _ in range(k)], dtype=np.uint64)
    return a, b


def build_padded_matrix(notice_ids, sets, base_cache):
    max_len = max(len(sets[n]) for n in notice_ids)
    mat = np.full((len(notice_ids), max_len), PAD_SENTINEL, dtype=np.uint64)
    for i, nid in enumerate(notice_ids):
        toks = list(sets[nid])
        if toks:
            mat[i, : len(toks)] = np.array([base_cache[t] for t in toks], dtype=np.uint64)
    return mat


def signatures_for_matrix(mat, a_arr, b_arr):
    """mat: (n_notices, max_len) padded base hashes. Returns (n_notices, k) signatures.
    Looped over k hash functions (vectorized over notices) to keep peak memory bounded."""
    n, max_len = mat.shape
    k = len(a_arr)
    sig = np.empty((n, k), dtype=np.uint64)
    for j in range(k):
        vals = (a_arr[j] * mat + b_arr[j]) % PRIME
        # padding sentinel maps to some value under the affine hash too, but
        # since it's identical across all rows for a given j and typically
        # far from the true min of a non-empty real set, guard explicitly:
        vals = np.where(mat == PAD_SENTINEL, np.uint64(PRIME), vals)
        sig[:, j] = vals.min(axis=1)
    return sig


def estimate_from_signatures(sig_a, sig_b):
    return float((sig_a == sig_b).mean())


def derive_k(exact_scores, tau, protect_quantile=0.05, sigma=2.0):
    margins = sorted(abs(s - tau) for s in exact_scores)
    idx = max(0, int(protect_quantile * len(margins)) - 1)
    margin_p = margins[idx]
    se_target = margin_p / sigma
    k = math.ceil(1.0 / (4 * se_target ** 2))
    return k, margin_p, se_target


def run_at_k(notice_ids, id_index, mat, pairs, k, seed):
    a_arr, b_arr = make_hash_family(k, seed)
    sig = signatures_for_matrix(mat, a_arr, b_arr)
    ests = []
    for a, b, label in pairs:
        est = estimate_from_signatures(sig[id_index[a]], sig[id_index[b]])
        ests.append((a, b, label, est))
    return ests


def main():
    notices = sectionA.load_notices()
    stopwords, structural_cache = sectionA.build_corpus_stopwords(notices, 0.40)
    pairs = sectionA.load_labelled_pairs()

    # sketching/estimation is only measured on the notices the labelled pairs
    # actually touch -- computing signatures for all 12,000 would just be
    # wasted work for a check that only ever reads these ~1,700
    notice_ids = sorted({nid for a, b, _ in pairs for nid in (a, b)})
    sets = {nid: sectionA.choice2_tokens(structural_cache[nid], stopwords) for nid in notice_ids}
    id_index = {nid: i for i, nid in enumerate(notice_ids)}

    all_tokens = set()
    for s in sets.values():
        all_tokens.update(s)
    base_cache = {t: base_hash(t) for t in all_tokens}
    mat = build_padded_matrix(notice_ids, sets, base_cache)

    exact = {(a, b): sectionA.jaccard(sets[a], sets[b]) for a, b, _ in pairs}
    exact_scores = list(exact.values())
    tau = 0.215  # adopted threshold, Section A(a)

    k, margin_p5, se_target = derive_k(exact_scores, tau, protect_quantile=0.05, sigma=2.0)

    out = []
    out.append("=== The argument ===")
    out.append(f"tau (adopted threshold, Section A(a)) = {tau}")
    out.append(f"p5 margin-to-threshold across 900 labelled pairs = {margin_p5:.4f}")
    out.append(f"SE_target (margin/2, one-sided 2-sigma) = {se_target:.4f}")
    out.append(f"k = ceil(1 / margin_p5^2) = {k}   <- derived, not a round-number default")
    out.append("")

    # baseline: exact-score classification accuracy (for comparison)
    def acc_at(scores_by_label, thr):
        correct = 0
        for a, b, label in pairs:
            s = scores_by_label[(a, b)]
            pred = "same" if s >= thr else "different"
            correct += pred == label
        return correct / len(pairs)

    exact_acc = acc_at(exact, tau)
    out.append(f"Exact-score classification accuracy at tau={tau}: {exact_acc:.4f}")
    out.append("")

    out.append("=== Sweep: does error actually shrink like the 1/sqrt(k) argument predicts? ===")
    out.append(f"{'k':>6} {'trials':>7} {'mean|err|':>10} {'observed_SE':>12} {'theory_SE(worst-case)':>22} {'flips_vs_exact':>15} {'acc':>7}")
    ks_to_test = sorted(set([25, 50, 100, k, 200, 400, 800]))
    N_TRIALS = 8
    for kk in ks_to_test:
        errs = []
        flip_counts = []
        acc_list = []
        for trial in range(N_TRIALS):
            ests = run_at_k(notice_ids, id_index, mat, pairs, kk, seed=1000 + trial)
            flips = 0
            correct = 0
            for a, b, label, est in ests:
                true_s = exact[(a, b)]
                errs.append(abs(est - true_s))
                exact_pred = "same" if true_s >= tau else "different"
                est_pred = "same" if est >= tau else "different"
                if exact_pred != est_pred:
                    flips += 1
                correct += (est_pred == label)
            flip_counts.append(flips)
            acc_list.append(correct / len(pairs))
        observed_se = statistics.pstdev(errs)
        theory_se_worst_case = math.sqrt(1 / (4 * kk))
        out.append(
            f"{kk:>6} {N_TRIALS:>7} {statistics.mean(errs):>10.4f} {observed_se:>12.4f} "
            f"{theory_se_worst_case:>22.4f} {statistics.mean(flip_counts):>15.1f} {statistics.mean(acc_list):>7.4f}"
        )

    out.append("")
    out.append(f"=== Detail at the derived k={k} (averaged over {N_TRIALS} independent hash families) ===")
    all_errs_by_region = {"same": [], "different": []}
    all_flips_detail = []
    for trial in range(N_TRIALS):
        ests = run_at_k(notice_ids, id_index, mat, pairs, k, seed=2000 + trial)
        for a, b, label, est in ests:
            true_s = exact[(a, b)]
            all_errs_by_region[label].append(abs(est - true_s))
            exact_pred = "same" if true_s >= tau else "different"
            est_pred = "same" if est >= tau else "different"
            if exact_pred != est_pred:
                all_flips_detail.append((a, b, label, true_s, est, trial))

    for label in ("same", "different"):
        errs = all_errs_by_region[label]
        out.append(f"  label={label:10s} mean|err|={statistics.mean(errs):.4f}  observed_SE={statistics.pstdev(errs):.4f}")

    out.append(f"  total flips across {N_TRIALS} trials x 900 pairs = {len(all_flips_detail)}  "
               f"({len(all_flips_detail)/(N_TRIALS*len(pairs))*100:.2f}% of trial-pairs)")
    out.append("  flip examples (true_score, estimate, trial):")
    seen_pairs = set()
    for a, b, label, true_s, est, trial in all_flips_detail:
        if (a, b) in seen_pairs:
            continue
        seen_pairs.add((a, b))
        margin = abs(true_s - tau)
        out.append(f"    {a}/{b} label={label:10s} true={true_s:.3f} margin_to_tau={margin:.3f} est={est:.3f} (trial {trial})")

    text = "\n".join(out)
    print(text)
    with open(REPORT_DIR / "minhash_sketch.txt", "w", encoding="utf-8") as f:
        f.write(text + "\n")


if __name__ == "__main__":
    main()
