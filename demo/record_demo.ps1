#!/usr/bin/env powershell
# GOLD//REAPER :: demo recording pipeline (Windows)
# ==================================================
# asciinema rec -> .cast, agg -> .gif, ffmpeg -> .mp4
# Usage:  powershell -ExecutionPolicy Bypass -File demo\record_demo.ps1
# Tools:  asciinema (pip install asciinema), agg (scoop/cargo), ffmpeg
# Fallback: python demo\render_gif.py renders the .gif from the .cast
#           without agg (Pillow-based, deterministic).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

New-Item -ItemType Directory -Force -Path demo | Out-Null
$cast = "demo\gold-reaper-demo.cast"
$gif  = "demo\gold-reaper-demo.gif"
$mp4  = "demo\gold-reaper-demo.mp4"

Write-Host "[1/4] recording terminal demo -> $cast"
$rec = Get-Command asciinema -ErrorAction SilentlyContinue
if ($rec) {
    asciinema rec $cast --command "python demo\terminal_demo.py" --overwrite --quiet
} else {
    Write-Host "  asciinema missing - install with: pip install asciinema"
    Write-Host "  (or run python demo\render_gif.py after producing a .cast)"
    exit 1
}

Write-Host "[2/4] cast -> gif"
$agg = Get-Command agg -ErrorAction SilentlyContinue
if ($agg) {
    agg $cast $gif
} else {
    Write-Host "  agg missing - falling back to deterministic Pillow renderer"
    python demo\render_gif.py --cast $cast --out $gif
    if ($LASTEXITCODE -ne 0) { exit 1 }
}

Write-Host "[3/4] gif -> mp4"
$ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
if ($ff) {
    ffmpeg -y -loglevel error -i $gif -movflags +faststart -pix_fmt yuv420p $mp4
} else {
    Write-Host "  ffmpeg missing - gif is still a valid deliverable; mp4 skipped"
    exit 0
}

Write-Host "[4/4] done:"
Get-Item $cast, $gif, $mp4 | Format-Table Name, Length
