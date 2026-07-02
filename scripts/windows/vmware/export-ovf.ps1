[CmdletBinding()]
param(
    [string]$SourceVmxPath = 'image/vmware-workstation/output/labfoundry-photon-vmware-workstation/LabFoundry-Photon-Builder-VMware.vmx',
    [string]$OutputDirectory = '',
    [string]$Name = 'LabFoundry-Photon',
    [string]$OvfToolPath = '',
    [string]$TarPath = '',
    [switch]$NoOva,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$ovfNamespace = 'http://schemas.dmtf.org/ovf/envelope/1'
$rasdNamespace = 'http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_ResourceAllocationSettingData'
$vmwNamespace = 'http://www.vmware.com/schema/ovf'

function Resolve-OvfToolPath {
    param([string]$Path)

    if ($Path) {
        $candidate = if (Test-Path -LiteralPath $Path -PathType Container) {
            Join-Path $Path 'ovftool.exe'
        }
        else {
            $Path
        }
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            throw "ovftool was not found: $Path"
        }
        return (Resolve-Path -LiteralPath $candidate).Path
    }

    foreach ($candidate in @(
            'C:\Program Files\VMware\VMware Workstation\OVFTool\ovftool.exe',
            'C:\Program Files (x86)\VMware\VMware Workstation\OVFTool\ovftool.exe',
            'C:\Program Files\VMware\VMware OVF Tool\ovftool.exe',
            'C:\Program Files (x86)\VMware\VMware OVF Tool\ovftool.exe'
        )) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }

    $command = Get-Command ovftool -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    throw 'ovftool was not found. Install VMware Workstation or pass -OvfToolPath.'
}

function Resolve-TarPath {
    param([string]$Path)

    if ($Path) {
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
            throw "tar was not found: $Path"
        }
        return (Resolve-Path -LiteralPath $Path).Path
    }

    $command = Get-Command tar.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    $fallback = Join-Path $env:SystemRoot 'System32\tar.exe'
    if (Test-Path -LiteralPath $fallback -PathType Leaf) {
        return $fallback
    }
    throw 'tar.exe was not found. Pass -NoOva to keep only the OVF folder.'
}

function New-OvfAttribute {
    param(
        [xml]$Document,
        [string]$Name,
        [string]$Value
    )

    $attribute = $Document.CreateAttribute('ovf', $Name, $ovfNamespace)
    $attribute.Value = $Value
    return $attribute
}

function Set-OvfAttribute {
    param(
        [xml]$Document,
        [System.Xml.XmlElement]$Element,
        [string]$Name,
        [string]$Value
    )

    $existing = $Element.Attributes.GetNamedItem($Name, $ovfNamespace)
    if ($existing) {
        $existing.Value = $Value
        return
    }
    [void]$Element.Attributes.Append((New-OvfAttribute -Document $Document -Name $Name -Value $Value))
}

function Set-VmwAttribute {
    param(
        [xml]$Document,
        [System.Xml.XmlElement]$Element,
        [string]$Name,
        [string]$Value
    )

    $existing = $Element.Attributes.GetNamedItem($Name, $vmwNamespace)
    if ($existing) {
        $existing.Value = $Value
        return
    }
    $attribute = $Document.CreateAttribute('vmw', $Name, $vmwNamespace)
    $attribute.Value = $Value
    [void]$Element.Attributes.Append($attribute)
}

function Add-TextElement {
    param(
        [xml]$Document,
        [System.Xml.XmlElement]$Parent,
        [string]$LocalName,
        [string]$Value
    )

    $element = $Document.CreateElement('ovf', $LocalName, $ovfNamespace)
    $element.InnerText = $Value
    [void]$Parent.AppendChild($element)
    return $element
}

function Get-OvfProperty {
    param(
        [System.Xml.XmlElement]$ProductSection,
        [string]$Key
    )

    foreach ($node in $ProductSection.GetElementsByTagName('Property', $ovfNamespace)) {
        $existingKey = $node.Attributes.GetNamedItem('key', $ovfNamespace)
        if ($existingKey -and $existingKey.Value -eq $Key) {
            return $node
        }
    }
    return $null
}

function Set-LabFoundryOvfProperty {
    param(
        [xml]$Document,
        [System.Xml.XmlElement]$ProductSection,
        [string]$Key,
        [string]$Label,
        [string]$Description,
        [bool]$Required,
        [bool]$Password = $false
    )

    $property = Get-OvfProperty -ProductSection $ProductSection -Key $Key
    if (-not $property) {
        $property = $Document.CreateElement('ovf', 'Property', $ovfNamespace)
        [void]$ProductSection.AppendChild($property)
    }
    Set-OvfAttribute -Document $Document -Element $property -Name 'key' -Value $Key
    Set-OvfAttribute -Document $Document -Element $property -Name 'type' -Value 'string'
    Set-OvfAttribute -Document $Document -Element $property -Name 'userConfigurable' -Value 'true'
    Set-OvfAttribute -Document $Document -Element $property -Name 'required' -Value ($Required.ToString().ToLowerInvariant())
    if ($Password) {
        Set-VmwAttribute -Document $Document -Element $property -Name 'password' -Value 'true'
    }

    foreach ($childName in @('Label', 'Description')) {
        foreach ($child in @($property.GetElementsByTagName($childName, $ovfNamespace))) {
            [void]$property.RemoveChild($child)
        }
    }
    [void](Add-TextElement -Document $Document -Parent $property -LocalName 'Label' -Value $Label)
    [void](Add-TextElement -Document $Document -Parent $property -LocalName 'Description' -Value $Description)
}

