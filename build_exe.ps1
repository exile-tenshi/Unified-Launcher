param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ScriptPath = Join-Path $ProjectRoot "vrchat_steamvr_optimizer.py"
$InstallerPath = Join-Path $ProjectRoot "installer.py"
$DistPath = Join-Path $ProjectRoot "dist"
$ReleasePath = Join-Path $ProjectRoot "release"
$BuildPath = Join-Path $ProjectRoot "build"
$EmbeddedDistPath = Join-Path $BuildPath "embedded_app"
$SpecPath = Join-Path $BuildPath "specs"
$ReadmePath = Join-Path $ProjectRoot "README.md"
$LicensePath = Join-Path $ProjectRoot "LICENSE"

if (-not (Test-Path $ScriptPath)) {
    throw "Missing $ScriptPath"
}

if (-not (Test-Path $InstallerPath)) {
    throw "Missing $InstallerPath"
}

if (-not (Test-Path $LicensePath)) {
    throw "Missing $LicensePath"
}

if ($Clean) {
    if (Test-Path $DistPath) { Remove-Item $DistPath -Recurse -Force }
    if (Test-Path $ReleasePath) { Remove-Item $ReleasePath -Recurse -Force }
    if (Test-Path $BuildPath) { Remove-Item $BuildPath -Recurse -Force }
    if (Test-Path (Join-Path $ProjectRoot "VRChatSteamVROptimizer.spec")) { Remove-Item (Join-Path $ProjectRoot "VRChatSteamVROptimizer.spec") -Force }
    if (Test-Path (Join-Path $ProjectRoot "VRChatSteamVROptimizerSetup.spec")) { Remove-Item (Join-Path $ProjectRoot "VRChatSteamVROptimizerSetup.spec") -Force }
}

New-Item -ItemType Directory -Force -Path $EmbeddedDistPath | Out-Null
New-Item -ItemType Directory -Force -Path $SpecPath | Out-Null

python -m PyInstaller `
    --noconfirm `
    --onefile `
    --windowed `
    --name "VRChatSteamVROptimizer" `
    --distpath $EmbeddedDistPath `
    --workpath (Join-Path $BuildPath "app") `
    --specpath $SpecPath `
    $ScriptPath

$AppExe = Join-Path $EmbeddedDistPath "VRChatSteamVROptimizer.exe"
if (-not (Test-Path $AppExe)) {
    throw "Embedded app EXE was not created: $AppExe"
}

python -m PyInstaller `
    --noconfirm `
    --onefile `
    --windowed `
    --name "VRChatSteamVROptimizer" `
    --distpath $DistPath `
    --workpath (Join-Path $BuildPath "installer") `
    --specpath $SpecPath `
    --add-data "$AppExe;." `
    --add-data "$ReadmePath;." `
    --add-data "$LicensePath;." `
    $InstallerPath

$SetupExe = Join-Path $DistPath "VRChatSteamVROptimizer.exe"
if (-not (Test-Path $SetupExe)) {
    throw "Setup EXE was not created: $SetupExe"
}

New-Item -ItemType Directory -Force -Path $ReleasePath | Out-Null
Copy-Item -LiteralPath $SetupExe -Destination $ReleasePath
Copy-Item -LiteralPath $ReadmePath -Destination $ReleasePath
Copy-Item -LiteralPath $LicensePath -Destination $ReleasePath

$ZipPath = Join-Path $DistPath "VRChatSteamVROptimizer-FreeRelease.zip"
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
Compress-Archive -Path (Join-Path $ReleasePath "*") -DestinationPath $ZipPath -Force

Write-Host ""
Write-Host "Built public installer EXE:" -ForegroundColor Green
Write-Host $SetupExe
Write-Host ""
Write-Host "Embedded app artifact:" -ForegroundColor DarkGray
Write-Host $AppExe
Write-Host ""
Write-Host "Shareable release:" -ForegroundColor Green
Write-Host $ReleasePath
Write-Host $ZipPath
