"""
Section A(a) -- define "similar" mechanically and measure it on the real corpus.

Loads all 12,000 notices and the 900 human-adjudicated pairs
(data_2/labelled_pairs.csv) and scores every pair under two competing
representations of a notice:

  Choice 1: word unigrams, bag-of-words over the raw title+body. No
            decomposition-granularity decision beyond "split on whitespace",
            no signal/noise decision at all.

  Choice 2: word bigrams over a denoised signal stream. Two noise removals:
            (a) structural -- drop the portal preamble/footer, and the
                GENERAL CONDITIONS / CONTACT / KEY DATES sections, and the
                reference-number/money-format fields, because these are
                either portal boilerplate or fields the case data itself
                says have no cross-portal crosswalk;
            (b) statistical -- drop tokens whose document frequency across
                the full 12,000-notice corpus exceeds a threshold, which
                catches the recurring "the work comprises ... as detailed in
                the bill of quantities and the approved drawings"-style
                department template without hand-listing every phrase.

Both choices are scored with Jaccard similarity on the resulting token sets.
Outputs: reports/taskA/pair_scores.csv, reports/taskA/summary.txt
"""
import csv
import glob
import re
import statistics
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # LAB-1/Question-2
DATA_DIR = ROOT / "data_2"
NOTICES_DIR = DATA_DIR / "notices"
LABELLED_PAIRS = DATA_DIR / "labelled_pairs.csv"
REPORT_DIR = Path(__file__).resolve().parents[1] / "reports" / "taskA"

SECTION_HEADERS = {
    "SCOPE OF WORK",
    "ABSTRACT BILL OF QUANTITIES",
    "KEY DATES",
    "CONTACT",
    "GENERAL CONDITIONS",
}
# sections whose lines are structural noise (portal-format metadata / a small
# pool of filler sentences, not tender identity)
NOISE_SECTIONS = {"KEY DATES", "CONTACT", "GENERAL CONDITIONS"}

FIELD_RE = re.compile(
    r"^(Name of work|Tender reference number|Procuring entity|"
    r"Estimated cost put to tender|Earnest money deposit|Cost of tender document|"
    r"Period of completion|Eligibility|"
    r"Minimum average annual turnover in the last three financial years|"
    r"Experience of at least one similar completed work of value not less than|"
    r"Bid validity):\s*(.*)$"
)
# fields dropped as signal: reference number has no cross-portal crosswalk
# (portal_profiles.md); money fields are the same figure in five different
# text formats (Rs./INR/lakh/Cr/bare digits) -- the corpus already parses
# the true figure into estimated_value, so the raw text of these fields adds
# format noise, not identity signal.
DROP_FIELDS = {
    "tender reference number",
    "estimated cost put to tender",
    "earnest money deposit",
    "cost of tender document",
    "minimum average annual turnover in the last three financial years",
    "experience of at least one similar completed work of value not less than",
}

TOKEN_RE = re.compile(r"[a-z0-9]+(?:[.+-][a-z0-9]+)*")


