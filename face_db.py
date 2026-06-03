"""Face database: model loading, the enrolled-embedding gallery, and matching.

Every enrolled image is turned into a 512 dimension, L2-normalised embedding by
ArcFace. A person can have several embeddings (usually front, left, right) which
are stored as separate rows that all map to the same name. Vectors
are unit-length therefore cosine similarity is just a dot product (fun), so identification is a
single matrix multiplication followed by an argmax to get the best match.
"""

import os
import pickle
import numpy as np
import config

EMBED_DIM = 512  # Size of embeddign created by buffalo model


# The pkl numpy database that carys doesn't like
class FaceDB:

    def __init__(self):
        self.embeddings = np.empty((0, EMBED_DIM), dtype=np.float32)
        self.labels = []  # parallel to rows of self.embeddings

    def save(self, path=config.DB_PATH):
        with open(path, "wb") as f:
            pickle.dump({"embeddings": self.embeddings, "labels": self.labels}, f)

    @classmethod
    def load(cls, path=config.DB_PATH):
        db = cls()
        if os.path.exists(path):
            with open(path, "rb") as f:
                data = pickle.load(f)
            db.embeddings = data["embeddings"].astype(np.float32)
            db.labels = list(data["labels"])
        return db

    # ---- enrolment ------------------------------------------------------
    def add(self, name, embedding):
        emb = np.asarray(embedding, dtype=np.float32).reshape(1, -1)
        self.embeddings = np.vstack([self.embeddings, emb])
        self.labels.append(name)

    def remove(self, name):
        keep = [i for i, lbl in enumerate(self.labels) if lbl != name]
        self.embeddings = self.embeddings[keep]
        self.labels = [self.labels[i] for i in keep]

    def people(self):
        return sorted(set(self.labels))

    def count_for(self, name):
        return sum(1 for lbl in self.labels if lbl == name)

    # Face matching
    def identify(self, embedding, threshold=config.MATCH_THRESHOLD):
        # Takes in current frame embedding and matches it to embeddings in the database
        if len(self.labels) == 0:
            return None, 0.0
        np_emb = np.asarray(embedding, dtype=np.float32)
        sims = self.embeddings @ np_emb          # cosine similarity for every entry through matrix multiplication
        best_match = int(np.argmax(sims))
        score = float(sims[best_match])
        if score >= threshold:
            return self.labels[best_match], score
        return None, score
