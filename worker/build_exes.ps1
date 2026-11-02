# Reconstruye Servidor_Trampas.exe y Worker_IA.exe y los copia al Escritorio.
# Uso:  powershell -ExecutionPolicy Bypass -File build_exes.ps1
$ErrorActionPreference = "Stop"
$py   = "C:\ento_ml\venv\Scripts\python.exe"
$work = "C:\entomira\worker"
$ico  = "$work\entomira.ico"
$out  = "$work\build"
$desk = [Environment]::GetFolderPath('Desktop')

if (-not (Test-Path $ico)) { & $py "$work\make_icon.py" }

$targets = @(
  @{ Name = "Servidor_Trampas"; Src = "launcher_servidor.py" },
  @{ Name = "Worker_IA";        Src = "launcher_worker.py"   }
)
foreach ($t in $targets) {
  Write-Host "== Compilando $($t.Name) =="
  & $py -m PyInstaller --noconfirm --onefile --noconsole --clean `
        --name $t.Name --icon $ico `
        --distpath "$out\dist" --workpath "$out\work" --specpath "$out" `
        "$work\$($t.Src)"
  Copy-Item "$out\dist\$($t.Name).exe" $desk -Force
  Write-Host "   -> copiado a $desk\$($t.Name).exe"
}
Write-Host "Listo."
