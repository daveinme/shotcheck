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


def raw_presigned_url(raw_batch_id: int, stored_filename: str, ttl: int = R2_PRESIGNED_TTL) -> str:
    return _client.generate_presigned_url(
        "get_object",
        Params={"Bucket": R2_BUCKET, "Key": raw_upload_key(raw_batch_id, stored_filename)},
        ExpiresIn=ttl,
    )
