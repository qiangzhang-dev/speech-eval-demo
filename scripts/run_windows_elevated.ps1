[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("test", "acceptance", "recording")]
    [string]$Task,

    [string]$ExperimentId = "SYNTHETIC-DEMO-0001"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ScriptPath = $MyInvocation.MyCommand.Path

$isAdministrator = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)

if (-not $isAdministrator) {
    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", ('"{0}"' -f $ScriptPath),
        "-Task", $Task,
        "-ExperimentId", $ExperimentId
    )
    $process = Start-Process powershell.exe -Verb RunAs -Wait -PassThru -WindowStyle Hidden -ArgumentList $arguments
    exit $process.ExitCode
}

Set-Location -LiteralPath $ProjectRoot
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"

switch ($Task) {
    "test" {
        & python -m unittest discover -s tests -v
        exit $LASTEXITCODE
    }
    "acceptance" {
        & python scripts/acceptance_check.py `
            --db var/evaluation.db `
            --export exports/evaluation-results.jsonl `
            --export exports/evaluation-results.csv `
            --schema schemas/evaluation-result.schema.json `
            --batch-id BATCH-SYNTHETIC `
            --run-id RUN-SYNTHETIC
        exit $LASTEXITCODE
    }
    "recording" {
        if ($ExperimentId -notmatch '^[A-Z0-9][A-Z0-9-]{7,80}$') {
            throw "ExperimentId must contain only uppercase letters, digits, and hyphens."
        }
        $node = $env:SPEECH_EVAL_NODE_EXE
        if (-not $node) {
            $nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
            if ($nodeCommand) { $node = $nodeCommand.Source }
        }
        if (-not $node -or -not (Test-Path -LiteralPath $node)) {
            throw "Node.js runtime not found. Install Node.js or set SPEECH_EVAL_NODE_EXE to node.exe."
        }
        & $node scripts/capture_real_experiment.mjs $ExperimentId
        exit $LASTEXITCODE
    }
}
