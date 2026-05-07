$ErrorActionPreference = "Stop"

New-Item -ItemType Directory -Force -Path "icons" | Out-Null

Add-Type -AssemblyName System.Drawing

$p = Join-Path (Get-Location) "icons\app-icon.png"
$bmp = New-Object System.Drawing.Bitmap 512, 512
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
$g.Clear([System.Drawing.Color]::FromArgb(11, 18, 32))

$rect = New-Object System.Drawing.Rectangle 0, 0, 512, 512
$brush = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
  $rect,
  [System.Drawing.Color]::FromArgb(37, 99, 235),
  [System.Drawing.Color]::FromArgb(99, 102, 241),
  45
)
$g.FillEllipse($brush, 56, 56, 400, 400)

$pen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(226, 232, 240), 10)
$g.DrawEllipse($pen, 56, 56, 400, 400)

$font = New-Object System.Drawing.Font("Segoe UI", 64, [System.Drawing.FontStyle]::Bold)
$sf = New-Object System.Drawing.StringFormat
$sf.Alignment = [System.Drawing.StringAlignment]::Center
$sf.LineAlignment = [System.Drawing.StringAlignment]::Center
$g.DrawString("UGL", $font, [System.Drawing.Brushes]::White, 256, 256, $sf)

$g.Dispose()
$bmp.Save($p, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()

Write-Host "Wrote $p"

