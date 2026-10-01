# Exit 0: current app ready; 1: not ready; 2: an older backend lacks the library API.
Add-Type -AssemblyName System.Net.Http
$verifierReadyClient = New-Object System.Net.Http.HttpClient
$verifierReadyClient.Timeout = [TimeSpan]::FromSeconds(2)
try {
    $verifierApi = $verifierReadyClient.GetAsync('http://127.0.0.1:5173/api/status').GetAwaiter().GetResult()
    if ([int]$verifierApi.StatusCode -ne 200) { exit 1 }
    $verifierStatus = $verifierApi.Content.ReadAsStringAsync().GetAwaiter().GetResult() | ConvertFrom-Json
    if ($null -eq $verifierStatus.search_mode) { exit 1 }

    $verifierLibrary = $verifierReadyClient.GetAsync('http://127.0.0.1:5173/api/knowledge/status').GetAwaiter().GetResult()
    if ([int]$verifierLibrary.StatusCode -eq 404) { exit 2 }
    if ([int]$verifierLibrary.StatusCode -ne 200) { exit 1 }
    $verifierLibraryStatus = $verifierLibrary.Content.ReadAsStringAsync().GetAwaiter().GetResult() | ConvertFrom-Json
    if ($null -eq $verifierLibraryStatus.document_count -or $null -eq $verifierLibraryStatus.mode) { exit 1 }

    $verifierPage = $verifierReadyClient.GetAsync('http://127.0.0.1:5173/').GetAwaiter().GetResult()
    $verifierHtml = $verifierPage.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    if ([int]$verifierPage.StatusCode -ne 200 -or -not $verifierHtml.Contains('<div id=')) { exit 1 }
    exit 0
} catch {
    exit 1
} finally {
    $verifierReadyClient.Dispose()
}
