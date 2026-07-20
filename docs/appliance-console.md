# Local appliance console

Photon appliances reserve only the first virtual terminal (`tty1`) for the LabFoundry local appliance console. Other virtual terminals keep the normal Photon login prompt; use `Alt+F2`, `Alt+F3`, and later function-key terminals for ordinary local login.

The console is a read-mostly recovery surface using the same pale blue, slate, blue, green, amber, and red palette as the web UI, mapped to the colors supported by the Linux virtual console. Its pale-blue header keeps the LabFoundry version beside the appliance title, leaves one separating row, then shows Photon OS, kernel, CPU, memory, and 1/5/15-minute load averages on separate rows. A complete pale-blue spacer row and a blank body row separate Load from the management URLs. The management block uses stable table columns for Interface, IPv4/GW/mode, IPv6/GW/mode, DNS and Firewall state. Exceptional control-plane or maintenance-isolation state shares the Firewall row when needed. Healthy state is implied; the console does not display a generic `ONLINE` label. It refreshes every 5 seconds by default and after resize or completed actions without continuously clearing the screen. Set `LABFOUNDRY_CONSOLE_REFRESH_SECONDS` in `/etc/labfoundry/labfoundry.env` to an integer from 1 through 300 and restart `labfoundry-console.service` to change the normal refresh interval. Invalid values fall back to 5 seconds. A terminal smaller than 72 columns by 22 rows shows only the resize requirement.

Press `F3` from the main screen to hand `tty1` temporarily to the interactive `top` process. Exit `top` with `q` to restore and redraw the LabFoundry console. This diagnostic view does not grant a shell and does not reuse F2/F12 authorization.

Press `F4` to open an interactive root Bash login session on `tty1`. F4 requires the Photon root password on every entry and records shell open/close audit events as `console:root`. Use `exit` or `Ctrl+D` to close Bash and restore a physically cleared appliance screen. The password is not retained, logged, or reused by another menu.

Every entry into `F2 Customize` requires the Photon `root` password and authorization is discarded when the menu closes. It intentionally exposes only:

- management IPv4 and IPv6 modes, addresses, and gateways for the existing management interface, together with external DNS servers in the same editor;
- persistent Firewall enable/disable state; and
- reversible **Disable all appliance services** maintenance isolation.

The management-network editor uses one compact framed form for IPv4, IPv6, and DNS. Move between controls with `Tab`, `Shift+Tab`, or Up/Down; change mode selectors with Left/Right; and edit text at the cursor with Left/Right, Home/End, Backspace, and Delete. Address and gateway fields are disabled unless their family is in Static mode. Closing the editor physically clears its window before rebuilding the Customize menu. Password prompts use the same cursor-aware text editing behavior.

IPv4 supports DHCP or static address/prefix with an optional on-link gateway. IPv6 is independent and supports Disabled, Automatic RA/SLAAC, or static address/prefix with an optional gateway. A static IPv6 gateway must be within the configured prefix or link-local (`fe80::/10`) and cannot equal the interface address. Selecting Disabled or Automatic clears stored static IPv6 address and gateway state.

Network, DNS, and Firewall edits update LabFoundry desired state and create one synchronous global `appliance-apply` task with `console:root` as the actor. The combined management editor selects Network and Appliance Settings together, plus Firewall only if the address change alters its valid rendered state. Other unrelated pending units remain unselected. The console rejects a change while another appliance-apply task is active and forces the selected reviewed helper path to run as a real local recovery action even when ordinary adapters are configured for dry-run. Validation or apply failures leave the new desired state pending for web review and show a bounded local error; the console never falls back to unvalidated host commands.

Disabling Firewall persists `FirewallSettings.enabled=false` and applies the existing ruleset-clearing configuration. Enabling it rebuilds current LabFoundry rules. This state survives reboot, remains visible in the web UI, and is independent of service isolation.

Maintenance isolation snapshots service enable/active state under `/var/lib/labfoundry/console/services.json`, then stops and disables the appliance application services. It deliberately preserves `labfoundry-console.service`, `systemd-networkd.service`, `systemd-resolved.service`, and `labfoundry-firewall.service`. The F2 menu changes to **Restore appliance services** while isolation is active and restores only units that were enabled or active in the snapshot.

Every entry into `F12` separately requires the root password. Restart and shutdown require confirmation and use the delayed, auditable constrained appliance-power helper. `F12` remains separate from the F2 customization list.

## Runtime ownership

`labfoundry-console.service` owns `/dev/tty1`, conflicts with and replaces only `getty@tty1.service`, and restarts automatically. Image provisioning masks `getty@tty1.service` without changing the getty template or later virtual terminals. The appliance also sets systemd `ShowStatus=no`: unit progress remains available in the journal but is not written over the full-screen tty1 UI. Completed recovery actions and return from interactive processes force a physical redraw. Additional bounded redraws at 1, 3, and 8 seconds repair late terminal writes while startup or service jobs settle, without introducing continuous redraw flicker.