def load_notices():
    notices = {}
    for fp in sorted(NOTICES_DIR.glob("part-*.csv")):
        with open(fp, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                notices[row["notice_id"]] = row
    return notices


def load_labelled_pairs():
    pairs = []
    with open(LABELLED_PAIRS, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            pairs.append((row["notice_id_a"], row["notice_id_b"], row["label"]))
    return pairs


def jaccard(a, b):
    u = a | b
    return len(a & b) / len(u) if u else 1.0


# ---------------- Choice 1: raw unigrams, no noise removal ----------------

def choice1_tokens(notice):
    text = (notice["title"] + " " + notice["body"]).lower()
    return set(TOKEN_RE.findall(text))


# ---------------- Choice 2: structurally + statistically denoised bigrams ----------------

def structural_signal_tokens(notice):
    """Strip portal preamble/footer + noise sections + format-variant fields.
    Return the ordered list of surviving word tokens (order kept, for bigrams)."""
    body = notice["body"]
    idx = body.find("Name of work:")
    if idx > 0:
        body = body[idx:]  # drops any preamble/legal boilerplate before the first field

    tokens = list(TOKEN_RE.findall(notice["title"].lower()))
    section = None
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.upper() in SECTION_HEADERS:
            section = line.upper()
            continue
        if section in NOISE_SECTIONS:
            continue
        m = FIELD_RE.match(line)
        if m:
            field, value = m.group(1).lower(), m.group(2)
            if field in DROP_FIELDS:
                continue
            tokens.extend(TOKEN_RE.findall(value.lower()))
            continue
        # SCOPE OF WORK / ABSTRACT BILL OF QUANTITIES / no-section-yet lines
        tokens.extend(TOKEN_RE.findall(line.lower()))
    return tokens


def build_corpus_stopwords(notices, df_threshold=0.40):
    """Statistical noise decision: any token appearing in more than
    df_threshold of notices (after structural stripping) carries ~zero
    identity signal -- it's department-template vocabulary, not tender
    identity. Measured empirically on the corpus, not hand-listed."""
    n = len(notices)
    df = Counter()
    cache = {}
    for nid, notice in notices.items():
        toks = structural_signal_tokens(notice)
        cache[nid] = toks
        df.update(set(toks))
    stopwords = {t for t, c in df.items() if c / n > df_threshold}
    return stopwords, cache


def choice2_tokens(tokens, stopwords):
    content = [t for t in tokens if t not in stopwords]
    if len(content) < 2:
        return set(content)
    return set(zip(content, content[1:]))


def separation_stats(scores_same, scores_diff):
    best_thr, best_acc = None, -1
    all_scores = sorted(set(scores_same + scores_diff))
    for thr in all_scores:
        tp = sum(1 for s in scores_same if s >= thr)
        tn = sum(1 for s in scores_diff if s < thr)
        acc = (tp + tn) / (len(scores_same) + len(scores_diff))
        if acc > best_acc:
            best_acc, best_thr = acc, thr
    return best_thr, best_acc


def main():
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    print("Loading 12,000 notices ...")
    notices = load_notices()
    pairs = load_labelled_pairs()
    print(f"Loaded {len(notices)} notices, {len(pairs)} labelled pairs.")

    print("Computing corpus document frequencies for statistical stopwording ...")
    stopwords, structural_cache = build_corpus_stopwords(notices, df_threshold=0.40)
    print(f"Corpus-frequency stopwords found: {len(stopwords)}")
    print("Examples:", sorted(list(stopwords))[:25])

    c1_cache = {nid: choice1_tokens(n) for nid, n in notices.items()}
    c2_cache = {nid: choice2_tokens(structural_cache[nid], stopwords) for nid in notices}

    rows = []
    for a, b, label in pairs:
        s1 = jaccard(c1_cache[a], c1_cache[b])
        s2 = jaccard(c2_cache[a], c2_cache[b])
        rows.append((a, b, label, s1, s2))

    with open(REPORT_DIR / "pair_scores.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["notice_id_a", "notice_id_b", "label", "choice1_unigram_raw", "choice2_bigram_denoised"])
        w.writerows(rows)

    same1 = [r[3] for r in rows if r[2] == "same"]
    diff1 = [r[3] for r in rows if r[2] == "different"]
    same2 = [r[4] for r in rows if r[2] == "same"]
    diff2 = [r[4] for r in rows if r[2] == "different"]

    thr1, acc1 = separation_stats(same1, diff1)
    thr2, acc2 = separation_stats(same2, diff2)

    lines = []
    lines.append(f"Notices: {len(notices)}  Labelled pairs: {len(pairs)} (same={len(same1)}, different={len(diff1)})")
    lines.append(f"Corpus-frequency stopwords (df > 40% of {len(notices)} notices): {len(stopwords)} tokens")
    lines.append("")
    lines.append("Choice 1 -- word unigrams, raw text, no noise removal")
    lines.append(f"  same:      mean={statistics.mean(same1):.3f}  median={statistics.median(same1):.3f}  min={min(same1):.3f}  max={max(same1):.3f}")
    lines.append(f"  different: mean={statistics.mean(diff1):.3f}  median={statistics.median(diff1):.3f}  min={min(diff1):.3f}  max={max(diff1):.3f}")
    lines.append(f"  best single threshold: {thr1:.3f} -> accuracy {acc1:.3f}")
    lines.append("")
    lines.append("Choice 2 -- word bigrams, structurally + statistically denoised")
    lines.append(f"  same:      mean={statistics.mean(same2):.3f}  median={statistics.median(same2):.3f}  min={min(same2):.3f}  max={max(same2):.3f}")
    lines.append(f"  different: mean={statistics.mean(diff2):.3f}  median={statistics.median(diff2):.3f}  min={min(diff2):.3f}  max={max(diff2):.3f}")
    lines.append(f"  best single threshold: {thr2:.3f} -> accuracy {acc2:.3f}")
    lines.append("")
    lines.append("Worked example -- N010018/N010020 (labelled 'same', corrigendum republish):")
    for a, b, label, s1, s2 in rows:
        if {a, b} == {"N010018", "N010020"}:
            lines.append(f"  choice1={s1:.3f}  choice2={s2:.3f}")
    lines.append("Worked example -- N007876/N008565 (labelled 'different'):")
    for a, b, label, s1, s2 in rows:
        if {a, b} == {"N007876", "N008565"}:
            lines.append(f"  choice1={s1:.3f}  choice2={s2:.3f}")

    summary = "\n".join(lines)
    print("\n" + summary)
    with open(REPORT_DIR / "summary.txt", "w", encoding="utf-8") as f:
        f.write(summary + "\n")


if __name__ == "__main__":
    main()
