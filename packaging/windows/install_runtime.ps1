$ErrorActionPreference = 'Stop'
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $runtimeInstaller = Join-Path $env:TEMP ('UnionBakery-vc-' + [guid]::NewGuid().ToString() + '.exe')
    Write-Host 'Downloading the official Microsoft Visual C++ x64 runtime...'
    Invoke-WebRequest -UseBasicParsing -Uri 'https://aka.ms/vc14/vc_redist.x64.exe' -OutFile $runtimeInstaller
    $signature = Get-AuthenticodeSignature -FilePath $runtimeInstaller
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation(?:,|$)') {
        throw 'Microsoft installer signature verification failed. Nothing was executed.'
    }
    Write-Host 'Please complete the Microsoft setup window, then start Union Bakery again.'
    $installed = Start-Process -FilePath $runtimeInstaller -Wait -PassThru
    if ($installed.ExitCode -notin @(0, 3010, 1638)) { throw 'Runtime setup did not finish successfully.' }
} catch {
    Write-Host 'Runtime setup failed. Please contact the maintainer.'
    exit 1
} finally {
    if ($runtimeInstaller -and (Test-Path $runtimeInstaller)) { Remove-Item -LiteralPath $runtimeInstaller }
}
