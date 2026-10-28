$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

python -m PyInstaller `
    --specpath build `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --name PDFDots `
    --icon "$PSScriptRoot\dots.ico" `
    --add-data "$PSScriptRoot\dots.png;." `
    --paths src `
    src\pdfdots\__main__.py

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE."
}

Write-Host "Application folder created at: $PSScriptRoot\dist\PDFDots"
Write-Host "Launch the app with: $PSScriptRoot\dist\PDFDots\PDFDots.exe"
