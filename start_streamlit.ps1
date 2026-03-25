$python = "C:\Users\Val\AppData\Local\Programs\Python\Python311\python.exe"
$log = "C:\WORK\GQQFM\streamlit.log"

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = "cmd.exe"
$psi.Arguments = "/c `"$python`" -m streamlit run ui/app.py --server.headless true --server.address 0.0.0.0 --server.port 8501 > `"$log`" 2>&1"
$psi.WorkingDirectory = "C:\WORK\GQQFM"
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true

$p = New-Object System.Diagnostics.Process
$p.StartInfo = $psi
$p.Start() | Out-Null
Write-Output "Started PID $($p.Id)"
