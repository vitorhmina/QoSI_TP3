"""Loads the trained scikit-learn model and reloads it when the file changes (e.g. after
`vnf.py train`), so a retrained model is used without restarting the VNF."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT = os.path.join(HERE, "model.joblib")


class Model:
    def __init__(self, path=DEFAULT):
        self.path, self.mtime, self.clf, self.meta = path, None, None, {}

    @property
    def ok(self):
        return self.clf is not None

    def reload(self):
        """Loads the file if it changed. Returns True when a new model was loaded."""
        try:
            mt = os.stat(self.path).st_mtime_ns
        except OSError:
            self.clf = self.mtime = None
            return False
        if mt == self.mtime:
            return False
        import joblib                                    # heavy import: only when needed
        try:
            self.clf, self.mtime = joblib.load(self.path), mt
        except Exception:
            self.clf = None
            return False
        try:
            with open(os.path.splitext(self.path)[0] + ".meta.json") as f:
                self.meta = json.load(f)
        except (OSError, ValueError):
            self.meta = {}
        return True

    def predict(self, vec):
        """Returns (class label, probability)."""
        proba = self.clf.predict_proba([vec])[0]
        i = max(range(len(proba)), key=proba.__getitem__)
        return str(self.clf.classes_[i]), float(proba[i])

    def describe(self):
        if not self.ok:
            return "no model"
        mt = self.meta.get("metrics", {})
        return (f"{self.meta.get('model', '?')}, {mt.get('train_flows', '?')} training flows, "
                f"holdout accuracy {mt.get('accuracy', 0) * 100:.1f}%")
