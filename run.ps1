# Relay dev runner.
#
#   .\run.ps1 test     unit tests (no keys needed)
#   .\run.ps1 verify   check every credential against the real service
#   .\run.ps1 seed     reset + seed Medplum        (needs MEDPLUM_* in .env)
#   .\run.ps1 reset    wipe seeded Medplum data    (needs MEDPLUM_* in .env)
#   .\run.ps1 serve    FastAPI on :8000            (needs DEEPGRAM/ANTHROPIC/TWILIO)
#   .\run.ps1 tunnel   ngrok tunnel to :8000
#   .\run.ps1 relink   ngrok URL changed? point .env + Twilio at the new one
#
# Then, in a second shell:  ngrok http 8000
# Put the https URL in PUBLIC_BASE_URL and point the Twilio number's voice
# webhook at <PUBLIC_BASE_URL>/twiml (HTTP POST).

param([Parameter(Position = 0)][string]$Command = "test")

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"

# Run from the parent directory with PYTHONPATH pointing at the repo. Keeping
# cwd off sys.path avoids a class of import-shadowing problems and costs nothing.
$env:PYTHONPATH = $Root
Set-Location (Split-Path $Root -Parent)

switch ($Command.ToLower()) {
    "test"   { & $Python -m pytest $Root -q }
    "verify" { & $Python -m scripts.verify }
    "relink" { & $Python -m scripts.relink }
    "fakecall" { & $Python -m scripts.fake_call }
    "dryrun"   { & $Python -m scripts.dry_run }
    "tunnel" {
        $ngrok = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Ngrok.Ngrok_Microsoft.Winget.Source_8wekyb3d8bbwe\ngrok.exe"
        if (-not (Test-Path $ngrok)) { $ngrok = "ngrok" }
        & $ngrok http 8000
    }
    "seed"  { & $Python -m scripts.seed_medplum }
    "reset" { & $Python -m scripts.seed_medplum --reset }
    "serve" { & $Python -m uvicorn voice.server:app --host 0.0.0.0 --port 8000 --reload }
    default {
        Write-Host "Unknown command '$Command'. Use: test | seed | reset | serve"
        exit 1
    }
}


