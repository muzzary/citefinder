# Sign a CiteFinder build artifact with an Authenticode code-signing certificate.
#
# Removing the Windows SmartScreen "Run anyway" warning for downloaders requires a
# real OV or EV certificate from a CA (paid, identity-verified). A self-signed cert
# will not help other machines. Once you have a .pfx, sign the installer (and,
# ideally, the inner exe before Inno packages it):
#
#   .\sign.ps1 -File installer\CiteFinder-Setup-1.1.1.exe -Pfx C:\path\to\cert.pfx -Password ****
#   .\sign.ps1 -File dist\CiteFinder\CiteFinder.exe        -Pfx C:\path\to\cert.pfx -Password ****   # before ISCC
#
# Uses a SHA-256 signature + an RFC-3161 timestamp so signatures stay valid after
# the certificate expires.
param(
  [Parameter(Mandatory = $true)] [string]$File,
  [Parameter(Mandatory = $true)] [string]$Pfx,
  [string]$Password,
  [string]$Timestamp = "http://timestamp.digicert.com"
)

$signtool = Get-ChildItem "C:\Program Files (x86)\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue |
  Sort-Object FullName -Descending | Select-Object -First 1
if (-not $signtool) {
  Write-Error "signtool.exe not found. Install the Windows 10/11 SDK (it ships with Visual Studio)."
  exit 1
}
if (-not (Test-Path $File)) { Write-Error "File not found: $File"; exit 1 }
if (-not (Test-Path $Pfx))  { Write-Error "Certificate not found: $Pfx"; exit 1 }

$signArgs = @("sign", "/fd", "SHA256", "/tr", $Timestamp, "/td", "SHA256", "/f", $Pfx)
if ($Password) { $signArgs += @("/p", $Password) }
$signArgs += $File

& $signtool.FullName @signArgs
if ($LASTEXITCODE -ne 0) { Write-Error "Signing failed (exit $LASTEXITCODE)."; exit $LASTEXITCODE }
& $signtool.FullName verify /pa $File
Write-Output "Signed and verified: $File"
