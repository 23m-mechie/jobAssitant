from google.cloud import storage
from dotenv import load_dotenv
import os

load_dotenv()

project_id = os.getenv("GOOGLE_PROJECT_ID")

client = storage.Client(project=project_id)


def get_all_buckets():
    buckets = list(client.list_buckets())
    all_buckets = []

    for b in buckets:
        print(b.name)
        all_buckets.append(b.name)

    return all_buckets


def upload_pdf(local_pdf_path, bucket_name, destination_blob_name=None):
    """
    Upload a PDF file to Google Cloud Storage
    """

    # Create bucket object
    bucket = client.bucket(bucket_name)

    # Use original filename if not provided
    if destination_blob_name is None:
        destination_blob_name = os.path.basename(local_pdf_path)

    # Create blob
    blob = bucket.blob(destination_blob_name)

    # Upload file
    blob.upload_from_filename(local_pdf_path)

    print(f"✅ Uploaded '{local_pdf_path}' to bucket '{bucket_name}'")

    return f"gs://{bucket_name}/{destination_blob_name}"


if __name__ == "__main__":

    buckets = get_all_buckets()

    pdf_path = "/home/sonu/Desktop/jobAssitant/data/resume.pdf"

    if not buckets:
        print("No buckets found")
    else:
        gcs_path = upload_pdf(pdf_path, buckets[0])

        print("GCS Path:", gcs_path)