terraform {
  required_version = ">= 1.3"
  required_providers {
    aws = { source = "hashicorp/aws", version = ">= 5.0" }
  }
}

variable "bucket_name" {
  type        = string
  description = "Dedicated HARNESS_PRIVATE_BUCKET for this deployment."
}

resource "aws_s3_bucket" "sources" {
  bucket = var.bucket_name
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "sources" {
  bucket                  = aws_s3_bucket.sources.id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "sources" {
  bucket = aws_s3_bucket.sources.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

variable "application_role_name" {
  type        = string
  description = "Existing IAM role used by the platform storage client."
}

resource "aws_iam_role_policy" "sources" {
  name = "harness-sources-${aws_s3_bucket.sources.id}"
  role = var.application_role_name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetBucketLocation", "s3:ListBucket"], Resource = aws_s3_bucket.sources.arn },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"], Resource = "${aws_s3_bucket.sources.arn}/*" },
    ]
  })
}

output "harness_private_bucket" {
  value = aws_s3_bucket.sources.id
}
