$exe = Join-Path $PSScriptRoot 'dist\ZhiYanAgent\ZhiYanAgent.exe'
Write-Output ("exe exists: " + (Test-Path $exe))
$p = Start-Process -FilePath $exe -WorkingDirectory (Split-Path $exe) -PassThru
Start-Sleep -Seconds 15
if ($p.HasExited) {
    Write-Output ("exited code=" + $p.ExitCode)
} else {
    Write-Output ("running pid=" + $p.Id)
    $conns = Get-NetTCPConnection -OwningProcess $p.Id -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) { Write-Output ("listening " + $c.LocalAddress + ":" + $c.LocalPort) }
}
