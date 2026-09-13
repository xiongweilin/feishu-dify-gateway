#Requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet('github', 'sonar', 'windows-maintenance', 'watchdog', 'test')]
    [string]$Source,

    [Parameter(Mandatory)]
    [ValidateSet('info', 'warning', 'critical')]
    [string]$Severity,

    [Parameter(Mandatory)]
    [ValidateLength(1, 512)]
    [string]$Title,

    [Parameter(Mandatory)]
    [ValidateLength(1, 10000)]
    [string]$Text,

    [Uri]$Url,
    [string]$EventId = [Guid]::NewGuid().ToString('N'),
    [Uri]$GatewayUri = 'http://127.0.0.1:18082/v1/notifications'
)

$ErrorActionPreference = 'Stop'
$credentialName = 'Agent:FeishuGateway:Hmac'

if (-not ('FeishuNotify.NativeMethods' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

namespace FeishuNotify {
    public static class NativeMethods {
        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        public struct CREDENTIAL {
            public uint Flags;
            public uint Type;
            public string TargetName;
            public string Comment;
            public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
            public uint CredentialBlobSize;
            public IntPtr CredentialBlob;
            public uint Persist;
            public uint AttributeCount;
            public IntPtr Attributes;
            public string TargetAlias;
            public string UserName;
        }

        [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern bool CredRead(string target, uint type, uint flags, out IntPtr credential);

        [DllImport("advapi32.dll", SetLastError = true)]
        public static extern void CredFree(IntPtr buffer);
    }
}
'@
}
function Read-HmacSecret {
    $pointer = [IntPtr]::Zero
    if (-not [FeishuNotify.NativeMethods]::CredRead($credentialName, 1, 0, [ref]$pointer)) {
        throw "Credential is not configured: $credentialName"
    }
    try {
        $credential = [Runtime.InteropServices.Marshal]::PtrToStructure(
            $pointer,
            [type][FeishuNotify.NativeMethods+CREDENTIAL]
        )
        if ($credential.CredentialBlobSize -eq 0) { throw 'Credential value is empty.' }
        return [Runtime.InteropServices.Marshal]::PtrToStringUni(
            $credential.CredentialBlob,
            [int]($credential.CredentialBlobSize / 2)
        )
    }
    finally {
        [FeishuNotify.NativeMethods]::CredFree($pointer)
    }
}

if ($GatewayUri.Scheme -ne 'http' -or $GatewayUri.Host -ne '127.0.0.1' -or $GatewayUri.Port -ne 18082) {
    throw 'GatewayUri must remain on http://127.0.0.1:18082.'
}
if ($Url -and $Url.Scheme -notin @('http', 'https')) { throw 'Url must use HTTP or HTTPS.' }

$payload = [ordered]@{
    source = $Source
    severity = $Severity
    title = $Title
    text = $Text
    occurredAt = [DateTimeOffset]::UtcNow.ToString('o')
}
if ($Url) { $payload.url = $Url.AbsoluteUri }

$body = $payload | ConvertTo-Json -Compress
$bodyBytes = [Text.Encoding]::UTF8.GetBytes($body)
$timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds().ToString()
$digestBytes = [Security.Cryptography.SHA256]::HashData($bodyBytes)
$digest = [Convert]::ToHexString($digestBytes).ToLowerInvariant()
$canonical = [Text.Encoding]::UTF8.GetBytes("$timestamp`n$EventId`n$digest")
$secret = Read-HmacSecret
try {
    $hmac = [Security.Cryptography.HMACSHA256]::new([Text.Encoding]::UTF8.GetBytes($secret))
    try {
        $signature = [Convert]::ToHexString($hmac.ComputeHash($canonical)).ToLowerInvariant()
    }
    finally {
        $hmac.Dispose()
    }
    Invoke-RestMethod -Uri $GatewayUri -Method Post -ContentType 'application/json; charset=utf-8' `
        -Headers @{
            'X-Event-ID' = $EventId
            'X-Timestamp' = $timestamp
            'X-Signature' = $signature
        } -Body $bodyBytes -TimeoutSec 15
}
finally {
    $secret = $null
    [Array]::Clear($bodyBytes, 0, $bodyBytes.Length)
    [Array]::Clear($canonical, 0, $canonical.Length)
}
