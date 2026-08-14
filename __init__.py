import os
_REPO_DIR = os.path.dirname(os.path.realpath(__file__))
_EXTERNAL_DRIVE = "/Volumes/OWC Express 1M2/datasets"

DATASET_DIR = _EXTERNAL_DRIVE if os.path.isdir(_EXTERNAL_DRIVE) else os.path.join(_REPO_DIR, 'datasets')
LOGGING_DIR = os.path.join(_REPO_DIR, 'logs')

if not os.path.exists(DATASET_DIR):
	os.mkdir(DATASET_DIR)

if not os.path.exists(LOGGING_DIR):
	os.mkdir(LOGGING_DIR)
