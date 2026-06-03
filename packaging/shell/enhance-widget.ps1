# PSReadLine widget: enhance the current command-line buffer in place.
# Add to your PowerShell $PROFILE:   . C:\path\to\enhance-widget.ps1
# Then press Ctrl+x,Ctrl+e to rewrite whatever you've typed.

Set-PSReadLineKeyHandler -Chord 'Ctrl+x,Ctrl+e' -ScriptBlock {
    $line = $null
    $cursor = $null
    [Microsoft.PowerShell.PSConsoleReadLine]::GetBufferState([ref]$line, [ref]$cursor)
    if ([string]::IsNullOrWhiteSpace($line)) { return }
    $rewritten = (& enhance-cli --no-clipboard -y $line 2>$null | Out-String).TrimEnd()
    if ($rewritten) {
        [Microsoft.PowerShell.PSConsoleReadLine]::Replace(0, $line.Length, $rewritten)
    }
}