function Add-LabFoundryOvfProperties {
    param([string]$OvfPath)

    [xml]$document = Get-Content -LiteralPath $OvfPath -Raw
    $document.PreserveWhitespace = $false
    if (-not $document.DocumentElement.HasAttribute('xmlns:vmw')) {
        $document.DocumentElement.SetAttribute('xmlns:vmw', $vmwNamespace)
    }
    if (-not $document.DocumentElement.HasAttribute('xmlns:rasd')) {
        $document.DocumentElement.SetAttribute('xmlns:rasd', $rasdNamespace)
    }

    $manager = New-Object System.Xml.XmlNamespaceManager($document.NameTable)
    $manager.AddNamespace('ovf', $ovfNamespace)
    $virtualSystem = $document.SelectSingleNode('//ovf:VirtualSystem', $manager)
    if (-not $virtualSystem) {
        throw "OVF descriptor does not contain an ovf:VirtualSystem: $OvfPath"
    }

    $productSection = $document.SelectSingleNode('//ovf:VirtualSystem/ovf:ProductSection[@ovf:class="labfoundry"]', $manager)
    if (-not $productSection) {
        $productSection = $document.CreateElement('ovf', 'ProductSection', $ovfNamespace)
        Set-OvfAttribute -Document $document -Element $productSection -Name 'class' -Value 'labfoundry'
        [void](Add-TextElement -Document $document -Parent $productSection -LocalName 'Info' -Value 'LabFoundry deployment properties')
        [void](Add-TextElement -Document $document -Parent $productSection -LocalName 'Product' -Value 'LabFoundry Photon Appliance')
        $hardwareSection = $document.SelectSingleNode('//ovf:VirtualSystem/ovf:VirtualHardwareSection', $manager)
        if ($hardwareSection) {
            [void]$virtualSystem.InsertBefore($productSection, $hardwareSection)
        }
        else {
            [void]$virtualSystem.AppendChild($productSection)
        }
    }

    $hardware = $document.SelectSingleNode('//ovf:VirtualSystem/ovf:VirtualHardwareSection', $manager)
    if ($hardware) {
        Set-OvfAttribute -Document $document -Element $hardware -Name 'transport' -Value 'com.vmware.guestInfo'
    }

    Set-LabFoundryOvfProperty -Document $document -ProductSection $productSection -Key 'labfoundry.cidr' -Label 'Management IP CIDR' -Description 'Static management address for eth0, for example 192.168.10.10/24.' -Required $true
    Set-LabFoundryOvfProperty -Document $document -ProductSection $productSection -Key 'labfoundry.gateway' -Label 'Management gateway' -Description 'IPv4 gateway used by the management interface.' -Required $true
    Set-LabFoundryOvfProperty -Document $document -ProductSection $productSection -Key 'labfoundry.fqdn' -Label 'Appliance FQDN' -Description 'Fully qualified appliance name applied to Photon OS and LabFoundry desired state.' -Required $true
    Set-LabFoundryOvfProperty -Document $document -ProductSection $productSection -Key 'labfoundry.dns_servers' -Label 'DNS servers' -Description 'One or more resolver IPs separated by commas, spaces, or new lines.' -Required $true
    Set-LabFoundryOvfProperty -Document $document -ProductSection $productSection -Key 'labfoundry.ntp_servers' -Label 'NTP servers' -Description 'Optional NTP server names or IPs. If blank, the image defaults are kept.' -Required $false
    Set-LabFoundryOvfProperty -Document $document -ProductSection $productSection -Key 'labfoundry.admin_password' -Label 'LabFoundry admin password' -Description 'Initial LabFoundry web admin password. The value is consumed on first boot and not logged.' -Required $true -Password $true
    Set-LabFoundryOvfProperty -Document $document -ProductSection $productSection -Key 'labfoundry.root_password' -Label 'Photon root password' -Description 'Photon root console password for recovery. Root SSH remains disabled by default.' -Required $true -Password $true

    $settings = New-Object System.Xml.XmlWriterSettings
    $settings.Indent = $true
    $settings.Encoding = [System.Text.UTF8Encoding]::new($false)
    $writer = [System.Xml.XmlWriter]::Create($OvfPath, $settings)
    try {
        $document.Save($writer)
    }
    finally {
        $writer.Close()
    }
}

