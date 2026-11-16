# .env access for bin/install.ps1, which dot-sources this file from the
# repository root.

# Read/write .env as UTF-8 no BOM. PS 5.1's default Set-Content writes a
# UTF-8 BOM that some env-file readers choke on.
function Read-EnvLines {
  if (Test-Path '.env') {
    [System.IO.File]::ReadAllLines('.env')
  } else {
    @()
  }
}
function Write-EnvLines {
  param([string[]]$Lines)
  $utf8 = [System.Text.UTF8Encoding]::new($false)
  [System.IO.File]::WriteAllLines((Join-Path (Get-Location) '.env'), [string[]]$Lines, $utf8)
}

function Get-EnvValue {
  param([string]$Var)
  $line = Read-EnvLines | Where-Object { $_ -match "^$Var=" } | Select-Object -Last 1
  if ($line) { ($line -split '=', 2)[1] } else { '' }
}

# Var as Compose resolves it: exported in the shell, else from .env, else
# Default.
function Get-ComposeEnvValue {
  param([string]$Var, [string]$Default = '')
  $value = [Environment]::GetEnvironmentVariable($Var)
  if (-not $value) { $value = Get-EnvValue $Var }
  if (-not $value) { $value = $Default }
  $value
}

function Set-EnvValue {
  param([string]$Var, [string]$Val)
  $lines = Read-EnvLines
  $found = $false
  $out = @()
  foreach ($line in $lines) {
    if ($line -match "^$Var=") {
      $out += "$Var=$Val"
      $found = $true
    } else {
      $out += $line
    }
  }
  if (-not $found) { $out += "$Var=$Val" }
  Write-EnvLines $out
}
