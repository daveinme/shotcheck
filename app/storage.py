import boto3
from botocore.config import Config

from app.config import (
    R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ENDPOINT_URL,
    R2_BUCKET, R2_PRESIGNED_TTL,
)

_client = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT_URL,
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
    config=Config(signature_version="s3v4", region_name="auto"),
)


# Blocco di lettura per lo streaming da R2: compromesso tra numero di round
# trip verso Cloudflare e RAM occupata per file in transito.
CHUNK_SIZE = 1024 * 1024


def object_key(batch_id: int, stored_filename: str) -> str:
    return f"batches/{batch_id}/{stored_filename}"


def upload_photo(batch_id: int, stored_filename: str, content: bytes, content_type: str = "image/jpeg") -> None:
    _client.put_object(
        Bucket=R2_BUCKET,
        Key=object_key(batch_id, stored_filename),
        Body=content,
        ContentType=content_type,
    )


def delete_photo(batch_id: int, stored_filename: str) -> None:
    _client.delete_object(Bucket=R2_BUCKET, Key=object_key(batch_id, stored_filename))


def presigned_url(batch_id: int, stored_filename: str, ttl: int = R2_PRESIGNED_TTL) -> str:
    return _client.generate_presigned_url(
        "get_object",
        Params={"Bucket": R2_BUCKET, "Key": object_key(batch_id, stored_filename)},
        ExpiresIn=ttl,
    )


def get_photo_bytes(batch_id: int, stored_filename: str) -> bytes:
    obj = _client.get_object(Bucket=R2_BUCKET, Key=object_key(batch_id, stored_filename))
    return obj["Body"].read()


def iter_photo_chunks(batch_id: int, stored_filename: str, chunk_size: int = CHUNK_SIZE):
    """Legge un oggetto da R2 a blocchi invece di caricarlo tutto in memoria.

    Serve alla generazione degli ZIP: con get_photo_bytes() un archivio di 52
    TIFF teneva in RAM ~3GB e mandava il VPS in thrashing. Così ne resta in
    memoria un blocco alla volta."""
    obj = _client.get_object(Bucket=R2_BUCKET, Key=object_key(batch_id, stored_filename))
    body = obj["Body"]
    try:
        while True:
            chunk = body.read(chunk_size)
            if not chunk:
                break
            yield chunk
    finally:
        body.close()


def photo_size(batch_id: int, stored_filename: str) -> int:
    """Dimensione dell'oggetto senza scaricarlo (HEAD), per lo ZIP sized."""
    return _client.head_object(Bucket=R2_BUCKET, Key=object_key(batch_id, stored_filename))["ContentLength"]


def raw_upload_key(raw_batch_id: int, stored_filename: str) -> str:
    return f"raw-uploads/{raw_batch_id}/{stored_filename}"


def upload_raw_photo(raw_batch_id: int, stored_filename: str, content: bytes, content_type: str = "image/jpeg") -> None:
    _client.put_object(
        Bucket=R2_BUCKET,
        Key=raw_upload_key(raw_batch_id, stored_filename),
        Body=content,
        ContentType=content_type,
    )


def get_raw_photo_bytes(raw_batch_id: int, stored_filename: str) -> bytes:
    obj = _client.get_object(Bucket=R2_BUCKET, Key=raw_upload_key(raw_batch_id, stored_filename))
    return obj["Body"].read()


def delete_raw_photo(raw_batch_id: int, stored_filename: str) -> None:
    _client.delete_object(Bucket=R2_BUCKET, Key=raw_upload_key(raw_batch_id, stored_filename))


def iter_raw_photo_chunks(raw_batch_id: int, stored_filename: str, chunk_size: int = CHUNK_SIZE):
    """Come iter_photo_chunks(), per i file grezzi caricati dal brand."""
    obj = _client.get_object(Bucket=R2_BUCKET, Key=raw_upload_key(raw_batch_id, stored_filename))
    body = obj["Body"]
    try:
        while True:
            chunk = body.read(chunk_size)
            if not chunk:
                break
            yield chunk
    finally:
        body.close()


def raw_photo_size(raw_batch_id: int, stored_filename: str) -> int:
    return _client.head_object(Bucket=R2_BUCKET, Key=raw_upload_key(raw_batch_id, stored_filename))["ContentLength"]


def raw_presigned_url(raw_batch_id: int, stored_filename: str, ttl: int = R2_PRESIGNED_TTL) -> str:
    return _client.generate_presigned_url(
        "get_object",
        Params={"Bucket": R2_BUCKET, "Key": raw_upload_key(raw_batch_id, stored_filename)},
        ExpiresIn=ttl,
    )
