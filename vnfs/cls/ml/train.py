#!/usr/bin/env python3
"""Trains the flow classifier (scikit-learn) and writes model.joblib + model.meta.json.
Usage: python3 train.py [--model tree|forest] [--synthetic N] [--data a.csv ...] [--out model.joblib]
  --model        tree = DecisionTreeClassifier (default: fast, readable), forest = RandomForest
  --synthetic N  synthetic flows per class (default 1000, 0 = only real data)
  --data         CSVs with a 'label' column plus the feature columns (default: data/*.csv),
                 e.g. flows recorded by the VNF in collect mode (/var/log/cls.flows.csv)
Both models are compared (holdout split + 5-fold cross-validation); the chosen one is then
trained on all the data and saved.
"""
import argparse
import csv
import glob
import json
import os
import random
import time

import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from sklearn.tree import DecisionTreeClassifier, export_text

import synth
from features import FEATURES, MAX_WAIT, WINDOW

HERE = os.path.dirname(os.path.abspath(__file__))
CLASSES = ["voip", "video", "bulk"]


def make(kind, seed, depth):
    if kind == "tree":
        return DecisionTreeClassifier(max_depth=depth, min_samples_leaf=5, random_state=seed)
    return RandomForestClassifier(n_estimators=100, max_depth=depth + 4, min_samples_leaf=2,
                                  random_state=seed)


def load_csv(path):
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if r.get("label") in CLASSES:
                rows.append((r["label"], os.path.basename(path), [float(r[k]) for k in FEATURES]))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=["tree", "forest"], default="tree")
    ap.add_argument("--synthetic", type=int, default=1000)
    ap.add_argument("--data", nargs="*", default=None)
    ap.add_argument("--out", default=os.path.join(HERE, "model.joblib"))
    ap.add_argument("--depth", type=int, default=6)
    ap.add_argument("--test", type=float, default=0.25)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    say = (lambda *x: None) if a.quiet else print

    files = a.data if a.data is not None else sorted(glob.glob(os.path.join(HERE, "data", "*.csv")))
    rows = []
    if a.synthetic:
        rows += [(lbl, "synthetic", x) for lbl, _p, x in synth.generate(a.synthetic, a.seed)]
    for f in files:
        if os.path.exists(f):
            got = load_csv(f)
            rows += got
            print(f"loaded {len(got)} labelled flows from {f}")
    if not rows:
        raise SystemExit("no training data")
    random.Random(a.seed).shuffle(rows)
    y = [l for l, _, _ in rows]
    X = [x for _, _, x in rows]
    src = [s for _, s, _ in rows]
    Xtr, Xte, ytr, yte, _str, ste = train_test_split(X, y, src, test_size=a.test, stratify=y,
                                                     random_state=a.seed)
    folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=a.seed)

    # ---- comparison: decision tree vs random forest ----
    say(f"{len(rows)} flows ({len(Xtr)} train / {len(Xte)} holdout)\n")
    say(f"{'model':8} {'holdout acc':>11} {'macro F1':>9} {'5-fold CV acc':>15} {'us/flow':>8}")
    results = {}
    for kind in ("tree", "forest"):
        clf = make(kind, a.seed, a.depth).fit(Xtr, ytr)
        t0 = time.perf_counter()
        for x in Xte[:200]:                              # the VNF predicts one flow at a time
            clf.predict([x])
        us = (time.perf_counter() - t0) / min(200, len(Xte)) * 1e6
        pred = clf.predict(Xte)
        cv = cross_val_score(make(kind, a.seed, a.depth), X, y, cv=folds)
        results[kind] = {"accuracy": round(accuracy_score(yte, pred), 4),
                         "macro_f1": round(f1_score(yte, pred, average="macro"), 4),
                         "cv_mean": round(cv.mean(), 4), "cv_std": round(cv.std(), 4),
                         "us_per_flow": round(us, 1)}
        r = results[kind]
        say(f"{kind:8} {r['accuracy'] * 100:10.1f}% {r['macro_f1']:9.3f} "
            f"{r['cv_mean'] * 100:8.1f}% ±{r['cv_std'] * 100:4.1f} {us:8.0f}")
        if kind == a.model:
            chosen_pred = pred

    # ---- details for the chosen model ----
    say(f"\nChosen: {a.model}\n")
    say(classification_report(yte, chosen_pred, labels=CLASSES, digits=3))
    conf = confusion_matrix(yte, chosen_pred, labels=CLASSES)
    say("confusion (rows = true, cols = predicted)")
    say("        " + "".join(f"{c:>8}" for c in CLASSES))
    for c, row in zip(CLASSES, conf):
        say(f"{c:8}" + "".join(f"{v:8}" for v in row))
    for s in sorted(set(ste)):
        idx = [i for i, v in enumerate(ste) if v == s]
        ok = sum(chosen_pred[i] == yte[i] for i in idx)
        say(f"  holdout {s:26} {ok}/{len(idx)} = {ok / len(idx) * 100:.1f}%")

    final = make(a.model, a.seed, a.depth).fit(X, y)     # final model: all the data
    importance = {f: round(float(v), 4) for f, v in zip(FEATURES, final.feature_importances_)}
    meta = {"model": a.model, "features": FEATURES, "classes": CLASSES, "window": WINDOW,
            "max_wait": MAX_WAIT, "importance": importance, "comparison": results,
            "metrics": {**results[a.model], "train_flows": len(rows),
                        "confusion": conf.tolist(), "sources": sorted(set(src)),
                        "trained_at": time.strftime("%Y-%m-%d %H:%M:%S")}}
    tmp = a.out + ".tmp"
    joblib.dump(final, tmp)
    os.replace(tmp, a.out)                       # atomic: the running VNF never reads half a file
    meta_path = os.path.splitext(a.out)[0] + ".meta.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=1)

    top = sorted(importance.items(), key=lambda kv: -kv[1])
    print("\nFeature importance: " + ", ".join(f"{k} {v:.2f}" for k, v in top if v >= 0.01))
    if a.model == "tree":
        say("\nTree:\n" + export_text(final, feature_names=FEATURES))
    r = results[a.model]
    print(f"Saved {a.out} ({a.model}, {len(rows)} flows, holdout accuracy {r['accuracy'] * 100:.1f}%, "
          f"CV {r['cv_mean'] * 100:.1f}% ±{r['cv_std'] * 100:.1f})")


if __name__ == "__main__":
    main()
