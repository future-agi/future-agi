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

# The value of a .env line, from the text after its '=', as Compose reads it:
# leading whitespace goes; a value in matching double or single quotes is what
# lies between them, when only whitespace or a # comment follows the closing
# quote; an unquoted value ends before ' #' (a space, then #) and loses
# trailing whitespace. Text Compose reads no other way, an unclosed quote, is
# kept as written. Escapes and ${VAR} in values are left as they are.
function ConvertFrom-EnvText {
  param([string]$Text)
  $value = $Text.TrimStart()
  if ($value -match '^["'']') {
    $quote = $value[0]
    $close = $value.IndexOf($quote, 1)
    if ($close -gt 0) {
      $rest = $value.Substring($close + 1).TrimStart()
      if ($rest -eq '' -or $rest -like '#*') { return $value.Substring(1, $close - 1) }
    }
    return $Text
  }
  $comment = $value.IndexOf(' #', [System.StringComparison]::Ordinal)
  if ($comment -ge 0) { $value = $value.Substring(0, $comment) }
  return $value.TrimEnd()
}

# The text after the '=' of Var's last line in .env, as written, or ''. Copy
# this, not Get-EnvValue, from one key to another: Compose then reads the same
# value from both lines, quotes, ' #' and surrounding spaces included.
function Get-EnvText {
  param([string]$Var)
  $line = Read-EnvLines | Where-Object { $_ -match "^$Var=" } | Select-Object -Last 1
  if ($line) { ($line -split '=', 2)[1] } else { '' }
}

# Value of Var in .env (its last line) as Compose reads it, or '' when unset.
function Get-EnvValue {
  param([string]$Var)
  ConvertFrom-EnvText (Get-EnvText $Var)
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
