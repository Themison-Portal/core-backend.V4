# Delete all untagged images from themison-repo-eu, keeping only 'latest'
$repo = "europe-west1-docker.pkg.dev/braided-visitor-484216-i0/themison-repo-eu/core-backend"
$project = "braided-visitor-484216-i0"

# These are the untagged digests to delete (all except 'latest')
$toDelete = @(
    "sha256:6d9f4bb2f8b3f7ed9845ecc247f8fa27337f1923e77b9158612e32b840884b86",  # 2026-02-19 11:43
    "sha256:711450e4916e93533dc4652735cf4d34be521e0de4e66986d79e0b7dd40b3e10",  # 2026-02-19 12:20
    "sha256:f4bd26db9bc6d82617e5ce2b7ecac4ebba51dc4b5bce82d280fd72bfc54ac193",  # 2026-02-19 12:54
    "sha256:d73ea75890dc5fbb0cf2936168439e6bf55a3340622317c17dbe306b5ad4bdaf",  # 2026-02-19 13:38
    "sha256:9d6b9605b659b6762534d9bed29fe8e0c35b5a49f70f122ef302228d7e56b2dc",  # 2026-02-19 21:48
    "sha256:5813203c1a4d72baca79953e74a60541bfd9c5898bf4847e624eebe3dce870ea",  # 2026-02-19 22:27
    "sha256:a4dc69b41da43f0192ab35305c98fd57eb7737c5c344665fc24efad2764ad7a1"   # 2026-02-19 22:58
)

Write-Host "Keeping: sha256:90966396... (latest - the current live image)" -ForegroundColor Green
Write-Host "Deleting $($toDelete.Count) old untagged image versions..." -ForegroundColor Yellow
Write-Host ""

foreach ($digest in $toDelete) {
    $fullRef = "${repo}@${digest}"
    Write-Host "Deleting $($digest.Substring(0,20))..." -ForegroundColor Yellow
    gcloud artifacts docker images delete $fullRef --project=$project --quiet --delete-tags
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  Deleted." -ForegroundColor Green
    }
    else {
        Write-Host "  Failed (may have already been cleaned up)." -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "Done! Registry should now be ~4.6 GB (one image) instead of 38.5 GB." -ForegroundColor Cyan
