from google.cloud import storage

client = storage.Client()

buckets = list(client.list_buckets())

for b in buckets:
    print(b.name)