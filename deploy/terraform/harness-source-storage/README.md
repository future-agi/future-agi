# Private harness source storage

Bundled MinIO provisions the source bucket during platform bootstrap. For external AWS S3 or Google Cloud Storage, call the corresponding module from your deployment's Terraform root. Use the existing application IAM role on AWS or the service account associated with the application's HMAC credentials on GCS. Pass your deployment region through the AWS provider or the GCS module's `location` variable.

Set `HARNESS_PRIVATE_BUCKET` (Helm `objectStorage.harnessPrivateBucket`) to the module's `harness_private_bucket` output. Use a dedicated name for each deployment. External MinIO requires a private bucket with no anonymous policy.

After upgrading all readers, run `python manage.py migrate_harness_source_storage --copy`. This copies legacy design documents and Environment snapshots, reads both copies and checks their SHA-256 digests. Rerun with `--copy --remove-verified-legacy` to remove matching legacy objects. A conflicting private object stops migration with its legacy copy retained. On versioned legacy buckets, configure retention for noncurrent object versions according to your organization's erasure policy.
