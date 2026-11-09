# Run once, in an ELEVATED PowerShell on the test VM: OpenSSH server, key login for the host, firewall open to the host only.
# The host's public key replaces @PUBKEY@ (see README.md); never commit a real key here.
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd; Set-Service sshd -StartupType Automatic
Set-Content -Force -Encoding ascii C:\ProgramData\ssh\administrators_authorized_keys '@PUBKEY@'
icacls C:\ProgramData\ssh\administrators_authorized_keys /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"
New-ItemProperty -Path HKLM:\SOFTWARE\OpenSSH -Name DefaultShell -Value "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -PropertyType String -Force
# The capability's own firewall rule covers the Private profile only, and a libvirt NAT network is classed Public:
# without this, port 22 is silently dropped. Limited to the host (192.168.122.1).
Set-NetFirewallRule -Name OpenSSH-Server-In-TCP -Profile Any -RemoteAddress 192.168.122.1
Write-Host "`nDONE. Your username is:" (whoami)
