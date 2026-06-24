packer {
  required_version = ">= 1.10.0"

  required_plugins {
    hyperv = {
      version = ">= 1.1.3"
      source  = "github.com/hashicorp/hyperv"
    }
  }
}

variable "vm_name" {
  type    = string
  default = "LabFoundry-Photon-Builder"
}

variable "output_directory" {
  type    = string
  default = "output/labfoundry-photon-hyperv"
}

variable "switch_name" {
  type    = string
  default = "LabFoundry-Mgmt"
}

variable "iso_url" {
  type        = string
  description = "Photon OS 5.0 ISO URL. Use the current Photon 5.0 full ISO from VMware package downloads."
}

variable "iso_checksum" {
  type        = string
  description = "Photon OS ISO checksum in Packer format, for example sha256:<checksum>."
}

variable "ssh_username" {
  type    = string
  default = "labfoundry-build"
}

variable "ssh_password" {
  type      = string
  default   = "LabFoundry-ChangeMe-Photon!"
  sensitive = true
}

variable "bootstrap_admin_password" {
  type        = string
  default     = ""
  sensitive   = true
  description = "Initial LabFoundry admin password. If empty, the build falls back to ssh_password for compatibility."
}

variable "ssh_host" {
  type        = string
  default     = null
  description = "Optional Photon guest SSH host override. Leave null to let the Hyper-V builder discover the guest IP through KVP."
}

variable "builder_static_ip" {
  type        = string
  default     = "192.168.49.30/24"
  description = "Static IP or CIDR for the installed Photon builder VM. Packer uses this address for SSH."
}

variable "builder_static_netmask" {
  type        = string
  default     = "255.255.255.0"
  description = "Netmask for builder_static_ip when using Photon legacy static kickstart networking."
}

variable "builder_static_gateway" {
  type        = string
  default     = "192.168.49.254"
  description = "Gateway for builder_static_ip. For LabFoundry-Mgmt this is the Windows host-side vEthernet address."
}

variable "builder_static_dns" {
  type        = list(string)
  default     = ["1.1.1.1", "9.9.9.9"]
  description = "DNS servers for builder_static_ip."
}

locals {
  builder_static_address   = var.builder_static_ip != "" ? split("/", var.builder_static_ip)[0] : ""
  bootstrap_admin_password = var.bootstrap_admin_password != "" ? var.bootstrap_admin_password : var.ssh_password
}

source "hyperv-iso" "photon" {
  vm_name            = var.vm_name
  output_directory   = var.output_directory
  switch_name        = var.switch_name
  generation         = 2
  enable_secure_boot = false
  cpus               = 2
  memory             = 4096
  disk_size          = 40960
  differencing_disk  = false
  headless           = true
  iso_url            = var.iso_url
  iso_checksum       = var.iso_checksum
  http_content = {
    "/photon-ks.json" = templatefile("${path.root}/http/photon-ks.json.pkrtpl", {
      root_password          = var.ssh_password
      build_username         = var.ssh_username
      build_password         = var.ssh_password
      builder_static_ip      = local.builder_static_address
      builder_static_netmask = var.builder_static_netmask
      builder_static_gateway = var.builder_static_gateway
      builder_static_dns     = var.builder_static_dns
    })
  }
  http_port_min = 8591
  http_port_max = 8591
  communicator  = "ssh"
  # Hyper-V auto-detects this through the guest KVP daemon. Use ssh_host only
  # as a fallback when Photon is reachable but Packer cannot infer the guest IP.
  ssh_host               = var.ssh_host != null ? var.ssh_host : (local.builder_static_address != "" ? local.builder_static_address : null)
  ssh_port               = 22
  ssh_username           = var.ssh_username
  ssh_password           = var.ssh_password
  ssh_timeout            = "45m"
  ssh_handshake_attempts = 200
  shutdown_command       = "echo '${var.ssh_password}' | sudo -S systemctl poweroff"

  boot_wait              = "2s"
  boot_keygroup_interval = "250ms"
  boot_command = [
    "c<wait>",
    "linux /isolinux/vmlinuz root=/dev/ram0 loglevel=3 ks=http://{{ .HTTPIP }}:{{ .HTTPPort }}/photon-ks.json insecure_installation=1 photon.media=cdrom",
    "<enter><wait>",
    "initrd /isolinux/initrd.img",
    "<enter><wait>",
    "boot",
    "<enter>"
  ]
}

build {
  name    = "labfoundry-photon-hyperv"
  sources = ["source.hyperv-iso.photon"]

  provisioner "shell" {
    inline = [
      "mkdir -p /tmp/labfoundry-src/scripts /tmp/labfoundry-src/image/hyperv"
    ]
  }

  provisioner "file" {
    source      = "../../labfoundry"
    destination = "/tmp/labfoundry-src/labfoundry"
  }

  provisioner "file" {
    source      = "../../pyproject.toml"
    destination = "/tmp/labfoundry-src/pyproject.toml"
  }

  provisioner "file" {
    source      = "../../README.md"
    destination = "/tmp/labfoundry-src/README.md"
  }

  provisioner "file" {
    source      = "../../scripts/appliance"
    destination = "/tmp/labfoundry-src/scripts/appliance"
  }

  provisioner "file" {
    source      = "../../scripts/check_photon_compatibility.py"
    destination = "/tmp/labfoundry-src/scripts/check_photon_compatibility.py"
  }

  provisioner "file" {
    source      = "systemd"
    destination = "/tmp/labfoundry-src/image/hyperv/systemd"
  }

  provisioner "file" {
    source      = "sudoers.d"
    destination = "/tmp/labfoundry-src/image/hyperv/sudoers.d"
  }

  provisioner "shell" {
    environment_vars = [
      "LABFOUNDRY_BOOTSTRAP_ADMIN_PASSWORD=${local.bootstrap_admin_password}"
    ]
    execute_command = "echo '${var.ssh_password}' | sudo -S -E sh -c '{{ .Vars }} {{ .Path }}'"
    script          = "${path.root}/scripts/provision-labfoundry.sh"
  }
}
