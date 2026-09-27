# Loaded by the harness when it starts PowerShell (P10, P11a). The user's profile
# has already run. Two hooks bracket every command with invisible markers
# (OSC 7331): PSConsoleHostReadLine fires after a command line is read and before
# it runs, and prompt fires before the next prompt, carrying the exit code.
$global:__harnessEsc = [char]27
$global:__harnessBel = [char]7

function global:PSConsoleHostReadLine {
    $line = [Microsoft.PowerShell.PSConsoleReadLine]::ReadLine($Host.Runspace, $ExecutionContext)
    if ($line -ne $null -and $line.Trim().Length -gt 0) {
        [Console]::Out.Write("$global:__harnessEsc]7331;S$global:__harnessBel")
    }
    $line
}

$global:__harnessOriginalPrompt = $function:prompt
function global:prompt {
    $success = $?
    $code = if ($LASTEXITCODE -ne $null) { $LASTEXITCODE } elseif ($success) { 0 } else { 1 }
    if ($success -and $LASTEXITCODE -eq $null) { $code = 0 }
    [Console]::Out.Write("$global:__harnessEsc]7331;E;$code$global:__harnessBel")
    $global:LASTEXITCODE = $null
    if ($global:__harnessOriginalPrompt) { & $global:__harnessOriginalPrompt } else { "PS $PWD> " }
}
$env:HARNESS_TERMINAL = "1"