function Update-OvfManifest {
    param([string]$OvfDirectory)

    $ovf = Get-ChildItem -LiteralPath $OvfDirectory -Filter '*.ovf' -File | Select-Object -First 1
    if (-not $ovf) {
        throw "No .ovf descriptor found in $OvfDirectory"
    }
    $manifest = Join-Path $OvfDirectory "$([System.IO.Path]::GetFileNameWithoutExtension($ovf.Name)).mf"
    $files = Get-ChildItem -LiteralPath $OvfDirectory -File |
    Where-Object { $_.Extension -notin @('.mf', '.ova') } |
    Sort-Object Name
    $lines = foreach ($file in $files) {
        $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "SHA256($($file.Name))= $hash"
    }
    [System.IO.File]::WriteAllLines($manifest, [string[]]$lines, [System.Text.UTF8Encoding]::new($false))
    return $manifest
}

function New-OvaArchive {
    param(
        [string]$OvfDirectory,
        [string]$OvaPath,
        [string]$ResolvedTarPath
    )

    if (Test-Path -LiteralPath $OvaPath -PathType Leaf) {
        Remove-Item -LiteralPath $OvaPath -Force
    }
    $ovf = Get-ChildItem -LiteralPath $OvfDirectory -Filter '*.ovf' -File | Select-Object -First 1
    $manifest = Get-ChildItem -LiteralPath $OvfDirectory -Filter '*.mf' -File | Select-Object -First 1
    if (-not $ovf -or -not $manifest) {
        throw "Cannot package OVA because OVF or manifest is missing in $OvfDirectory"
    }
    $otherFiles = Get-ChildItem -LiteralPath $OvfDirectory -File |
    Where-Object { $_.Name -notin @($ovf.Name, $manifest.Name) -and $_.Extension -ne '.ova' } |
    Sort-Object Name |
    ForEach-Object { $_.Name }
    $arguments = @('-cf', $OvaPath, '-C', $OvfDirectory, $ovf.Name, $manifest.Name) + $otherFiles
    & $ResolvedTarPath @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "tar failed while creating OVA with exit code $LASTEXITCODE."
    }
}

function Get-OvfDescriptorPath {
    param([string]$OutputDirectory)

    $ovfFiles = @(Get-ChildItem -LiteralPath $OutputDirectory -Filter '*.ovf' -File)
    if ($ovfFiles.Count -eq 0) {
        $ovfFiles = @(Get-ChildItem -LiteralPath $OutputDirectory -Filter '*.ovf' -File -Recurse)
    }
    if ($ovfFiles.Count -eq 0) {
        throw "ovftool did not produce an OVF descriptor under $OutputDirectory"
    }
    if ($ovfFiles.Count -gt 1) {
        $paths = ($ovfFiles | ForEach-Object { $_.FullName }) -join ', '
        throw "ovftool produced multiple OVF descriptors under $OutputDirectory`: $paths"
    }
    return $ovfFiles[0].FullName
}

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..')).Path
$resolvedSourceVmx = (Resolve-Path -LiteralPath $SourceVmxPath).Path
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $repoRoot "image\vmware-workstation\ovf\$Name"
}
$resolvedOutputDirectory = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputDirectory)
$resolvedOvfTool = Resolve-OvfToolPath -Path $OvfToolPath
$resolvedTar = if ($NoOva) { '' } else { Resolve-TarPath -Path $TarPath }

if (Test-Path -LiteralPath $resolvedOutputDirectory) {
    if (-not $Force) {
        throw "OVF output directory already exists: $resolvedOutputDirectory. Pass -Force to replace it."
    }
    Remove-Item -LiteralPath $resolvedOutputDirectory -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $resolvedOutputDirectory | Out-Null

& $resolvedOvfTool --acceptAllEulas $resolvedSourceVmx $resolvedOutputDirectory
if ($LASTEXITCODE -ne 0) {
    throw "ovftool failed with exit code $LASTEXITCODE."
}

$ovfPath = Get-OvfDescriptorPath -OutputDirectory $resolvedOutputDirectory
$ovfPackageDirectory = Split-Path -Parent $ovfPath
Add-LabFoundryOvfProperties -OvfPath $ovfPath
$manifestPath = Update-OvfManifest -OvfDirectory $ovfPackageDirectory

$ovaPath = ''
if (-not $NoOva) {
    $ovaPath = Join-Path (Split-Path -Parent $resolvedOutputDirectory) "$Name.ova"
    New-OvaArchive -OvfDirectory $ovfPackageDirectory -OvaPath $ovaPath -ResolvedTarPath $resolvedTar
}

Write-Host "LabFoundry OVF export root: $resolvedOutputDirectory"
Write-Host "LabFoundry OVF folder: $ovfPackageDirectory"
Write-Host "LabFoundry OVF descriptor: $ovfPath"
Write-Host "LabFoundry OVF manifest: $manifestPath"
if ($ovaPath) {
    Write-Host "LabFoundry OVA archive: $ovaPath"
}
