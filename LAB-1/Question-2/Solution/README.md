# SetuBid tender deduplication — Question 2

**Section A(a)** solves: *"before anything can be compared cheaply, commit to
a way of turning a notice into something over which a similarity score
between two notices is well defined, state the score, and prove — on the
given corpus, not a toy example — that it actually separates known-same
pairs from known-different pairs."*

Setup (only `03_sectionA_minhash_sketch.py` needs numpy; `01`/`02` are
stdlib-only):

```bash
cd LAB-1/Question-2/Solution
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

All numbers below are measured against the real data in `../data_2/`:
12,000 notices, 900 human-adjudicated pairs (`labelled_pairs.csv`: 279
`same`, 621 `different`). `../data_2/_truth/` (the full cluster answer key)
was deliberately **not** read while designing the score below — using it to
tune the method would be leakage; it exists for a final, honest check after
the method is fixed, not for shaping the method itself.

Run it yourself:

```bash
cd LAB-1/Question-2/Solution
python3 scripts/01_sectionA_similarity_score.py
```

Output: `reports/taskA/pair_scores.csv` (per-pair score under both choices)
and `reports/taskA/summary.txt` (the numbers reproduced below).

## Task A(a) — defining "similar" mechanically

A notice is projected into a **set of tokens**, and similarity between two
notices is the **Jaccard index** of their token sets:

```
sim(X, Y) = |S(X) ∩ S(Y)| / |S(X) ∪ S(Y)|
```

`S(·)` — the function that turns a notice into a token set — is where the
two required decisions actually live:

1. **Decomposition granularity**: unigram (bag-of-words, order thrown away)
   vs. bigram (adjacent-token pairs, order preserved locally).
2. **Signal vs. noise**: which spans of the notice are allowed to
   contribute tokens at all.

### Choice 1 — word unigrams, no noise removal

Bag-of-words over the raw `title + body`, lowercased, everything kept:
portal preamble, reference number, formatted money strings, contact block,
boilerplate legal conditions, the lot.

### Choice 2 — word bigrams, denoised (adopted)

Two noise removals, both mechanical and reproducible from `portal_profiles.md`
and direct inspection of the corpus, not guesswork:

- **Structural.** Drop everything before the first `Name of work:` field
  (this is exactly the portal preamble the profile notes complain about —
  `NATIONAL PROCUREMENT AGGREGATION SERVICE` / `STATE PROCUREMENT CELL`
  blocks, ~1,400 characters pasted unchanged onto every notice from six
  nodal portals). Drop the `KEY DATES`, `CONTACT`, and `GENERAL CONDITIONS`
  sections (dates are portal-format noise already captured separately in
  `published_at`/`closing_date`; contact and general-conditions text turn
  out, by inspection of confirmed same-tender pairs, to vary copy-to-copy —
  they read like filler drawn from a small pool, not tender identity). Drop
  the `Tender reference number`, `Estimated cost`, `Earnest money deposit`,
  `Cost of tender document`, turnover, and experience-value fields — the
  case data explicitly says reference numbers have no cross-portal
  crosswalk, and money appears in five incompatible formats
  (`Rs. 4,50,00,000/-`, `INR 4.500 Cr`, `45000000`, `RUPEES ... ONLY`) that
  the corpus already normalizes into `estimated_value` — matching on their
  raw text rewards format luck, not identity.
- **Statistical.** Among what survives structural stripping, compute each
  token's document frequency across all 12,000 notices and drop tokens
  appearing in more than 40% of them. This is a measured, not hand-listed,
  way of catching the recurring department template (*"the work comprises
  ... including all associated earthwork, sub-base and base courses, cross
  drainage structures, protective works, road furniture and incidental
  items as detailed in the bill of quantities and the approved drawings"*)
  — 209 tokens cross that line on this corpus.

What survives — title, procuring entity, eligibility class, and the
scope-of-work / bill-of-quantities sentences (which is where the location
name, chainage ranges, and reach numbers that are actually specific to one
physical tender live) — is then shingled into **word bigrams**, so that
adjacency is preserved: `ramgarh` next to `devipur` means something that
`ramgarh` and `devipur` floating independently in a bag do not.

## Measured separation (900 labelled pairs, real corpus)

| | Choice 1: raw unigram | Choice 2: denoised bigram |
|---|---|---|
| same — mean / median | 0.722 / 0.716 | 0.718 / 0.835 |
| same — min / max | 0.271 / 0.996 | 0.126 / 1.000 |
| different — mean / median | 0.440 / 0.440 | 0.011 / 0.000 |
| different — min / max | 0.214 / 0.672 | 0.000 / 0.217 |
| best single-threshold accuracy | **0.893** | **0.993** |
| mean token-set size (200-notice sample) | 356 | 82 |

Choice 1 separates the two classes, but poorly: its `same` and `different`
distributions overlap heavily (`same` goes as low as 0.271, `different`
goes as high as 0.672 — a 0.4-wide dead zone with no safe threshold).
That's the boilerplate-homogeneity trap the ops team's nightly job is
presumably falling into: two *different* tenders scraped from the same
nodal portal share so much pasted preamble and department-template
vocabulary at unigram granularity that they look nearly as similar as two
copies of the *same* tender.

Choice 2 nearly eliminates the overlap (`different` tops out at 0.217,
`same` drops as low as 0.126 only in one heavily-truncated corrigendum
copy) and gets there with token sets ~4.3× smaller, so it is cheaper per
comparison as well as more correct — not a trade-off, a strict improvement.

Worked examples (real notices, not constructed):

- `N010018` / `N010020` — labelled **same** (`SPC/2025-26/041336` vs bare
  `00061681`, portal `P004` vs `P008`, one with the full State Procurement
  Cell preamble, one apparently truncated by its portal): choice 1 = 0.301,
  choice 2 = 0.235.
- `N007876` / `N008565` — labelled **different** (two unrelated tenders
  both scraped through nodal aggregators, so both carry heavy legal
  boilerplate): choice 1 = 0.405, choice 2 = 0.000.

Notice that under Choice 1 the *different* pair (0.405) scores closer to
"same" than the *same* pair (0.301) does — raw unigram similarity is
actually non-monotonic with respect to ground truth here. Choice 2 gets
both pairs on the correct side of any reasonable threshold.

### Adopted choice and its cost

**Choice 2**: word-bigram shingles over structurally- and
statistically-denoised signal tokens, scored by Jaccard. Per-pair cost is a
hash-set intersection/union over sets averaging 82 tokens (vs. 356 for
Choice 1), so it is both the more accurate score (0.993 vs. 0.893
best-threshold accuracy on the labelled set) and the cheaper one to
evaluate per pair. The one-time cost of the statistical stopword pass is a
single linear scan of the corpus (`O(total tokens)`, done once, not per
comparison).

### Self-check: was this actually earned, or just the first thing tried?

The design above was worked out on a hand-built toy example before this
corpus was found, then carried over largely unchanged. That is a real risk
— a choice that looks right on an invented pair can be an artifact of how
the pair was constructed. `scripts/02_sectionA_ablation.py` re-derives it
honestly by decomposing the two decisions and sweeping the free parameter,
against the real 900 labelled pairs (`reports/taskA/ablation.txt`):

| variant | accuracy | mean(same) | mean(different) |
|---|---|---|---|
| raw + unigram | 0.893 | 0.722 | 0.440 |
| raw + bigram (granularity alone) | 0.889 | 0.659 | 0.323 |
| denoised + unigram (denoising alone) | 0.986 | 0.746 | 0.051 |
| **denoised + bigram (adopted)** | **0.993** | 0.718 | 0.011 |

This overturns part of the original framing. Bigram granularity was
presented as the important lever (adjacency of "ramgarh"/"devipur"); on
the real corpus it isn't — bigram alone, with no denoising, is *not even
an improvement* on raw unigram (0.889 vs 0.893). The decision doing almost
all the work is **which text counts as signal**: stripping preamble,
noise sections, and the corpus's own high-frequency template vocabulary
takes accuracy from 0.893 to 0.986 by itself. Bigramming on top of that
denoised stream is a real but secondary refinement (0.986 → 0.993) — it's
kept because it's a strict improvement at no extra cost, not because it's
the main mechanism.

Checked and not just asserted:

- **Threshold sensitivity.** The 40%-document-frequency stopword cutoff is
  not a fragile, hand-tuned number — accuracy stays in 0.989–0.994 for any
  cutoff between 15% and 60%, and only degrades once the cutoff is so
  loose (≥80%) that department-template vocabulary starts leaking back in
  (0.981), or so loose it's disabled entirely (0.972 — still beating raw
  unigram, because structural stripping alone already does most of the
  work).
- **Character-shingle alternative**, same structural denoising, no
  stopwording: 3/5/8-char shingles score 0.941/0.956/0.963 — worse than
  denoised word-bigrams *and* 12–24× larger token sets (~1,000–2,000
  shingles vs. 82). Ruled out on both correctness and cost.
- **Higher-order word n-grams**: trigrams (0.992) and 4-grams (0.991) are
  statistically indistinguishable from bigrams (0.993) but shrink the
  token sets further, discarding evidence for no measured gain. Bigram is
  the smallest n that captures adjacency; going further doesn't pay for
  itself here.

Conclusion: the adopted method (denoised word-bigrams, Jaccard) is still
the best-measured configuration, but for a different reason than first
assumed — denoising, not decomposition granularity, is what makes this
corpus tractable to compare correctly. That's worth stating plainly rather
than leaving the original (partly incorrect) justification standing.

## Task A(b) — trade exactness for space, deliberately

**Solves:** *"instead of holding the whole corpus, hold a reduced,
fixed-size form of each notice and accept the similarity computed from it
as an estimate — fix that size by arguing from a stated accuracy
requirement before implementing it, then measure the actual error against
`labelled_pairs.csv` and report honestly whether the estimator behaved as
argued, including where it didn't."*

Run it yourself:

```bash
cd LAB-1/Question-2/Solution
.venv/bin/python scripts/03_sectionA_minhash_sketch.py   # needs numpy; see .venv setup below
```

Output: `reports/taskA/minhash_sketch.txt` (the numbers reproduced below).

### The reduced form

Each notice's exact Choice-2 token set (Section A(a): mean 81 shingles,
range 19–205, so its storage size is variable and grows with document
length) is replaced by a **MinHash signature**: `k` fixed hash functions,
each producing the minimum hashed shingle value over the set. Two
notices' Jaccard similarity is then *estimated* as the fraction of the `k`
positions where their signatures agree:

```
est(X, Y) = |{i : sig(X)[i] == sig(Y)[i]}| / k
```

This is a real trade: a notice now costs exactly `k` integers to store,
*independent of how long its text is* — a truncated 600-character notice
and an untruncated 3,000-character one cost the same — at the price of
turning an exact score into a noisy one.

### The argument for `k` (done before implementing anything)

Section A(a) fixed a decision threshold `τ = 0.215` on the exact score.
Across the 900 labelled pairs, the **distance from each pair's exact score
to τ** has a 5th-percentile value of **0.089** — only the closest 5% of
labelled pairs sit nearer to the boundary than that. Trying to protect
that closest 5% fully is not worth it (that band already contains most of
the exact classifier's own 0.8% error rate, `2/279` "same" pairs land
below τ at exact score already — no sketch fixes that). What *is* worth
requiring: a pair whose true score sits at least that p5-margin from τ
should survive sketch noise at least ~97.7% of the time — a classical
one-sided 2σ bound. That fixes a target standard error:

```
SE_target = margin_p5 / 2 = 0.089 / 2 ≈ 0.0446
```

MinHash's classical worst-case variance bound, true for *any* similarity
`s` since `s(1-s) ≤ 1/4`:

```
Var[estimate] ≤ 1/(4k)   =>   k ≥ 1 / (4·SE_target²) = 1 / margin_p5²
```

gives **k = ⌈1 / 0.089²⌉ = 126**. That's the number implemented — not 128
(the number a MinHash tutorial would reach for), even though it lands
close to it by coincidence of this corpus's own margin distribution, not
because 128 is a customary sketch size.

### Measured against the real labelled pairs

| k | mean\|error\| | observed SE | worst-case theoretical SE | extra flips / 900 (vs. exact) | accuracy |
|---|---|---|---|---|---|
| 25 | 0.0228 | 0.0383 | 0.1000 | 16.1 | 0.980 |
| 50 | 0.0163 | 0.0274 | 0.0707 | 11.0 | 0.984 |
| 100 | 0.0118 | 0.0197 | 0.0500 | 8.8 | 0.988 |
| **126 (adopted)** | **0.0101** | **0.0169** | **0.0445** | **7.4** | **0.987** |
| 200 | 0.0083 | 0.0140 | 0.0354 | 4.9 | 0.991 |
| 400 | 0.0058 | 0.0097 | 0.0250 | 4.8 | 0.992 |
| 800 | 0.0042 | 0.0070 | 0.0177 | 2.6 | 0.993 |

(each row averaged over 8 independent hash-function families, against the
900 labelled pairs; exact-score accuracy at τ=0.215 is 0.9922.)

### Did it behave as argued? Yes, on the two things that were actually
### checkable — and one place the one-line argument needed more care.

**Matched: the 1/√k scaling.** Doubling k should cut error by ≈1.41×.
Observed successive ratios: 1.40, 1.38, 1.42, 1.43, 1.38 — all within 2%
of √2. The theory's *shape* is exactly right.

**Matched, in the expected direction: worst-case SE is conservative.**
At k=126 the worst-case bound predicts SE=0.0445; observed is 0.0169,
~2.6× tighter. That's expected, not a discrepancy — the worst-case bound
assumes every pair sits at the highest-variance point s=0.5, but this
corpus's real scores cluster near 0 (`different`, mean 0.011) or 0.7–0.8
(`same`). Confirming this: the error broken out by label shows
`different` pairs (near s≈0, low variance) at SE=0.0085, `same` pairs
(near higher s, higher variance) at SE=0.0220 — the s(1-s) shape holds.

**Where the simple version of the argument was too simple.** Read
literally, "protect the p5-margin pairs at 2σ" suggests you should expect
about 1 flip (2.3% of ~45 at-risk pairs) at k=126. The actual result is
7.4 — about 7× that naive reading. The k-derivation is still correct; the
one-line sanity check of it is not sufficient by itself. Risk isn't
concentrated only at exactly the p5 boundary — every pair inside it (down
to margin 0, of which there are several) contributes more probability
than a single point estimate captures. Integrating the true per-pair flip
probability `1 − Φ(margin / SE(s))` over *all 900* pairs (not just
checking the boundary pair) predicts **7.29 flips** — almost exactly the
observed 7.4. So: the k-sizing argument holds up quantitatively, but only
once verified with the full distribution, not a single representative
point. That gap between "derivation is right" and "the shorthand
justification of it is right" is exactly the kind of thing this exercise
is meant to catch, and it's reported here rather than smoothed over.

### Cost

Signature values here are all `< 2³¹`, so each fits in 4 bytes: a k=126
signature costs a **fixed 504 bytes/notice**, regardless of document
length. The exact representation it replaces costs `19×8` to `205×8` = 152
to 1,640 bytes (8-byte hashed bigrams), scaling with how long the notice
text happens to be — including the handful of very long, untruncated
notices at the top of that range. The sketch is worse than the *average*
exact set (504 vs. ~648 bytes) but bounds the *worst case* (504 vs. 1,640)
and gives every pairwise comparison a fixed `O(k)` cost instead of a
variable `O(|A|+|B|)` one — the property this section actually asked to
trade for, not raw average-case space.

At the adopted k=126 the price for that fixed cost is a 0.5-point
accuracy drop (99.22% → 98.74%) on the labelled set, concentrated almost
entirely among pairs that were already within a few hundredths of the
decision boundary at exact score.

This section only fixes the **score**. It does not yet address the
`O(n²)` blow-up from evaluating that score on every pair — at ~12,000
notices growing by ~400/week, still means comparing every pair nightly,
which is the failure the ops team already hit (31-hour run, killed). That
is the next problem (candidate generation / blocking before the score is
ever invoked), not this one.
