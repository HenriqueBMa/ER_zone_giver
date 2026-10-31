<# : batch launcher - double-click this file
@echo off
powershell -NoProfile -ExecutionPolicy Bypass -Command "iex (Get-Content -Raw -LiteralPath '%~f0')"
pause
goto :EOF
#>

# Teleports a spectator around a square so Xaero's World Map records every chunk.
# Needs nothing installed - just Windows.

Add-Type -AssemblyName System.Windows.Forms
Add-Type -Namespace Win -Name Keys -MemberDefinition '[DllImport("user32.dll")] public static extern short GetAsyncKeyState(int k);'

function Ask($prompt, $default) {
    $v = Read-Host "$prompt [$default]"
    if ([string]::IsNullOrWhiteSpace($v)) { return [double]$default }
    return [double]$v
}

function F8Pressed { return ([Win.Keys]::GetAsyncKeyState(0x77) -band 0x8000) -ne 0 }

Write-Host "=== Xaero World Map auto-explorer ===" -ForegroundColor Cyan
Write-Host "Before starting: singleplayer, cheats on, spectator mode, render distance 7."
Write-Host "Press ENTER to accept the [default] values."
Write-Host ""
$cx    = [int](Ask "Centre X" 0)
$cz    = [int](Ask "Centre Z" 0)
$size  = [int](Ask "Square size in blocks" 2600)
$view  = [int](Ask "Render distance (chunks)" 7)
$wait  = Ask "Seconds to wait at each spot" 4
$start = [int](Ask "Start from step (to resume)" 0)

# Loaded chunks form a circle, so spacing <= r*sqrt(2) leaves no gaps (minus 1 for safety).
$step = [math]::Max(1, [math]::Floor($view * [math]::Sqrt(2)) - 1)
$half = [math]::Floor($size / 2)
function Axis($lo, $hi) {
    $pts = [System.Collections.Generic.List[int]]::new()
    for ($c = $lo; $c -le $hi; $c += $step) { $pts.Add($c) }
    if ($pts[$pts.Count - 1] -ne $hi) { $pts.Add($hi) }
    return ,$pts
}
$xs = Axis ([math]::Floor(($cx - $half) / 16)) ([math]::Floor(($cx + $half - 1) / 16))
$zs = Axis ([math]::Floor(($cz - $half) / 16)) ([math]::Floor(($cz + $half - 1) / 16))

$route = [System.Collections.Generic.List[object]]::new()
for ($i = 0; $i -lt $zs.Count; $i++) {
    $row = @($xs)
    if ($i % 2 -eq 1) { [array]::Reverse($row) }
    foreach ($x in $row) { $route.Add(@(($x * 16 + 8), ($zs[$i] * 16 + 8))) }
}

$mins = [math]::Round(($route.Count - $start) * ($wait + 0.5) / 60)
Write-Host ""
Write-Host "$($route.Count) spots, $($step * 16) blocks apart, about $mins minutes." -ForegroundColor Yellow

if ($env:MAPPER_DRYRUN) {
    Write-Host "first: $($route[0] -join ', ')  last: $($route[$route.Count - 1] -join ', ')"
    return
}

Write-Host "Click into the Minecraft window now. Hold F8 at any time to stop."
for ($s = 5; $s -gt 0; $s--) { Write-Host "Starting in $s..."; Start-Sleep 1 }

for ($i = $start; $i -lt $route.Count; $i++) {
    $x, $z = $route[$i]
    Write-Host "[$($i + 1)/$($route.Count)] /tp @s $x 200 $z"
    [System.Windows.Forms.SendKeys]::SendWait("t")
    Start-Sleep -Milliseconds 200
    [System.Windows.Forms.SendKeys]::SendWait("/tp @s $x 200 $z")
    Start-Sleep -Milliseconds 50
    [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")

    $until = (Get-Date).AddSeconds($wait)
    while ((Get-Date) -lt $until) {
        if (F8Pressed) {
            Write-Host "Stopped. To resume, enter $($i + 1) as the start step." -ForegroundColor Red
            return
        }
        Start-Sleep -Milliseconds 100
    }
}
Write-Host "Done! Open Xaero's World Map and export it as PNG." -ForegroundColor Green
