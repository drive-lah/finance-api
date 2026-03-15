"""
S3 Service

Handles uploading invoice PDFs and images to AWS S3.
Falls back gracefully if AWS credentials are not configured — extraction
still works but no file is persisted.

Required env vars:
    AWS_S3_BUCKET            - bucket name
    AWS_ACCESS_KEY_ID        - AWS access key
    AWS_SECRET_ACCESS_KEY    - AWS secret key
    AWS_REGION               - (optional, defaults to ap-southeast-1)
"""
import logging
import os
import uuid
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


class S3Service:
    """Uploads invoice PDFs to S3 and returns the stored key."""

    def _client(self):
        import boto3  # lazy import so missing boto3 doesn't break the whole app
        return boto3.client(
            "s3",
            region_name=os.environ.get("AWS_REGION", "ap-southeast-1"),
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        )

    @property
    def bucket(self) -> Optional[str]:
        return os.environ.get("AWS_S3_BUCKET")

    def is_configured(self) -> bool:
        """Returns True if all required S3 env vars are set."""
        return bool(
            self.bucket
            and os.environ.get("AWS_ACCESS_KEY_ID")
            and os.environ.get("AWS_SECRET_ACCESS_KEY")
        )

    def upload_invoice_pdf(
        self,
        file_bytes: bytes,
        filename: str = "invoice.pdf",
        entity_id: Optional[int] = None,
    ) -> Optional[str]:
        """
        Upload a file (PDF or image) to S3.

        Returns the S3 key (e.g. "invoices/2026/03/abc123_invoice.pdf") on success,
        or None if S3 is not configured or the upload fails.
        """
        logger.info(f"S3 upload requested. filename={filename}, file_size={len(file_bytes)} bytes")

        if not self.is_configured():
            logger.warning("S3 not configured — file will not be stored")
            return None

        try:
            date_prefix = datetime.utcnow().strftime("%Y/%m")
            unique_id = uuid.uuid4().hex[:12]
            safe_filename = "".join(
                c if c.isalnum() or c in "-_." else "_" for c in filename
            )
            entity_prefix = f"entity_{entity_id}/" if entity_id else ""
            key = f"invoices/{entity_prefix}{date_prefix}/{unique_id}_{safe_filename}"

            # Infer ContentType from filename extension
            ext = os.path.splitext(filename.lower())[1]
            content_type = _CONTENT_TYPES.get(ext, "application/pdf")
            logger.info(f"S3 upload details. ext={ext}, content_type={content_type}, key={key}")

            self._client().put_object(
                Bucket=self.bucket,
                Key=key,
                Body=file_bytes,
                ContentType=content_type,
            )
            logger.info(f"Successfully uploaded invoice file to s3://{self.bucket}/{key}")
            return key

        except Exception as e:
            logger.error(f"S3 upload failed: {e}", exc_info=True)
            return None

    def get_presigned_url(self, s3_key: str, expiration_seconds: int = 3600) -> Optional[str]:
        """
        Generate a pre-signed URL for an S3 object.

        Args:
            s3_key: The S3 key (path) of the object
            expiration_seconds: URL expiration time in seconds (default: 1 hour)

        Returns:
            Pre-signed URL string, or None if S3 is not configured
        """
        if not self.is_configured() or not s3_key:
            return None

        try:
            url = self._client().generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": s3_key},
                ExpiresIn=expiration_seconds,
            )
            logger.info(f"Generated pre-signed URL for {s3_key} (expires in {expiration_seconds}s)")
            return url
        except Exception as e:
            logger.error(f"Failed to generate pre-signed URL for {s3_key}: {e}")
            return None


s3_service = S3Service()
