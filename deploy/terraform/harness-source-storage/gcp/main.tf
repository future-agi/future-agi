terraform {
  required_version = ">= 1.3"
  required_providers {
    google = { source = "hashicorp/google", version = ">= 5.0" }
  }
}

variable "bucket_name" {
  type        = string
  description = "Dedicated HARNESS_PRIVATE_BUCKET for this deployment."
}

variable "location" {
  type        = string
  description = "Approved region for customer sources."
}

variable "service_account_email" {
  type        = string
  description = "Existing service account that owns the platform storage HMAC credentials."
}

resource "google_storage_bucket" "sources" {
  name                        = var.bucket_name
  location                    = var.location
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_storage_bucket_iam_member" "application" {
  bucket = google_storage_bucket.sources.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${var.service_account_email}"
}

output "harness_private_bucket" {
  value = google_storage_bucket.sources.name
}
