"""
Config file Work In Progress to transfer all configs here
"""

# InsightFace library models pack.
#   "buffalo_l" -> SCRFD-10G detector + w600k_r50 ArcFace recogniser  (accurate)
#   "buffalo_s" -> lighter detector + recogniser                      (faster on CPU)
MODEL_NAME = "buffalo_l"

# Detector input size. Smaller = faster on CPU, but misses small/distant faces.
DET_SIZE = (320, 320)

# PKL database to store face embeddings.
DB_PATH = "face_db.pkl"

# Cosine-similarity threshold for declaring a match.
MATCH_THRESHOLD = 0.5

# CPU optimisation for the live loop
# Detect and recognise every N frames to save resources
DETECT_EVERY_N = 1
