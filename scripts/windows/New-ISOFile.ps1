function New-ISOFile {
    <#
    .SYNOPSIS
        Create an ISO file from a source folder.

    .DESCRIPTION
        Creates an ISO file from a source folder using the Windows IMAPI2
        interfaces. This helper is vendored from TheDotSource/New-ISOFile so
        LabFoundry Hyper-V builds do not depend on downloading build tooling at
        runtime.

    .PARAMETER source
        Source folder to add to the ISO.

    .PARAMETER destinationIso
        ISO file to create.

    .PARAMETER bootFile
        Optional boot image to add to the ISO.

    .PARAMETER media
        IMAPI media type for the resulting ISO.

    .PARAMETER title
        ISO volume title.

    .PARAMETER force
        Overwrite an existing ISO file.
    #>
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Low')]
    param(
        [Parameter(Mandatory = $true, ValueFromPipeline = $false)]
        [string]$source,

        [Parameter(Mandatory = $true, ValueFromPipeline = $false)]
        [string]$destinationIso,

        [Parameter(Mandatory = $false, ValueFromPipeline = $false)]
        [string]$bootFile = $null,

        [Parameter(Mandatory = $false, ValueFromPipeline = $false)]
        [ValidateSet('CDR', 'CDRW', 'DVDRAM', 'DVDPLUSR', 'DVDPLUSRW', 'DVDPLUSR_DUALLAYER', 'DVDDASHR', 'DVDDASHRW', 'DVDDASHR_DUALLAYER', 'DISK', 'DVDPLUSRW_DUALLAYER', 'BDR', 'BDRE')]
        [string]$media = 'DVDPLUSRW_DUALLAYER',

        [Parameter(Mandatory = $false, ValueFromPipeline = $false)]
        [string]$title = 'untitled',

        [Parameter(Mandatory = $false, ValueFromPipeline = $false)]
        [switch]$force
    )

    $sourceItem = Get-Item -LiteralPath $source -ErrorAction Stop
    if (-not $sourceItem.PSIsContainer) {
        throw "Source path must be a directory: $source"
    }

    if ((Test-Path -LiteralPath $destinationIso) -and -not $force) {
        throw "Destination ISO already exists: $destinationIso"
    }

    if ($bootFile -and -not (Test-Path -LiteralPath $bootFile)) {
        throw "Boot file does not exist: $bootFile"
    }

    $destinationParent = Split-Path -Parent $destinationIso
    if ($destinationParent) {
        New-Item -ItemType Directory -Force -Path $destinationParent | Out-Null
    }
    if (Test-Path -LiteralPath $destinationIso) {
        Remove-Item -LiteralPath $destinationIso -Force
    }

    $typeDefinition = @'
public class ISOFile  {
    public unsafe static void Create(string Path, object Stream, int BlockSize, int TotalBlocks) {
        int bytes = 0;
        byte[] buf = new byte[BlockSize];
        var ptr = (System.IntPtr)(&bytes);
        var o = System.IO.File.OpenWrite(Path);
        var i = Stream as System.Runtime.InteropServices.ComTypes.IStream;

        if (o != null) {
            while (TotalBlocks-- > 0) {
                i.Read(buf, BlockSize, ptr); o.Write(buf, 0, bytes);
            }

            o.Flush(); o.Close();
        }
    }
}
'@

    if (-not ('ISOFile' -as [type])) {
        switch ($PSVersionTable.PSVersion.Major) {
            { $_ -ge 7 } {
                Add-Type -CompilerOptions '/unsafe' -TypeDefinition $typeDefinition
            }
            5 {
                $compilerOptions = New-Object System.CodeDom.Compiler.CompilerParameters
                $compilerOptions.CompilerOptions = '/unsafe'
                Add-Type -CompilerParameters $compilerOptions -TypeDefinition $typeDefinition
            }
            default {
                throw 'Unsupported PowerShell version. New-ISOFile requires Windows PowerShell 5.1 or PowerShell 7+.'
            }
        }
    }

    $mediaType = @(
        'UNKNOWN',
        'CDROM',
        'CDR',
        'CDRW',
        'DVDROM',
        'DVDRAM',
        'DVDPLUSR',
        'DVDPLUSRW',
        'DVDPLUSR_DUALLAYER',
        'DVDDASHR',
        'DVDDASHRW',
        'DVDDASHR_DUALLAYER',
        'DISK',
        'DVDPLUSRW_DUALLAYER',
        'HDDVDROM',
        'HDDVDR',
        'HDDVDRAM',
        'BDROM',
        'BDR',
        'BDRE'
    )

    $fsi = New-Object -ComObject IMAPI2FS.MsftFileSystemImage
    $fsi.FileSystemsToCreate = 4
    $fsi.VolumeName = $title
    $fsi.ChooseImageDefaultsForMediaType($mediaType.IndexOf($media))

    if ($bootFile) {
        $bootOptions = New-Object -ComObject IMAPI2FS.BootOptions
        $bootOptions.Manufacturer = 'LabFoundry'
        $bootOptions.PlatformId = 0
        $bootStream = New-Object -ComObject ADODB.Stream
        $bootStream.Open()
        $bootStream.Type = 1
        $bootStream.LoadFromFile((Resolve-Path -LiteralPath $bootFile).Path)
        $bootOptions.AssignBootImage($bootStream)
        $fsi.BootImageOptions = $bootOptions
    }

    $fsi.Root.AddTree($sourceItem.FullName, $false)
    $result = $fsi.CreateResultImage()

    if ($PSCmdlet.ShouldProcess($destinationIso, 'Create ISO file')) {
        [ISOFile]::Create($destinationIso, $result.ImageStream, $result.BlockSize, $result.TotalBlocks)
    }
}
