"""
Automatic topic discovery for complaints (scripts/topics.py).

Keyword theming (in analyze.py) is precise but can only find themes you thought of.
This module does the opposite: it clusters the actual complaint text to surface
themes you DIDN'T hardcode — emerging issues you'd otherwise miss.

We use TF-IDF + KMeans from scikit-learn rather than BERTopic on purpose: it's light
(no transformers / GPU), fast, fully offline, and transparent — each cluster is
summarized by its top terms, which is easy to explain in an interview. BERTopic is a
reasonable heavier alternative once you have tens of thousands of documents.

Usage:
    python scripts/topics.py --clusters 6 --negative-only
"""
import os
import sys
import argparse

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scripts.database import get_connection, init_db


def _load_texts(conn, negative_only=True, limit=5000):
    """Pull cleaned complaint text from the scored table."""
    q = "SELECT clean_text FROM scored_mentions WHERE clean_text IS NOT NULL"
    if negative_only:
        q += " AND sentiment_label = 'negative'"
    q += " ORDER BY created_utc DESC LIMIT ?"
    return [r[0] for r in conn.execute(q, (limit,)) if r[0] and len(r[0]) > 8]


def discover_topics(texts, n_clusters=6, top_terms=6):
    """Cluster `texts` and return a list of {size, terms, examples} per topic.

    Returns [] if there isn't enough text to cluster meaningfully.
    """
    # Lazy imports so the rest of the project doesn't depend on scikit-learn.
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.cluster import KMeans

    # Need clearly more documents than clusters for this to mean anything.
    if len(texts) < max(n_clusters * 3, 12):
        return []

    vectorizer = TfidfVectorizer(
        max_df=0.6,          # ignore words in >60% of docs (too generic)
        min_df=2,            # ignore words appearing in only one doc
        stop_words="english",
        ngram_range=(1, 2),  # unigrams + bigrams ("late delivery")
        max_features=2000,
    )
    try:
        matrix = vectorizer.fit_transform(texts)
    except ValueError:
        # Texts too homogeneous (e.g. near-duplicate complaints): after pruning, no
        # terms survive. That's not an error worth crashing on — just nothing to cluster.
        return []
    terms = vectorizer.get_feature_names_out()
    if matrix.shape[1] == 0:
        return []

    k = min(n_clusters, matrix.shape[0])
    model = KMeans(n_clusters=k, random_state=42, n_init=10)
    labels = model.fit_predict(matrix)

    topics = []
    for c in range(k):
        members = [i for i, lab in enumerate(labels) if lab == c]
        if not members:
            continue
        # Top terms = highest weights in this cluster's centroid.
        centroid = model.cluster_centers_[c]
        top_idx = centroid.argsort()[::-1][:top_terms]
        topics.append({
            "size": len(members),
            "terms": [terms[i] for i in top_idx],
            "examples": [texts[i] for i in members[:2]],
        })
    topics.sort(key=lambda t: t["size"], reverse=True)
    return topics


def main():
    parser = argparse.ArgumentParser(description="Auto-discover complaint topics by clustering.")
    parser.add_argument("--clusters", type=int, default=6)
    parser.add_argument("--negative-only", action="store_true", default=True)
    parser.add_argument("--all", dest="negative_only", action="store_false",
                        help="cluster all mentions, not just negative ones")
    args = parser.parse_args()

    conn = get_connection()
    init_db(conn)
    texts = _load_texts(conn, negative_only=args.negative_only)
    conn.close()

    topics = discover_topics(texts, n_clusters=args.clusters)
    if not topics:
        print("Not enough complaint text to cluster yet. Collect more data first.")
        return

    print(f"Discovered {len(topics)} topic clusters from {len(texts)} complaints:\n")
    for i, t in enumerate(topics, 1):
        print(f"  Topic {i}  ({t['size']} complaints)")
        print(f"    key terms: {', '.join(t['terms'])}")
        if t["examples"]:
            print(f"    example:   \"{t['examples'][0][:90]}\"")
        print()


if __name__ == "__main__":
    main()
