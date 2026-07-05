"""Deterministic GCS transfer via google-cloud-storage + explicit JSON SA key
(avoids snap-gsutil confinement + boto P12 ambiguity). Resumable for big files.

usage:
  python3 gcs_xfer.py download <blob> <local>
  python3 gcs_xfer.py upload_dir <local_dir> <blob_prefix>
  python3 gcs_xfer.py check
"""
import sys, glob, os
from google.cloud import storage

KEY = "/home/ubuntu/sa-key.json"
BUCKET = "happyhunting-10604-markets-transfer-usw1"
client = storage.Client.from_service_account_json(KEY)
bucket = client.bucket(BUCKET)


def main():
    mode = sys.argv[1]
    if mode == "check":
        blobs = list(client.list_blobs(BUCKET, prefix="chunks/", max_results=10))
        print("AUTH_OK, chunks:", [b.name for b in blobs])
    elif mode == "download":
        blob_name, local = sys.argv[2], sys.argv[3]
        b = bucket.blob(blob_name)
        b.chunk_size = 32 * 1024 * 1024
        b.download_to_filename(local)
        print("DOWNLOADED", blob_name, "->", local, os.path.getsize(local), "bytes")
    elif mode == "upload_dir":
        local_dir, prefix = sys.argv[2], sys.argv[3]
        files = sorted(glob.glob(local_dir + "/*.parquet"))
        for i, f in enumerate(files):
            name = prefix.rstrip("/") + "/" + os.path.basename(f)
            b = bucket.blob(name)
            b.chunk_size = 32 * 1024 * 1024
            b.upload_from_filename(f)
            print(f"UPLOADED {i+1}/{len(files)} {os.path.basename(f)} -> {name}", flush=True)
        print("UPLOAD_DIR_DONE", prefix, len(files), "files")


if __name__ == "__main__":
    main()
