#Requires -Version 7.0
[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
param()

$ErrorActionPreference = 'Stop'
$credentialName = 'Agent:FeishuGateway:Hmac'

if (-not ('FeishuNotify.NativeWriteMethods' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

namespace FeishuNotify {
    public static class NativeWriteMethods {
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
        public static extern bool CredWrite(ref CREDENTIAL credential, uint flags);
    }
}
'@
}
$secureValue = Read-Host 'Paste the shared notification HMAC key' -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToCoTaskMemUnicode($secureValue)
try {
    $bytes = [checked]($secureValue.Length * 2)
    if ($bytes -eq 0) { throw 'Credential value cannot be empty.' }
    $credential = [FeishuNotify.NativeWriteMethods+CREDENTIAL]::new()
    $credential.Type = 1
    $credential.TargetName = $credentialName
    $credential.CredentialBlobSize = $bytes
    $credential.CredentialBlob = $pointer
    $credential.Persist = 2
    $credential.UserName = 'hmac'
    if ($PSCmdlet.ShouldProcess($credentialName, 'Store generic credential')) {
        if (-not [FeishuNotify.NativeWriteMethods]::CredWrite([ref]$credential, 0)) {
            throw [ComponentModel.Win32Exception]::new(
                [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            )
        }
        'Feishu gateway HMAC credential stored. No value was displayed.'
    }
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeCoTaskMemUnicode($pointer)
}
